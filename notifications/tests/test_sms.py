from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from core.testutils import reset_sms
from notifications.models import SMSLog, SMSTemplate
from notifications.services import process_retries, send_sms


class SMSTests(TestCase):
    def setUp(self):
        self.sms = reset_sms()

    def test_template_override_and_disable(self):
        SMSTemplate.objects.create(code="payment_approved", body="OK {order_number}")
        send_sms("payment_approved", "09120000001", {"order_number": "NB1"})
        self.assertEqual(self.sms.outbox[-1]["message"], "OK NB1")
        SMSTemplate.objects.filter(code="payment_approved").update(is_active=False)
        log = send_sms("payment_approved", "09120000001", {"order_number": "NB1"})
        self.assertEqual(log.status, SMSLog.Status.SKIPPED)

    @override_settings(SMS_ENABLED=False)
    def test_globally_disabled(self):
        self.assertEqual(send_sms("payment_approved", "09120000001", {}).status, SMSLog.Status.SKIPPED)
        self.assertEqual(self.sms.outbox, [])

    @override_settings(SMS_MAX_ATTEMPTS=2)
    def test_retry_then_give_up(self):
        self.sms.fail = True
        log = send_sms("payment_approved", "09120000001", {"order_number": "NB1"})
        self.assertEqual((log.status, log.attempts), (SMSLog.Status.FAILED, 1))
        SMSLog.objects.update(next_retry_at=timezone.now() - timedelta(seconds=1))
        process_retries()
        log.refresh_from_db()
        self.assertEqual((log.status, log.attempts), (SMSLog.Status.GAVE_UP, 2))

    def test_retry_succeeds(self):
        self.sms.fail = True
        log = send_sms("payment_approved", "09120000001", {"order_number": "NB1"})
        self.sms.fail = False
        SMSLog.objects.update(next_retry_at=timezone.now())
        self.assertEqual(process_retries(), 1)
        log.refresh_from_db()
        self.assertEqual(log.status, SMSLog.Status.SENT)
