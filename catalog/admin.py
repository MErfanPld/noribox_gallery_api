from django.contrib import admin

from catalog.models import (
    Attribute, AttributeOption, Brand, Category, CategoryAttribute, Product, ProductAttributeValue,
    ProductCategory, ProductImage, ProductVariant,
)


class VariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ["sku", "label", "price", "compare_at_price", "source_price", "is_active"]
    readonly_fields = ["source_price"]


class ImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


class ProductCategoryInline(admin.TabularInline):
    model = ProductCategory
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "brand", "status", "is_featured", "published_at", "created_at"]
    list_filter = ["status", "brand", "is_featured"]
    search_fields = ["name", "slug", "variants__sku"]
    inlines = [ProductCategoryInline, VariantInline, ImageInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "parent", "is_active", "sort_order"]
    search_fields = ["name", "slug"]


class OptionInline(admin.TabularInline):
    model = AttributeOption
    extra = 0


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "type", "is_filterable", "is_variant_attribute"]
    inlines = [OptionInline]


admin.site.register(Brand)
admin.site.register(CategoryAttribute)
admin.site.register(ProductAttributeValue)


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["sku", "product", "label", "price", "source_price", "is_active"]
    search_fields = ["sku", "product__name"]
