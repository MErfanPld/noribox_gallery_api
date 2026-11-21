"""Upload validation (extension + declared MIME + magic-byte signature + size)."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils.functional import cached_property

SIGNATURES = {
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "pdf": (b"%PDF-",),
    "webp": (b"RIFF",),
    "gif": (b"GIF87a", b"GIF89a"),
}
MIME_TYPES = {
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "pdf": {"application/pdf"},
    "webp": {"image/webp"},
    "gif": {"image/gif"},
}


def detect_kind(head: bytes):
    for ext, sigs in SIGNATURES.items():
        if any(head.startswith(sig) for sig in sigs):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    return None


def validate_upload(uploaded, allowed_extensions, max_size):
    """Return the canonical extension or raise ValidationError."""
    name = getattr(uploaded, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    allowed = {e.lower() for e in allowed_extensions}
    if ext not in allowed:
        raise ValidationError(f"File extension not allowed. Allowed: {', '.join(sorted(allowed))}")
    if uploaded.size is None or uploaded.size == 0:
        raise ValidationError("Empty file.")
    if uploaded.size > max_size:
        raise ValidationError(f"File too large. Max {max_size // 1024} KB.")
    declared = (getattr(uploaded, "content_type", "") or "").lower()
    if declared and declared not in MIME_TYPES.get(ext, set()):
        raise ValidationError("File MIME type does not match its extension.")
    uploaded.seek(0)
    head = uploaded.read(16)
    uploaded.seek(0)
    kind = detect_kind(head)
    if kind is None or MIME_TYPES.get(kind) != MIME_TYPES.get(ext):
        raise ValidationError("File content does not match its type.")
    if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
        try:
            from PIL import Image

            img = Image.open(uploaded)
            img.verify()
        except Exception as exc:  # noqa: BLE001
            raise ValidationError("Invalid or corrupted image.") from exc
        finally:
            uploaded.seek(0)
    return ext


@deconstructible
class ImageUploadValidator:
    def __call__(self, value):
        file = getattr(value, "file", value)
        if getattr(value, "_committed", True) and not hasattr(file, "content_type"):
            return  # already stored file, nothing new to validate
        validate_upload(
            value,
            ["jpg", "jpeg", "png", "webp", "gif"],
            settings.PRODUCT_IMAGE_MAX_SIZE_BYTES,
        )

    def __eq__(self, other):
        return isinstance(other, ImageUploadValidator)


@deconstructible
class PrivateStorage(FileSystemStorage):
    """Filesystem storage with no public URL. Files are streamed by permission-checked views."""

    @cached_property
    def base_location(self):
        return self._value_or_setting(self._location, settings.PRIVATE_MEDIA_ROOT)

    def url(self, name):
        raise NotImplementedError("Private files have no public URL.")


private_storage = PrivateStorage()


class ClearableFilesMixin:
    """Admin serializers: an optional file/image accepts JSON ``null`` to remove it."""

    def get_fields(self):
        from rest_framework import serializers

        fields = super().get_fields()
        for field in fields.values():
            if isinstance(field, serializers.FileField) and not field.required and not field.read_only:
                field.allow_null = True
        return fields
