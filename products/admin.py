from django.contrib import admin

from products.models import ImportErrorLog, ImportItem, ImportJob, SourceProduct


@admin.register(SourceProduct)
class SourceProductAdmin(admin.ModelAdmin):
    list_display = ("name", "source_site", "source_product_id", "regular_price", "sale_price",
                    "availability", "review_status", "linked_variant", "last_successful_sync_at")
    list_filter = ("source_site", "availability", "review_status")
    search_fields = ("name", "source_product_id", "product_url")
    readonly_fields = ("created_at", "updated_at", "source_last_seen_at", "last_successful_sync_at")
    raw_id_fields = ("linked_product", "linked_variant")


class ImportItemInline(admin.TabularInline):
    model = ImportItem
    extra = 0
    readonly_fields = ("source_product_id", "name", "action", "changes", "source_product")
    can_delete = False


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "source_site", "source_type", "dry_run", "status", "created_at", "finished_at")
    list_filter = ("status", "dry_run", "source_type")
    inlines = [ImportItemInline]


admin.site.register(ImportErrorLog)
