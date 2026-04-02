import pytest
from llm.extractor.utils import is_informative_filename, get_tz, today_in_tz
import zoneinfo


class TestIsInformativeFilename:
    def test_informative(self):
        assert is_informative_filename('calendar.jpeg') is True
        assert is_informative_filename('exam_schedule.pdf') is True

    def test_junk(self):
        assert is_informative_filename('screenshot.png') is False
        assert is_informative_filename('image.jpg') is False
        assert is_informative_filename('untitled.pdf') is False

    def test_uuid(self):
        assert is_informative_filename('a1b2c3d4-e5f6-7890-abcd-ef1234567890.png') is False

    def test_timestamp(self):
        assert is_informative_filename('2026-04-01T12:00:00.png') is False

    def test_empty(self):
        assert is_informative_filename('') is False

    def test_short(self):
        assert is_informative_filename('a.png') is False


class TestGetTz:
    def test_valid(self):
        tz = get_tz('America/Toronto')
        assert isinstance(tz, zoneinfo.ZoneInfo)

    def test_invalid(self):
        tz = get_tz('Not/Real')
        # falls back to UTC
        assert tz is not None


class TestTodayInTz:
    def test_returns_iso(self):
        tz = get_tz('UTC')
        result = today_in_tz(tz)
        assert len(result) == 10  # YYYY-MM-DD
