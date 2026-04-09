# billing/tests/test_webhook_http.py
"""
Webhook HTTP layer — signature verification, routing, error handling.
Uses Django test client; does NOT call Stripe for event objects (mocked payload).
"""
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse


def _sign(payload_bytes, secret):
    ts = str(int(time.time()))
    msg = f"{ts}.{payload_bytes.decode()}"
    sig = hmac.HMAC(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _post_webhook(client, payload, secret=None):
    secret = secret or settings.STRIPE_WEBHOOK_SECRET
    payload_bytes = json.dumps(payload).encode()
    sig = _sign(payload_bytes, secret)
    return client.post(
        reverse("billing:webhook"),
        data=payload_bytes,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=sig,
    )


class WebhookSignatureTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_missing_signature_returns_400(self):
        payload = json.dumps({"type": "ping", "data": {"object": {}}}).encode()
        r = self.client.post(
            reverse("billing:webhook"),
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_bad_signature_returns_400(self):
        payload = json.dumps({"type": "ping", "data": {"object": {}}}).encode()
        r = self.client.post(
            reverse("billing:webhook"),
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=badhash",
        )
        self.assertEqual(r.status_code, 400)

    def test_unknown_event_type_returns_200(self):
        """Unhandled event types should be gracefully ignored."""
        payload = {"type": "totally.unknown.event", "data": {"object": {}}}
        r = _post_webhook(self.client, payload)
        self.assertEqual(r.status_code, 200)

    def test_valid_signature_but_handler_gets_no_customer_returns_200(self):
        """Handler survives missing customer id without crashing."""
        payload = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_test",
                    "customer": "cus_nonexistent",
                    "status": "active",
                    "items": {"data": [{"current_period_end": 9999999999}]},
                }
            },
        }
        r = _post_webhook(self.client, payload)
        self.assertEqual(r.status_code, 200)
