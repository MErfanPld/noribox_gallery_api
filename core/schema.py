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


def public_endpoints(endpoints, **kwargs):
    """Preprocessing hook: storefront schema excludes the management API."""
    return [e for e in endpoints if not e[0].startswith("/api/admin/")]


def admin_endpoints(endpoints, **kwargs):
    return [e for e in endpoints if e[0].startswith("/api/admin/")]
