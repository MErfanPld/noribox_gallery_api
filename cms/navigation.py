"""Navigation menus: validation, draft tree, publishing and page-link sync."""
import re
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from catalog.models import Category
from cms.models import NavigationItem, NavigationMenu
from core import audit
from core.exceptions import ServiceError

INTERNAL_PATH = re.compile(r"^/(?!/)[^\s\\]*$")


def validate_link(link_type, url, targets):
    """Return an error dict (field → message) or {} for one item's link settings."""
    T = NavigationItem.LinkType
    errors = {}
    if link_type == T.INTERNAL:
        if not url or not INTERNAL_PATH.match(url):
            errors["url"] = "مسیر داخلی باید با / شروع شود (مثلاً /shop?category=perfume)."
    elif link_type == T.EXTERNAL:
        parts = urlsplit(url or "")
        if parts.scheme not in ("http", "https") or not parts.netloc:
            errors["url"] = "نشانی خارجی باید با http:// یا https:// شروع شود."
    elif link_type == T.NONE:
        if url:
            errors["url"] = "برای آیتم بدون لینک، نشانی را خالی بگذارید."
    else:
        field = {T.PAGE: "page", T.CATEGORY: "category", T.BRAND: "brand", T.BLOG_CATEGORY: "blog_category"}[link_type]
        if targets.get(field) is None:
            errors[field] = "مقصد لینک را انتخاب کنید."
    return errors


def _link(item):
    T = NavigationItem.LinkType
    if item.link_type in (T.INTERNAL, T.EXTERNAL):
        return {"type": item.link_type, "url": item.url}
    if item.link_type == T.PAGE:
        return {"type": "page", "slug": item.page.slug}
    if item.link_type == T.CATEGORY:
        return {"type": "category", "slug": item.category.slug}
    if item.link_type == T.BRAND:
        return {"type": "brand", "slug": item.brand.slug}
    if item.link_type == T.BLOG_CATEGORY:
        return {"type": "blog_category", "slug": item.blog_category.slug}
    return {"type": "none"}


def _target_live(item):
    T = NavigationItem.LinkType
    if item.link_type == T.PAGE:
        return item.page.status == "published" and (item.page.published_at is None or item.page.published_at <= timezone.now())
    if item.link_type == T.CATEGORY:
        return item.category.is_active
    if item.link_type == T.BRAND:
        return item.brand.is_active
    if item.link_type == T.BLOG_CATEGORY:
        return item.blog_category.is_active
    return True


def build_tree(menu, *, include_inactive=False):
    """Nested draft tree. ``include_inactive`` keeps disabled/broken items (admin editor);
    otherwise this is exactly what ``publish`` freezes."""
    items = list(menu.items.select_related("page", "category", "brand", "blog_category"))
    by_parent = {}
    for it in items:
        by_parent.setdefault(it.parent_id, []).append(it)

    def node(it):
        live = it.is_active and _target_live(it)
        children = [c for c in (node(ch) for ch in by_parent.get(it.pk, [])) if c is not None]
        if not live and not include_inactive:
            return None
        data = {
            "id": it.pk, "label": it.label, "link": _link(it),
            "open_in_new_tab": it.open_in_new_tab, "nofollow": it.nofollow,
            "show_on_desktop": it.show_on_desktop, "show_on_mobile": it.show_on_mobile,
            "auto_children": it.auto_children, "children": children,
        }
        if include_inactive:
            data.update(is_active=it.is_active, target_live=_target_live(it), parent=it.parent_id, sort_order=it.sort_order)
        return data

    return [n for n in (node(it) for it in by_parent.get(None, [])) if n is not None]


@transaction.atomic
def publish(menu, user):
    menu = NavigationMenu.objects.select_for_update().get(pk=menu.pk)
    menu.published_data = build_tree(menu)
    menu.published_at = timezone.now()
    menu.published_by = user if getattr(user, "is_authenticated", False) else None
    menu.save(update_fields=["published_data", "published_at", "published_by", "updated_at"])
    audit.record(user, "navigation.publish", menu, {"items": len(menu.published_data)})
    return menu


