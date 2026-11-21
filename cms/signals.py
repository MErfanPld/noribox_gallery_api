"""Invalidate cached public CMS payloads when anything they are built from changes."""
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from cms import cache
from cms.models import Banner, BlogCategory, BlogPost, HomepageSection, NavigationItem, NavigationMenu, Page, StoreSettings
from inventory.models import VariantAvailability

WATCHED = (Banner, BlogCategory, BlogPost, HomepageSection, NavigationMenu, Page, StoreSettings,
           Brand, Category, Product, ProductImage, ProductVariant, VariantAvailability)


def _bump(**kwargs):
    cache.bump()


for model in WATCHED:
    post_save.connect(_bump, sender=model, dispatch_uid=f"cms-bump-save-{model.__name__}")
    post_delete.connect(_bump, sender=model, dispatch_uid=f"cms-bump-delete-{model.__name__}")
for through in (HomepageSection.products.through, HomepageSection.categories.through, HomepageSection.brands.through):
    m2m_changed.connect(_bump, sender=through, dispatch_uid=f"cms-bump-m2m-{through.__name__}")


@receiver(post_save, sender=Page, dispatch_uid="cms-page-nav-sync")
def page_nav_sync(sender, instance, raw=False, **kwargs):
    if not raw:
        from cms.navigation import sync_page_links

        sync_page_links(instance)


@receiver(post_save, sender=NavigationItem, dispatch_uid="cms-nav-touch-save")
@receiver(post_delete, sender=NavigationItem, dispatch_uid="cms-nav-touch-delete")
def nav_item_changed(sender, instance, **kwargs):
    NavigationMenu.objects.filter(pk=instance.menu_id).update(draft_updated_at=timezone.now())
