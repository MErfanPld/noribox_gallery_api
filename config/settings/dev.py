from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env, env_bool

# Safety net: a production host sets DJANGO_ENV=production, so it can never
# silently boot development settings (e.g. a manage.py command without
# DJANGO_SETTINGS_MODULE=config.settings.prod).
if env("DJANGO_ENV", "development").lower() == "production":
    raise ImproperlyConfigured(
        "DJANGO_ENV=production but development settings were loaded; "
        "set DJANGO_SETTINGS_MODULE=config.settings.prod"
    )

DEBUG = env_bool("DJANGO_DEBUG", True)
CORS_ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS or ["http://localhost:3000"]  # noqa: F405
