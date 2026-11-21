# Noribox Gallery API reference

Machine-readable contracts: storefront `GET /api/schema/` (public, UI at `/api/docs/`); management `GET /api/admin/schema/` (staff only, UI at `/api/admin/docs/`).
This page summarises endpoints, permissions and example payloads.

## Conventions

* **Auth**: `Authorization: Bearer <access>` (JWT). Access 30 min, refresh 14 days, refresh rotation + blacklist.
* **Money**: integers in Toman.
* **Phones**: any common Iranian format is accepted (`+98912…`, `0912…`, Persian digits) and normalised to `09XXXXXXXXX`.
* **Pagination**: `?page=N&page_size=M` (max 100) → `{"count", "next", "previous", "results"}`.
* **Errors** (always):
  ```json
  {"error": {"code": "reservation_conflict", "message": "…", "details": {"unavailable_variant_ids": [12]}}}
  ```
  Validation errors use `code: "validation_error"` with field errors in `details`.
  Throttled responses (429) include `details.retry_after_seconds`. Repeated failed password logins for one phone return 429 `login_locked`.
* **Multipart booleans**: in `multipart/form-data` requests an omitted boolean is read as `false` (HTML form semantics) — send `is_active=true` explicitly.

## Storefront `/api/v1/`

### Auth (public, throttled)

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `auth/otp/request/` | `{phone, purpose: login\|register\|reset_password}` | 202. Cooldown + hourly limit per phone |
| POST | `auth/otp/login/` | `{phone, code}` | Creates the account on first login (`OTP_LOGIN_AUTO_REGISTER`) |
| POST | `auth/register/` | `{phone, code, password, first_name?, last_name?}` | Needs a `register` OTP |
| POST | `auth/login/` | `{phone, password}` | |
| POST | `auth/password/reset/` | `{phone, code, new_password}` | Needs a `reset_password` OTP |
| POST | `auth/token/refresh/` | `{refresh}` | |
| POST | `auth/logout/` | `{refresh}` | Auth required; blacklists the refresh token |

Password change (`account/password/`) and reset revoke **all** existing access and refresh tokens; the change endpoint returns a fresh token pair. `otp/request` always answers 202 with the same body (no account enumeration).

Token response:
```json
{"access": "eyJ…", "refresh": "eyJ…",
 "user": {"id": 1, "phone": "09121234567", "first_name": "", "last_name": "", "email": "",
          "national_code": "", "phone_verified_at": "2026-11-20T10:00:00Z", "date_joined": "…",
          "is_staff": false, "has_password": false}}
```

### Account (auth)

`GET/PATCH account/me/` · `POST account/password/` `{current_password?, new_password}` ·
`GET/POST account/addresses/`, `GET/PUT/PATCH/DELETE account/addresses/{id}/`

```json
{"title": "Home", "recipient_name": "Ali Rezaei", "recipient_phone": "09121112233",
 "province": "Tehran", "city": "Tehran", "address_line": "Valiasr St.", "postal_code": "1234567890",
 "plaque": "12", "unit": "3", "is_default": true}
```

### Catalog (public)

| Path | Notes |
|---|---|
| `categories/`, `categories/tree/`, `categories/{slug}/`, `categories/{slug}/attributes/` | active categories, nested tree, filterable attributes |
| `brands/`, `brands/{slug}/` | |
| `products/` | published only. Filters: `category` (slug, includes subcategories), `brand` (slugs, comma), `min_price`, `max_price`, `available`, `featured`, `options=gender:male,volume:100ml`, `q`. Sort: `sort=newest\|oldest\|price_asc\|price_desc\|name\|featured` |
| `products/{slug}/` | variants with availability, images, attributes, SEO |

Product detail (abridged):
```json
{"id": 7, "name": "Sauvage EDP", "slug": "sauvage-edp", "brand": {"name": "Dior", "slug": "dior"},
 "min_price": 5000000, "max_compare_at_price": 5500000, "is_available": true,
 "variants": [{"id": 31, "sku": "DIOR-SAU-100", "label": "100ml", "options": [],
               "price": 5000000, "compare_at_price": 5500000,
               "availability": "available", "is_available": true, "condition_note": ""}],
 "images": [{"id": 3, "image": "/media/products/2026/11/a.webp", "alt_text": "", "is_primary": true}],
 "attributes": [{"attribute": "gender", "attribute_name": "Gender", "options": [{"value": "Male", "slug": "male"}]}],
 "meta_title": "", "meta_description": ""}
```

### Cart (auth)

