"""Production settings. Fails fast when required configuration is missing."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import DB_ENGINE, SECRET_KEY, env, env_bool, env_int, env_list


def _require(name, value):
    if not value:
        raise ImproperlyConfigured(f"{name} must be set in production")
    return value


DEBUG = False
if not SECRET_KEY or SECRET_KEY.startswith("django-insecure") or len(SECRET_KEY) < 32:
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY (>= 32 chars) in production")

ALLOWED_HOSTS = _require("DJANGO_ALLOWED_HOSTS", env_list("DJANGO_ALLOWED_HOSTS", ""))
if "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must not contain '*' in production")
if DB_ENGINE != "postgresql":
    raise ImproperlyConfigured("Production requires PostgreSQL (DB_ENGINE=postgresql)")
_require("POSTGRES_PASSWORD", env("POSTGRES_PASSWORD", ""))
_require("REDIS_URL", env("REDIS_URL", ""))  # shared throttling / lockout state across workers

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
REST_FRAMEWORK = {**REST_FRAMEWORK}  # noqa: F405
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = ["rest_framework.renderers.JSONRenderer"]
