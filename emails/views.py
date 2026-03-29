# emails/views.py
import json
import logging

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhook import (
    extract_attachments,
    extract_email_text,
    fetch_full_email,
    get_user_from_recipient,
    verify_resend_signature,
)

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def inbound(request):
    payload_bytes = request.body

    if not verify_resend_signature(payload_bytes, request.META):
        return HttpResponse('Invalid signature', status=400)

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return HttpResponse('Invalid JSON', status=400)

    if payload.get('type') != 'email.received':
        return HttpResponse('OK', status=200)

    data = payload.get('data', {})
    recipient = data.get('to', [''])[0] if isinstance(data.get('to'), list) else data.get('to', '')
    sender = data.get('from', '')
    source_email_id = data.get('email_id') or payload.get('id', '')

    user = get_user_from_recipient(recipient)
    if not user:
        return HttpResponse('OK', status=200)

    from .webhook import sender_is_allowed
    if not sender_is_allowed(user, sender):
        return HttpResponse('OK', status=200)

    # Fetch full email content from Resend API (webhooks only contain metadata)
    email_id = data.get('email_id')
    full_email = fetch_full_email(email_id) if email_id else {}

    # Body comes from full_email; attachments are fetched separately via Attachments API
    body = extract_email_text(payload, full_email=full_email)
    attachments = extract_attachments(payload)

    from .tasks import process_inbound_email
    from .models import ScanJob
    
    # Create the job ONCE before queuing the task. On retries, the task will use this job_id.
    try:
        job = ScanJob.objects.create(
            user=user,
            source=ScanJob.SOURCE_EMAIL,
            from_address=sender or '',
            status=ScanJob.STATUS_QUEUED,
        )
        process_inbound_email.delay(job.id, user.id, body, sender, source_email_id, attachments or None)
    except Exception as exc:
        logger.error("emails.inbound: queue failed | user_id=%s sender=%s error=%s", user.id, sender, exc, exc_info=True)
        # Still return 200 to avoid Resend retries, but log for manual recovery

    return HttpResponse('OK', status=200)


def send_email(to, subject, html):
    """Send a transactional email via Resend."""
    import resend
    from django.conf import settings

    resend.api_key = settings.RESEND_API_KEY
    try:
        resend.Emails.send({
            'from': settings.RESEND_FROM_EMAIL,
            'to': [to],
            'subject': subject,
            'html': html,
        })
    except Exception:
        pass