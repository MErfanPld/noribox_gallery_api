import io
import itertools

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Address, User
from catalog.models import Brand, Category, Product, ProductCategory, ProductVariant
from notifications.providers import DummyProvider
from payments.models import PaymentMethod, StoreBankCard
from shipping.models import ShippingMethod

_seq = itertools.count(1)


def make_user(phone=None, password="S3cure-pass!", **extra):
    phone = phone or f"0912{next(_seq):07d}"
    return User.objects.create_user(phone=phone, password=password, **extra)


def make_staff(*perms, phone=None):
    user = make_user(phone=phone, is_staff=True)
    for code in perms:
        app, codename = code.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    return User.objects.get(pk=user.pk)


def client_for(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user)
    return c


def make_product(name=None, price=1_000_000, status=Product.Status.PUBLISHED, n_variants=1, brand=None, category=None, **kw):
    name = name or f"Perfume {next(_seq)}"
    p = Product.objects.create(
        name=name, status=status, brand=brand,
        published_at=timezone.now() if status == Product.Status.PUBLISHED else None, **kw,
    )
    if category:
        ProductCategory.objects.create(product=p, category=category, is_primary=True)
    for i in range(n_variants):
        ProductVariant.objects.create(product=p, sku=f"SKU-{p.pk}-{i}-{next(_seq)}", label=f"{50 + i * 50}ml", price=price + i * 100_000)
    return p


def make_address(user):
    return Address.objects.create(
        user=user, recipient_name="Ali", recipient_phone="09121112233", province="Tehran", city="Tehran",
        address_line="Valiasr St.", postal_code="1234567890", is_default=True,
    )


def setup_store():
    PaymentMethod.objects.get_or_create(code="bank-transfer", defaults={"name": "Bank transfer", "is_default": True})
    StoreBankCard.objects.get_or_create(bank_name="Mellat", holder_name="Noribox", card_number="6104337812345678")
    method, _ = ShippingMethod.objects.get_or_create(code="post", defaults={"name": "Post", "cost": 50_000})
    return method


def png_file(name="receipt.png", size=(20, 20)):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def reset_sms():
    DummyProvider.outbox.clear()
    DummyProvider.fail = False
    return DummyProvider


__all__ = ["make_user", "make_staff", "client_for", "make_product", "make_address", "setup_store", "png_file",
           "reset_sms", "Brand", "Category"]
