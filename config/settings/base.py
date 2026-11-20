"""Base settings shared by every environment. Values come from environment variables."""
from datetime import timedelta

from config.env import BASE_DIR, env, env_bool, env_int, env_list

SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    # local
    "core",
    "accounts",
    "catalog",
    "inventory",
    "categories",  # source (imported) categories
    "products",  # source products staging + import jobs
    "shipping",
    "commerce",
    "payments",
    "notifications",
    "cms",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------
DB_ENGINE = env("DB_ENGINE", "postgresql")
if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "noribox"),
            "USER": env("POSTGRES_USER", "noribox"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
        }
    }

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE", "fa")
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True

# --- Static / media / private files ----------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = env("STATIC_ROOT", str(BASE_DIR / "staticfiles"))
MEDIA_URL = "/media/"
MEDIA_ROOT = env("MEDIA_ROOT", str(BASE_DIR / "media"))
# Private files (payment receipts). MUST NOT be served by the web server.
PRIVATE_MEDIA_ROOT = env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "private_media"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
FILE_UPLOAD_PERMISSIONS = 0o640
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF / JWT / OpenAPI ----------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "core.schema.NoriboxAutoSchema",
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", "120/min"),
        "user": env("THROTTLE_USER", "600/min"),
        "auth": env("THROTTLE_AUTH", "10/min"),
        "otp_request": env("THROTTLE_OTP_REQUEST", "5/min"),
        "otp_verify": env("THROTTLE_OTP_VERIFY", "10/min"),
        "receipt_upload": env("THROTTLE_RECEIPT_UPLOAD", "10/hour"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_MINUTES", 30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_DAYS", 14)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Noribox Gallery API",
    "DESCRIPTION": (
        "Multi-category e-commerce backend. Public storefront API under /api/v1/, "
        "role-based management API under /api/admin/. Prices are integer Toman."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/(v1|admin)",
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "ENUM_NAME_OVERRIDES": {
        "OrderStatusEnum": "commerce.models.Order.Status",
        "PaymentStatusEnum": "payments.models.Payment.Status",
        "ReceiptStatusEnum": "payments.models.PaymentReceipt.Status",
        "ShipmentStatusEnum": "shipping.models.Shipment.Status",
        "ReservationStatusEnum": "inventory.models.Reservation.Status",
        "ImportJobStatusEnum": "products.models.ImportJob.Status",
        "SMSStatusEnum": "notifications.models.SMSLog.Status",
        "PublishStatusEnum": "core.models.PublishStatus",
    },
    "SECURITY": [{"jwtAuth": []}],
}

# --- CORS / CSRF ------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
CORS_ALLOW_CREDENTIALS = False
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:3000")

# --- Logging ----------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "noribox": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# --- Business configuration -------------------------------------------------
# OTP
OTP_LENGTH = env_int("OTP_LENGTH", 6)
OTP_TTL_SECONDS = env_int("OTP_TTL_SECONDS", 120)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
OTP_MAX_PER_HOUR = env_int("OTP_MAX_PER_HOUR", 5)
OTP_LOGIN_AUTO_REGISTER = env_bool("OTP_LOGIN_AUTO_REGISTER", True)

# Inventory / orders
RESERVATION_TTL_HOURS = env_int("RESERVATION_TTL_HOURS", 72)
UNPAID_ORDER_TTL_HOURS = env_int("UNPAID_ORDER_TTL_HOURS", 48)
ORDER_MAX_ITEMS = env_int("ORDER_MAX_ITEMS", 50)

# Receipts
RECEIPT_MAX_SIZE_BYTES = env_int("RECEIPT_MAX_SIZE_BYTES", 5 * 1024 * 1024)
RECEIPT_ALLOWED_EXTENSIONS = env_list("RECEIPT_ALLOWED_EXTENSIONS", "jpg,jpeg,png,webp,pdf")
PRODUCT_IMAGE_MAX_SIZE_BYTES = env_int("PRODUCT_IMAGE_MAX_SIZE_BYTES", 8 * 1024 * 1024)

# SMS
SMS_ENABLED = env_bool("SMS_ENABLED", False)
SMS_PROVIDER = env("SMS_PROVIDER", "console")  # console | kavenegar | dummy
SMS_API_KEY = env("SMS_API_KEY", "")
SMS_SENDER = env("SMS_SENDER", "")
SMS_MAX_ATTEMPTS = env_int("SMS_MAX_ATTEMPTS", 3)
SMS_RETRY_BASE_SECONDS = env_int("SMS_RETRY_BASE_SECONDS", 60)
SMS_ADMIN_PHONES = env_list("SMS_ADMIN_PHONES", "")
SMS_TIMEOUT_SECONDS = env_int("SMS_TIMEOUT_SECONDS", 10)

# Source import (existing parser/importer)
SOURCE_SITE_ID = env("SOURCE_SITE_ID", "mehrdadgallery")
SOURCE_BASE_URL = env("SOURCE_BASE_URL", "https://mehrdadgallery.com")
IMPORT_DEFAULT_LIMIT = env_int("IMPORT_DEFAULT_LIMIT", 0) or None
IMPORT_REQUEST_TIMEOUT = env_int("IMPORT_REQUEST_TIMEOUT", 15)
IMPORT_MAX_PAGES = env_int("IMPORT_MAX_PAGES", 50)
IMPORT_USER_AGENT = env(
    "IMPORT_USER_AGENT",
    "Mozilla/5.0 (compatible; NoriboxImportBot/1.0)",
)
