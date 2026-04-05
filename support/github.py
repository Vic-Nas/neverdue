# support/github.py
import httpx
from django.conf import settings

_REPO = "Vic-Nas/neverdue"
_API = "https://api.github.com"


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