from rest_framework import serializers

from core.files import ClearableFilesMixin

from cms.models import (
    FAQ, Banner, BlogCategory, BlogPost, HomepageSection, NavigationItem, NavigationMenu, Page, SEOMetadata, Slide,
    Slider, StoreSettings,
)

SEO = ["meta_title", "meta_description", "meta_keywords", "canonical_url"]


class PageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Page
        fields = ["id", "title", "slug", "excerpt", "body", "featured_image", "published_at", "updated_at", *SEO]


class BlogCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogCategory
        fields = ["id", "name", "slug", "description", *SEO]


class BlogPostListSerializer(serializers.ModelSerializer):
    category = BlogCategorySerializer(read_only=True)

    class Meta:
        model = BlogPost
        fields = ["id", "title", "slug", "excerpt", "cover_image", "category", "tags", "is_featured", "published_at"]


class BlogPostDetailSerializer(BlogPostListSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta(BlogPostListSerializer.Meta):
        fields = BlogPostListSerializer.Meta.fields + ["body", "author_name", "updated_at", "related", *SEO]

    related = serializers.SerializerMethodField()

    def get_related(self, obj) -> list:
        posts = [p for p in obj.related_posts.published().select_related("category")[:4]]
        return BlogPostListSerializer(posts, many=True, context=self.context).data

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
    return type(f"Admin{model_cls.__name__}Serializer", (ClearableFilesMixin, serializers.ModelSerializer), {"Meta": meta})


SOCIAL_PLATFORMS = ("instagram", "telegram", "whatsapp", "youtube", "aparat", "x", "linkedin", "eitaa", "bale", "rubika")


def validate_site_link(value, field="url"):
    """Internal path (/...) or http(s) URL only — never javascript:, data: etc."""
    from urllib.parse import urlsplit

    if not value:
        return value
    if value.startswith("/") and not value.startswith("//"):
        return value
    parts = urlsplit(value)
    if parts.scheme in ("http", "https") and parts.netloc:
        return value
    raise serializers.ValidationError("نشانی باید با / یا https:// شروع شود.")


class AdminPageSerializer(admin_serializer(Page)):
    class Meta(admin_serializer(Page).Meta):
        extra_kwargs = {"slug": {"required": False}}


class AdminBlogCategorySerializer(admin_serializer(BlogCategory)):
    class Meta(admin_serializer(BlogCategory).Meta):
        extra_kwargs = {"slug": {"required": False}}


class AdminBlogPostSerializer(admin_serializer(BlogPost)):
    author_name = serializers.SerializerMethodField()

    class Meta(admin_serializer(BlogPost).Meta):
        read_only_fields = ["created_at", "updated_at", "author"]
        extra_kwargs = {"slug": {"required": False}}

    def get_author_name(self, obj) -> str:
        return obj.author.get_full_name() or obj.author.phone if obj.author else ""

    def validate_related_posts(self, posts):
        if self.instance and any(p.pk == self.instance.pk for p in posts):
            raise serializers.ValidationError("A post cannot be related to itself.")
        return posts[:12]
AdminFAQSerializer = admin_serializer(FAQ)
class AdminBannerSerializer(admin_serializer(Banner)):
    def validate_link_url(self, value):
        return validate_site_link(value)

    def validate(self, attrs):
        start = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        end = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"ends_at": "پایان باید بعد از شروع باشد."})
        return attrs
AdminSliderSerializer = admin_serializer(Slider)
AdminSlideSerializer = admin_serializer(Slide)
AdminSEOMetadataSerializer = admin_serializer(SEOMetadata)
class AdminStoreSettingsSerializer(admin_serializer(StoreSettings, read_only=("id", "updated_at"))):
    def validate_social_links(self, value):
        if not isinstance(value, list) or len(value) > 20:
            raise serializers.ValidationError("فهرست شبکه‌های اجتماعی نامعتبر است.")
        out = []
        for row in value:
            if not isinstance(row, dict) or row.get("platform") not in SOCIAL_PLATFORMS:
                raise serializers.ValidationError(f"پلتفرم باید یکی از {', '.join(SOCIAL_PLATFORMS)} باشد.")
            url = str(row.get("url", "")).strip()
            if not url.startswith("https://"):
                raise serializers.ValidationError("نشانی شبکه اجتماعی باید با https:// شروع شود.")
            out.append({"platform": row["platform"], "url": url[:500]})
        return out

    def validate_announcement_link(self, value):
        return validate_site_link(value)

    def validate_whatsapp_number(self, value):
        if value and not value.isdigit():
            raise serializers.ValidationError("فقط رقم، با کد کشور (مثلاً 98912...).")
        return value

    def validate(self, attrs):
        if attrs.get("announcement_enabled") and not attrs.get("announcement_text", getattr(self.instance, "announcement_text", "")):
            raise serializers.ValidationError({"announcement_text": "متن اعلان را وارد کنید."})
        if attrs.get("maintenance_enabled") and not attrs.get("maintenance_message", getattr(self.instance, "maintenance_message", "")):
            raise serializers.ValidationError({"maintenance_message": "پیام حالت تعمیر را وارد کنید."})
        return attrs


