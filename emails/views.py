# emails/views.py
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhook import get_user_from_recipient, verify_resend_signature

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def inbound(request):
    """
    Resend inbound-email webhook.

    Intentionally thin — metadata only, no HTTP calls.
    Full email body and attachments are fetched inside process_inbound_email,
    so any network failure is retried automatically by Procrastinate.

    Flow:
      1. Verify Svix signature
      2. Parse metadata: email_id, sender, recipient
      3. Resolve user from recipient address
      4. Create ScanJob (queued) with typed retry fields
      5. Defer process_inbound_email — returns 200 instantly

    Sender allow/block rules are evaluated inside the task, not here,
    so discarded-by-rule jobs are visible in the user's queue.
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

    from .tasks import process_inbound_email
    from .models import ScanJob

    try:
        job = ScanJob.objects.create(
            user=user,
            source=ScanJob.SOURCE_EMAIL,
            from_address=sender or '',
            status=ScanJob.STATUS_QUEUED,
            email_id=email_id,
            message_id=message_id,
        )
        process_inbound_email.defer(
            job_id=job.id,
            user_id=user.id,
            email_id=email_id,
            sender=sender,
            message_id=message_id,
        )
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