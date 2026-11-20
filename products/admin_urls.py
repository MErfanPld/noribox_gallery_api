from rest_framework.routers import DefaultRouter

from products import views

router = DefaultRouter()
router.register("imports/jobs", views.AdminImportJobViewSet, basename="admin-import-job")
router.register("imports/items", views.AdminImportItemViewSet, basename="admin-import-item")
router.register("imports/errors", views.AdminImportErrorViewSet, basename="admin-import-error")
router.register("imports/source-products", views.AdminSourceProductViewSet, basename="admin-source-product")
router.register("imports/source-categories", views.AdminSourceCategoryViewSet, basename="admin-source-category")
urlpatterns = router.urls