`GET cart/` · `DELETE cart/` (clear) · `POST cart/items/` `{variant_id}` (201 new / 200 already present; quantity is always 1) · `DELETE cart/items/{id}/`

### Orders & payment (auth, owner-scoped — other users' orders return 404)

| Method | Path | Notes |
|---|---|---|
| GET | `shipping-methods/` | public |
| GET | `payment-methods/`, `bank-cards/` | public; store card/SHEBA numbers to transfer to |
| POST | `orders/` | `{address_id, shipping_method_id, customer_note?}` → order from cart (no reservation) |
| GET | `orders/`, `orders/{number}/` | detail includes items, shipment, payment + receipts, history |
| POST | `orders/{number}/cancel/` | only while `awaiting_payment` |
| POST | `orders/track/` | **public** guest tracking `{number, phone}` — phone of the account owner or the order's recipient. Limited output (status, payment status, `payment_expires_at`, totals, items, masked recipient name, province/city, shipment tracking, timeline). Unknown order and wrong phone both → 404 `order_not_found`; throttled per IP (`THROTTLE_ORDER_TRACK`) and locked per order number after `ORDER_TRACK_MAX_FAILURES` → 429 `track_locked` |
| POST | `orders/{number}/receipts/` | multipart: `file` (jpg/png/webp/pdf ≤ 5 MB, signature checked), `reference_number?`, `payer_card_last4?`, `paid_at?`, `declared_amount?`, `customer_note?` |
| GET | `receipts/{id}/file/` | streams the owner's receipt (`Cache-Control: private, no-store`). Requires the Bearer token — fetch it and render via a blob URL |

`payment_expires_at` (order detail, admin, tracking): server-computed receipt deadline =
max(created_at, last receipt upload) + `UNPAID_ORDER_TTL_HOURS`; `null` unless `awaiting_payment`.
The same function drives automatic cancellation, so the countdown and the server always agree.

Order status flow:
`awaiting_payment → payment_submitted (receipt) → paid (approved) → processing → shipped → delivered`;
rejection returns to `awaiting_payment`; `cancelled` is reachable before shipping.

Receipt response:
```json
{"id": 4, "status": "pending", "original_filename": "receipt.jpg", "content_type": "image/jpeg", "size": 81234,
 "reference_number": "123456", "payer_card_last4": "1234", "paid_at": null, "declared_amount": 4150000,
 "customer_note": "", "file_url": "/api/v1/receipts/4/file/", "reviews": [], "created_at": "…"}
```

### CMS (public)

`pages/{slug}/` · `blog/categories/` · `blog/posts/` (`?category__slug=`, `?search=`) · `blog/posts/{slug}/` ·
`faqs/` (`?group=`) · `banners/` (`?position=home_top`) · `sliders/{code}/` · `store-settings/` · `seo/?path=/brands`
· `navigation/{header|footer}/` (published snapshot) · `homepage/` (active sections, resolved content)

Navigation item `link`: `{type: internal|external, url}` · `{type: page|category|brand|blog_category, slug}` ·
`{type: none}` (heading/dropdown). Items carry `open_in_new_tab`, `nofollow`, `show_on_desktop`,
`show_on_mobile`, `children` (≤ 3 levels). `auto_children: "categories"` lists categories with
`show_in_navigation=true` at request time. Public CMS payloads are cached and invalidated on any edit.
`store-settings` never exposes `admin_notification_phones`; with `maintenance_enabled` new orders get
`503 store_maintenance`.

## Management `/api/admin/` (staff + permission)

