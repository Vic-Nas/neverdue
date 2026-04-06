# support/llm.py
import json
import re
from django.conf import settings
from llm.extractor.client import call_api  # noqa: reuse existing client

_ARCH = None

VALID_TYPES = {"bug", "feature", "howto", "perf", "privacy"}
VALID_LABELS = {"bug", "enhancement", "question", "documentation", "performance", "security"}

_TRIAGE_SYSTEM = """You are a support triage assistant for NeverDue, a calendar-event extraction app.
You receive a user's support message and must do two things in a single response:

1. Classify the message into exactly one type:
   - "howto"   — user is asking how to do something
   - "bug"     — something is broken or behaving unexpectedly
   - "feature" — user is requesting a new capability
   - "perf"    — something is slow or unresponsive
   - "privacy" — privacy or account data concern

2. Depending on the type:
   - If "howto": write a plain-text answer (2–5 sentences, no markdown headers) using the architecture doc below as context.
   - If "privacy": set answer to null.
   - For all other types: produce a GitHub issue with:
       "title"  — short, clear, no PII
       "body"   — GitHub-flavoured Markdown with sections:
                    ## Description
                    ## Steps to reproduce  (if applicable)
                    ## Expected behaviour  (if applicable)
                    ## Additional context
                  Strip ALL personal information.
       "labels" — array chosen only from: bug, enhancement, question, documentation, performance, security

Respond with raw JSON only. No markdown fences, no backticks, no preamble. The very first character of your response must be '{'.

{
  "type": "<type>",
  "answer": "<plain text answer or null>",
  "title": "<issue title or null>",
  "body": "<issue markdown body or null>",
  "labels": ["<label>"] or null
}

Architecture context:
{arch}"""


def _arch_md() -> str:
    global _ARCH
    if _ARCH is None:
        try:
            _ARCH = (settings.BASE_DIR / "ARCH.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            _ARCH = ""
    return _ARCH


def _parse_json(text: str) -> dict:
    """Strip markdown fences if present, then parse JSON."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


def triage(user_body: str) -> dict:
    """
    Single LLM call that classifies the ticket and produces all needed output.

    Returns a dict with keys: type, answer, title, body, labels
      - type:   one of VALID_TYPES
      - answer: plain-text string for howto, None otherwise
      - title:  GitHub issue title for non-howto/non-privacy, None otherwise
      - body:   GitHub issue markdown body, None otherwise
      - labels: list of GitHub label strings, None otherwise
    """
    system = _TRIAGE_SYSTEM.format(arch=_arch_md())
    response = call_api(
        model=settings.LLM_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_body}],
    )
    data = _parse_json(response.content[0].text)
    ticket_type = data.get("type") if data.get("type") in VALID_TYPES else "bug"
    labels = [l for l in (data.get("labels") or []) if l in VALID_LABELS]
    return {
        "type":   ticket_type,
        "answer": data.get("answer"),
        "title":  data.get("title"),
        "body":   data.get("body"),
        "labels": labels or None,
    }