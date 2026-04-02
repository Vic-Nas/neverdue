"""
True end-to-end tests: text → Ollama LLM → validation → pipeline → writer → DB.

Every test asserts the *content* of saved Event rows matches the input prompt:
correct dates, times, year inference, timezone handling, recurrence, user
instructions, edge cases.  If a 7B model passes these, Claude rarely fails.

Run:  DEBUG=True python -m pytest tests/llm/test_e2e_pipeline.py -v
"""
import pytest
import requests
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
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

# GCal mock — returns unique IDs per call.
_call_counter = 0


def _fake_gcal_execute():
    global _call_counter
    _call_counter += 1
    return {'id': f'gcal_fake_{_call_counter}', 'htmlLink': 'https://calendar.google.com/fake'}


_fake_svc = MagicMock()
_fake_svc.events.return_value.insert.return_value.execute = _fake_gcal_execute

# User fixture has timezone='America/Toronto' (UTC-4 in summer, UTC-5 in winter).
# Prompts output local time → validation converts to UTC.
TORONTO = ZoneInfo('America/Toronto')

# ── helpers ──────────────────────────────────────────────────────────────

def _run_pipeline(user, text):
    """Run process_text and return (outcome, list[Event])."""
    from llm.pipeline import process_text
    from dashboard.models import Event

    user.scan_reset_date = tz.now().date()
    user.save(update_fields=['scan_reset_date'])

    outcome = process_text(user, text)
    events = list(Event.objects.filter(user=user).order_by('start'))
    return outcome, events


def _to_toronto(ev):
    """Return event start as a Toronto-local datetime for assertion."""
    return ev.start.astimezone(TORONTO)


# ═════════════════════════════════════════════════════════════════════════
# Tests
# ═════════════════════════════════════════════════════════════════════════

@skip_no_ollama
@pytest.mark.django_db
class TestExactDateTimeContent:
    """Events must have the correct date and time from the input text."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_explicit_date_and_time(self, _u, _api, _gc, user):
        """'June 20 2026 at 3pm' → event on 2026-06-20 at 15:00 Toronto."""
        _, events = _run_pipeline(user, 'My dentist appointment is June 20 2026 at 3pm.')

        assert len(events) >= 1
        ev = events[0]
        local = _to_toronto(ev)
        assert local.year == 2026
        assert local.month == 6
        assert local.day == 20
        assert local.hour == 15
        assert 'dentist' in ev.title.lower()

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_multiple_events_correct_dates(self, _u, _api, _gc, user):
        """Three distinct events → three correct dates and times."""
        _, events = _run_pipeline(
            user,
            'Math exam June 10 2026 at 9am. '
            'Physics lab June 12 2026 at 2pm. '
            'CS assignment due June 14 2026 at 5pm.',
        )

        assert len(events) >= 3
        local_events = sorted(events, key=lambda e: e.start)
        by_day = {_to_toronto(e).day: _to_toronto(e) for e in local_events}
        assert 10 in by_day, f"Missing June 10, got days: {list(by_day.keys())}"
        assert 12 in by_day, f"Missing June 12, got days: {list(by_day.keys())}"
        assert 14 in by_day, f"Missing June 14, got days: {list(by_day.keys())}"
        assert by_day[10].hour == 9
        assert by_day[12].hour == 14
        assert by_day[14].hour == 17

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_date_only_defaults_to_9am(self, _u, _api, _gc, user):
        """Prompt says only a date → should default start to 09:00."""
        _, events = _run_pipeline(user, 'Project report due July 5 2026.')

        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.month == 7
        assert local.day == 5
        assert local.hour == 9, f"Expected 09:00 default, got {local.hour}:00"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_end_after_start(self, _u, _api, _gc, user):
        """End must always be after start."""
        _, events = _run_pipeline(
            user, 'Team lunch June 18 2026 from 12pm to 1:30pm.',
        )

        assert len(events) >= 1
        ev = events[0]
        assert ev.end > ev.start
        local_end = ev.end.astimezone(TORONTO)
        assert local_end.hour == 13 and local_end.minute == 30


@skip_no_ollama
@pytest.mark.django_db
class TestYearInference:
    """When no year is given, the system must pick the correct one."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_year_future_month(self, _u, _api, _gc, user):
        """'November 15' with no year → current year (2026) since it's in the future."""
        _, events = _run_pipeline(user, 'Conference on November 15 at 10am.')

        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.year == 2026
        assert local.month == 11
        assert local.day == 15

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_year_past_month(self, _u, _api, _gc, user):
        """'January 10' with no year — today is April 2026, so Jan 2026 is past.
        Prompt says use current year, validation bumps past-year dates.
        Either 2026 (LLM may pick current year) or 2027 (next year) is acceptable
        — but not 2025 or earlier."""
        _, events = _run_pipeline(user, 'Dentist on January 10 at 2pm.')

        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.year >= 2026
        assert local.month == 1
        assert local.day == 10
        assert local.hour == 14

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_explicit_past_year_gets_fixed(self, _u, _api, _gc, user):
        """'March 5 2024 at noon' — LLM might return 2024, validation must fix it."""
        _, events = _run_pipeline(
            user, 'Renew passport — was due March 5 2024 at noon.',
        )

        if events:
            local = _to_toronto(events[0])
            assert local.year >= 2026, f"Past year {local.year} not corrected"


