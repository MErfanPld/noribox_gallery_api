from django.db import transaction
from rest_framework import serializers

from catalog.models import (
    Attribute, AttributeOption, Brand, Category, CategoryAttribute, Product, ProductAttributeValue,
    ProductCategory, ProductImage, ProductVariant,
)

SEO = ["meta_title", "meta_description", "meta_keywords", "canonical_url"]


# --- public ------------------------------------------------------------------
class CategoryTreeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "image", "parent", "children"]

    def get_children(self, obj) -> list:
        kids = self.context.get("children_map", {}).get(obj.pk, [])
        return CategoryTreeSerializer(kids, many=True, context=self.context).data


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "description", "image", *SEO]


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "logo", "description", "country", *SEO]


class AttributeOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeOption
        fields = ["id", "value", "slug"]


class PublicAttributeSerializer(serializers.ModelSerializer):
    options = AttributeOptionSerializer(many=True, read_only=True)

    class Meta:
        model = Attribute
        fields = ["id", "name", "code", "type", "unit", "is_variant_attribute", "options"]


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image", "alt_text", "is_primary", "variant", "sort_order"]


class VariantOptionSerializer(serializers.ModelSerializer):
    attribute = serializers.CharField(source="attribute.code")
    attribute_name = serializers.CharField(source="attribute.name")

    class Meta:
        model = AttributeOption
        fields = ["id", "attribute", "attribute_name", "value", "slug"]


class PublicVariantSerializer(serializers.ModelSerializer):
    availability = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()
    options = VariantOptionSerializer(many=True, read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "sku", "label", "options", "price", "compare_at_price", "availability", "is_available", "condition_note"]

    def get_availability(self, obj) -> str:
        av = getattr(obj, "availability", None)
        return av.state if av else "unavailable"

    def get_is_available(self, obj) -> bool:
        return self.get_availability(obj) == "available"


class AttributeValueSerializer(serializers.ModelSerializer):
    attribute = serializers.CharField(source="attribute.code")
    attribute_name = serializers.CharField(source="attribute.name")
    unit = serializers.CharField(source="attribute.unit")
    options = AttributeOptionSerializer(many=True, read_only=True)

    class Meta:
        model = ProductAttributeValue
        fields = ["attribute", "attribute_name", "unit", "value_text", "value_number", "value_boolean", "options"]


class ProductListSerializer(serializers.ModelSerializer):
    brand = BrandSerializer(read_only=True)
    min_price = serializers.IntegerField(read_only=True)
    max_compare_at_price = serializers.IntegerField(read_only=True, allow_null=True)
    is_available = serializers.BooleanField(read_only=True)
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "brand", "short_description", "min_price", "max_compare_at_price",
                  "is_available", "is_featured", "primary_image", "published_at"]

    def get_primary_image(self, obj) -> dict | None:
        images = list(obj.images.all())
        if not images:
            return None
        return ProductImageSerializer(images[0], context=self.context).data


