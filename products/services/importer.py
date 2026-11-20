"""
Import orchestration: fetch (or load fixture) → parse → stage SourceProduct records.

Rules:
- Source data is staged only; catalog products are never created/published here.
- Source prices update ``ProductVariant.source_price`` of linked variants but
  NEVER the internal selling price.
- Existing non-null values are never cleared by missing data.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.http import FetchError, UnsafeURLError, safe_fetch
from products.models import ImportErrorLog, ImportItem, ImportJob, SourceProduct
from products.services.parser import ParsedProduct, parse_listing_html

logger = logging.getLogger(__name__)

TRACKED_FIELDS = ("name", "product_url", "regular_price", "sale_price", "availability", "image_url")


class SourceUnavailableError(Exception):
    """Raised when the remote source cannot be reached."""


def fetch_html(url: str, timeout: int | None = None, user_agent: str | None = None) -> str:
    """Fetch a listing page through the SSRF-safe client (http/https, public hosts only)."""
    headers = {"Accept": "text/html"}
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        result = safe_fetch(url, max_bytes=settings.IMPORT_MAX_PAGE_BYTES, timeout=timeout, headers=headers)
    except UnsafeURLError as exc:
        raise SourceUnavailableError(f"Refused to fetch {url!r}: {exc}") from exc
    except (FetchError, OSError) as exc:
        raise SourceUnavailableError(f"Failed to fetch {url}: {exc}") from exc
    return result.body.decode(result.charset or "utf-8", errors="replace")


def load_fixture(path: Path | str) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Fixture not found: {p}")
    return p.read_text(encoding="utf-8")


def _jsonable(value):
    return str(value) if value is not None else None


def _incoming_values(parsed: ParsedProduct) -> dict:
    """Only fields actually parsed; None never clears an existing value."""
    values = {
        "name": parsed.name,
        "product_url": parsed.product_url,
        "availability": parsed.availability,
    }
    if parsed.regular_price is not None:
        values["regular_price"] = parsed.regular_price
    if parsed.sale_price is not None:
        values["sale_price"] = parsed.sale_price
    if parsed.image_url:
        values["image_url"] = parsed.image_url
    if parsed.stock_quantity is not None:
        values["stock_quantity"] = parsed.stock_quantity
    return {k: v for k, v in values.items() if v not in (None, "")}


def find_duplicate_by_url(parsed: ParsedProduct, source_site: str):
    """Same permalink stored under a different source id → probable duplicate."""
    return (
        SourceProduct.objects.filter(source_site=source_site, product_url=parsed.product_url)
        .exclude(source_product_id=parsed.source_product_id)
        .first()
    )


def sync_linked_variant_source_price(obj: SourceProduct) -> bool:
    """Update source_price on a linked variant. Never touches ``price``."""
    variant = obj.linked_variant
    if variant is None or obj.effective_price is None:
        return False
    new_price, new_compare = obj.effective_price, obj.effective_compare_at
    if variant.source_price == new_price and variant.source_compare_at_price == new_compare:
        return False
    variant.source_price = new_price
    variant.source_compare_at_price = new_compare
    variant.source_price_updated_at = timezone.now()
    variant.save(update_fields=["source_price", "source_compare_at_price", "source_price_updated_at", "updated_at"])
    return True


def stage_product(parsed: ParsedProduct, source_site: str, dry_run: bool = False):
    """Return (instance_or_None, action, changes)."""
    incoming = _incoming_values(parsed)
    existing = SourceProduct.objects.filter(source_site=source_site, source_product_id=parsed.source_product_id).first()
    now = timezone.now()

    if existing is None:
        if dry_run:
            return None, ImportItem.Action.WOULD_CREATE, {k: _jsonable(v) for k, v in incoming.items()}
        obj = SourceProduct.objects.create(
            source_site=source_site,
            source_product_id=parsed.source_product_id,
            currency=parsed.currency or "تومان",
            raw_data=parsed.raw_data or {},
            source_last_seen_at=now,
            last_successful_sync_at=now,
            **incoming,
        )
        return obj, ImportItem.Action.CREATED, {}

    changes = {}
    for field, value in incoming.items():
        old = getattr(existing, field)
        if old != value:
            changes[field] = {"old": _jsonable(old), "new": _jsonable(value)}
    if dry_run:
        return existing, (ImportItem.Action.WOULD_UPDATE if changes else ImportItem.Action.UNCHANGED), changes

    for field, value in incoming.items():
        setattr(existing, field, value)
    if parsed.raw_data:
        existing.raw_data = parsed.raw_data
    existing.source_last_seen_at = now
    existing.last_successful_sync_at = now
    existing.save()
    if sync_linked_variant_source_price(existing):
        changes["linked_variant_source_price"] = existing.effective_price
    return existing, (ImportItem.Action.UPDATED if changes else ImportItem.Action.UNCHANGED), changes


def upsert_product(parsed: ParsedProduct, source_site: str) -> tuple[SourceProduct, bool]:
    """Backward-compatible API: create or update a SourceProduct. Returns (instance, created)."""
    obj, action, _ = stage_product(parsed, source_site)
    return obj, action == ImportItem.Action.CREATED


def import_products(
    products: Iterable[ParsedProduct],
    source_site: str | None = None,
    limit: int | None = None,
    job: ImportJob | None = None,
    dry_run: bool = False,
) -> dict:
    source_site = source_site or getattr(settings, "SOURCE_SITE_ID", "mehrdadgallery")
    if limit is None:
        limit = getattr(settings, "IMPORT_DEFAULT_LIMIT", None)

    stats = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "duplicates": 0, "errors": 0}
    seen: set[str] = set()
    count = 0
    for parsed in products:
        if limit is not None and count >= limit:
            break
        count += 1
        if count % 25 == 0:
            heartbeat(job)
        item_kwargs = {"external_id": (parsed.source_product_id or "")[:64], "name": (parsed.name or "")[:512]}
        try:
            if not parsed.source_product_id or not parsed.name:
                stats["skipped"] += 1
                _record(job, ImportItem.Action.SKIPPED, item_kwargs, {"reason": "incomplete"})
                continue
            if parsed.source_product_id in seen:
                stats["duplicates"] += 1
                _record(job, ImportItem.Action.DUPLICATE, item_kwargs, {"reason": "repeated in this import"})
                continue
            seen.add(parsed.source_product_id)
            dup = find_duplicate_by_url(parsed, source_site)
            if dup is not None:
                stats["duplicates"] += 1
                _record(job, ImportItem.Action.DUPLICATE, item_kwargs,
                        {"reason": "same product_url as existing record", "existing_id": dup.pk}, dup)
                continue
            with transaction.atomic():
                obj, action, changes = stage_product(parsed, source_site, dry_run=dry_run)
            key = {
                ImportItem.Action.CREATED: "created", ImportItem.Action.WOULD_CREATE: "created",
                ImportItem.Action.UPDATED: "updated", ImportItem.Action.WOULD_UPDATE: "updated",
                ImportItem.Action.UNCHANGED: "unchanged",
            }[action]
            # Backward compatibility: unchanged re-imports count as updates.
            stats["updated" if key == "unchanged" else key] += 1
            if key == "unchanged":
                stats["unchanged"] += 1
            _record(job, action, item_kwargs, changes, obj, parsed)
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            logger.exception("Error importing product %s", parsed.source_product_id)
            item = _record(job, ImportItem.Action.ERROR, item_kwargs, {})
            if job is not None:
                ImportErrorLog.objects.create(job=job, item=item, external_id=item_kwargs["external_id"], message=str(exc)[:5000])
    return stats


def _record(job, action, item_kwargs, changes, obj=None, parsed=None):
    if job is None:
        return None
    parsed_data = {}
    if parsed is not None:
        parsed_data = {
            "product_url": parsed.product_url,
            "regular_price": _jsonable(parsed.regular_price),
            "sale_price": _jsonable(parsed.sale_price),
            "availability": parsed.availability,
            "image_url": parsed.image_url,
        }
    return ImportItem.objects.create(
        job=job, action=action, changes=changes, parsed_data=parsed_data,
        source_product=obj if obj is not None and obj.pk else None, **item_kwargs,
    )


def _merge(stats, page):
    for k, v in page.items():
        stats[k] = stats.get(k, 0) + v


def iterate_listing_pages(url: str, max_pages: int | None = None):
    visited: set[str] = set()
    current = url
    max_pages = max_pages or getattr(settings, "IMPORT_MAX_PAGES", 50)
    origin = urlsplit(url).hostname
    while current and current not in visited and len(visited) < max_pages:
        visited.add(current)
        html = fetch_html(current)  # every page is SSRF-validated
        yield html
        soup = BeautifulSoup(html, "html.parser")
        nxt = soup.select_one("a.next.page-numbers") or soup.select_one("a[rel='next']")
        current = urljoin(current, nxt.get("href")) if nxt and nxt.get("href") else None
        # Pagination links come from untrusted HTML: stay on the starting host.
        if current and urlsplit(current).hostname != origin:
            logger.warning("Ignoring off-site pagination link %s", current)
            current = None


def run_import_from_url(url, source_site=None, limit=None, base_url=None, job=None, dry_run=False) -> dict:
    base = base_url or getattr(settings, "SOURCE_BASE_URL", "https://mehrdadgallery.com")
    if limit is None:
        limit = getattr(settings, "IMPORT_DEFAULT_LIMIT", None)
    stats: dict = {}
    count = 0
    for html in iterate_listing_pages(url):
        heartbeat(job)
        if limit is not None and count >= limit:
            break
        products = parse_listing_html(html, base_url=base)
        remaining = None if limit is None else limit - count
        batch = products if remaining is None else products[:remaining]
        _merge(stats, import_products(batch, source_site=source_site, limit=remaining, job=job, dry_run=dry_run))
        count += len(batch)
    return stats or {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "duplicates": 0, "errors": 0}


def run_import_from_html(html, source_site=None, limit=None, base_url=None, job=None, dry_run=False) -> dict:
    base = base_url or getattr(settings, "SOURCE_BASE_URL", "https://mehrdadgallery.com")
    parsed = parse_listing_html(html, base_url=base)
    return import_products(parsed, source_site=source_site, limit=limit, job=job, dry_run=dry_run)


def run_import_from_fixture(fixture_path, source_site=None, limit=None, base_url=None, job=None, dry_run=False) -> dict:
    return run_import_from_html(load_fixture(fixture_path), source_site, limit, base_url, job, dry_run)


def heartbeat(job: ImportJob | None) -> None:
    if job is not None and job.pk:
        ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.RUNNING).update(heartbeat_at=timezone.now())


def claim_job(job: ImportJob) -> bool:
    """Atomically move a pending job to running. Only one worker can win."""
    now = timezone.now()
    won = ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.PENDING).update(
        status=ImportJob.Status.RUNNING, started_at=now, heartbeat_at=now, finished_at=None,
        attempts=F("attempts") + 1, updated_at=now,
    )
    return bool(won)


def run_job(job: ImportJob, html: str | None = None) -> ImportJob:
    """Claim and execute a pending ImportJob, recording status, stats, items and errors.

    Returns the job unchanged if another worker already claimed it.
    """
    if not claim_job(job):
        job.refresh_from_db()
        logger.info("Import job %s not pending (status=%s); skipping", job.pk, job.status)
        return job
    job.refresh_from_db()
    job.items.all().delete()  # an interrupted earlier attempt may have left partial items
    html = html if html is not None else job.source_html
    try:
        if job.source_type == ImportJob.SourceType.URL:
            stats = run_import_from_url(job.source_url, job.source_site, job.limit, job=job, dry_run=job.dry_run)
        elif job.source_type == ImportJob.SourceType.FIXTURE:
            stats = run_import_from_fixture(job.fixture_path, job.source_site, job.limit, job=job, dry_run=job.dry_run)
        else:
            stats = run_import_from_html(html or "", job.source_site, job.limit, job=job, dry_run=job.dry_run)
        job.status = ImportJob.Status.COMPLETED
        job.stats = stats
        job.source_html = ""  # no longer needed
    except Exception as exc:  # noqa: BLE001
        job.status = ImportJob.Status.FAILED
        job.log = f"{exc.__class__.__name__}: {exc}"[:10000]
        ImportErrorLog.objects.create(job=job, message=job.log)
        logger.warning("Import job %s failed: %s", job.pk, exc)
    job.finished_at = timezone.now()
    job.save()
    return job


def recover_stale_jobs(now=None) -> dict:
    """Re-queue (or fail) jobs whose worker died while 'running'."""
    from datetime import timedelta

    now = now or timezone.now()
    cutoff = now - timedelta(minutes=settings.IMPORT_STALE_MINUTES)
    stale = ImportJob.objects.filter(status=ImportJob.Status.RUNNING).filter(
        Q(heartbeat_at__lt=cutoff) | Q(heartbeat_at__isnull=True, started_at__lt=cutoff)
    )
    result = {"requeued": 0, "failed": 0}
    for job in stale:
        msg = f"Worker stopped responding (last heartbeat {job.heartbeat_at or job.started_at})."
        if job.attempts < settings.IMPORT_MAX_ATTEMPTS:
            changed = ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.RUNNING).update(
                status=ImportJob.Status.PENDING, log=msg + " Re-queued.", updated_at=now)
            result["requeued"] += changed
        else:
            changed = ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.RUNNING).update(
                status=ImportJob.Status.FAILED, log=msg + " Giving up.", finished_at=now, updated_at=now)
            result["failed"] += changed
        if changed:
            ImportErrorLog.objects.create(job=job, message=msg)
    return result


def process_pending_jobs(limit=5) -> list:
    """Worker entry point (management command / cron). Runs pending jobs oldest first."""
    recover_stale_jobs()
    done = []
    for job in ImportJob.objects.filter(status=ImportJob.Status.PENDING).order_by("created_at")[:limit]:
        job = run_job(job)
        done.append((job.pk, job.status))
    return done
