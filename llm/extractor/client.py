import anthropic
from django.conf import settings

client = anthropic.Anthropic(api_key=settings.LLM_API_KEY)


def call_api(**kwargs):
    try:
        return client.messages.create(**kwargs)
    except anthropic.APIError as exc:
        raise ValueError(f"Anthropic API error: {exc}") from exc
