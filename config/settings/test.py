from .base import *  # noqa: F401,F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SMS_ENABLED = True
SMS_PROVIDER = "dummy"
SMS_ADMIN_PHONES = ["09120000000"]
MEDIA_ROOT = "/tmp/noribox-test-media"
PRIVATE_MEDIA_ROOT = "/tmp/noribox-test-private"
REST_FRAMEWORK = {**REST_FRAMEWORK}  # noqa: F405
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    k: "10000/min" for k in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
}
LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "CRITICAL"}}
