from rest_framework import serializers

from catalog.models import Brand, Category, ProductVariant
from categories.models import Category as SourceCategory
from products.models import ImportErrorLog, ImportItem, ImportJob, SourceProduct


class SourceProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceProduct
        fields = "__all__"
        read_only_fields = [f.name for f in SourceProduct._meta.fields] + ["categories"]


class ImportItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportItem
        fields = "__all__"


class ImportErrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportErrorLog
        fields = "__all__"


class ImportJobSerializer(serializers.ModelSerializer):
    error_count = serializers.IntegerField(source="errors.count", read_only=True)

    class Meta:
        model = ImportJob
        fields = ["id", "source_site", "source_type", "source_url", "limit", "dry_run", "status", "stats", "log",
                  "started_at", "finished_at", "created_by", "created_at", "error_count"]
        read_only_fields = ["status", "stats", "log", "started_at", "finished_at", "created_by", "created_at", "source_type"]


class CreateImportJobSerializer(serializers.Serializer):
    source_url = serializers.URLField(required=False, allow_blank=True)
    html_file = serializers.FileField(required=False)
    source_site = serializers.SlugField(required=False, max_length=64)
    limit = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5000)
    dry_run = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if bool(attrs.get("source_url")) == bool(attrs.get("html_file")):
            raise serializers.ValidationError("Provide exactly one of source_url or html_file.")
        f = attrs.get("html_file")
        if f is not None:
            if f.size > 10 * 1024 * 1024:
                raise serializers.ValidationError({"html_file": "HTML file too large (max 10MB)."})
            if not f.name.lower().endswith((".html", ".htm")):
                raise serializers.ValidationError({"html_file": "Only .html files are accepted."})
        return attrs


class CreateDraftSerializer(serializers.Serializer):
    price = serializers.IntegerField(required=False, min_value=1, help_text="Selling price (Toman); defaults to source price")
    sku = serializers.CharField(required=False, max_length=64)
    name = serializers.CharField(required=False, max_length=512)
    label = serializers.CharField(required=False, allow_blank=True, max_length=255)
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), required=False, allow_null=True)
    brand = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all(), required=False, allow_null=True)
    download_images = serializers.BooleanField(default=True)

    def validate_sku(self, value):
        if ProductVariant.objects.filter(sku=value).exists():
            raise serializers.ValidationError("SKU already exists.")
        return value


class LinkVariantSerializer(serializers.Serializer):
    variant = serializers.PrimaryKeyRelatedField(queryset=ProductVariant.objects.all())


class SourceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceCategory
        fields = "__all__"
        read_only_fields = ["source_site", "source_category_id", "source_url", "last_successful_sync_at", "created_at", "updated_at"]
