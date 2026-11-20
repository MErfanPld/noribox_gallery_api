from rest_framework.routers import DefaultRouter

from catalog import views

router = DefaultRouter()
router.register("categories", views.AdminCategoryViewSet, basename="admin-category")
router.register("brands", views.AdminBrandViewSet, basename="admin-brand")
router.register("attributes", views.AdminAttributeViewSet, basename="admin-attribute")
router.register("attribute-options", views.AdminAttributeOptionViewSet, basename="admin-attribute-option")
router.register("category-attributes", views.AdminCategoryAttributeViewSet, basename="admin-category-attribute")
router.register("products", views.AdminProductViewSet, basename="admin-product")
router.register("variants", views.AdminVariantViewSet, basename="admin-variant")
router.register("product-images", views.AdminProductImageViewSet, basename="admin-product-image")
urlpatterns = router.urls
