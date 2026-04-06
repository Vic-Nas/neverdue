# support/llm.py
import json
import re
from django.conf import settings
from llm.extractor.client import call_api  # noqa: reuse existing client

_ARCH = None

VALID_TYPES = {"bug", "feature", "howto", "perf", "privacy"}
VALID_LABELS = {"bug", "enhancement", "question", "documentation", "performance", "security"}

_TRIAGE_SYSTEM = """You are a support triage assistant for NeverDue, a calendar-event extraction app.
You have full knowledge of the codebase via the architecture doc below. Use it actively.

When writing a GitHub issue body, do not produce generic checklists of possible causes.
Instead, reason about the user's specific complaint against the architecture and name the exact
files, functions, models, or task names that are most likely involved. Be specific and technical.
If the user gives enough detail to narrow it down, do so. If not, describe what to look for
and where — using real names from the codebase, not placeholders.

Respond with a single raw JSON object — no markdown fences, no backticks, no explanation.

The JSON must have exactly these keys:
- "type": one of "howto", "bug", "feature", "perf", "privacy"
- "answer": for "howto" only — a plain-text answer (2–5 sentences, no markdown headers) using the architecture doc as context. Null for all other types.
- "title": for non-howto, non-privacy types — a short, specific GitHub issue title with no PII. Null otherwise.
- "body": for non-howto, non-privacy types — a GitHub-flavoured Markdown issue body with sections:
    ## Description
    ## Steps to reproduce  (if applicable)
    ## Expected behaviour  (if applicable)
    ## Relevant code / investigation starting points
  Strip all PII. Be specific: name exact files, functions, task names, model fields, error codes
  from the architecture doc. Do not list generic possibilities — reason and point to what matters.
  Null otherwise.
- "labels": for non-howto, non-privacy types — a JSON array chosen only from:
    bug, enhancement, question, documentation, performance, security
  Null otherwise.

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