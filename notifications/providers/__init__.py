"""Provider-independent SMS layer. Select with SMS_PROVIDER."""
import json
import re
import logging
from dataclasses import dataclass
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger("noribox")


class SMSError(Exception):
    pass


@dataclass
class SendResult:
    message_id: str = ""


class BaseSMSProvider:
    name = "base"

    def send(self, phone: str, message: str, template=None, context=None) -> SendResult:  # pragma: no cover
        raise NotImplementedError


class ConsoleProvider(BaseSMSProvider):
    """Development provider: logs messages instead of sending."""

    name = "console"

    def send(self, phone, message, template=None, context=None):
        # Only show bodies (which may contain OTP codes) in DEBUG; mask digits otherwise.
        shown = message if settings.DEBUG else re.sub(r"\d", "*", message)
        logger.info("[SMS to %s] %s", phone, shown)
        return SendResult("console")


class DummyProvider(BaseSMSProvider):
    """Test provider: records messages in memory; set ``fail`` to simulate errors."""

    name = "dummy"
    outbox: list = []
    fail = False

    def send(self, phone, message, template=None, context=None):
        if DummyProvider.fail:
            raise SMSError("simulated failure")
        DummyProvider.outbox.append({"phone": phone, "message": message})
        return SendResult(f"dummy-{len(DummyProvider.outbox)}")


class KavenegarProvider(BaseSMSProvider):
    """Kavenegar REST provider (https://kavenegar.com/rest.html)."""

    name = "kavenegar"

    def send(self, phone, message, template=None, context=None):
        key = settings.SMS_API_KEY
        if not key:
            raise SMSError("SMS_API_KEY is not configured")
        if template and template.provider_template and context and "code" in context:
            url = f"https://api.kavenegar.com/v1/{quote(key)}/verify/lookup.json"
            params = {"receptor": phone, "token": context["code"], "template": template.provider_template}
        else:
            url = f"https://api.kavenegar.com/v1/{quote(key)}/sms/send.json"
            params = {"receptor": phone, "message": message}
            if settings.SMS_SENDER:
                params["sender"] = settings.SMS_SENDER
        req = Request(url, data=urlencode(params).encode(), method="POST")
        try:
            with urlopen(req, timeout=settings.SMS_TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            raise SMSError(f"Provider request failed: {exc.__class__.__name__}") from exc
        if payload.get("return", {}).get("status") != 200:
            raise SMSError(f"Provider error: {payload.get('return')}")
        entries = payload.get("entries") or [{}]
        return SendResult(str(entries[0].get("messageid", "")))


PROVIDERS = {"console": ConsoleProvider, "dummy": DummyProvider, "kavenegar": KavenegarProvider}


def get_provider() -> BaseSMSProvider:
    name = settings.SMS_PROVIDER
    cls = PROVIDERS.get(name) or import_string(name)
    return cls()
