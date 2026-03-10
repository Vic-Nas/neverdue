# emails/views.py
import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhook import extract_email_text, get_user_from_recipient, verify_resend_signature


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

    # Only handle inbound email events
    if payload.get('type') != 'email.received':
        return HttpResponse('OK', status=200)

    data = payload.get('data', {})
    recipient = data.get('to', [''])[0] if isinstance(data.get('to'), list) else data.get('to', '')
    sender = data.get('from', '')
    source_email_id = data.get('email_id') or payload.get('id', '')

    user = get_user_from_recipient(recipient)
    if not user:
        return HttpResponse('OK', status=200)

    text = extract_email_text(payload)
    if not text:
        return HttpResponse('OK', status=200)

    from .tasks import process_inbound_email
    process_inbound_email.delay(user.id, text, sender, source_email_id)

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