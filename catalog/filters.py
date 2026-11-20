import django_filters
from django.db.models import Q

from catalog.models import Category, Product


class ProductFilter(django_filters.FilterSet):
    category = django_filters.CharFilter(method="filter_category", help_text="Category slug (includes subcategories)")
    brand = django_filters.CharFilter(method="filter_brand", help_text="Brand slug(s), comma separated")
    min_price = django_filters.NumberFilter(field_name="min_price", lookup_expr="gte")
    max_price = django_filters.NumberFilter(field_name="min_price", lookup_expr="lte")
    available = django_filters.BooleanFilter(field_name="is_available")
    featured = django_filters.BooleanFilter(field_name="is_featured")
    options = django_filters.CharFilter(
        method="filter_options", help_text="Attribute option filters: 'code:slug,code:slug2' (OR within attribute, AND across)"
    )
    q = django_filters.CharFilter(method="filter_search", help_text="Full-text-ish search")

    class Meta:
        model = Product
        fields = []

    def filter_category(self, qs, name, value):
        cat = Category.objects.filter(slug=value, is_active=True).first()
        if cat is None:
            return qs.none()
        return qs.filter(categories__in=cat.descendant_ids()).distinct()

    def filter_brand(self, qs, name, value):
        return qs.filter(brand__slug__in=[v.strip() for v in value.split(",") if v.strip()])

    def filter_options(self, qs, name, value):
        groups = {}
        for token in value.split(","):
            if ":" in token:
                code, slug = token.split(":", 1)
                groups.setdefault(code.strip(), []).append(slug.strip())
        for code, slugs in groups.items():
            qs = qs.filter(
                Q(attribute_values__attribute__code=code, attribute_values__options__slug__in=slugs)
                | Q(variants__options__attribute__code=code, variants__options__slug__in=slugs)
            )
        return qs.distinct()

    def filter_search(self, qs, name, value):
        value = value.strip()[:100]
        if not value:
            return qs
        return qs.filter(
            Q(name__icontains=value) | Q(short_description__icontains=value) | Q(brand__name__icontains=value)
            | Q(variants__sku__iexact=value)
        ).distinct()
