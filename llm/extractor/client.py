# llm/extractor/client.py
import anthropic
from django.conf import settings

client = anthropic.Anthropic(api_key=settings.LLM_API_KEY)


class LLMAPIError(Exception):
    """Raised when the LLM provider returns an API-level error (auth, quota, server)."""


def call_api(**kwargs):
    try:
        return client.messages.create(**kwargs)
    except anthropic.APIError as exc:
        raise LLMAPIError(f"Anthropic API error: {exc}") from exc
