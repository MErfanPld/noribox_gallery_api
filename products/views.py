from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from categories.models import Category as SourceCategory
from core import audit
from core.exceptions import ConflictError
from core.permissions import AdminModelPermission
from products import serializers as s
from products.models import ImportErrorLog, ImportItem, ImportJob, SourceProduct
from products.services.review import (
    approve_source, create_draft_product, ignore_source, link_to_variant, restore_source,
)


class AdminImportJobViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = ImportJob.objects.all()
    serializer_class = s.ImportJobSerializer
    search_fields = ["source_url", "log"]
    ordering_fields = ["created_at", "finished_at", "status"]
    permission_classes = [AdminModelPermission]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filterset_fields = ["status", "dry_run", "source_site"]
    action_perms = {"create": ["products.run_import"], "retry": ["products.run_import"], "cancel": ["products.run_import"]}

    def get_queryset(self):
        return ImportJob.objects.select_related("created_by").defer("source_html")

    @extend_schema(request=s.CreateImportJobSerializer, responses={202: s.ImportJobSerializer})
    def create(self, request):
        """Queue an import (dry-run by default). Executed by ``manage.py process_import_jobs``.

        Items are staged as SourceProducts, never published.
        """
        ser = s.CreateImportJobSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        job = ImportJob.objects.create(
            source_site=d.get("source_site") or settings.SOURCE_SITE_ID,
            source_type=ImportJob.SourceType.URL if d.get("source_url") else ImportJob.SourceType.UPLOAD,
            source_url=d.get("source_url", ""),
            limit=d.get("limit"),
            dry_run=d["dry_run"],
            created_by=request.user,
        )
        if d.get("html_file"):
            job.source_html = d["html_file"].read().decode("utf-8", errors="replace")
            job.save(update_fields=["source_html"])
        return Response(s.ImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(request=None, responses=s.ImportJobSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Re-queue a failed, partially failed or cancelled job. Re-runs are idempotent
        (products are upserted by source id; duplicates are detected by URL)."""
        job = self.get_object()
        updated = ImportJob.objects.filter(pk=job.pk, status__in=ImportJob.RETRYABLE).update(
            status=ImportJob.Status.PENDING, cancel_requested=False, finished_at=None)
        if not updated:
            raise ConflictError("Only failed, partially failed or cancelled jobs can be retried.", code="not_retryable")
        job.refresh_from_db()
        audit.record(request.user, "import.retry", job)
        return Response(s.ImportJobSerializer(job).data)

    @extend_schema(request=None, responses=s.ImportJobSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel a pending job immediately, or ask a running one to stop at its next checkpoint."""
        job = self.get_object()
        if ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.PENDING).update(
                status=ImportJob.Status.CANCELLED, finished_at=timezone.now(), log="Cancelled by an administrator."):
            pass
        elif not ImportJob.objects.filter(pk=job.pk, status=ImportJob.Status.RUNNING).update(cancel_requested=True):
            raise ConflictError("Only pending or running jobs can be cancelled.", code="not_cancellable")
        job.refresh_from_db()
        audit.record(request.user, "import.cancel", job)
        return Response(s.ImportJobSerializer(job).data)


class AdminImportItemViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = ImportItem.objects.all()
    serializer_class = s.ImportItemSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["job", "action"]
    search_fields = ["name", "external_id"]


class AdminImportErrorViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = ImportErrorLog.objects.all()
    serializer_class = s.ImportErrorSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["job"]


class AdminSourceProductViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = SourceProduct.objects.select_related("linked_product", "linked_variant", "reviewed_by").prefetch_related("categories")
    serializer_class = s.SourceProductSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["source_site", "availability", "review_status", "import_items__job", "categories"]
    search_fields = ["name", "source_product_id", "product_url"]
    ordering_fields = ["updated_at", "created_at", "name", "sale_price", "regular_price"]
    review_perm = ["products.review_sourceproduct", "catalog.add_product"]
    approve_perm = review_perm + ["catalog.publish_product"]
    action_perms = {"create_draft": review_perm, "link_variant": ["products.review_sourceproduct"],
                    "ignore": ["products.review_sourceproduct"], "reject": ["products.review_sourceproduct"],
                    "restore": ["products.review_sourceproduct"], "approve": approve_perm,
                    "bulk": review_perm, "summary": ["products.view_sourceproduct"]}

    def get_serializer_class(self):
        return s.SourceProductDetailSerializer if self.action == "retrieve" else s.SourceProductSerializer

    def filter_queryset(self, queryset):
        return super().filter_queryset(queryset).distinct()

    @extend_schema(responses={200: dict})
    @action(detail=False)
    def summary(self, request):
        """Counts per review status (for the dashboard and the review queue)."""
        rows = SourceProduct.objects.values("review_status").annotate(n=Count("id"))
        counts = {r["review_status"]: r["n"] for r in rows}
        return Response({k: counts.get(k, 0) for k in SourceProduct.ReviewStatus.values})

    @extend_schema(request=s.ApproveSerializer, responses={200: dict})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Create the catalog product (if not yet linked) and publish it — one transaction."""
        ser = s.ApproveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        publish = data.pop("publish")
        product = approve_source(self.get_object(), request.user, publish=publish, **data)
        return Response({"product_id": product.pk, "status": product.status, "slug": product.slug})

    @extend_schema(request=s.RejectSerializer, responses=s.SourceProductSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        ser = s.RejectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(s.SourceProductSerializer(ignore_source(self.get_object(), request.user, ser.validated_data["reason"])).data)

    @extend_schema(request=None, responses=s.SourceProductSerializer)
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        return Response(s.SourceProductSerializer(restore_source(self.get_object(), request.user)).data)

    @extend_schema(request=s.BulkReviewSerializer, responses={200: dict})
    @action(detail=False, methods=["post"])
    def bulk(self, request):
        """Apply one review operation to many staged products. Each item is processed in its own
        transaction; per-item failures are reported, never hidden."""
        ser = s.BulkReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        op = d["operation"]
        if op == "approve" and not request.user.has_perms(["catalog.publish_product"]):
            raise PermissionDenied("Publishing requires the publish_product permission.")
        sources = {sp.pk: sp for sp in SourceProduct.objects.filter(pk__in=d["ids"])}
        results = []
        for pk in d["ids"]:
            source = sources.get(pk)
            if source is None:
                results.append({"id": pk, "ok": False, "code": "not_found", "message": "Not found."})
                continue
            try:
                if op in ("approve", "create_draft"):
                    product = approve_source(source, request.user, publish=op == "approve", category=d.get("category"),
                                             brand=d.get("brand"), download_images=d["download_images"])
                    results.append({"id": pk, "ok": True, "product_id": product.pk, "status": product.status})
                elif op == "reject":
                    ignore_source(source, request.user)
                    results.append({"id": pk, "ok": True})
                else:
                    restore_source(source, request.user)
                    results.append({"id": pk, "ok": True})
            except APIException as exc:
                results.append({"id": pk, "ok": False, "code": getattr(exc, "code", "error"), "message": str(exc.detail)})
        return Response({"results": results, "succeeded": sum(r["ok"] for r in results),
                         "failed": sum(not r["ok"] for r in results)})

    @extend_schema(request=s.CreateDraftSerializer, responses={201: dict})
    @action(detail=True, methods=["post"], url_path="create-draft")
    def create_draft(self, request, pk=None):
        ser = s.CreateDraftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = create_draft_product(self.get_object(), request.user, **ser.validated_data)
        return Response({"product_id": product.pk, "status": product.status}, status=status.HTTP_201_CREATED)

    @extend_schema(request=s.LinkVariantSerializer, responses=s.SourceProductSerializer)
    @action(detail=True, methods=["post"], url_path="link-variant")
    def link_variant(self, request, pk=None):
        ser = s.LinkVariantSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(s.SourceProductSerializer(link_to_variant(self.get_object(), ser.validated_data["variant"], request.user)).data)

    @extend_schema(request=None, responses=s.SourceProductSerializer)
    @action(detail=True, methods=["post"])
    def ignore(self, request, pk=None):
        return Response(s.SourceProductSerializer(ignore_source(self.get_object(), request.user)).data)


class AdminSourceCategoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Map source categories to internal catalog categories."""

    queryset = SourceCategory.objects.all()
    serializer_class = s.SourceCategorySerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["source_site", "mapped_category"]
