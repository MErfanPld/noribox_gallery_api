from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from categories.models import Category as SourceCategory
from core.exceptions import ConflictError
from core.permissions import AdminModelPermission
from products import serializers as s
from products.models import ImportErrorLog, ImportItem, ImportJob, SourceProduct
from products.services.review import create_draft_product, ignore_source, link_to_variant


class AdminImportJobViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    queryset = ImportJob.objects.all()
    serializer_class = s.ImportJobSerializer
    permission_classes = [AdminModelPermission]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filterset_fields = ["status", "dry_run", "source_site"]
    action_perms = {"create": ["products.run_import"], "retry": ["products.run_import"]}

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
        """Re-queue a failed job."""
        updated = ImportJob.objects.filter(pk=pk, status=ImportJob.Status.FAILED).update(status=ImportJob.Status.PENDING)
        if not updated:
            raise ConflictError("Only failed jobs can be retried.", code="not_retryable")
        return Response(s.ImportJobSerializer(ImportJob.objects.get(pk=pk)).data)


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
    queryset = SourceProduct.objects.select_related("linked_product", "linked_variant")
    serializer_class = s.SourceProductSerializer
    permission_classes = [AdminModelPermission]
    filterset_fields = ["source_site", "availability", "review_status"]
    search_fields = ["name", "source_product_id", "product_url"]
    ordering_fields = ["updated_at", "created_at", "name"]
    review_perm = ["products.review_sourceproduct", "catalog.add_product"]
    action_perms = {"create_draft": review_perm, "link_variant": ["products.review_sourceproduct"],
                    "ignore": ["products.review_sourceproduct"]}

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
