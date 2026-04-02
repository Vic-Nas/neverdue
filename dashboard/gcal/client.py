import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from accounts.utils import get_valid_token

logger = logging.getLogger(__name__)


def _service(user):
    """Build an authenticated Google Calendar API service."""
    token = get_valid_token(user)
    creds = Credentials(token=token)
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)
