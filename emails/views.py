# emails/views.py
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhook import get_user_from_recipient, sender_is_allowed, verify_resend_signature

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def inbound(request):
    """
    Resend inbound-email webhook.

    This handler is intentionally thin — metadata only, no HTTP calls.
    Full email body and attachments are fetched inside process_inbound_email
    (the Celery task), so any network failure is retried automatically.

    Flow:
      1. Verify Svix signature
      2. Parse metadata: email_id, sender, recipient
      3. Resolve user from recipient address
      4. Check sender filter
      5. Create ScanJob (queued) with task_args stored immediately
      6. Queue process_inbound_email — returns 200 instantly
    """
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
    email_id = data.get('email_id', '')
    message_id = email_id or payload.get('id', '')

    user = get_user_from_recipient(recipient)
    if not user:
        return HttpResponse('OK', status=200)

    if not sender_is_allowed(user, sender):
        if settings.DEBUG:
            logger.debug(
                "emails.inbound: sender blocked | user_id=%s sender=%s",
                user.id, sender,
            )
        return HttpResponse('OK', status=200)

    from .tasks import process_inbound_email
    from .models import ScanJob

    try:
        job = ScanJob.objects.create(
            user=user,
            source=ScanJob.SOURCE_EMAIL,
            from_address=sender or '',
            status=ScanJob.STATUS_QUEUED,
            task_args=json.dumps({
                'user_id': user.id,
                'email_id': email_id,
                'sender': sender,
                'message_id': message_id,
            }),
        )
        process_inbound_email.delay(job.id, user.id, email_id, sender, message_id)
        if settings.DEBUG:
            logger.debug(
                "emails.inbound: job queued | job_id=%s user_id=%s email_id=%s sender=%s",
                job.id, user.id, email_id, sender,
            )
    except Exception as exc:
        logger.error(
            "emails.inbound: queue failed | user_id=%s sender=%s error=%s",
            user.id, sender, exc, exc_info=True,
        )
        # Return 200 so Resend does not retry — failure is logged for manual recovery.

    return HttpResponse('OK', status=200)


def send_email(to, subject, html):
    """Send a transactional email via Resend."""
    import resend
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
