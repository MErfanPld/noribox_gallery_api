from django.urls import path
from rest_framework.routers import DefaultRouter

from commerce import views
from commerce.reports import ReportSummaryView
from shipping import views as shipping_views

router = DefaultRouter()
router.register("orders", views.AdminOrderViewSet, basename="admin-order")
router.register("shipping-methods", shipping_views.AdminShippingMethodViewSet, basename="admin-shipping-method")
router.register("shipments", shipping_views.AdminShipmentViewSet, basename="admin-shipment")
urlpatterns = [path("reports/summary/", ReportSummaryView.as_view(), name="admin-report-summary"), *router.urls]
