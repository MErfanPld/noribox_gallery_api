from django.test import TestCase

from catalog.models import Attribute, AttributeOption, Brand, Category, Product, ProductAttributeValue
from core.testutils import client_for, make_product, make_staff
from inventory.models import AvailabilityState
from inventory.services import set_availability


class StorefrontTests(TestCase):
    def setUp(self):
        self.c = client_for()
        self.root = Category.objects.create(name="Perfume")
        self.men = Category.objects.create(name="Men", parent=self.root)
        self.brand = Brand.objects.create(name="Dior")
        self.a = make_product("Sauvage", price=5_000_000, brand=self.brand, category=self.men)
        self.b = make_product("Cheap", price=500_000)
        self.draft = make_product("Secret", status=Product.Status.DRAFT)
        self.archived = make_product("Old", status=Product.Status.ARCHIVED)

    def names(self, **params):
        return [p["name"] for p in self.c.get("/api/v1/products/", params).data["results"]]

    def test_only_published_visible(self):
        self.assertEqual(set(self.names()), {"Sauvage", "Cheap"})
        self.assertEqual(self.c.get(f"/api/v1/products/{self.draft.slug}/").status_code, 404)

    def test_filters_and_sorting(self):
        self.assertEqual(self.names(category="perfume"), ["Sauvage"])  # includes subcategories
        self.assertEqual(self.names(brand="dior"), ["Sauvage"])
        self.assertEqual(self.names(min_price=1_000_000), ["Sauvage"])
        self.assertEqual(self.names(sort="price_asc"), ["Cheap", "Sauvage"])
        self.assertEqual(self.names(sort="price_desc"), ["Sauvage", "Cheap"])
        self.assertEqual(self.names(q="sauv"), ["Sauvage"])
        set_availability(self.b.variants.first(), AvailabilityState.UNAVAILABLE)
        self.assertEqual(self.names(available="true"), ["Sauvage"])

    def test_option_filter(self):
        vol = Attribute.objects.create(name="Gender", code="gender", type="option", is_filterable=True)
        male = AttributeOption.objects.create(attribute=vol, value="Male")
        pav = ProductAttributeValue.objects.create(product=self.a, attribute=vol)
        pav.options.add(male)
        self.assertEqual(self.names(options="gender:male"), ["Sauvage"])

    def test_detail_hides_internal_fields(self):
        v = self.a.variants.first()
        v.source_price = 123
        v.save()
        data = self.c.get(f"/api/v1/products/{self.a.slug}/").data
        self.assertEqual(data["variants"][0]["availability"], "available")
        self.assertNotIn("source_price", data["variants"][0])
        self.assertEqual(data["min_price"], 5_000_000)

    def test_category_tree(self):
        tree = self.c.get("/api/v1/categories/tree/").data
        self.assertEqual(tree[0]["children"][0]["name"], "Men")


class AdminCatalogTests(TestCase):
    def test_product_crud_publish_requires_variant(self):
        staff = make_staff("catalog.add_product", "catalog.change_product", "catalog.view_product", "catalog.publish_product",
                           "catalog.add_productvariant")
        c = client_for(staff)
        cat = Category.objects.create(name="Watches")
        res = c.post("/api/admin/products/", {"name": "Rolex", "category_links": [{"category": cat.pk}]}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        pid = res.data["id"]
        self.assertEqual(res.data["status"], "draft")
        self.assertEqual(c.post(f"/api/admin/products/{pid}/publish/").status_code, 400)
        res = c.post("/api/admin/variants/", {"product": pid, "sku": "RLX-1", "price": 100, "compare_at_price": 50})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(c.post("/api/admin/variants/", {"product": pid, "sku": "RLX-1", "price": 100}).status_code, 201)
        self.assertEqual(c.post(f"/api/admin/products/{pid}/publish/").data["status"], "published")
        self.assertTrue(Product.objects.get(pk=pid).product_categories.get().is_primary)

    def test_publish_requires_specific_permission(self):
        c = client_for(make_staff("catalog.change_product", "catalog.view_product"))
        p = make_product(status=Product.Status.DRAFT)
        self.assertEqual(c.post(f"/api/admin/products/{p.pk}/publish/").status_code, 403)
