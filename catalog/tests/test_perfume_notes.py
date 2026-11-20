from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from catalog.models import Attribute, AttributeOption, Category, ProductAttributeValue
from core.testutils import client_for, make_product


class PerfumeNotesTests(TestCase):
    def test_seed_is_idempotent_and_notes_are_filterable(self):
        cat = Category.objects.create(name="Perfume", slug="perfume")
        call_command("seed_perfume_attributes", "--category", "perfume", stdout=StringIO())
        call_command("seed_perfume_attributes", "--category", "perfume", stdout=StringIO())
        attrs = Attribute.objects.filter(code__in=["notes_top", "notes_heart", "notes_base"])
        self.assertEqual(attrs.count(), 3)
        self.assertTrue(all(a.type == "multi_option" and a.is_filterable for a in attrs))
        self.assertEqual(cat.category_attributes.count(), 3)
        base = attrs.get(code="notes_base")
        oud = AttributeOption.objects.create(attribute=base, value="عود", slug="oud")
        p = make_product("Oud Royal", category=cat)
        make_product("Fresh", category=cat)
        ProductAttributeValue.objects.create(product=p, attribute=base).options.add(oud)
        res = client_for().get("/api/v1/products/", {"options": "notes_base:oud"})
        self.assertEqual([r["name"] for r in res.data["results"]], ["Oud Royal"])
        detail = client_for().get(f"/api/v1/products/{p.slug}/").data
        self.assertEqual(detail["attributes"][0]["attribute"], "notes_base")
        self.assertEqual(detail["attributes"][0]["options"][0]["value"], "عود")
        codes = [a["code"] for a in client_for().get("/api/v1/categories/perfume/attributes/").data]
        self.assertEqual(sorted(codes), ["notes_base", "notes_heart", "notes_top"])
