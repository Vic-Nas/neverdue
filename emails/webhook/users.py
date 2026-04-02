# emails/webhook/users.py
import logging

logger = logging.getLogger(__name__)

RESERVED_USERNAMES = {
    'admin', 'status', 'support', 'help', 'billing', 'api', 'www',
    'noreply', 'no-reply', 'mail', 'email', 'info', 'hello', 'contact',
    'abuse', 'security', 'postmaster', 'hostmaster', 'webmaster',
}


def get_user_from_recipient(recipient: str):
    from accounts.models import User
    try:
        local, _ = recipient.lower().split('@', 1)
        username = local.split('.')[0] if '.' in local else local
        if username in RESERVED_USERNAMES:
            return None
        return User.objects.get(username=username)
    except User.DoesNotExist:
        logger.error("emails.get_user_from_recipient: user not found | recipient=%s", recipient)
        return None
    except (IndexError, ValueError) as exc:
        logger.error("emails.get_user_from_recipient: parse failed | recipient=%s error=%s", recipient, exc)
        return None
