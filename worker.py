import os
import traceback
from base64 import b64encode
from traceback import print_exc
from urllib.parse import urlparse
from urllib.parse import urlunparse

import celery
import orjson
import redis
import requests
import urllib3
from celery import Task
from celery.utils.log import get_task_logger
from dotenv import load_dotenv

from cache import DISK_CACHE
from utils import parse_kv_pairs

logger = get_task_logger(__name__)

load_dotenv()

REDIS_FLUSHED_RECENTLY_KEY = "redis_flushed_recently"
REDIS_URL = os.environ["REDIS_URL"]

app = celery.Celery(
    "worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

redis_instance = redis.StrictRedis.from_url(REDIS_URL)

cache = DISK_CACHE


# cache_lock = CACHE_LOCK


class BaseTask(Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            resp = redis_instance.ping()
            print(f"{redis}: ping response: {resp}")
        except Exception as e:
            print(f"error connecting to redis: {e}")
            traceback.print_exception(e)

    def run(self, *args, **kwargs):
        super().run(*args, **kwargs)


def set_redis(key: str, value: bytes, ttl: int):
    # noinspection PyBroadException
    try:
        # print(f"set_redis: key={key}")
        resp = redis_instance.set(key, value)
        # print(f"redis set: {resp}")
        resp = redis_instance.expire(key, ttl)
        # print(f"redis expire: {resp}")
    except Exception:
        print_exc()


def set_cache(key: str, value: bytes):
    # noinspection PyBroadException
    try:
        # with cache_lock:
        # print(f"set_cache: key={key}")
        cache.set(key, value)
    except Exception:
        print_exc()


@app.task(base=BaseTask, ignore_result=True)
def do_vercel_get(vercel_url: str, vercel_route: str):
    logger.info("do_vercel_get: '%s' '%s'",
                vercel_url, vercel_route)

    vercel_url_parts = urlparse(vercel_url)
    vercel_route_parts = urlparse(vercel_route)
    # scheme, netloc, url, params, query, fragment
    url = urlunparse((
        vercel_url_parts.scheme,
        vercel_url_parts.netloc,
        vercel_route_parts.path,
        None,
        vercel_route_parts.query,
        None,
    ))

    resp = requests.get(url, timeout=30, stream=True)
    headers = resp.headers
    raw_response: urllib3.HTTPResponse = resp.raw
    data: bytes = b""
    for stream_bytes in raw_response.stream(1024, decode_content=False):
        data += stream_bytes

    # print(f"do_vercel_get headers: {pformat(headers)}")

    dump_headers = {str(k): str(v) for k, v in headers.items()}
    dump_payload = b64encode(data).decode("utf-8")
    json_dump = orjson.dumps({
        "headers": dump_headers,
        "payload": dump_payload,
    })

    url_parts = urlparse(url)
    key = urlunparse((
        "",
        "",
        url_parts.path,
        None,
        url_parts.query,
        None,
    ))

    set_cache(key, json_dump)

    ttl = 7500
    cc = headers.get("cache-control")
    if cc:
        try:
            kvs = parse_kv_pairs(cc)
            ttl = int(kvs.get("max-age", 7500))
        except ValueError as e:
            logger.warning(
                "error parsing cache control headers: %s: %s: %s",
                cc, type(e).__name__, e)
    set_redis(key, json_dump, ttl)


@app.task(base=BaseTask, ignore_result=True)
def flush_redis():
    if not redis_instance.get(REDIS_FLUSHED_RECENTLY_KEY):
        print("flushing redis")
        redis_instance.set(REDIS_FLUSHED_RECENTLY_KEY, 1)
        redis_instance.expire(REDIS_FLUSHED_RECENTLY_KEY, 30)
        redis_instance.flushdb(asynchronous=True)
