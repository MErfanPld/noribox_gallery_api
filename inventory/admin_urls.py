from rest_framework.routers import DefaultRouter

from inventory import views

router = DefaultRouter()
router.register("inventory/availability", views.AdminAvailabilityViewSet, basename="admin-availability")
router.register("inventory/reservations", views.AdminReservationViewSet, basename="admin-reservation")
urlpatterns = router.urls
