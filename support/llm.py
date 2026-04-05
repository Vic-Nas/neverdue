# support/llm.py
from django.conf import settings
from llm.extractor.client import call_api, LLMAPIError  # noqa: reuse existing client

_ARCH = None

VALID_LABELS = {"bug", "enhancement", "question", "documentation", "performance", "security"}


def _arch_md() -> str:
    global _ARCH
    if _ARCH is None:
        try:
            _ARCH = (settings.BASE_DIR / "ARCH.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            _ARCH = ""
    return _ARCH


_HOWTO_SYSTEM = """You are a concise support assistant for NeverDue, a calendar-event extraction app.
Answer the user's question directly using the architecture doc below as context.
Be helpful, plain, and brief — 2–5 sentences max. No markdown headers.

{arch}"""

_ISSUE_SYSTEM = """You are preparing a GitHub issue for NeverDue (internal project).
Analyse the user's message and produce a JSON object with exactly three keys:

"title"  — short, clear issue title. No user names, emails, or personal info.
"body"   — full GitHub-flavoured Markdown body. Use:
             ## Description
             ## Steps to reproduce  (if applicable)
             ## Expected behaviour  (if applicable)
             ## Additional context
           Strip ALL personal information. Be detailed and technical where possible.
"labels" — JSON array of labels that best describe the issue. Choose only from:
             bug, enhancement, question, documentation, performance, security
           Pick what fits the actual content — ignore what the user self-reported.
           Use multiple labels if appropriate.

Respond with raw JSON only, no markdown fences."""


def answer_howto(user_body: str) -> str:
    """Call the LLM to answer a 'how do I' question. Returns plain text answer."""
    system = _HOWTO_SYSTEM.format(arch=_arch_md())
    response = call_api(
        model=settings.LLM_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user_body}],
    )
    return response.content[0].text.strip()


def draft_issue(ticket_type: str, user_body: str) -> tuple[str, str, list[str]]:
    """Return (title, body, labels) for a GitHub issue, PII stripped, labels LLM-determined."""
    import json
    prompt = f"User-selected category: {ticket_type}\n\nUser message:\n{user_body}"
    response = call_api(
        model=settings.LLM_MODEL,
        max_tokens=1024,
        system=_ISSUE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    data = json.loads(response.content[0].text.strip())
    labels = [l for l in data.get("labels", []) if l in VALID_LABELS]
    return data["title"], data["body"], labels