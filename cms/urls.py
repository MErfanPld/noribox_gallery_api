from django.urls import path
from rest_framework.routers import DefaultRouter

from cms import views

router = DefaultRouter()
router.register("pages", views.PageViewSet, basename="page")
router.register("blog/categories", views.BlogCategoryViewSet, basename="blog-category")
router.register("blog/posts", views.BlogPostViewSet, basename="blog-post")
router.register("faqs", views.FAQViewSet, basename="faq")
router.register("banners", views.BannerViewSet, basename="banner")
router.register("sliders", views.SliderViewSet, basename="slider")
urlpatterns = [
    path("store-settings/", views.StoreSettingsView.as_view(), name="store-settings"),
    path("seo/", views.SEOMetadataView.as_view(), name="seo-metadata"),
    *router.urls,
]
