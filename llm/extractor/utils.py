# llm/extractor/utils.py
import re
import zoneinfo
from datetime import datetime, timezone as dt_timezone


_JUNK_STEMS = frozenset({
    'screenshot', 'screen shot', 'image', 'img', 'photo', 'pic', 'picture',
    'scan', 'scanned', 'document', 'doc', 'file', 'attachment', 'attach',
    'untitled', 'unnamed', 'noname', 'new', 'copy', 'temp', 'tmp',
    'download', 'export', 'output',
})

_RE_UUID = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)
_RE_TIMESTAMP = re.compile(r'^[\d_\-T:.]+$')


def is_informative_filename(filename: str) -> bool:
    if not filename:
        return False
    stem = filename.rsplit('.', 1)[0].strip()
    if len(stem) <= 4:
        return False
    if _RE_UUID.match(stem):
        return False
    if _RE_TIMESTAMP.match(stem):
        return False
    words = re.split(r'[\s_\-.()\[\]]+', stem.lower())
    non_numeric_words = [w for w in words if w and not w.isdigit()]
    if not non_numeric_words:
        return False
    return not all(w in _JUNK_STEMS for w in non_numeric_words)


def get_tz(tz_name: str) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        return dt_timezone.utc


def today_in_tz(tz) -> str:
    return datetime.now(tz=tz).date().isoformat()
