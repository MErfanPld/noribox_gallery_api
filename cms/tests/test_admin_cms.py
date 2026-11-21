from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from catalog.models import Category
from cms.models import HomepageSection, NavigationItem, NavigationMenu, Page, StoreSettings
from core.models import AuditLog
from core.testutils import client_for, make_product, make_staff, make_user, setup_store

NAV = "/api/admin/cms/navigation-items/"
MENUS = "/api/admin/cms/navigation-menus/"


def superuser():
    u = make_user(is_staff=True)
    u.is_superuser = True
    u.save()
    return u


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cms-tests"}})
class NavigationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = client_for(superuser())
        self.header = NavigationMenu.objects.get(location="header")  # created by migration

    def item(self, **data):
        res = self.api.post(NAV, {"menu": self.header.pk, **data}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def public(self):
        return client_for().get("/api/v1/navigation/header/").json()["items"]

    def test_crud_publish_and_draft_isolation(self):
        shop = self.item(label="فروشگاه", url="/shop")
        self.assertEqual(self.public(), [])  # draft only
        menu = self.api.get(f"{MENUS}{self.header.pk}/").json()
        self.assertTrue(menu["has_unpublished_changes"])
        preview = self.api.get(f"{MENUS}{self.header.pk}/preview/").json()["items"]
        self.assertEqual([i["label"] for i in preview], ["فروشگاه"])
        self.assertEqual(self.api.post(f"{MENUS}{self.header.pk}/publish/").status_code, 200)
        self.assertEqual(self.public()[0]["link"], {"type": "internal", "url": "/shop"})
        self.api.patch(f"{NAV}{shop['id']}/", {"label": "همه محصولات"}, format="json")
        self.assertEqual(self.public()[0]["label"], "فروشگاه")  # unpublished edit not live
        self.api.post(f"{MENUS}{self.header.pk}/publish/")
        self.assertEqual(self.public()[0]["label"], "همه محصولات")
        self.api.delete(f"{NAV}{shop['id']}/")
        self.api.post(f"{MENUS}{self.header.pk}/publish/")
        self.assertEqual(self.public(), [])
        self.assertTrue(AuditLog.objects.filter(action="navigation.publish").exists())

    def test_nested_dropdown_flags_and_inactive(self):
        root = self.item(label="فروشگاه", link_type="none")
        self.item(label="عطر مردانه", url="/shop?category=men", parent=root["id"], show_on_mobile=False, sort_order=1)
        self.item(label="عطر زنانه", url="/shop?category=women", parent=root["id"], sort_order=0)
        self.item(label="غیرفعال", url="/x", parent=root["id"], is_active=False)
        self.item(label="سایت همکار", link_type="external", url="https://example.com", open_in_new_tab=True, nofollow=True)
        self.api.post(f"{MENUS}{self.header.pk}/publish/")
        items = self.public()
        self.assertEqual([c["label"] for c in items[0]["children"]], ["عطر زنانه", "عطر مردانه"])
        self.assertFalse(items[0]["children"][1]["show_on_mobile"])
        self.assertTrue(items[1]["open_in_new_tab"] and items[1]["nofollow"])

    def test_link_validation_and_depth_limit(self):
        for bad in ({"url": "javascript:alert(1)"}, {"url": "//evil.com"}, {"link_type": "external", "url": "ftp://x"},
                    {"link_type": "page"}):
            res = self.api.post(NAV, {"menu": self.header.pk, "label": "x", **bad}, format="json")
            self.assertEqual(res.status_code, 400, bad)
        a = self.item(label="a", link_type="none")
        b = self.item(label="b", link_type="none", parent=a["id"])
        c = self.item(label="c", url="/c", parent=b["id"])
        res = self.api.post(NAV, {"menu": self.header.pk, "label": "d", "url": "/d", "parent": c["id"]}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.api.patch(f"{NAV}{a['id']}/", {"parent": c["id"]}, format="json")  # cycle
        self.assertEqual(res.status_code, 400)

    def test_reorder_atomic_and_validated(self):
        a = self.item(label="a", url="/a", sort_order=0)
        b = self.item(label="b", url="/b", sort_order=1)
        res = self.api.post(f"{MENUS}{self.header.pk}/reorder/", {"items": [
            {"id": a["id"], "parent": None, "sort_order": 1}, {"id": b["id"], "parent": None, "sort_order": 0}]}, format="json")
        self.assertEqual([n["label"] for n in res.json()], ["b", "a"])
        footer = NavigationMenu.objects.get(location="footer")
        other = NavigationItem.objects.create(menu=footer, label="f", url="/f")
        res = self.api.post(f"{MENUS}{self.header.pk}/reorder/", {"items": [
            {"id": a["id"], "parent": other.pk, "sort_order": 0}]}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.api.post(f"{MENUS}{self.header.pk}/reorder/", {"items": [
            {"id": a["id"], "parent": b["id"], "sort_order": 0}, {"id": b["id"], "parent": a["id"], "sort_order": 0}]}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIsNone(NavigationItem.objects.get(pk=a["id"]).parent_id)

    def test_category_and_page_links_resolve_and_auto_children(self):
        cat = Category.objects.create(name="عطر", slug="perfume", show_in_navigation=True)
        Category.objects.create(name="ساعت", slug="watch", show_in_navigation=False)
        page = Page.objects.create(title="قوانین", slug="terms", status="published")
        self.item(label="دسته‌ها", link_type="none", auto_children="categories")
        self.item(label="عطر", link_type="category", category=cat.pk)
        self.item(label="قوانین", link_type="page", page=page.pk)
        self.api.post(f"{MENUS}{self.header.pk}/publish/")
        items = self.public()
        self.assertEqual([c["label"] for c in items[0]["children"]], ["عطر"])
        self.assertEqual(items[1]["link"], {"type": "category", "slug": "perfume"})
        self.assertEqual(items[2]["link"], {"type": "page", "slug": "terms"})
        # A category added to navigation later appears without republishing.
        Category.objects.create(name="اکسسوری", slug="acc", show_in_navigation=True, sort_order=5)
        self.assertEqual(len(self.public()[0]["children"]), 2)
        # An unpublished page is dropped at the next publish.
        page.status = "draft"
        page.save()
        self.api.post(f"{MENUS}{self.header.pk}/publish/")
        self.assertEqual(len(self.public()), 2)

    def test_page_flags_sync_into_header_and_footer_drafts(self):
        footer = NavigationMenu.objects.get(location="footer")
        col = NavigationItem.objects.create(menu=footer, label="اطلاعات", link_type="none")
        res = self.api.post("/api/admin/cms/pages/", {"title": "حریم خصوصی", "body": "متن", "status": "published",
                                                      "show_in_header": True, "show_in_footer": True}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        page = Page.objects.get(pk=res.json()["id"])
        self.assertEqual(page.slug, "حریم-خصوصی")
        self.assertTrue(NavigationItem.objects.filter(menu=self.header, page=page, managed_by_page=True).exists())
        self.assertEqual(NavigationItem.objects.get(menu=footer, page=page).parent, col)
        self.api.patch(f"/api/admin/cms/pages/{page.pk}/", {"show_in_header": False}, format="json")
        self.assertFalse(NavigationItem.objects.filter(menu=self.header, page=page).exists())


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cms-tests-2"}})
class PagesHomepageSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = client_for(superuser())

    def test_page_publication(self):
        res = self.api.post("/api/admin/cms/pages/", {"title": "قوانین", "slug": "terms", "body": "x"}, format="json")
        self.assertEqual(res.json()["status"], "draft")
        self.assertEqual(client_for().get("/api/v1/pages/terms/").status_code, 404)
        self.api.patch(f"/api/admin/cms/pages/{res.json()['id']}/", {"status": "published"}, format="json")
        body = client_for().get("/api/v1/pages/terms/").json()
        self.assertEqual(body["title"], "قوانین")
        self.api.delete(f"/api/admin/cms/pages/{res.json()['id']}/")
        self.assertEqual(client_for().get("/api/v1/pages/terms/").status_code, 404)

    def test_homepage_sections_order_visibility_and_products(self):
        call_command("seed_storefront", stdout=open("/dev/null", "w"))
        draft = make_product(name="Draft", status="draft")
        live = make_product(name="Live")
        featured = HomepageSection.objects.get(kind="featured_products")
        res = self.api.patch(f"/api/admin/cms/homepage-sections/{featured.pk}/", {"products": [draft.pk, live.pk]}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        home = client_for().get("/api/v1/homepage/").json()["sections"]
        section = next(s for s in home if s["kind"] == "featured_products")
        self.assertEqual([p["name"] for p in section["products"]], ["Live"])  # drafts never leak
        hero = HomepageSection.objects.get(kind="hero")
        self.api.patch(f"/api/admin/cms/homepage-sections/{hero.pk}/", {"is_active": False}, format="json")
        ids = list(HomepageSection.objects.order_by("-sort_order").values_list("pk", flat=True))
        self.api.post("/api/admin/cms/homepage-sections/reorder/", {"ids": ids}, format="json")
        kinds = [s["kind"] for s in client_for().get("/api/v1/homepage/").json()["sections"]]
        self.assertNotIn("hero", kinds)
        self.assertEqual(kinds[0], "track_order")
        res = self.api.patch(f"/api/admin/cms/homepage-sections/{hero.pk}/", {"cta_url": "javascript:x"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_settings_validation_and_public_exposure(self):
        url = "/api/admin/store-settings/"
        res = self.api.patch(url, {"social_links": [{"platform": "instagram", "url": "http://insecure"}]}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.api.patch(url, {"announcement_enabled": True}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.api.patch(url, {"store_name": "نوری‌باکس", "footer_description": "توضیح", "copyright_text": "© نوری‌باکس",
                                   "social_links": [{"platform": "instagram", "url": "https://instagram.com/x"}],
                                   "announcement_enabled": True, "announcement_text": "ارسال رایگان",
                                   "admin_notification_phones": "09120000000"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        public = client_for().get("/api/v1/store-settings/").json()
        self.assertEqual((public["store_name"], public["announcement_text"]), ("نوری‌باکس", "ارسال رایگان"))
        self.assertNotIn("admin_notification_phones", public)
        self.assertTrue(AuditLog.objects.filter(action="settings.update").exists())

    def test_maintenance_blocks_new_orders(self):
        from core.testutils import make_address

        method = setup_store()
        user = make_user()
        p = make_product()
        api = client_for(user)
        api.post("/api/v1/cart/items/", {"variant_id": p.variants.first().pk}, format="json")
        settings = StoreSettings.load()
        settings.maintenance_enabled, settings.maintenance_message = True, "در حال به‌روزرسانی"
        settings.save()
        res = api.post("/api/v1/orders/", {"address_id": make_address(user).pk, "shipping_method_id": method.pk}, format="json")
        self.assertEqual(res.status_code, 503, res.content)
        self.assertEqual(res.json()["error"]["code"], "store_maintenance")

    def test_blog_crud_related_and_schedule(self):
        a = self.api.post("/api/admin/cms/blog-posts/", {"title": "مقاله یک", "body": "x", "status": "published"}, format="json").json()
        b = self.api.post("/api/admin/cms/blog-posts/", {"title": "مقاله دو", "body": "y", "status": "published",
                                                        "related_posts": [a["id"]]}, format="json").json()
        self.assertEqual(self.api.patch(f"/api/admin/cms/blog-posts/{a['id']}/", {"related_posts": [a["id"]]},
                                        format="json").status_code, 400)
        detail = client_for().get(f"/api/v1/blog/posts/{b['slug']}/").json()
        self.assertEqual([r["slug"] for r in detail["related"]], [a["slug"]])
        self.assertTrue(b["author"])
        future = self.api.post("/api/admin/cms/blog-posts/", {"title": "آینده", "body": "z", "status": "published",
                                                             "published_at": "2099-01-01T00:00:00Z"}, format="json").json()
        self.assertEqual(client_for().get(f"/api/v1/blog/posts/{future['slug']}/").status_code, 404)

    def test_public_cache_invalidated_on_edit(self):
        self.assertEqual(client_for().get("/api/v1/store-settings/").json()["store_name"], "Noribox Gallery")
        self.api.patch("/api/admin/store-settings/", {"store_name": "تغییر"}, format="json")
        self.assertEqual(client_for().get("/api/v1/store-settings/").json()["store_name"], "تغییر")


class CmsPermissionTests(TestCase):
    def test_non_staff_and_unprivileged_staff_refused(self):
        header = NavigationMenu.objects.get(location="header")
        urls = [NAV, MENUS, "/api/admin/cms/homepage-sections/", "/api/admin/cms/pages/", "/api/admin/store-settings/"]
        for url in urls:
            self.assertEqual(client_for().get(url).status_code, 401, url)
            self.assertEqual(client_for(make_user()).get(url).status_code, 403, url)
        editor = make_staff("cms.view_navigationmenu", "cms.add_navigationitem", "cms.view_navigationitem")
        api = client_for(editor)
        self.assertEqual(api.post(NAV, {"menu": header.pk, "label": "x", "url": "/x"}, format="json").status_code, 201)
        self.assertEqual(api.post(f"{MENUS}{header.pk}/publish/").status_code, 403)  # needs publish permission
