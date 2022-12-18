import asyncio
import ctypes
import multiprocessing as mp
import os
import random
import shlex
import ssl
import sys
import urllib.parse
from base64 import b64decode
from base64 import b64encode
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator
from typing import Dict
from typing import Optional
from urllib.parse import urlparse
from urllib.parse import urlunparse

import aiohttp
import certifi
import diskcache
import orjson
import redis.asyncio as redis
from dotenv import load_dotenv
from multidict import CIMultiDict
from multidict import CIMultiDictProxy
from sanic import HTTPResponse
from sanic import Request
from sanic import Sanic
from sanic import redirect

load_dotenv()

# API_CACHE_FILE = "cache_api.json"

REDIS_POOL = redis.ConnectionPool.from_url(
    url=os.environ["REDIS_URL"]
)


class AppContext(SimpleNamespace):
    def __init__(self, vercel_url: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._vercel_url: str = vercel_url or ""
        self._vercel_url_components: urllib.parse.ParseResult = urlparse(self._vercel_url)
        self._aiohttp_session: Optional[aiohttp.ClientSession] = None
        self._redis: Optional[redis.Redis] = None
        self._vercel_session: Optional[VercelSession] = None

        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    @property
    def vercel_session(self) -> "VercelSession":
        if not self._vercel_session:
            raise RuntimeError("vercel_session not initialized")
        return self._vercel_session

    @vercel_session.setter
    def vercel_session(self, vercel_session: "VercelSession"):
        self._vercel_session = vercel_session

    # TODO: refactor this into VercelSession.
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


async def refresh_cache():
    """TODO: refresh evicted keys from Vercel. Maybe store them longer in
             Redis than what Vercel cache max-age is? Just return the cached
             copy and trigger a new fetch from Vercel so the next request
             will return the newest version?
    """
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

        with app.shared_ctx.cache_refresh_needed.get_lock():
            # print(_app.shared_ctx.cache_refresh_needed.value)
            if app.shared_ctx.cache_refresh_needed.value:
                app.shared_ctx.cache_refresh_needed.value = False
                refreshing = True

        if refreshing:
            await asyncio.sleep(360)
            with app.shared_ctx.cache_refresh_needed.get_lock():
                app.shared_ctx.cache_refresh_needed.value = True
                refreshing = False

        await asyncio.sleep(10)


# app.add_task(refresh_cache())


def parse_kv_pairs(s: str) -> dict:
    lexer = shlex.shlex(s.replace(" ", ""), posix=True)
    lexer.whitespace = ","
    lexer.wordchars += "=-"
    # noinspection PyTypeChecker
    return dict(word.split("=", maxsplit=1) for word in lexer)


# async def load_disk_cache(cache_file: str) -> dict:
#     data = {}
#     if cache_file:
#         with app.shared_ctx.file_lock:
#             try:
#                 if await aiofiles.os.path.isfile(cache_file):
#                     async with aiofiles.open(cache_file, "rb") as f:
#                         raw = await f.read()
#                         print(f"read {len(raw)} bytes from {cache_file}")
#                         if raw:
#                             data = orjson.loads(raw)
#             except Exception as e:
#                 print(f"load_disk_cache error: {e}")
#     return data


def _set_cache(key: str, value: bytes):
    with app.shared_ctx.cache_lock:
        app.shared_ctx.cache[key] = value


async def set_disk_cache(key: str, value: bytes):
    future = app.loop.run_in_executor(None, _set_cache, key, value)
    await future


def _get_cache(key: str) -> dict:
    data = {}

    with app.shared_ctx.cache_lock:
        data = orjson.loads(app.shared_ctx.cache.get(key))

    if data["headers"]:
        data["headers"] = CIMultiDict(data["headers"])

    return data


async def get_disk_cache(key: str) -> dict:
    future = app.loop.run_in_executor(None, _get_cache, key)
    return await future


class VercelSession:
    def __init__(self) -> None:
        self._resp: Optional[aiohttp.ClientResponse] = None
        self._vercel_route = ""
        # self._cache_file = API_CACHE_FILE
        # TODO: turn this into a dataclass or separate instance variables.
        self._cached_data: Dict[
            str,
            Dict[str, CIMultiDictProxy[str] | bytes | str]] = {}

    @property
    def cached_data(self) -> dict:
        return self._cached_data

    @cached_data.setter
    def cached_data(self, data: dict):
        self._cached_data = data

    @asynccontextmanager
    async def get(self, request: Request) -> AsyncIterator[CIMultiDictProxy]:
        self._vercel_route = urlunparse((
            "",
            "",
            request.path,
            None,
            request.query_string,
            None,
        ))

        # TODO: if not cached in redis
        #  -> check disk cache
        #  -> return disk value
        #  -> schedule vercel api fetch with caching in redis
        #  -> subsequent queries return redis cached value
        #     while key is still present in redis

        vr = self._vercel_route
        if await app.ctx.redis.exists(vr):
            headers = self._cached_data.get(vr, {}).get("headers")
            if headers:
                yield headers
                return

        disk_cached = await get_disk_cache(vr)
        if disk_cached:
            headers = disk_cached.get("headers")
            if headers:
                yield headers
                # schedule vercel refresh
                return

        async with app.ctx.aiohttp_session.get(
                url=vr, ssl_context=app.ctx.ssl_context) as resp:
            self._resp = resp
            yield self._resp.headers

    async def iter_chunked(self, n: int = 4096) -> AsyncIterator[bytes]:
        vr = self._vercel_route
        if await app.ctx.redis.exists(vr):
            payload_bytes = b""
            mem_cached = self._cached_data.get(vr, {})
            if not mem_cached:
                cached = await app.ctx.redis.get(vr)
                if cached:
                    cached_dict = orjson.loads(cached)
                    payload = cached_dict.get("payload", "")
                    if payload:
                        payload_bytes = b64decode(payload)
                        self._cached_data[vr] = {
                            "headers": self._resp.headers,
                            "payload": payload_bytes,
                        }
            else:
                payload_bytes = self._cached_data.get(vr, {}).get("payload", b"")

            if payload_bytes:
                yield payload_bytes
                return
        else:
            disk_cached = await get_disk_cache(vr)
            if disk_cached:
                payload_bytes = disk_cached.get("payload", b"")
                if payload_bytes:
                    yield b64decode(payload_bytes)
                    # schedule vercel refresh
                    return

        chunks = b""
        async for chunk in self._resp.content.iter_chunked(n):
            chunks += chunk
            yield chunk

        dump_headers = {str(k): str(v) for k, v in self._resp.headers.items()}
        dump_payload = b64encode(chunks).decode("utf-8")
        json_dump = orjson.dumps({
            "headers": dump_headers,
            "payload": dump_payload,
        })
        await app.ctx.redis.set(vr, json_dump)

        self._cached_data[vr] = {
            "headers": self._resp.headers,
            "payload": chunks,
        }

        await set_disk_cache(key=vr, value=json_dump)

        cc = self._resp.headers.get("cache-control")
        if cc:
            kvs = parse_kv_pairs(cc)
            try:
                ttl = int(kvs.get("max-age", 1000))
                await app.ctx.redis.expire(vr, ttl)
            except ValueError:
                pass


async def vercel_get(request: Request):
    async with app.ctx.vercel_session.get(request) as vercel_headers:
        vercel_headers: CIMultiDictProxy  # type: ignore[no-redef]
        response = await request.respond(
            headers={
                "content-encoding": vercel_headers.get("content-encoding", ""),
            },
            content_type=vercel_headers.get("content-type"),
        )
        async for data in app.ctx.vercel_session.iter_chunked():
            await response.send(data)


@app.before_server_start
async def before_server_start(_app: CustomSanic):
    _app.ctx.aiohttp_session = aiohttp.ClientSession(
        base_url=_app.ctx.vercel_url,
        auto_decompress=False,
    )

    _app.ctx.vercel_session = VercelSession()

    _app.ctx.redis = redis.StrictRedis(
        connection_pool=REDIS_POOL,
    )
    try:
        await _app.ctx.redis.ping()
    except Exception as e:
        print(f"error connecting to redis: {e}")

    with _app.shared_ctx.cache_refresh_needed.get_lock():
        if not _app.shared_ctx.cache_refresh_needed.value:
            _app.shared_ctx.cache_refresh_needed.value = True

    # _app.ctx.vercel_session.cached_data = await load_disk_cache(
    #     cache_file=API_CACHE_FILE)


@app.after_server_stop
async def after_server_stop(_app: CustomSanic):
    await _app.ctx.aiohttp_session.close()
    await _app.ctx.redis.close()


@app.main_process_start
async def main_process_start(_app: CustomSanic):
    _app.shared_ctx.cache_refresh_needed = mp.Value(ctypes.c_bool, True)
    _app.shared_ctx.cache = diskcache.Cache(
        directory="./.cache/",
        size_limit=178956970,  # ~0.167 GiB
    )
    _app.shared_ctx.cache_lock = diskcache.RLock(
        cache=_app.shared_ctx.cache,
        key="rlock",
    )


@app.get("/api/<_endpoint:(.*)>/")
async def api_endpoint(request: Request, _endpoint: str):
    await vercel_get(request)


@app.get("/api/")
async def api_root(request: Request):
    await vercel_get(request)


@app.get("/")
async def root(*_) -> HTTPResponse:
    return redirect("https://github.com/tuokri/github-readme-stats-cache")


if __name__ == "__main__":
    print(sys.version)
    app.run(debug=True, dev=True, workers=4)
