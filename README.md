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
pip install -r requirements.txt -c constraints.txt
cp .env.example .env            # set POSTGRES_PASSWORD, DJANGO_SECRET_KEY, REDIS_URL …
createdb noribox                # or use DB_ENGINE=sqlite for a quick try
python manage.py migrate
python manage.py setup_roles    # RBAC groups
python manage.py seed_defaults  # bank-transfer method, shipping methods, store settings
python manage.py createsuperuser
python manage.py runserver
python manage.py test           # uses config.settings.test automatically
```

Settings selection: `.env` is loaded first, then `DJANGO_SETTINGS_MODULE` decides.
Without it, `manage.py` uses `config.settings.dev` and `wsgi.py`/`asgi.py` use
`config.settings.prod`; `manage.py test` always uses `config.settings.test`.
On production hosts set `DJANGO_SETTINGS_MODULE=config.settings.prod` **and**
`DJANGO_ENV=production` (dev settings then refuse to boot).
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

The admin API (`POST /api/admin/imports/jobs/`) only **queues** a job (HTTP 202);
`manage.py process_import_jobs` executes queued jobs outside the request cycle.
Jobs are claimed atomically (no double execution), send heartbeats, and a job
whose worker died is re-queued (up to `IMPORT_MAX_ATTEMPTS`) or failed by
`run_scheduled_jobs`. Failed jobs can be re-queued with `POST …/jobs/{id}/retry/`.

All outbound fetches (listing pages, pagination links, scraped image URLs) go
through `core/http.py`: http/https only, ports 80/443, every resolved address
must be public, the connection is pinned to the vetted IP (no DNS rebinding),
every redirect is re-validated, and responses are size-capped. Pagination never
leaves the starting host.

Each run records per-item actions (`created`, `updated`, `unchanged`,
`would_create`, `duplicate`, `error`…) and field-level diffs. Admins review
`SourceProduct`s via `/api/admin/imports/source-products/` and either
`create-draft` (always a **draft** product), `link-variant`, or `ignore`.
Source prices only ever update `source_price`, never `price`.

## Scheduled jobs (cron)

No Celery: two idempotent commands, each protected against overlapping runs
(PostgreSQL advisory lock):

```cron
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_ENV=production
* * * * * cd /srv/noribox && .venv/bin/python manage.py run_scheduled_jobs >> /var/log/noribox/jobs.log 2>&1
* * * * * cd /srv/noribox && .venv/bin/python manage.py process_import_jobs >> /var/log/noribox/imports.log 2>&1
```

`run_scheduled_jobs` (use `--only receipts|reservations|orders|sms|imports`):
1. re-attempts reservations for receipts left `not_attempted` (e.g. a crash after upload);
2. expires reservations older than `RESERVATION_TTL_HOURS` (orders still under review are flagged);
3. cancels orders still `awaiting_payment` after `UNPAID_ORDER_TTL_HOURS` — each order is locked
   and re-checked, so an order with an uploaded receipt is never cancelled;
4. sends queued notification SMS and retries failures with exponential backoff;
5. recovers stale import jobs.

## Deployment

1. PostgreSQL 14+, Redis 6+, Python 3.12+ (tested on 3.13).
2. Environment (see `.env.example`). Production settings refuse to start unless
   `DJANGO_SECRET_KEY` (≥ 32 chars), `DJANGO_ALLOWED_HOSTS` (no `*`), `POSTGRES_PASSWORD`
   and `REDIS_URL` are set. Also set `DJANGO_SETTINGS_MODULE=config.settings.prod`,
   `DJANGO_ENV=production`, `TRUSTED_PROXIES`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, SMS keys.
3. Install and prepare (with the production variables exported):
   ```bash
   pip install -r requirements.txt -c constraints.txt
   export DJANGO_SETTINGS_MODULE=config.settings.prod DJANGO_ENV=production
   python manage.py check --deploy
   python manage.py migrate
   python manage.py setup_roles
   python manage.py seed_defaults
   python manage.py collectstatic --noinput
   python manage.py createsuperuser
   ```
4. Run `gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60` behind Nginx.
5. Nginx: use [`deploy/nginx.conf`](deploy/nginx.conf). It **overwrites** `X-Real-IP` and
   `X-Forwarded-For` with `$remote_addr`; set `TRUSTED_PROXIES` to the address Nginx connects
   from (`127.0.0.1/32` on one host). Without a trusted proxy entry the app ignores forwarded
   headers entirely, so clients cannot spoof their IP to evade throttling. **Never** serve
   `PRIVATE_MEDIA_ROOT`.
6. Install the two cron entries above. Back up the database and both media directories.

### Receipt files

Receipts are never public. `file_url` in receipt payloads points to
`/api/v1/receipts/{id}/file/` (owner) or `/api/admin/receipts/{id}/file/` (staff with
`payments.view_paymentreceipt`); both need `Authorization: Bearer …`, so the frontend must
`fetch()` the file and display it via a blob/object URL (a plain `<img src>` cannot send the token).

### API documentation access

`/api/schema/` + `/api/docs/` describe the storefront API and are public.
`/api/admin/schema/` + `/api/admin/docs/` describe the management API and require a staff
user (JWT, or a Django-admin session for the browsable UI).

### Migrating from the prototype

Migration `products.0003` renames the prototype `products.Product` table to
`SourceProduct` (data preserved; covered by a migration test). The project uses a
custom user model (`accounts.User`, phone-based). A database created by the
prototype (which used `auth.User`) cannot switch user models in place: create a
fresh database, run `migrate`, and re-run `import_source_products` (source data
is fully re-importable). No production database existed for the prototype.