def expand_auto_children(tree):
    """Serve-time expansion so category changes appear without republishing the menu."""
    cats = None
    out = []
    for node in tree:
        node = dict(node)
        if node.get("auto_children") == NavigationItem.AutoChildren.CATEGORIES:
            if cats is None:
                cats = list(Category.objects.filter(is_active=True, show_in_navigation=True).order_by("sort_order", "name"))
            existing = {c["link"].get("slug") for c in node["children"] if c["link"].get("type") == "category"}
            node["children"] = list(node["children"]) + [
                {"id": f"category-{c.pk}", "label": c.name, "link": {"type": "category", "slug": c.slug},
                 "open_in_new_tab": False, "nofollow": False, "show_on_desktop": True, "show_on_mobile": True,
                 "auto_children": "", "children": []}
                for c in cats if c.slug not in existing
            ]
        node["children"] = expand_auto_children(node["children"])
        out.append(node)
    return out


def public_menu(location):
    menu = NavigationMenu.objects.filter(location=location).first()
    if menu is None:
        return None
    return {"location": menu.location, "name": menu.name, "published_at": menu.published_at,
            "items": expand_auto_children(menu.published_data or [])}


def check_parent(menu, item_pk, parent):
    """Validate a parent choice: same menu, no cycle, depth limit (counting the subtree)."""
    if parent is None:
        return
    if parent.menu_id != menu.pk:
        raise ServiceError("Parent belongs to another menu.", code="invalid_parent")
    node, depth = parent, 1
    while node is not None:
        if item_pk and node.pk == item_pk:
            raise ServiceError("An item cannot be nested under itself.", code="invalid_parent")
        depth += 1
        node = node.parent
    if depth + subtree_height(item_pk) - 1 > NavigationMenu.MAX_DEPTH:
        raise ServiceError(f"Menus can be nested at most {NavigationMenu.MAX_DEPTH} levels deep.", code="too_deep")


def subtree_height(item_pk):
    if not item_pk:
        return 1
    height, frontier = 1, [item_pk]
    while True:
        frontier = list(NavigationItem.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
        if not frontier:
            return height
        height += 1


@transaction.atomic
def reorder(menu, rows, user):
    """Apply [{id, parent, sort_order}] for items of ``menu`` atomically (drag-and-drop)."""
    items = {it.pk: it for it in NavigationItem.objects.select_for_update().filter(menu=menu)}
    for row in rows:
        if row["id"] not in items:
            raise ServiceError("Item does not belong to this menu.", code="invalid_item", details={"id": row["id"]})
        parent = row.get("parent")
        if parent is not None and parent not in items:
            raise ServiceError("Parent does not belong to this menu.", code="invalid_parent", details={"id": row["id"]})
        items[row["id"]].parent_id = parent
        items[row["id"]].sort_order = row["sort_order"]
    # Validate the resulting forest in memory: no cycles, depth limit.
    for it in items.values():
        seen, node, depth = set(), it, 1
        while node.parent_id is not None:
            if node.pk in seen:
                raise ServiceError("Cyclic nesting.", code="invalid_parent", details={"id": it.pk})
            seen.add(node.pk)
            node = items[node.parent_id]
            depth += 1
        if depth > NavigationMenu.MAX_DEPTH:
            raise ServiceError(f"Menus can be nested at most {NavigationMenu.MAX_DEPTH} levels deep.", code="too_deep",
                               details={"id": it.pk})
    NavigationItem.objects.bulk_update(items.values(), ["parent", "sort_order"])
    menu.touch()
    audit.record(user, "navigation.reorder", menu, {"items": len(rows)})


def sync_page_links(page):
    """Keep page-managed links in the header/footer drafts in line with the page flags."""
    for location, wanted in ((NavigationMenu.Location.HEADER, page.show_in_header),
                             (NavigationMenu.Location.FOOTER, page.show_in_footer)):
        menu = NavigationMenu.objects.filter(location=location).first()
        if menu is None:
            continue
        existing = NavigationItem.objects.filter(menu=menu, page=page)
        if wanted and not existing.exists():
            parent = None
            if location == NavigationMenu.Location.FOOTER:
                parent = menu.items.filter(parent=None, link_type=NavigationItem.LinkType.NONE).order_by("sort_order", "id").last()
            last = menu.items.filter(parent=parent).order_by("-sort_order").first()
            NavigationItem.objects.create(
                menu=menu, parent=parent, label=page.title, link_type=NavigationItem.LinkType.PAGE, page=page,
                sort_order=(last.sort_order + 1) if last else 0, managed_by_page=True,
            )
            menu.touch()
        elif not wanted and existing.filter(managed_by_page=True).exists():
            existing.filter(managed_by_page=True).delete()
            menu.touch()
