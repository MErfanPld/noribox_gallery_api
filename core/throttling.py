"""DRF throttles keyed on the trusted client IP and backed by the shared cache."""
from rest_framework import throttling

from core.net import get_client_ip


class ClientIPMixin:
    def get_ident(self, request):
        return get_client_ip(request) or "unknown"


class AnonRateThrottle(ClientIPMixin, throttling.AnonRateThrottle):
    pass


class UserRateThrottle(ClientIPMixin, throttling.UserRateThrottle):
    pass


class ScopedRateThrottle(ClientIPMixin, throttling.ScopedRateThrottle):
    pass
