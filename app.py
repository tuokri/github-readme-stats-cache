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
from sanic import Request
from sanic import Sanic


class AppContext(SimpleNamespace):
    def __init__(self, vercel_url: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._vercel_url: str = vercel_url or ""
        self._vercel_url_components: urllib.parse.ParseResult = urlparse(self._vercel_url)
        self._aiohttp_session: Optional[aiohttp.ClientSession] = None

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
    async def get(self) -> AsyncIterator[aiohttp.ClientResponse]:
        async with app.ctx.aiohttp_session.get(
                self._vercel_route, ssl_context=app.ctx.ssl_context) as resp:
            self._resp = resp
            yield self._resp

    async def iter_chunked(self, n: int = 4096) -> AsyncIterator[bytes]:
        if not self._resp:
            raise RuntimeError("cannot begin streaming before get is performed")
        # TODO: cache response+bytes with Redis here?
        async for chunk in self._resp.content.iter_chunked(n):
            yield chunk


async def vercel_get(request: Request):
    async with VercelSession(request) as vs:
        async with vs.get() as vercel_resp:
            vercel_resp: aiohttp.ClientResponse
            response = await request.respond(
                headers={
                    "Content-Encoding": vercel_resp.headers.get("Content-Encoding"),
                },
                content_type=vercel_resp.content_type,
            )
            async for data in vs.iter_chunked():
                await response.send(data)


@app.before_server_start
async def before_server_start(_app: CustomSanic):
    _app.ctx.aiohttp_session = aiohttp.ClientSession(
        base_url=_app.ctx.vercel_url,
        auto_decompress=False,
    )


@app.after_server_stop
async def after_server_stop(_app: CustomSanic):
    await _app.ctx.aiohttp_session.close()


@app.get("/api/<_endpoint:(.*)>/")
async def api_endpoint(request: Request, _endpoint: str):
    await vercel_get(request)


@app.get("/api/")
async def api_root(request: Request):
    await vercel_get(request)


if __name__ == "__main__":
    app.run(debug=True, dev=True, workers=4)
