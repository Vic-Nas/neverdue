# llm/extractor/client.py
import anthropic
from django.conf import settings

client = anthropic.Anthropic(api_key=settings.LLM_API_KEY)

_RETRYABLE_STATUS_CODES = {429, 529, 500, 502, 503}
_OVERLOAD_STATUS_CODES = {429, 529}  # capacity errors — need longer retry delays


class LLMAPIError(Exception):
    """Raised when the LLM provider returns an API-level error (auth, quota, server).

    Attributes:
        retryable: True when the error is transient and safe to re-queue.
        overloaded: True specifically for 529/429 — needs much longer waits than
                    a generic 500. The retry strategy uses this to pick the right
                    delay schedule.
    """

    def __init__(self, message: str, retryable: bool = False, overloaded: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.overloaded = overloaded


def call_api(**kwargs):
    try:
        return client.messages.create(**kwargs)
    except anthropic.APIStatusError as exc:
        retryable = exc.status_code in _RETRYABLE_STATUS_CODES
        overloaded = exc.status_code in _OVERLOAD_STATUS_CODES
        raise LLMAPIError(
            f"Anthropic API error {exc.status_code}: {exc}",
            retryable=retryable,
            overloaded=overloaded,
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise LLMAPIError(f"Anthropic connection error: {exc}", retryable=True) from exc
    except anthropic.APIError as exc:
        raise LLMAPIError(f"Anthropic API error: {exc}", retryable=False) from exc
