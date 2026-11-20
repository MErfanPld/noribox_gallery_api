from rest_framework import serializers

from cms.models import FAQ, Banner, BlogCategory, BlogPost, Page, SEOMetadata, Slide, Slider, StoreSettings

SEO = ["meta_title", "meta_description", "meta_keywords", "canonical_url"]


class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Page
        fields = ["id", "title", "slug", "body", "published_at", "updated_at", *SEO]


class BlogCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogCategory
        fields = ["id", "name", "slug", "description", *SEO]


class BlogPostListSerializer(serializers.ModelSerializer):
    category = BlogCategorySerializer(read_only=True)

    class Meta:
        model = BlogPost
        fields = ["id", "title", "slug", "excerpt", "cover_image", "category", "tags", "published_at"]


class BlogPostDetailSerializer(BlogPostListSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta(BlogPostListSerializer.Meta):
        fields = BlogPostListSerializer.Meta.fields + ["body", "author_name", "updated_at", *SEO]

    def get_author_name(self, obj) -> str:
        return obj.author.get_full_name() if obj.author else ""


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ["id", "question", "answer", "group", "sort_order"]


class BannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banner
        fields = ["id", "title", "position", "image", "mobile_image", "link_url", "alt_text", "sort_order"]


class SlideSerializer(serializers.ModelSerializer):
    class Meta:
        model = Slide
        fields = ["id", "title", "subtitle", "image", "mobile_image", "link_url", "sort_order"]


class SliderSerializer(serializers.ModelSerializer):
    slides = serializers.SerializerMethodField()

    class Meta:
        model = Slider
        fields = ["id", "name", "code", "slides"]

    def get_slides(self, obj) -> list:
        return SlideSerializer([sl for sl in obj.slides.all() if sl.is_active], many=True, context=self.context).data


class SEOMetadataSerializer(serializers.ModelSerializer):
    class Meta:
        model = SEOMetadata
        fields = ["path", "title", "description", "keywords", "og_image", "canonical_url", "noindex"]


class PublicStoreSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreSettings
        exclude = ["id", "admin_notification_phones"]


def admin_serializer(model_cls, read_only=("created_at", "updated_at")):
    meta = type("Meta", (), {"model": model_cls, "fields": "__all__",
                             "read_only_fields": list(read_only)})
    return type(f"Admin{model_cls.__name__}Serializer", (serializers.ModelSerializer,), {"Meta": meta})


AdminPageSerializer = admin_serializer(Page)
AdminBlogCategorySerializer = admin_serializer(BlogCategory)
AdminBlogPostSerializer = admin_serializer(BlogPost)
AdminFAQSerializer = admin_serializer(FAQ)
AdminBannerSerializer = admin_serializer(Banner)
AdminSliderSerializer = admin_serializer(Slider)
AdminSlideSerializer = admin_serializer(Slide)
AdminSEOMetadataSerializer = admin_serializer(SEOMetadata)
AdminStoreSettingsSerializer = admin_serializer(StoreSettings, read_only=("id", "updated_at"))
