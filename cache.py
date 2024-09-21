import os

import diskcache

# Production deployment.
if "FLY_APP_NAME" in os.environ:
    DISKCACHE_DIR = os.environ["DISKCACHE_DIR"]
else:
    DISKCACHE_DIR = "./.diskcache/"

DISK_CACHE = diskcache.FanoutCache(
    directory=DISKCACHE_DIR,
    size_limit=524288000,  # 500 MiB.
    shards=4,
)