@skip_no_ollama
@pytest.mark.django_db
class TestTimezoneHandling:
    """User timezone must be respected in the saved events."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_toronto_tz_summer(self, _u, _api, _gc, user):
        """Summer: Toronto is UTC-4. '3pm Toronto' → 19:00 UTC."""
        _, events = _run_pipeline(user, 'Meeting July 1 2026 at 3pm.')

        assert len(events) >= 1
        ev = events[0]
        # Stored as UTC — check the UTC hour
        utc_hour = ev.start.astimezone(dt_timezone.utc).hour
        local_hour = _to_toronto(ev).hour
        assert local_hour == 15, f"Expected 15:00 local, got {local_hour}"
        assert utc_hour == 19, f"Expected 19:00 UTC (EDT offset), got {utc_hour}"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_toronto_tz_winter(self, _u, _api, _gc, user):
        """Winter: Toronto is UTC-5. '3pm Toronto' → 20:00 UTC."""
        _, events = _run_pipeline(user, 'Meeting January 15 2027 at 3pm.')

        assert len(events) >= 1
        ev = events[0]
        utc_hour = ev.start.astimezone(dt_timezone.utc).hour
        local_hour = _to_toronto(ev).hour
        assert local_hour == 15, f"Expected 15:00 local, got {local_hour}"
        assert utc_hour == 20, f"Expected 20:00 UTC (EST offset), got {utc_hour}"


@skip_no_ollama
@pytest.mark.django_db
class TestRecurrence:
    """Recurring events must have correct freq/until when specified."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_weekly_recurrence(self, _u, _api, _gc, user):
        _, events = _run_pipeline(
            user,
            'Weekly team standup every Monday at 10am from June 1 2026 to August 31 2026.',
        )
        assert len(events) >= 1
        ev = events[0]
        assert ev.recurrence_freq == 'WEEKLY', f"Expected WEEKLY, got {ev.recurrence_freq!r}"
        assert ev.recurrence_until is not None
        # Until should be in August 2026
        assert ev.recurrence_until.year == 2026
        assert ev.recurrence_until.month == 8

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_recurrence_on_single_event(self, _u, _api, _gc, user):
        """A one-time event should NOT have recurrence."""
        _, events = _run_pipeline(
            user, 'Company BBQ on July 4 2026 at 5pm.',
        )
        assert len(events) >= 1
        assert not events[0].recurrence_freq


