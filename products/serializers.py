from rest_framework import serializers

from catalog.models import Brand, Category, ProductVariant
from categories.models import Category as SourceCategory
from products.models import ImportErrorLog, ImportItem, ImportJob, SourceProduct


class SourceCategoryBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceCategory
        fields = ["id", "name", "slug", "mapped_category"]


class SourceProductSerializer(serializers.ModelSerializer):
    source_categories = SourceCategoryBriefSerializer(source="categories", many=True, read_only=True)
    linked_product_status = serializers.CharField(source="linked_product.status", read_only=True, default=None)
    linked_product_name = serializers.CharField(source="linked_product.name", read_only=True, default=None)
    suggested_category = serializers.SerializerMethodField()
    reviewed_by_phone = serializers.CharField(source="reviewed_by.phone", read_only=True, default=None)

    class Meta:
        model = SourceProduct
        exclude = ["raw_data"]
        read_only_fields = [f.name for f in SourceProduct._meta.fields] + ["categories"]

    def get_suggested_category(self, obj) -> int | None:
        for c in obj.categories.all():
            if c.mapped_category_id:
                return c.mapped_category_id
        return None


class SourceProductDetailSerializer(SourceProductSerializer):
    import_history = serializers.SerializerMethodField()

    class Meta(SourceProductSerializer.Meta):
        exclude = None
        fields = "__all__"

    def get_import_history(self, obj) -> list:
        items = obj.import_items.select_related("job").order_by("-id")[:20]
        return [{"job": i.job_id, "action": i.action, "changes": i.changes, "created_at": i.created_at} for i in items]


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
    created_by_phone = serializers.CharField(source="created_by.phone", read_only=True, default=None)

    class Meta:
        model = ImportJob
        fields = ["id", "source_site", "source_type", "source_url", "limit", "dry_run", "status", "stats", "log",
                  "attempts", "heartbeat_at", "started_at", "finished_at", "created_by", "created_by_phone",
                  "created_at", "error_count", "cancel_requested"]
        read_only_fields = fields


class CreateImportJobSerializer(serializers.Serializer):
    source_url = serializers.URLField(required=False, allow_blank=True)
    html_file = serializers.FileField(required=False)
    source_site = serializers.SlugField(required=False, max_length=64)
    limit = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5000)
    dry_run = serializers.BooleanField(default=True)

    def validate_source_url(self, value):
        from core.http import FetchError, UnsafeURLError, validate_url

        if value:
            try:
                validate_url(value)
            except (UnsafeURLError, FetchError) as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

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


class ApproveSerializer(CreateDraftSerializer):
    publish = serializers.BooleanField(default=True)


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class BulkReviewSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=100)
    operation = serializers.ChoiceField(choices=["approve", "create_draft", "reject", "restore"])
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), required=False, allow_null=True)
    brand = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all(), required=False, allow_null=True)
    download_images = serializers.BooleanField(default=True)


class LinkVariantSerializer(serializers.Serializer):
    variant = serializers.PrimaryKeyRelatedField(queryset=ProductVariant.objects.all())


class SourceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceCategory
        fields = "__all__"
        read_only_fields = ["source_site", "source_category_id", "source_url", "last_successful_sync_at", "created_at", "updated_at"]
