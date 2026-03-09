# emails/tasks.py
from celery import shared_task
from django.utils import timezone


@shared_task
def process_inbound_email(user_id: int, body: str, sender: str, message_id: str):
    """
    Process a single inbound email through the LLM pipeline.
    Runs asynchronously so the Mailgun webhook returns immediately.
    """
    from accounts.models import User
    from llm.pipeline import process_text
    from dashboard.models import Event

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    # Skip if already processed this message
    if message_id and Event.objects.filter(
        user=user,
        source_email_id=message_id
    ).exists():
        return

    process_text(user, body, sender=sender, source_email_id=message_id)


@shared_task
def reset_monthly_scans():
    """
    Reset monthly scan counters for all users at the start of each month.
    Schedule this to run on the 1st of each month via Celery Beat.
    """
    from accounts.models import User
    from django.utils import timezone

    today = timezone.now().date()
    User.objects.filter(
        scan_reset_date__month__lt=today.month
    ).update(monthly_scans=0, scan_reset_date=today)