@skip_no_ollama
@pytest.mark.django_db
class TestUserInstructions:
    """User-defined prompt injections via rules must override LLM inference."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_user_instruction_forces_category(self, _u, _api, _gc, user):
        """A prompt rule saying 'categorize everything as Work' should be in the LLM call."""
        from dashboard.models import Rule
        Rule.objects.create(
            user=user, rule_type=Rule.TYPE_PROMPT,
            prompt_text='Always set the category_hint to "Work" for any event.',
        )

        _, events = _run_pipeline(
            user, 'Sprint planning June 25 2026 at 11am.',
        )

        assert len(events) >= 1
        # The category should be Work (auto-created from hint)
        cat = events[0].category
        assert cat is not None
        assert cat.name.lower() == 'work', f"Expected 'Work' category, got '{cat.name}'"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_user_instruction_title_format(self, _u, _api, _gc, user):
        """User instruction 'prefix all titles with [UNI]' should appear in title."""
        from dashboard.models import Rule
        Rule.objects.create(
            user=user, rule_type=Rule.TYPE_PROMPT,
            prompt_text='Prefix every event title with "[UNI]".',
        )

        _, events = _run_pipeline(
            user, 'Linear algebra exam June 20 2026 at 9am.',
        )

        assert len(events) >= 1
        assert events[0].title.startswith('[UNI]'), f"Title missing prefix: {events[0].title!r}"


@skip_no_ollama
@pytest.mark.django_db
class TestEdgeCasesAndContent:
    """Tricky inputs that LLMs commonly get wrong."""

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_midnight_is_correct_day(self, _u, _api, _gc, user):
        """'due June 14 at midnight' — midnight is genuinely ambiguous.
        LLMs commonly return 23:00 or 23:59 on the 14th, or 00:00 on the 15th.
        Accept anything on June 14 (any hour) or June 15 at 00:00."""
        _, events = _run_pipeline(
            user, 'Assignment due June 14 2026 at midnight.',
        )

        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.month == 6
        valid = (
            (local.day == 14) or
            (local.day == 15 and local.hour == 0)
        )
        assert valid, f"Expected June 14 or June 15 00:00, got June {local.day} {local.hour}:00"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_no_events_in_chitchat(self, _u, _api, _gc, user):
        """Casual text with no events → empty."""
        _, events = _run_pipeline(
            user,
            'Hey! Hope you had a great weekend. Let me know if you want to grab coffee sometime.',
        )
        assert len(events) == 0

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_am_pm_not_confused(self, _u, _api, _gc, user):
        """'8:30am' must not become 20:30."""
        _, events = _run_pipeline(
            user, 'Morning class August 10 2026 at 8:30am.',
        )

        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.hour == 8, f"Expected 8 (AM), got {local.hour}"
        assert local.minute == 30

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_title_captures_event_nature(self, _u, _api, _gc, user):
        """Title should reflect the event, not be generic like 'Event 1'."""
        _, events = _run_pipeline(
            user, 'Board of directors meeting September 3 2026 at 4pm in the main conference room.',
        )

        assert len(events) >= 1
        title = events[0].title.lower()
        # Must contain at least one meaningful word from the input
        assert any(w in title for w in ('board', 'directors', 'meeting')), \
            f"Title too generic: {events[0].title!r}"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_description_has_context(self, _u, _api, _gc, user):
        """Description should include relevant context from the source text."""
        _, events = _run_pipeline(
            user,
            'Dentist appointment June 22 2026 at 10am. '
            'Address: 123 Main St, Suite 400. Bring insurance card.',
        )

        assert len(events) >= 1
        desc = events[0].description.lower()
        # At least some useful context should appear in description
        assert any(w in desc for w in ('123 main', 'suite 400', 'insurance')), \
            f"Description missing context: {events[0].description!r}"

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_french_content_correct(self, _u, _api, _gc, user):
        """French text with user language=French must produce correct date."""
        from accounts.models import User
        User.objects.filter(pk=user.pk).update(language='French')
        user.refresh_from_db()

        _, events = _run_pipeline(
            user,
            "Examen de mathématiques le 15 juin 2026 à 14h dans la salle A-200.",
        )

        assert len(events) >= 1
        local = _to_toronto(events[0])
        assert local.month == 6
        assert local.day == 15
        assert local.hour == 14

    @patch('dashboard.gcal.client._service', return_value=_fake_svc)
    @patch('llm.extractor.text.call_api', side_effect=ollama_call_api)
    @patch('llm.pipeline.saving._fire_usage')
    def test_scan_limit_blocks_without_hitting_llm(self, _u, mock_api, _gc, user):
        """Free user at 30 scans → failed with scan_limit, LLM never called."""
        from dashboard.models import Event

        user.scan_reset_date = tz.now().date()
        user.monthly_scans = 30
        user.save(update_fields=['scan_reset_date', 'monthly_scans'])

        from llm.pipeline import process_text
        outcome = process_text(user, 'Meeting tomorrow at noon.')

        assert outcome.status == 'failed'
        assert outcome.failure_reason == 'scan_limit'
        assert Event.objects.filter(user=user).count() == 0
        mock_api.assert_not_called()
