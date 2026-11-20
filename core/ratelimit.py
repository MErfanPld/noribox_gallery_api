"""Per-identifier failure counters in the shared cache (e.g. failed logins per phone)."""
from django.conf import settings
from django.core.cache import cache


def _key(scope, ident):
    return f"rl:{scope}:{ident}"


def is_locked(scope, ident, limit):
    return (cache.get(_key(scope, ident)) or 0) >= limit


def register_failure(scope, ident, window):
    key = _key(scope, ident)
    if cache.add(key, 1, timeout=window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.add(key, 1, timeout=window)
        return 1


def reset(scope, ident):
    cache.delete(_key(scope, ident))


def login_locked(phone):
    return is_locked("login", phone, settings.LOGIN_MAX_FAILURES)


def login_failed(phone):
    return register_failure("login", phone, settings.LOGIN_FAILURE_WINDOW_SECONDS)