class ProductDetailSerializer(ProductListSerializer):
    categories = CategorySerializer(many=True, read_only=True)
    variants = serializers.SerializerMethodField()
    images = ProductImageSerializer(many=True, read_only=True)
    attributes = AttributeValueSerializer(source="attribute_values", many=True, read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + ["description", "categories", "variants", "images", "attributes", *SEO]

    def get_variants(self, obj) -> list:
        variants = [v for v in obj.variants.all() if v.is_active]
        return PublicVariantSerializer(variants, many=True, context=self.context).data


# --- admin -------------------------------------------------------------------
class AdminCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "description", "image", "is_active", "sort_order", *SEO, "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def validate_parent(self, parent):
        if parent and self.instance:
            node = parent
            while node is not None:
                if node.pk == self.instance.pk:
                    raise serializers.ValidationError("Category cannot be its own ancestor.")
                node = node.parent
        return parent


class AdminBrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "logo", "description", "country", "is_active", *SEO, "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}


class AdminAttributeSerializer(serializers.ModelSerializer):
    options = AttributeOptionSerializer(many=True, read_only=True)

    class Meta:
        model = Attribute
        fields = ["id", "name", "code", "type", "unit", "is_filterable", "is_variant_attribute", "sort_order", "options"]


class AdminAttributeOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeOption
        fields = ["id", "attribute", "value", "slug", "sort_order"]
        extra_kwargs = {"slug": {"required": False}}


class AdminCategoryAttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoryAttribute
        fields = ["id", "category", "attribute", "is_required", "sort_order"]


class AdminVariantSerializer(serializers.ModelSerializer):
    availability = serializers.CharField(source="availability.state", read_only=True)
    sold = serializers.BooleanField(source="availability.sold", read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "product", "sku", "label", "options", "price", "compare_at_price", "source_price",
                  "source_compare_at_price", "source_price_updated_at", "barcode", "weight_grams",
                  "condition_note", "is_active", "sort_order", "availability", "sold", "created_at", "updated_at"]
        read_only_fields = ["source_price", "source_compare_at_price", "source_price_updated_at", "created_at", "updated_at"]

    def validate(self, attrs):
        price = attrs.get("price", getattr(self.instance, "price", None))
        compare = attrs.get("compare_at_price", getattr(self.instance, "compare_at_price", None))
        if price is not None and price <= 0:
            raise serializers.ValidationError({"price": "Price must be a positive integer (Toman)."})
        if compare is not None and price is not None and compare < price:
            raise serializers.ValidationError({"compare_at_price": "Must be greater than or equal to price."})
        options = attrs.get("options")
        if options:
            attrs_seen = [o.attribute_id for o in options]
            if len(attrs_seen) != len(set(attrs_seen)):
                raise serializers.ValidationError({"options": "Only one option per attribute."})
        return attrs


class AdminProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "product", "variant", "image", "alt_text", "sort_order", "is_primary", "source_url", "created_at"]
        read_only_fields = ["source_url", "created_at"]

    def validate(self, attrs):
        product = attrs.get("product", getattr(self.instance, "product", None))
        variant = attrs.get("variant")
        if variant and product and variant.product_id != product.pk:
            raise serializers.ValidationError({"variant": "Variant belongs to another product."})
        return attrs


class AdminProductCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ["category", "is_primary"]


class AdminAttributeValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductAttributeValue
        fields = ["attribute", "options", "value_text", "value_number", "value_boolean"]

    def validate(self, attrs):
        attribute = attrs["attribute"]
        for opt in attrs.get("options", []):
            if opt.attribute_id != attribute.pk:
                raise serializers.ValidationError({"options": "Option does not belong to attribute."})
        if attribute.type == Attribute.Type.OPTION and len(attrs.get("options", [])) > 1:
            raise serializers.ValidationError({"options": "Single-option attribute accepts one option."})
        return attrs


class AdminProductSerializer(serializers.ModelSerializer):
    category_links = AdminProductCategorySerializer(source="product_categories", many=True, required=False)
    attribute_values = AdminAttributeValueSerializer(many=True, required=False)
    variants = AdminVariantSerializer(many=True, read_only=True)
    images = AdminProductImageSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "brand", "short_description", "description", "status", "published_at",
                  "is_featured", "sort_order", *SEO, "category_links", "attribute_values", "variants", "images",
                  "created_at", "updated_at"]
        read_only_fields = ["status", "published_at", "created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def validate_category_links(self, links):
        if sum(1 for link in links if link.get("is_primary")) > 1:
            raise serializers.ValidationError("Only one primary category.")
        cats = [link["category"].pk for link in links]
        if len(cats) != len(set(cats)):
            raise serializers.ValidationError("Duplicate categories.")
        return links

    def _save_nested(self, product, links, values):
        if links is not None:
            product.product_categories.all().delete()
            if links and not any(link.get("is_primary") for link in links):
                links[0]["is_primary"] = True
            ProductCategory.objects.bulk_create(
                [ProductCategory(product=product, category=link["category"], is_primary=link.get("is_primary", False)) for link in links]
            )
        if values is not None:
            product.attribute_values.all().delete()
            for value in values:
                options = value.pop("options", [])
                pav = ProductAttributeValue.objects.create(product=product, **value)
                pav.options.set(options)

    @transaction.atomic
    def create(self, validated_data):
        links = validated_data.pop("product_categories", None)
        values = validated_data.pop("attribute_values", None)
        product = Product.objects.create(**validated_data)
        self._save_nested(product, links, values)
        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        links = validated_data.pop("product_categories", None)
        values = validated_data.pop("attribute_values", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        self._save_nested(instance, links, values)
        return instance
