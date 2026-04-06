# support/llm.py
import json
import re
from django.conf import settings
from llm.extractor.client import call_api  # noqa: reuse existing client

_ARCH = None

VALID_TYPES = {"bug", "feature", "howto", "perf", "privacy"}
VALID_LABELS = {"bug", "enhancement", "question", "documentation", "performance", "security"}

_TRIAGE_SYSTEM = """You are a support triage assistant for NeverDue, a calendar-event extraction app.
You receive a user's support message. Respond with a single raw JSON object — no markdown fences, no backticks, no explanation.

The JSON must have exactly these keys:
- "type": one of "howto", "bug", "feature", "perf", "privacy"
- "answer": for "howto" only — a plain-text answer (2–5 sentences, no markdown headers) using the architecture doc below as context. Null for all other types.
- "title": for non-howto, non-privacy types — a short GitHub issue title with no PII. Null otherwise.
- "body": for non-howto, non-privacy types — a GitHub-flavoured Markdown issue body with sections: Description, Steps to reproduce, Expected behaviour, Additional context. Strip all PII. Null otherwise.
- "labels": for non-howto, non-privacy types — a JSON array of labels chosen only from: bug, enhancement, question, documentation, performance, security. Null otherwise.

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