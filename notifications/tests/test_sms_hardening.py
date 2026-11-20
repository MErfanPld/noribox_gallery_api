from django.test import TestCase, override_settings

from core.testutils import reset_sms
from notifications.models import SMSLog
from notifications.providers import DummyProvider
from notifications.services import process_retries, send_sms


class OTPPrivacyTests(TestCase):
    def test_otp_never_written_to_database_even_during_send(self):
        sms = reset_sms()
        seen = []
        original = DummyProvider.send

        def spy(self, phone, message, template=None, context=None):
            seen.extend(SMSLog.objects.values_list("message", flat=True))
            seen.extend(str(c) for c in SMSLog.objects.values_list("context", flat=True))
            return original(self, phone, message, template, context)

        DummyProvider.send = spy
        try:
            send_sms("otp", "09120001111", {"code": "482913"}, sensitive=True)
        finally:
            DummyProvider.send = original
        self.assertIn("482913", sms.outbox[-1]["message"])
        self.assertTrue(seen)
        self.assertFalse(any("482913" in s for s in seen))
        self.assertFalse(SMSLog.objects.filter(message__contains="482913").exists())

    def test_failed_otp_not_retried_or_stored(self):
        sms = reset_sms()
        sms.fail = True
        log = send_sms("otp", "09120001111", {"code": "111222"}, sensitive=True)
        self.assertEqual(log.status, SMSLog.Status.GAVE_UP)
        self.assertEqual(process_retries(), 0)
        self.assertFalse(SMSLog.objects.filter(message__contains="111222").exists())


@override_settings(SMS_ASYNC_NOTIFICATIONS=True)
class AsyncNotificationTests(TestCase):
    def test_notifications_are_queued_then_sent_by_job(self):
        sms = reset_sms()
        log = send_sms("payment_approved", "09120002222", {"order_number": "NB1"})
        self.assertEqual(log.status, SMSLog.Status.QUEUED)
        self.assertEqual(sms.outbox, [])
        self.assertEqual(process_retries(), 1)
        log.refresh_from_db()
        self.assertEqual(log.status, SMSLog.Status.SENT)
        self.assertEqual(process_retries(), 0)  # not sent twice

    def test_otp_is_still_synchronous(self):
        sms = reset_sms()
        send_sms("otp", "09120003333", {"code": "999000"}, sensitive=True)
        self.assertEqual(len(sms.outbox), 1)
