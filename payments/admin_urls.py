from django.urls import path
from rest_framework.routers import DefaultRouter

from payments import views

router = DefaultRouter()
router.register("payment-methods", views.AdminPaymentMethodViewSet, basename="admin-payment-method")
router.register("bank-cards", views.AdminBankCardViewSet, basename="admin-bank-card")
router.register("payments", views.AdminPaymentViewSet, basename="admin-payment")
router.register("receipts", views.AdminReceiptViewSet, basename="admin-receipt")
urlpatterns = [
    path("receipts/<int:pk>/file/", views.AdminReceiptFileView.as_view(), name="admin-receipt-file"),
    *router.urls,
]
