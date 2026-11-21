from django.urls import path
from rest_framework.routers import DefaultRouter

from cms import views

router = DefaultRouter()
router.register("cms/pages", views.AdminPageViewSet, basename="admin-page")
router.register("cms/blog-categories", views.AdminBlogCategoryViewSet, basename="admin-blog-category")
router.register("cms/blog-posts", views.AdminBlogPostViewSet, basename="admin-blog-post")
router.register("cms/faqs", views.AdminFAQViewSet, basename="admin-faq")
router.register("cms/banners", views.AdminBannerViewSet, basename="admin-banner")
router.register("cms/sliders", views.AdminSliderViewSet, basename="admin-slider")
router.register("cms/slides", views.AdminSlideViewSet, basename="admin-slide")
router.register("cms/seo", views.AdminSEOMetadataViewSet, basename="admin-seo")
router.register("cms/navigation-menus", views.AdminNavigationMenuViewSet, basename="admin-navigation-menu")
router.register("cms/navigation-items", views.AdminNavigationItemViewSet, basename="admin-navigation-item")
router.register("cms/homepage-sections", views.AdminHomepageSectionViewSet, basename="admin-homepage-section")
urlpatterns = [
    path("store-settings/", views.AdminStoreSettingsView.as_view(), name="admin-store-settings"),
    *router.urls,
]
