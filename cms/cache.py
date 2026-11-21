"""Versioned cache for public CMS payloads (navigation, homepage, settings).

Any change to content the payloads depend on bumps the version (see cms.signals),
so stale entries are never served after an admin edit; the timeout is only a bound.
"""
from django.core.cache import cache

VERSION_KEY = "cms:version"
TIMEOUT = 300


def version() -> int:
    v = cache.get(VERSION_KEY)
    if v is None:
        v = 1
        cache.add(VERSION_KEY, v, None)
    return v


def bump() -> None:
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, 2, None)


def cached(name: str, builder, timeout: int = TIMEOUT):
    key = f"cms:{version()}:{name}"
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, timeout)
    return value
