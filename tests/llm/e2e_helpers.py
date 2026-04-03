"""Shared helpers for e2e pipeline tests."""
import pytest
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from django.utils import timezone as tz
from tests.llm.ollama_helpers import ollama_available, ollama_call_api  # noqa: F401

skip_no_ollama = pytest.mark.skipif(
    not ollama_available(), reason='Ollama not running',
)

_call_counter = 0


def _fake_gcal_execute():
    global _call_counter
    _call_counter += 1
    return {'id': f'gcal_fake_{_call_counter}', 'htmlLink': 'https://calendar.google.com/fake'}


_fake_svc = MagicMock()
_fake_svc.events.return_value.insert.return_value.execute = _fake_gcal_execute

TORONTO = ZoneInfo('America/Toronto')


def _run_pipeline(user, text):
    from llm.pipeline import process_text
    from dashboard.models import Event
    user.scan_reset_date = tz.now().date()
    user.save(update_fields=['scan_reset_date'])
    outcome = process_text(user, text)
    events = list(Event.objects.filter(user=user).order_by('start'))
    return outcome, events


def _to_toronto(ev):
    return ev.start.astimezone(TORONTO)
