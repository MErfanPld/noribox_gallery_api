"""SSRF-safe outbound HTTP for untrusted URLs (imports, scraped images).

* only http/https; no credentials in URLs; ports 80/443 (configurable)
* the hostname is resolved once and EVERY address must be public; the
  connection is then made to that vetted IP (no second DNS lookup → no DNS
  rebinding), with TLS SNI/certificate checks against the original hostname
* redirects are followed manually and each hop is validated the same way
* connect/read timeouts and a hard response-size cap
"""
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from django.conf import settings


class UnsafeURLError(ValueError):
    """The URL (or a redirect target) is not allowed."""


class FetchError(OSError):
    """Network/HTTP failure while fetching an allowed URL."""


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    charset: str | None
    body: bytes


def _allowed_ports():
    return set(getattr(settings, "OUTBOUND_ALLOWED_PORTS", [80, 443]))


def is_public_ip(ip) -> bool:
    ip = ipaddress.ip_address(ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                                 or ip.is_private or ip.is_unspecified)


def _resolve(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"Cannot resolve host {host!r}") from exc
    return sorted({info[4][0] for info in infos})


def validate_url(url):
    """Return (scheme, host, port, path, ip) for a safe URL or raise UnsafeURLError."""
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeURLError("Invalid URL.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("Malformed URL.") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError(f"URL scheme {scheme or '(none)'!r} is not allowed.")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in URLs are not allowed.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host.")
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
        raise UnsafeURLError("Internal hostnames are not allowed.")
    port = port or (443 if scheme == "https" else 80)
    if port not in _allowed_ports():
        raise UnsafeURLError(f"Port {port} is not allowed.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = [str(literal)] if literal else _resolve(host, port)
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise UnsafeURLError("URL resolves to a non-public address.")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, port, path, addresses[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _request(scheme, host, port, path, ip, headers, timeout):
    cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    conn = cls(host, ip, port, timeout)
    conn.request("GET", path, headers={"Host": host if port in (80, 443) else f"{host}:{port}", **headers})
    return conn, conn.getresponse()


def safe_fetch(url, *, max_bytes, timeout=None, headers=None, max_redirects=5):
    timeout = timeout or settings.IMPORT_REQUEST_TIMEOUT
    headers = {"User-Agent": settings.IMPORT_USER_AGENT, "Accept-Encoding": "identity", **(headers or {})}
    current = url
    for _ in range(max_redirects + 1):
        scheme, host, port, path, ip = validate_url(current)
        try:
            conn, resp = _request(scheme, host, port, path, ip, headers, timeout)
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f"Request to {host} failed: {exc.__class__.__name__}") from exc
        try:
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    raise FetchError("Redirect without Location header.")
                current = urljoin(current, location)
                continue
            if resp.status >= 400:
                raise FetchError(f"HTTP {resp.status} from {host}")
            length = resp.getheader("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("Response too large.")
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FetchError("Response too large.")
            ctype = resp.getheader("Content-Type", "") or ""
            charset = None
            for part in ctype.split(";")[1:]:
                key, _, val = part.strip().partition("=")
                if key.lower() == "charset":
                    charset = val.strip("\"' ") or None
            return FetchResult(current, resp.status, ctype.split(";")[0].strip().lower(), charset, body)
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError(f"Reading from {host} failed: {exc.__class__.__name__}") from exc
        finally:
            conn.close()
    raise FetchError("Too many redirects.")
