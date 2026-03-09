# emails/views.py
import resend
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from llm.pipeline import process_text
from .webhook import verify_mailgun_signature, get_user_from_recipient

resend.api_key = settings.RESEND_API_KEY


@csrf_exempt
@require_POST
def inbound(request):
    """
    Mailgun inbound webhook — receives forwarded emails and runs the pipeline.
    """
    token = request.POST.get('token', '')
    timestamp = request.POST.get('timestamp', '')
    signature = request.POST.get('signature', '')

    if not verify_mailgun_signature(token, timestamp, signature):
        return HttpResponse(status=403)

    recipient = request.POST.get('recipient', '')
    sender = request.POST.get('from', '')
    message_id = request.POST.get('Message-Id', '')
    body = request.POST.get('stripped-text', '') or request.POST.get('body-plain', '')

    if not recipient or not body:
        return HttpResponse(status=200)

    user = get_user_from_recipient(recipient)
    if not user:
        return HttpResponse(status=200)

    # Run async via Celery so webhook returns fast
    from .tasks import process_inbound_email
    process_inbound_email.delay(
        user_id=user.pk,
        body=body,
        sender=sender,
        message_id=message_id,
    )

    return HttpResponse(status=200)


def send_email(to: str, subject: str, html: str) -> bool:
    """
    Send a transactional email via Resend.
    Returns True on success.
    """
    try:
        resend.Emails.send({
            'from': settings.RESEND_FROM_EMAIL,
            'to': to,
            'subject': subject,
            'html': html,
        })
        return True
    except Exception:
        return False