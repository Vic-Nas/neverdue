# emails/tasks/helpers.py
import logging
from fnmatch import fnmatch

from django.db.models import F
from django.utils import timezone
from procrastinate.contrib.django import app
from procrastinate import RetryStrategy

from emails.models import ScanJob

logger = logging.getLogger(__name__)

_transient_retry = RetryStrategy(max_attempts=5, linear_wait=60)


def _check_sender_rules(user, sender: str) -> tuple[bool, str]:
    from dashboard.models import Rule
    sender_rules = Rule.objects.filter(
        user=user, rule_type=Rule.TYPE_SENDER,
        action__in=[Rule.ACTION_ALLOW, Rule.ACTION_BLOCK],
    )
    if not sender_rules.exists():
        return False, ''

    sender_lower = sender.lower()

    def matches(pattern: str) -> bool:
        p = pattern.lower()
        if p.startswith('@'):
            return sender_lower.endswith(p)
        return sender_lower == p or fnmatch(sender_lower, p)

    allow_patterns = [r.pattern for r in sender_rules if r.action == Rule.ACTION_ALLOW]
    block_patterns = [r.pattern for r in sender_rules if r.action == Rule.ACTION_BLOCK]

    if any(matches(p) for p in block_patterns):
        return True, f'Discarded — sender blocked by rule: {sender}'
    if allow_patterns and not any(matches(p) for p in allow_patterns):
        return True, f'Discarded — sender not in allow list: {sender}'
    return False, ''


def _load_user(user_id: int, job_id: int):
    from accounts.models import User
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("emails: user not found | user_id=%s job_id=%s", user_id, job_id)
        ScanJob.objects.filter(pk=job_id).update(
            status=ScanJob.STATUS_FAILED,
            failure_reason=ScanJob.REASON_INTERNAL_ERROR,
            notes='User account not found.',
            updated_at=timezone.now(),
        )
        return None


def _apply_outcome(job_id: int, outcome) -> None:
    updates = dict(
        status=outcome.status,
        failure_reason=outcome.failure_reason[:30] if outcome.failure_reason else '',
        notes=outcome.notes[:255] if outcome.notes else '',
        updated_at=timezone.now(),
    )
    # Purge raw inputs once processing succeeds — no reason to keep
    # images / text in the DB after extraction.  Failed jobs keep them
    # so retry can re-dispatch.
    if outcome.status != ScanJob.STATUS_FAILED:
        updates.update(file_b64='', upload_text='', upload_context='')
    ScanJob.objects.filter(pk=job_id).update(**updates)


@app.task(retry=_transient_retry)
def track_llm_usage(user_id: int, input_tokens: int, output_tokens: int) -> None:
    from accounts.models import User
    try:
        User.objects.filter(pk=user_id).update(
            monthly_input_tokens=F('monthly_input_tokens') + input_tokens,
            monthly_output_tokens=F('monthly_output_tokens') + output_tokens,
        )
    except Exception as exc:
        logger.error("emails.track_llm_usage: update failed | user_id=%s error=%s", user_id, exc)
        raise
