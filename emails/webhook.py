# emails/webhook.py
import hashlib
import hmac
from django.conf import settings
from accounts.models import User


def verify_mailgun_signature(token: str, timestamp: str, signature: str) -> bool:
    """
    Verify that the webhook request genuinely came from Mailgun.
    """
    value = f'{timestamp}{token}'.encode('utf-8')
    expected = hmac.new(
        settings.MAILGUN_API_KEY.encode('utf-8'),
        value,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def get_user_from_recipient(recipient: str) -> User | None:
    """
    Extract username from recipient address and look up the user.
    e.g. "johndoe@neverdue.com" -> User with username "johndoe"
    """
    try:
        username = recipient.split('@')[0].lower()
        return User.objects.get(username=username)
    except User.DoesNotExist:
        return None