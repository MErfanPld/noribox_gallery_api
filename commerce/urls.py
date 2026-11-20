from django.urls import path
from rest_framework.routers import DefaultRouter

from commerce import views

router = DefaultRouter()
router.register("orders", views.OrderViewSet, basename="order")
urlpatterns = [
    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/items/", views.CartItemView.as_view(), name="cart-items"),
    path("cart/items/<int:item_id>/", views.CartItemDeleteView.as_view(), name="cart-item-delete"),
    path("orders/track/", views.TrackOrderView.as_view(), name="order-track"),
    *router.urls,
]
