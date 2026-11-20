from django.urls import path

from payments import views

urlpatterns = [
    path("payment-methods/", views.PaymentMethodListView.as_view(), name="payment-methods"),
    path("bank-cards/", views.BankCardListView.as_view(), name="bank-cards"),
    path("orders/<str:number>/receipts/", views.OrderReceiptUploadView.as_view(), name="order-receipt-upload"),
    path("receipts/<int:pk>/file/", views.ReceiptFileView.as_view(), name="receipt-file"),
]
