"""Cross-process 'only one at a time' guard for scheduled jobs."""
import contextlib
import hashlib

from django.core.cache import cache
from django.db import connection


def _key(name):
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


@contextlib.contextmanager
def exclusive(name, timeout=3600):
    """Yield True if this process holds the lock, False if another one does.

    PostgreSQL: session-level advisory lock (released automatically if the
    process dies). Other databases: shared-cache lock with a timeout.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_key(name)])
            acquired = cur.fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", [_key(name)])
        return
    acquired = cache.add(f"lock:{name}", 1, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            cache.delete(f"lock:{name}")
