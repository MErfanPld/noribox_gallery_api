from drf_spectacular.openapi import AutoSchema


class NoriboxAutoSchema(AutoSchema):
    """Separate storefront and admin operations in OpenAPI (tags + unique operationIds)."""

    def _is_admin(self):
        return self.path.startswith("/api/admin/")

    def get_tags(self):
        tags = super().get_tags()
        if self._is_admin():
            return [f"admin-{t}" if not t.startswith("admin") else t for t in tags]
        return tags

    def get_operation_id(self):
        op = super().get_operation_id()
        return f"admin_{op}" if self._is_admin() else op