# --- navigation ----------------------------------------------------------------
class AdminNavigationMenuSerializer(serializers.ModelSerializer):
    has_unpublished_changes = serializers.BooleanField(read_only=True)
    item_count = serializers.IntegerField(source="items.count", read_only=True)
    published_by_phone = serializers.CharField(source="published_by.phone", read_only=True, default=None)

    class Meta:
        model = NavigationMenu
        fields = ["id", "location", "name", "published_at", "published_by_phone", "draft_updated_at",
                  "has_unpublished_changes", "item_count"]
        read_only_fields = ["published_at", "draft_updated_at"]


class AdminNavigationItemSerializer(serializers.ModelSerializer):
    depth = serializers.IntegerField(read_only=True)

    class Meta:
        model = NavigationItem
        fields = ["id", "menu", "parent", "label", "link_type", "url", "page", "category", "brand", "blog_category",
                  "auto_children", "is_active", "show_on_desktop", "show_on_mobile", "open_in_new_tab", "nofollow",
                  "sort_order", "managed_by_page", "depth", "created_at", "updated_at"]
        read_only_fields = ["managed_by_page", "created_at", "updated_at"]

    def validate(self, attrs):
        from cms.navigation import check_parent, validate_link
        from core.exceptions import ServiceError

        get = lambda f: attrs.get(f, getattr(self.instance, f, None))  # noqa: E731
        menu, parent, link_type = get("menu"), get("parent"), get("link_type") or NavigationItem.LinkType.INTERNAL
        if self.instance and "menu" in attrs and attrs["menu"].pk != self.instance.menu_id:
            raise serializers.ValidationError({"menu": "Items cannot move between menus."})
        url = (attrs.get("url", getattr(self.instance, "url", "")) or "").strip()
        targets = {f: get(f) for f in ("page", "category", "brand", "blog_category")}
        errors = validate_link(link_type, url, targets)
        if errors:
            raise serializers.ValidationError(errors)
        try:
            check_parent(menu, self.instance.pk if self.instance else None, parent)
        except ServiceError as exc:
            raise serializers.ValidationError({"parent": str(exc.detail)}) from exc
        if get("auto_children") and parent is not None and parent.depth + 1 >= NavigationMenu.MAX_DEPTH:
            raise serializers.ValidationError({"auto_children": "این سطح نمی‌تواند زیرمنوی خودکار داشته باشد."})
        # Clear targets that do not match the type (keeps rows unambiguous).
        T = NavigationItem.LinkType
        keep = {T.PAGE: "page", T.CATEGORY: "category", T.BRAND: "brand", T.BLOG_CATEGORY: "blog_category"}.get(link_type)
        for f in targets:
            if f != keep:
                attrs[f] = None
        attrs["url"] = url if link_type in (T.INTERNAL, T.EXTERNAL) else ""
        return attrs


class ReorderRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    parent = serializers.IntegerField(allow_null=True)
    sort_order = serializers.IntegerField(min_value=0, max_value=100000)


class ReorderSerializer(serializers.Serializer):
    items = ReorderRowSerializer(many=True, max_length=500)


# --- homepage ------------------------------------------------------------------
class AdminHomepageSectionSerializer(ClearableFilesMixin, serializers.ModelSerializer):
    class Meta:
        model = HomepageSection
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]

    def validate_cta_url(self, value):
        return validate_site_link(value)

    def validate_secondary_cta_url(self, value):
        return validate_site_link(value)

    def validate_limit(self, value):
        if not 1 <= value <= 24:
            raise serializers.ValidationError("بین ۱ تا ۲۴.")
        return value

    def validate_items(self, value):
        if not isinstance(value, list) or len(value) > 12:
            raise serializers.ValidationError("حداکثر ۱۲ مورد.")
        out = []
        for row in value:
            if not isinstance(row, dict):
                raise serializers.ValidationError("هر مورد باید شامل title و text باشد.")
            item = {k: str(row.get(k, ""))[:255] for k in ("title", "text", "url") if row.get(k) not in (None, "")}
            if not item.get("title"):
                raise serializers.ValidationError("عنوان هر مورد الزامی است.")
            if item.get("url"):
                validate_site_link(item["url"])
            out.append(item)
        return out


class HomepageReorderSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=100)
