# Noribox Gallery API

Django + Django REST Framework backend for **Noribox Gallery**, a multi-category
e-commerce platform (first category: perfumes). The catalog is
category-independent (categories, brands and attributes are admin-managed), so
phones, watches, accessories etc. need no code changes.

* Storefront API: `/api/v1/` · Management API (RBAC): `/api/admin/`
* OpenAPI: `/api/schema/` · Swagger UI: `/api/docs/` · ReDoc: `/api/redoc/`
* Health: `/api/health/` · Django admin: `/django-admin/`
* Endpoint reference and example payloads: [`docs/API.md`](docs/API.md)

## Core business rules

| Area | Rule |
|---|---|
| Currency | Integer Toman everywhere (`PositiveBigIntegerField`) |
| Auth | Phone + password, phone + OTP (JWT access/refresh, refresh blacklisting) |
| Inventory | Each `ProductVariant` is **one** sellable unit. State: `available` / `reserved` / `unavailable`. No numeric stock. |
| Cart | Authenticated users only; `CartItem.quantity` is always 1 (DB check constraint) |
| Orders | Creating an order validates availability but **does not reserve** |
| Receipt upload | Stores the receipt (private storage), then attempts an **all-or-nothing** reservation. On conflict the receipt is kept and the order is flagged `needs_review`. |
| Approval | Reservation → sale; variant becomes `unavailable` (`sold=true`) |
| Rejection | Reservation released, except variants an admin independently marked `unavailable`; customer may upload a new receipt |
| Imports | Staged as `SourceProduct`; never published automatically; source prices only update `source_price`, never `price` |
| WhatsApp | Frontend builds `wa.me` links from `store-settings.whatsapp_number` |

Concurrency: reservations lock `VariantAvailability` rows with
`SELECT … FOR UPDATE` in variant-id order, and a partial unique constraint
(`uniq_active_reservation_per_variant`) guarantees at most one active
reservation per variant.

## Apps

| App | Responsibility |
|---|---|
| `core` | Base models, error format, pagination, RBAC permission, file validation, private storage, health, scheduled-job commands |
| `accounts` | Custom `User` (phone login), OTP, addresses, admin users/roles |
| `catalog` | Category, Brand, Attribute, AttributeOption, CategoryAttribute, Product, ProductCategory, ProductAttributeValue, ProductVariant, ProductImage |
| `inventory` | VariantAvailability, Reservation + transaction-safe services |
| `categories` | (existing) source-site categories, mappable to catalog categories |
| `products` | (existing parser/importer) SourceProduct staging, ImportJob, ImportItem, ImportErrorLog, review → draft product |
| `commerce` | Cart, CartItem, Order, OrderItem, OrderStatusHistory, reports |
| `payments` | PaymentMethod, StoreBankCard, Payment, PaymentReceipt, PaymentReview |
| `shipping` | ShippingMethod (configurable cost / free threshold), Shipment (tracking) |
| `notifications` | Provider-independent SMS (console / Kavenegar / custom), templates, logs, retries |
| `cms` | Pages, blog, FAQ, banners, sliders, SEO metadata, store settings |

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set POSTGRES_PASSWORD, DJANGO_SECRET_KEY …
createdb noribox                # or use DB_ENGINE=sqlite for a quick try
python manage.py migrate
python manage.py setup_roles    # RBAC groups
python manage.py seed_defaults  # bank-transfer method, shipping methods, store settings
python manage.py createsuperuser
python manage.py runserver
python manage.py test           # uses config.settings.test automatically
```

`manage.py` defaults to `config.settings.dev` (and `config.settings.test` for
`manage.py test`); `wsgi.py`/`asgi.py` default to `config.settings.prod`.
All variables are documented in [`.env.example`](.env.example).

## Roles (RBAC)

`python manage.py setup_roles` creates Django groups; assign them to staff users
(`/api/admin/users/{id}/` → `groups`). Every admin endpoint requires
`is_staff` **and** the matching model permission (or the custom permission
listed); superusers have everything.

| Role | Scope |
|---|---|
| `catalog_manager` | Catalog CRUD, publish (`catalog.publish_product`), inventory (`inventory.manage_inventory`), imports & review |
| `order_manager` | Orders + status transitions (`commerce.manage_order_status`), shipments, shipping methods |
| `finance` | Payment review (`payments.review_payment`), payment methods, bank cards |
| `content_editor` | CMS + store settings |
| `support` | Read users/orders, SMS logs, SMS retry |
| `notifications_manager` | SMS templates and logs |

Changing `is_staff`/roles requires superuser or `accounts.manage_roles`.

## Imports

```bash
python manage.py import_source_products --fixture products/fixtures/mehrdadgallery_shop_sample.html --dry-run
python manage.py import_source_products --url https://mehrdadgallery.com/shop/ --limit 50
```

Each run creates an `ImportJob` with per-item actions (`created`, `updated`,
`unchanged`, `would_create`, `duplicate`, `error`…) and field-level diffs.
Admins then review `SourceProduct`s via `/api/admin/imports/source-products/`
and either `create-draft` (always a **draft** product), `link-variant`, or `ignore`.
Products without a WooCommerce ID (e.g. out-of-stock cards) are keyed by a
deterministic permalink hash (`url:…`).

## Scheduled jobs

There is no Celery dependency; run one idempotent command from cron/systemd every 5 minutes:

```cron
*/5 * * * * cd /srv/noribox && .venv/bin/python manage.py run_scheduled_jobs >> /var/log/noribox/jobs.log 2>&1
```

It (1) expires reservations older than `RESERVATION_TTL_HOURS` (released and the
order flagged for review), (2) cancels orders unpaid for `UNPAID_ORDER_TTL_HOURS`,
(3) retries failed SMS with exponential backoff up to `SMS_MAX_ATTEMPTS`.
Use `--only reservations|orders|sms` to run one task.

## Deployment

1. Provision PostgreSQL 14+ and Python 3.12+.
2. Set environment variables (`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
   `POSTGRES_*`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, SMS keys). Prod
   settings refuse to start with the default secret key.
3. `pip install -r requirements.txt`
4. `python manage.py migrate && python manage.py setup_roles && python manage.py seed_defaults && python manage.py collectstatic --noinput`
5. Run `gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3` behind nginx (TLS terminated at nginx, which must send `X-Forwarded-Proto`).
6. nginx: serve `/static/` from `STATIC_ROOT` and `/media/` from `MEDIA_ROOT`. **Never** expose `PRIVATE_MEDIA_ROOT` — receipts are streamed only by the permission-checked endpoints. Set `client_max_body_size 10m`.
7. Install the cron entry above. Back up the database and both media directories.

### Migrating from the prototype

Migration `products.0003` renames the prototype `products.Product` table to
`SourceProduct` (data preserved). The project now uses a custom user model
(`accounts.User`, phone-based). A database created by the prototype (which used
`auth.User`) cannot switch user models in place: create a fresh database,
run `migrate`, and re-run `import_source_products` (source data is fully
re-importable). No production database existed for the prototype.
