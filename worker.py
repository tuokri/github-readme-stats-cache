import os
from base64 import b64encode
from traceback import print_exc
from urllib.parse import urlparse
from urllib.parse import urlunparse

import celery
import orjson
import redis
import requests
from celery import Task
from dotenv import load_dotenv

from cache import CACHE_LOCK
from cache import DISK_CACHE
from utils import parse_kv_pairs

load_dotenv()

REDIS_URL = os.environ["REDIS_URL"]
x = urlparse(REDIS_URL)
REDIS_URL = urlunparse(x._replace(path="/0"))

app = celery.Celery(
    "worker",
    broker=REDIS_URL,
    # backend=os.environ["REDIS_URL"],
)
redis = redis.StrictRedis(os.environ["REDIS_URL"])
cache = DISK_CACHE
cache_lock = CACHE_LOCK


class BaseTask(Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        x = redis.ping()
        print(f"{redis}: ping response: {x}")

    def run(self, *args, **kwargs):
        super().run(*args, **kwargs)


def set_redis(key: str, value: bytes, ttl: int):
    # noinspection PyBroadException
    try:
        redis.set(key, value)
        redis.expire(key, ttl)
    except Exception:
        print_exc()


def set_cache(key: str, value: bytes):
    # noinspection PyBroadException
    try:
        with cache_lock:
            cache.set(key, value)
    except Exception:
        print_exc()


@app.task(base=BaseTask)
def do_vercel_get(vercel_url: str, vercel_route: str):
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
    data = b""
    for chunk in resp.iter_content():
        data += chunk

    dump_headers = {str(k): str(v) for k, v in headers.items()}
    dump_payload = b64encode(data).decode("utf-8")
    json_dump = orjson.dumps({
        "headers": dump_headers,
        "payload": dump_payload,
    })

    set_cache(url, json_dump)

    ttl = 7500
    cc = headers.get("cache-control")
    if cc:
        kvs = parse_kv_pairs(cc)
        try:
            ttl = int(kvs.get("max-age", 7500))
        except ValueError:
            pass
    set_redis(url, json_dump, ttl)