| Area | Endpoints | Extra actions (permission) |
|---|---|---|
| Users & roles | `users/`, `roles/` | role/staff changes need `accounts.manage_roles` |
| Catalog | `categories/`, `brands/`, `attributes/`, `attribute-options/`, `category-attributes/`, `products/`, `variants/`, `product-images/` (multipart) | `products/{id}/publish/`, `unpublish/`, `archive/` (`catalog.publish_product`) |
| Inventory | `inventory/availability/`, `inventory/reservations/` | `availability/{id}/set-state/` `{state: available\|unavailable, note}`, `reservations/{id}/release/` (`inventory.manage_inventory`) |
| Imports | `imports/jobs/` (POST **queues** a job → 202 `pending`: `source_url` (public http/https only) or multipart `html_file`, `limit`, `dry_run` default true — `products.run_import`; statuses `pending, running, completed, completed_with_errors, failed, cancelled`; `jobs/{id}/retry/` re-queues failed/partial/cancelled jobs, `jobs/{id}/cancel/`), `imports/items/` (`?job=`), `imports/errors/`, `imports/source-products/` (`?review_status=`, `?import_items__job=`, `summary/`), `imports/source-categories/` (map `mapped_category`) | `source-products/{id}/approve/` `{category?, price?, publish=true, …}` creates the draft (if not linked) and publishes it in one transaction (`+ catalog.publish_product`); `reject/` `{reason?}`, `restore/`, `bulk/` `{ids, operation: approve\|create_draft\|reject\|restore, category?}`, `create-draft/`, `link-variant/`, `ignore/` (`products.review_sourceproduct`) |
| Customers | `customers/` (non-staff; `order_count`, `paid_total` from paid orders only; detail adds addresses, orders, activity) | `customers/{id}/set-active/` `{is_active, reason?}` revokes tokens on disable (`accounts.change_user`) |
| Orders | `orders/` (`?needs_review=true`, `?status=`, `?status__in=`, `?created_after=`, `?created_before=`, `?payment_status=`, `?search=` number/phone/name); `orders/{id}/notes/` GET/POST internal notes | `orders/{id}/transition/` `{status, note}`, `clear-review/` (`commerce.manage_order_status`); `record-refund/` `{reference, note?}` (`commerce.record_refund`) for cancelled paid orders (`refund_status: pending → refunded`) |
| Payments | `payments/`, `receipts/`, `receipts/{id}/file/`, `payment-methods/`, `bank-cards/` | `receipts/{id}/approve/` `{reason?}`, `receipts/{id}/reject/` `{reason}`, `receipts/{id}/retry-reservation/` (`payments.review_payment`) |
| Shipping | `shipping-methods/`, `shipments/` (tracking fields; status via order transitions) | |
| Notifications | `notifications/sms-templates/` (+ `defaults/`), `notifications/sms-logs/` | `sms-logs/{id}/retry/` (`notifications.retry_sms`) |
| CMS | `cms/pages/` (`show_in_header`/`show_in_footer` add a link to the menu draft), `cms/blog-categories/`, `cms/blog-posts/` (`related_posts`, future `published_at` = scheduled), `cms/faqs/`, `cms/banners/`, `cms/sliders/`, `cms/slides/`, `cms/seo/`, `store-settings/` (GET/PATCH), `cms/homepage-sections/` (+ `reorder/` `{ids}`, `preview/`) | |
| Navigation | `cms/navigation-menus/` (`{id}/tree/`, `{id}/preview/`, `{id}/reorder/` `{items:[{id,parent,sort_order}]}`), `cms/navigation-items/` | `{id}/publish/` (`cms.publish_navigationmenu`) |
| Reports | `reports/summary/?days=30`, `dashboard/?days=30` | revenue, orders by status, queues, recent orders/imports |
| Staff | `me/` (effective permissions), `audit-logs/` (who approved/published/changed what) | |

Approve conflict example (HTTP 409):
```json
{"error": {"code": "reservation_conflict",
           "message": "Cannot approve: some items could not be reserved. Resolve inventory or reject the payment.",
           "details": {"unavailable_variant_ids": [31]}}}
```

## SMS templates

Default bodies (override per code in `sms-templates`; set `is_active=false` to disable an event):
`otp`, `receipt_uploaded_admin`, `payment_approved`, `payment_rejected`,
`order_status_processing`, `order_status_shipped`, `order_status_delivered`, `order_status_cancelled`.
Placeholders: `{code}`, `{order_number}`, `{amount}`, `{reason}`, `{tracking_code}`, `{conflict}`.
OTP messages are never stored in plaintext in `SMSLog` and are not retried.

## Conflict codes (HTTP 409)

| code | meaning |
|---|---|
| `reservation_conflict` | another order holds/sold a unit; resolve inventory or reject the payment |
| `admin_marked_unavailable` | an administrator marked a reserved unit unavailable; approval would sell it, so it is refused |
| `protected` | delete refused because orders/reservations/children reference the object; `details.referenced_by`, use archive/deactivate |
| `invalid_transition`, `invalid_order_state`, `receipt_pending`, `receipt_not_pending` | state-machine guards |
| `phone_registered` | concurrent/duplicate registration |

## Perfume note pyramid (data, no schema change)

`python manage.py seed_perfume_attributes --category perfume` creates three filterable
`multi_option` attributes: `notes_top`, `notes_heart`, `notes_base`. Each note is an
AttributeOption (reusable, filterable: `?options=notes_base:oud`). Product detail returns them
in `attributes[]`; the frontend perfume extension assembles them into the pyramid.
