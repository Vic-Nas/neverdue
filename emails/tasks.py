# emails/tasks.py
from celery import shared_task
from django.utils import timezone


@shared_task
def process_inbound_email(user_id: int, body: str, sender: str, message_id: str, attachments: list = None):
        print(f"TASK START user={user_id} message_id={message_id} body_len={len(body) if body else 0}")
    """
    Process a single inbound email through the LLM pipeline.
    Processes body text and each attachment as separate scans.
    Runs asynchronously so the Resend webhook returns immediately.
    attachments: list of [base64_string, media_type] pairs
    """
    from accounts.models import User
    from llm.pipeline import process_text, process_file
    from dashboard.models import Event
    import base64

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    # Skip entire email if already processed (dedup by message_id)
    if message_id and Event.objects.filter(user=user, source_email_id=message_id).exists():
        return

    # Process body text
    if body:
        process_text(user, body, sender=sender, source_email_id=message_id)

    # Process each attachment — counts as a scan each
    # Attachments are only processed for Pro users (process_file checks this)
    if attachments:
        for b64_content, media_type in attachments:
            try:
                file_bytes = base64.b64decode(b64_content)
                process_file(user, file_bytes, media_type)
            except Exception:
                continue


@shared_task
def reset_monthly_scans():
    """
    Reset monthly scan counters for all users at the start of each month.
    Scheduled via Celery Beat on the 1st of each month.
    """
    from accounts.models import User

    today = timezone.now().date()
    User.objects.filter(
        scan_reset_date__month__lt=today.month
    ).update(monthly_scans=0, scan_reset_date=today)