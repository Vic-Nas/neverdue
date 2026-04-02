"""
True end-to-end tests: text → Ollama LLM → validation → pipeline → writer → DB.

These call the real process_text() entry point (with Ollama standing in for
Claude) and assert that Event rows actually land in the database.

Run:  DEBUG=True python -m pytest tests/llm/test_e2e_pipeline.py -v
"""
import pytest
import requests
from unittest.mock import patch, MagicMock
from django.utils import timezone as tz

OLLAMA_URL = 'http://localhost:11434'
OLLAMA_MODEL = 'qwen2.5:7b'


def ollama_available():
    try:
        return requests.get(f'{OLLAMA_URL}/api/tags', timeout=2).status_code == 200
    except Exception:
        return False


def ollama_call_api(**kwargs):
    system = kwargs.get('system', '')
    messages = kwargs.get('messages', [])
    user_text = messages[0]['content'] if messages else ''
    prompt = f"{system}\n\n{user_text}" if system else user_text

    resp = requests.post(f'{OLLAMA_URL}/api/generate', json={
        'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False,
        'options': {'temperature': 0, 'num_predict': 2000},
    }, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=data['response'])]
    mock_msg.usage = MagicMock(
        input_tokens=data.get('prompt_eval_count', 0),
        output_tokens=data.get('eval_count', 0),
    )
    return mock_msg


skip_no_ollama = pytest.mark.skipif(
    not ollama_available(), reason='Ollama not running',
)

# Shared mock so writer never touches real Google Calendar.
_call_counter = 0


def _fake_gcal_execute():
    global _call_counter
    _call_counter += 1
    return {'id': f'gcal_fake_{_call_counter}', 'htmlLink': 'https://calendar.google.com/fake'}


_fake_svc = MagicMock()
_fake_svc.events.return_value.insert.return_value.execute = _fake_gcal_execute


@skip_no_ollama
@pytest.mark.django_db
class TestEndToEndPipeline:
    """Send real text through process_text → Ollama → writer → assert DB rows."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_single_event_saved(self, mock_usage, mock_api, mock_gcal, user):
        from llm.pipeline import process_text
        from dashboard.models import Event

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        outcome = process_text(
            user, 'My dentist appointment is June 20 2026 at 3pm.',
        )

        assert outcome.status in ('done', 'needs_review')
        assert len(outcome.created) >= 1

        # Real DB check
        events = Event.objects.filter(user=user)
        assert events.count() >= 1
        ev = events.first()
        assert ev.title  # non-empty title
        assert ev.start is not None
        assert ev.category is not None  # Uncategorized auto-created

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_multiple_events_created(self, mock_usage, mock_api, mock_gcal, user):
        from llm.pipeline import process_text
        from dashboard.models import Event

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        outcome = process_text(
            user,
            'Math exam June 10 2026 at 9am in hall A. '
            'Physics lab June 12 2026 at 2pm in room 204. '
            'CS project due June 14 2026 at midnight.',
        )

        assert outcome.status in ('done', 'needs_review')
        assert len(outcome.created) >= 2
        assert Event.objects.filter(user=user).count() >= 2

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_events_means_empty(self, mock_usage, mock_api, mock_gcal, user):
        from llm.pipeline import process_text
        from dashboard.models import Event

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        outcome = process_text(user, 'Hey, just wanted to say hi. Nothing planned.')

        assert outcome.status == 'done'
        assert len(outcome.created) == 0
        assert Event.objects.filter(user=user).count() == 0

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_event_has_gcal_link(self, mock_usage, mock_api, mock_gcal, user):
        """Active events should have a google_event_id and gcal_link."""
        from llm.pipeline import process_text
        from dashboard.models import Event

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        outcome = process_text(user, 'Team standup July 1 2026 at 10am.')

        active = Event.objects.filter(user=user, status='active')
        if active.exists():
            ev = active.first()
            assert ev.google_event_id.startswith('gcal_fake_')
            assert 'calendar.google.com' in ev.gcal_link

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_category_auto_created(self, mock_usage, mock_api, mock_gcal, user):
        """Pipeline should auto-create category from LLM hint or fall back to Uncategorized."""
        from llm.pipeline import process_text
        from dashboard.models import Category

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        process_text(user, 'Final exam December 15 2026 at 2pm in room 300.')

        # At least Uncategorized or a hint-based category must exist
        assert Category.objects.filter(user=user).count() >= 1

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_scan_job_lifecycle(self, mock_usage, mock_api, mock_gcal, user):
        """ScanJob should tie to created events."""
        from llm.pipeline import process_text
        from dashboard.models import Event
        from emails.models import ScanJob

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        job = ScanJob.objects.create(user=user, source='upload', status='processing')

        outcome = process_text(
            user, 'Doctor visit August 5 2026 at 11am.', scan_job=job,
        )

        assert len(outcome.created) >= 1
        ev = Event.objects.filter(user=user).first()
        assert ev.scan_job_id == job.pk

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_recurring_event_saved(self, mock_usage, mock_api, mock_gcal, user):
        from llm.pipeline import process_text
        from dashboard.models import Event

        user.scan_reset_date = tz.now().date()
        user.save(update_fields=['scan_reset_date'])

        outcome = process_text(
            user,
            'Weekly standup every Monday at 10am starting June 1 2026 until August 31 2026.',
        )

        assert len(outcome.created) >= 1
        events = Event.objects.filter(user=user)
        assert events.count() >= 1
        # Check if recurrence was captured (not guaranteed with 7B model)
        ev = events.first()
        if ev.recurrence_freq:
            assert ev.recurrence_freq == 'WEEKLY'

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_scan_limit_blocks_pipeline(self, mock_usage, mock_api, mock_gcal, user):
        """Free user at 30 scans should get 'failed' with scan_limit reason."""
        from llm.pipeline import process_text
        from dashboard.models import Event
        from accounts.models import User

        user.scan_reset_date = tz.now().date()
        user.monthly_scans = 30
        user.save(update_fields=['scan_reset_date', 'monthly_scans'])

        outcome = process_text(user, 'Meeting tomorrow at noon.')

        assert outcome.status == 'failed'
        assert outcome.failure_reason == 'scan_limit'
        assert Event.objects.filter(user=user).count() == 0
        mock_api.assert_not_called()  # LLM should never be hit
