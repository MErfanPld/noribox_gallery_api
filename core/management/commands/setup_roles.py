from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

ALL = ["view", "add", "change", "delete"]


def perms(app, models, verbs=ALL):
    return [f"{app}.{v}_{m}" for m in models for v in verbs]


ROLES = {
    "catalog_manager": [
        *perms("catalog", ["category", "brand", "attribute", "attributeoption", "categoryattribute", "product",
                           "productvariant", "productimage"]),
        "catalog.publish_product",
        *perms("inventory", ["variantavailability", "reservation"], ["view"]),
        "inventory.manage_inventory",
        *perms("products", ["sourceproduct", "importjob", "importitem", "importerrorlog"], ["view"]),
        *perms("categories", ["category"], ["view", "change"]),
        "products.add_importjob", "products.run_import", "products.review_sourceproduct",
        "core.view_auditlog",
    ],
    "order_manager": [
        *perms("commerce", ["order"], ["view", "change"]), "commerce.manage_order_status",
        *perms("shipping", ["shipment"], ["view", "change"]), *perms("shipping", ["shippingmethod"]),
        *perms("payments", ["payment", "paymentreceipt"], ["view"]),
        *perms("inventory", ["variantavailability", "reservation"], ["view"]),
        *perms("accounts", ["user"], ["view"]),
    ],
    "finance": [
        *perms("payments", ["payment", "paymentreceipt"], ["view"]), "payments.review_payment",
        "commerce.record_refund",
        *perms("payments", ["paymentmethod", "storebankcard"]),
        *perms("commerce", ["order"], ["view"]),
        *perms("inventory", ["reservation"], ["view"]),
    ],
    "content_editor": [
        *perms("cms", ["page", "blogcategory", "blogpost", "faq", "banner", "slider", "slide", "seometadata",
                       "navigationmenu", "navigationitem", "homepagesection"]),
        "cms.publish_navigationmenu", "cms.view_storesettings", "cms.change_storesettings",
        *perms("catalog", ["category", "brand", "product"], ["view"]),
    ],
    "support": [
        *perms("accounts", ["user", "address"], ["view"]),
        *perms("commerce", ["order"], ["view"]),
        *perms("notifications", ["smslog"], ["view"]), "notifications.retry_sms",
    ],
    "notifications_manager": [
        *perms("notifications", ["smstemplate"]), *perms("notifications", ["smslog"], ["view"]), "notifications.retry_sms",
    ],
}


class Command(BaseCommand):
    help = "Create/update RBAC roles (Django groups) with their permissions. Idempotent."

    def handle(self, *args, **options):
        for role, codes in ROLES.items():
            group, _ = Group.objects.get_or_create(name=role)
            objs = []
            for code in codes:
                app, codename = code.split(".")
                perm = Permission.objects.filter(content_type__app_label=app, codename=codename).first()
                if perm is None:
                    self.stderr.write(f"Missing permission {code}")
                    continue
                objs.append(perm)
            group.permissions.set(objs)
            self.stdout.write(self.style.SUCCESS(f"{role}: {len(objs)} permissions"))
