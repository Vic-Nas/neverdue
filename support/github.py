# support/github.py
import hashlib
import hmac
import httpx
from django.conf import settings

_REPO = "Vic-Nas/neverdue"
_API  = "https://api.github.com"


def create_issue(title: str, body: str, labels: list[str] | None = None) -> str:
    """Open a GitHub issue and return its HTML URL."""
    token = settings.GITHUB_TOKEN
    if not token:
        raise ValueError("GITHUB_TOKEN is not configured.")
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    resp = httpx.post(
        f"{_API}/repos/{_REPO}/issues",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["html_url"]


def verify_github_signature(body: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256 from GitHub webhook."""
    secret = getattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)