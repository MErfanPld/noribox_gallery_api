"""Client IP resolution behind a trusted reverse proxy.

Forwarded headers are honoured ONLY when the direct peer (REMOTE_ADDR) is one
of ``TRUSTED_PROXIES`` (CIDRs). Nginx must *overwrite* (not append)
``X-Real-IP`` / ``X-Forwarded-For`` with ``$remote_addr`` — see
``deploy/nginx.conf``. A client talking to the app directly can never choose
its own identity.
"""
import ipaddress
from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=32)
def _networks(spec: tuple):
    return tuple(ipaddress.ip_network(item, strict=False) for item in spec)


def _parse_ip(value):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def is_trusted_proxy(ip) -> bool:
    nets = _networks(tuple(settings.TRUSTED_PROXIES))
    return ip is not None and any(ip in net for net in nets)


def get_client_ip(request) -> str:
    meta = request.META
    remote = _parse_ip(meta.get("REMOTE_ADDR"))
    if remote is None:
        return ""
    if not is_trusted_proxy(remote):
        return str(remote)
    real = _parse_ip(meta.get("HTTP_X_REAL_IP"))
    if real is not None:
        return str(real)
    # Walk X-Forwarded-For right-to-left, skipping our own proxies; the first
    # untrusted hop is the client as seen by the outermost trusted proxy.
    for hop in reversed([h for h in meta.get("HTTP_X_FORWARDED_FOR", "").split(",") if h.strip()]):
        ip = _parse_ip(hop)
        if ip is None:
            break
        if not is_trusted_proxy(ip):
            return str(ip)
    return str(remote)
