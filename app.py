import os
import ssl
import urllib.parse
from base64 import b64decode
from base64 import b64encode
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from types import TracebackType
from typing import AsyncIterator
from typing import Optional
from typing import Type
from urllib.parse import urlparse
from urllib.parse import urlunparse

import aiohttp
import certifi
import orjson
import redis.asyncio as redis
from dotenv import load_dotenv
from multidict import CIMultiDict
from multidict import CIMultiDictProxy
from sanic import HTTPResponse
from sanic import Request
from sanic import Sanic
from sanic import redirect
from sanic.log import logger

from cache import DISK_CACHE
from utils import parse_kv_pairs
from worker import do_vercel_get
from worker import flush_redis

load_dotenv()

DISKCACHE_VERSION_KEY = "cache_version"

background_tasks = set()


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
            raise ValueError("redis not instance not set")
        return self._redis

    @redis.setter
    def redis(self, redis_: "redis.Redis"):
        self._redis = redis_


class CustomSanic(Sanic):
    def __init__(self, *args, ctx: Optional[AppContext] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx: AppContext = ctx or AppContext()


VERCEL_URL = "https://github-readme-stats-tuokri.vercel.app"

app = CustomSanic(
    name=__name__,
    ctx=AppContext(
        vercel_url=VERCEL_URL,
    ),
)
app.config.AUTO_EXTEND = False
app.config.LOGGING = False

app.ctx.cache = DISK_CACHE


def _set_cache(key: str, value: bytes):
    app.ctx.cache[key] = value


async def set_disk_cache(key: str, value: bytes):
    future = app.loop.run_in_executor(None, _set_cache, key, value)
    await future


def _get_cache(key: str) -> dict:
    data = {}

    cached = app.ctx.cache.get(key)

    if cached:
        data = orjson.loads(cached)

    if "headers" in data:
        data["headers"] = CIMultiDict(data["headers"])

    return data


async def get_disk_cache(key: str) -> dict:
    future = app.loop.run_in_executor(None, _get_cache, key)
    return await future


async def _do_vercel_get(vercel_url: str, vercel_route: str):
    logger.info("scheduling Vercel get")
    try:
        do_vercel_get.delay(vercel_url=vercel_url, vercel_route=vercel_route)
    except Exception as e:
        logger.error("failed to schedule Vercel get: %s: %s",
                     type(e).__name__, e)
    finally:
        app.purge_tasks()


async def _schedule_vercel_get_task(vercel_url: str, vercel_route: str):
    t = app.add_task(_do_vercel_get(vercel_url, vercel_route))
    logger.info("added task %s", t)
    background_tasks.add(t)
    t.add_done_callback(background_tasks.discard)


class VercelSession(AbstractAsyncContextManager["VercelSession"]):

    def __init__(self, request: Request) -> None:
        self._resp: Optional[aiohttp.ClientResponse] = None

        self._vercel_route = urlunparse((
            "",
            "",
            request.path,
            None,
            request.query_string,
            None,
        ))

        self._headers = CIMultiDictProxy(CIMultiDict())
        self._payload = b""

    @property
    def headers(self) -> CIMultiDictProxy:
        return self._headers

    async def __aenter__(self) -> "VercelSession":
        await self._get()
        return self

    async def __aexit__(
            self,
            __exc_type: Type[BaseException] | None,
            __exc_value: BaseException | None,
            __traceback: TracebackType | None,
    ) -> bool | None:
        return None

    async def _redis_set(self, data: bytes):
        try:
            await app.ctx.redis.set(self._vercel_route, data)

            ttl = 7500
            cc = self._headers.get("cache-control")
            if cc:
                try:
                    kvs = parse_kv_pairs(cc)
                    ttl = int(kvs.get("max-age", 7500))
                except ValueError as e:
                    logger.warning(
                        "error parsing cache control headers: %s: %s: %s",
                        cc, type(e).__name__, e)

            await app.ctx.redis.expire(self._vercel_route, ttl)

        except redis.ConnectionError as ce:
            logger.error("error setting redis data: %s: %s",
                         type(ce).__name__, ce, exc_info=False)

    async def _redis_get(self):
        try:
            redis_data = await app.ctx.redis.get(self._vercel_route)
            if redis_data:
                redis_dict = orjson.loads(redis_data)
                self._headers = CIMultiDict(redis_dict.get("headers"))
                self._payload = b64decode(redis_dict.get("payload"))
        except redis.ConnectionError as ce:
            logger.error("error getting redis data: %s: %s",
                         type(ce).__name__, ce)

    async def _diskcache_set(self, data: bytes):
        try:
            await set_disk_cache(self._vercel_route, data)
        except Exception as e:
            logger.info("error getting diskcache data: %s: %s",
                        type(e).__name__, e)

    async def _diskcache_get(self):
        try:
            disk_data = await get_disk_cache(self._vercel_route)
            if disk_data:
                self._headers = CIMultiDict(disk_data.get("headers"))
                self._payload = b64decode(disk_data.get("payload"))
        except Exception as e:
            logger.info("error setting diskcache data: %s: %s",
                        type(e).__name__, e)

    async def _vercel_get(self):
        logger.info("performing vercel get immediately")

        async with app.ctx.aiohttp_session.get(
                url=self._vercel_route,
                ssl_context=app.ctx.ssl_context) as resp:
            self._headers = resp.headers.copy()

            # print(f"_vercel_get headers: {pformat(self._headers)}")

            self._payload = b""
            async for chunk in resp.content.iter_chunked(4096):
                self._payload += chunk

        dump_headers = {str(k): str(v) for k, v in self._headers.items()}
        dump_payload = b64encode(self._payload).decode("utf-8")
        json_dump = orjson.dumps({
            "headers": dump_headers,
            "payload": dump_payload,
        })

        await self._redis_set(json_dump)
        await self._diskcache_set(json_dump)

        logger.info("vercel get results cached")

    async def _get(self):
        await self._redis_get()
        if self._headers and self._payload:
            return

        logger.info("redis cache miss for: '%s'", self._vercel_route)

        await self._diskcache_get()

        # Schedule always to refresh cache in the background.
        await _schedule_vercel_get_task(app.ctx.vercel_url, self._vercel_route)

        if self._headers and self._payload:
            return

        logger.info("diskcache miss for '%s'", self._vercel_route)

        # Not found in any caches, gotta do a Vercel get immediately.
        await self._vercel_get()

    async def iter_chunked(self) -> AsyncIterator[bytes]:
        # TODO: stream directly from Redis or Diskcache
        #  here instead of caching the payload in VercelSession
        #  instance variables?
        yield self._payload


async def vercel_get(request: Request):
    if not request.url:
        raise PermissionError

    async with VercelSession(request) as vs:
        response = await request.respond(
            headers={
                "content-encoding": vs.headers.get("content-encoding", ""),
            },
            content_type=vs.headers.get("content-type", ""),
        )
        async for data in vs.iter_chunked():
            await response.send(data)


@app.before_server_start
async def before_server_start(_app: CustomSanic):
    _app.ctx.aiohttp_session = aiohttp.ClientSession(
        base_url=_app.ctx.vercel_url,
        auto_decompress=False,
    )

    _app.ctx.redis = redis.StrictRedis(
        # connection_pool=REDIS_POOL,
    ).from_url(os.environ["REDIS_URL"])
    logger.info("created redis instance: %s", _app.ctx.redis)

    try:
        await _app.ctx.redis.ping()
    except Exception as e:
        logger.error(f"error connecting to redis: %s", e)
        logger.exception(e)


# noinspection PyBroadException
@app.after_server_stop
async def after_server_stop(_app: CustomSanic):
    try:
        await _app.ctx.aiohttp_session.close()
    except Exception:
        pass
    try:
        await _app.ctx.redis.close()
    except Exception:
        pass


@app.get("/api/<_endpoint:(.+)>/")
async def api_endpoint(request: Request, _endpoint: str) -> HTTPResponse:
    try:
        await vercel_get(request)
    except PermissionError:
        return HTTPResponse(status=400)


@app.get("/api/")
async def api_root(request: Request) -> HTTPResponse:
    try:
        await vercel_get(request)
    except PermissionError:
        return HTTPResponse(status=400)


@app.get("/")
async def root(*_) -> HTTPResponse:
    return redirect("https://github.com/tuokri/github-readme-stats-cache")


@app.exception
async def on_exception(request: Request, exc: Exception) -> HTTPResponse:
    logger.error("error on request:%s: %s: %s", request, type(exc).__name__, exc)
    logger.exception()
    return HTTPResponse(status=500)


@app.after_server_start
async def after_server_start(*_):
    # Pre-warm caches.
    # TODO: probably don't want to run this in each worker.
    # TODO: hard-coding these is kinda tedious.
    do_vercel_get.delay(
        VERCEL_URL,
        ("/api?username=tuokri&count_private=true&theme=default&"
         "show_icons=true&include_all_commits=true"),
    )
    do_vercel_get.delay(
        VERCEL_URL,
        ("/api?username=tuokri&count_private=true&theme=synthwave&"
         "show_icons=true&include_all_commits=true"),
    )
    do_vercel_get.delay(
        VERCEL_URL,
        ("/api/top-langs/?username=tuokri&layout=compact&"
         "theme=default&langs_count=10&count_private=true&"
         "size_weight=0.6&count_weight=0.4&"
         "exclude_repo=github-readme-stats,DPP,mumble,UnrealEngine,"
         "pyspellchecker,ftp-tail,SquadJS,CnC_Remastered_Collection,UDK-Lite,UE3-LibHTTP"),
    )
    do_vercel_get.delay(
        VERCEL_URL,
        ("/api/top-langs/?username=tuokri&layout=compact&"
         "theme=synthwave&langs_count=10&count_private=true&"
         "size_weight=0.6&count_weight=0.4&"
         "exclude_repo=github-readme-stats,DPP,mumble,UnrealEngine,"
         "pyspellchecker,ftp-tail,SquadJS,CnC_Remastered_Collection,UDK-Lite,UE3-LibHTTP"),
    )


@app.main_process_start
async def main_process_start(*_):
    from _version import __version__
    cache_version = app.ctx.cache.get(DISKCACHE_VERSION_KEY)
    logger.info("cache_version: %s", cache_version)
    logger.info("__version__: %s", __version__)
    if cache_version != __version__:
        logger.info("version changed, clearing cache")
        flush_redis.delay()
        app.ctx.cache.clear(retry=True)
        app.ctx.cache.set(DISKCACHE_VERSION_KEY, __version__)


if __name__ == "__main__":
    app.run(debug=True, dev=True, workers=4)
