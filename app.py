import asyncio
import ctypes
import multiprocessing as mp
import os
import random
import ssl
import urllib.parse
from contextlib import asynccontextmanager
from types import SimpleNamespace
from types import TracebackType
from typing import AsyncIterator
from typing import Optional
from typing import Type
from urllib.parse import urlparse
from urllib.parse import urlunparse

import aiohttp
import certifi
import redis.asyncio as redis
from dotenv import load_dotenv
from multidict import CIMultiDictProxy
from sanic import Request
from sanic import Sanic

load_dotenv()

REDIS_POOL = redis.ConnectionPool(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    username=os.environ["REDIS_USERNAME"],
    password=os.environ["REDIS_PASSWORD"],
)


class AppContext(SimpleNamespace):
    def __init__(self, vercel_url: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._vercel_url: str = vercel_url or ""
        self._vercel_url_components: urllib.parse.ParseResult = urlparse(self._vercel_url)
        self._aiohttp_session: Optional[aiohttp.ClientSession] = None
        self._redis: Optional[redis.Redis] = None

        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    @property
    def vercel_url(self) -> str:
        return self._vercel_url

    @property
    def vercel_url_components(self) -> urllib.parse.ParseResult:
        return self._vercel_url_components

    @property
    def aiohttp_session(self) -> aiohttp.ClientSession:
        if not self._aiohttp_session:
            self._aiohttp_session = aiohttp.ClientSession(
                base_url=self.vercel_url,
                auto_decompress=False,
            )
        return self._aiohttp_session

    @aiohttp_session.setter
    def aiohttp_session(self, session: aiohttp.ClientSession):
        self._aiohttp_session = session

    @property
    def redis(self) -> "redis.Redis":
        if not self._redis:
            raise RuntimeError("redis not instance not set")
        return self._redis

    @redis.setter
    def redis(self, redis_: "redis.Redis"):
        self._redis = redis_


class CustomSanic(Sanic):
    def __init__(self, *args, ctx: Optional[AppContext] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx: AppContext = ctx or AppContext()


app = CustomSanic(
    name=__name__,
    ctx=AppContext(
        vercel_url="https://github-readme-stats-tuokri.vercel.app",
    ),
)


async def refresh_cache(_app: CustomSanic):
    refreshing = False

    # Should probably wait her for all workers to initialize
    # before going into the refresh loop. This sleep is a cheap
    # workaround to help with unwanted concurrent cache refreshes
    # that happen when workers are started and other workers
    # that are still initializing flip cache_refresh_needed state
    # while one of the workers is already inside the loop.
    await asyncio.sleep(5 + random.random())

    while True:
        # print("hi from", os.getpid())

        with _app.shared_ctx.cache_refresh_needed.get_lock():
            # print(_app.shared_ctx.cache_refresh_needed.value)
            if _app.shared_ctx.cache_refresh_needed.value:
                _app.shared_ctx.cache_refresh_needed.value = False
                refreshing = True

        if refreshing:
            # print("doing refresh in", os.getpid(), "*" * 80, time.time())
            await asyncio.sleep(5)
            with _app.shared_ctx.cache_refresh_needed.get_lock():
                _app.shared_ctx.cache_refresh_needed.value = True
                refreshing = False

        await asyncio.sleep(2)


app.add_task(refresh_cache)


class VercelSession:
    def __init__(self, request: Request):
        self._resp: Optional[aiohttp.ClientResponse] = None
        self._vercel_route = urlunparse((
            "",
            "",
            request.path,
            None,
            request.query_string,
            None,
        ))

    async def __aenter__(self):
        return self

    async def __aexit__(
            self,
            __exc_type: Optional[Type[BaseException]],
            __exc_value: Optional[BaseException],
            __traceback: Optional[TracebackType],
    ):
        return None

    @asynccontextmanager
    async def get(self) -> AsyncIterator[CIMultiDictProxy]:
        if app.ctx.redis:
            pass

        async with app.ctx.aiohttp_session.get(
                self._vercel_route, ssl_context=app.ctx.ssl_context) as resp:
            self._resp = resp
            yield self._resp.headers

    async def iter_chunked(self, n: int = 4096) -> AsyncIterator[bytes]:
        if not self._resp:
            raise RuntimeError("cannot begin streaming before get is performed")
        # TODO: cache response+bytes with Redis here?
        async for chunk in self._resp.content.iter_chunked(n):
            yield chunk


async def vercel_get(request: Request):
    async with VercelSession(request) as vs:
        async with vs.get() as vercel_headers:
            vercel_headers: CIMultiDictProxy  # type: ignore[no-redef]
            response = await request.respond(
                headers={
                    "Content-Encoding": vercel_headers.get("Content-Encoding"),
                },
                content_type=vercel_headers.get("Content-Type"),
            )
            async for data in vs.iter_chunked():
                await response.send(data)


@app.before_server_start
async def before_server_start(_app: CustomSanic):
    _app.ctx.aiohttp_session = aiohttp.ClientSession(
        base_url=_app.ctx.vercel_url,
        auto_decompress=False,
    )

    _app.ctx.redis = redis.Redis(
        connection_pool=REDIS_POOL,
    )
    try:
        await _app.ctx.redis.ping()
    except Exception as e:
        print(f"error connecting to redis: {e}")

    with _app.shared_ctx.cache_refresh_needed.get_lock():
        if not _app.shared_ctx.cache_refresh_needed.value:
            _app.shared_ctx.cache_refresh_needed.value = True


@app.after_server_stop
async def after_server_stop(_app: CustomSanic):
    await _app.ctx.aiohttp_session.close()


@app.main_process_start
async def main_process_start(_app: CustomSanic):
    _app.shared_ctx.cache_refresh_needed = mp.Value(ctypes.c_bool, True)


@app.get("/api/<_endpoint:(.*)>/")
async def api_endpoint(request: Request, _endpoint: str):
    await vercel_get(request)


@app.get("/api/")
async def api_root(request: Request):
    await vercel_get(request)


if __name__ == "__main__":
    app.run(debug=True, dev=True, workers=4)
