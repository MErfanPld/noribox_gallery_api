"""Seed the perfume note-pyramid attributes (data only, no schema change).

Creates three filterable ``multi_option`` attributes — ``notes_top``,
``notes_heart`` and ``notes_base`` — and optionally attaches them to a
category. Individual notes are AttributeOptions, so they are reusable across
products and filterable (``?options=notes_base:oud``). Idempotent.
"""
from django.core.management.base import BaseCommand, CommandError

from catalog.models import Attribute, Category, CategoryAttribute

NOTE_ATTRIBUTES = [
    ("notes_top", "نت‌های آغازی", 10),
    ("notes_heart", "نت‌های میانی", 11),
    ("notes_base", "نت‌های پایه", 12),
]


class Command(BaseCommand):
    help = "Create notes_top/notes_heart/notes_base attributes and attach them to a category (by slug)."

    def add_arguments(self, parser):
        parser.add_argument("--category", help="Category slug to attach the attributes to (e.g. perfume)")

    def handle(self, *args, **options):
        attrs = []
        for code, name, order in NOTE_ATTRIBUTES:
            attr, created = Attribute.objects.get_or_create(
                code=code,
                defaults={"name": name, "type": Attribute.Type.MULTI_OPTION, "is_filterable": True, "sort_order": order},
            )
            if attr.type != Attribute.Type.MULTI_OPTION:
                raise CommandError(f"Attribute {code} exists with type {attr.type}; expected multi_option")
            attrs.append(attr)
            self.stdout.write(f"{'created' if created else 'exists '} {code}")
        slug = options.get("category")
        if slug:
            category = Category.objects.filter(slug=slug).first()
            if category is None:
                raise CommandError(f"Category {slug!r} not found")
            for attr in attrs:
                CategoryAttribute.objects.get_or_create(category=category, attribute=attr, defaults={"sort_order": attr.sort_order})
            self.stdout.write(self.style.SUCCESS(f"Attached to category {slug}"))
