# support/tasks.py
import logging
from procrastinate.contrib.django import app
from llm.extractor.client import LLMAPIError

logger = logging.getLogger(__name__)


@app.task
def process_ticket(ticket_id: str) -> None:
    from support.models import Ticket, CONTACT_SERVICES
    from support.llm import triage
    from support.github import create_issue

    try:
        ticket = Ticket.objects.get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.warning("support.process_ticket: ticket %s not found", ticket_id)
        return

    try:
        result = triage(ticket.body)
        ticket.type = result["type"]

        if result["type"] == Ticket.TYPE_HOWTO:
            ticket.llm_answer = result["answer"] or ""
            ticket.status = Ticket.STATUS_AWAITING
            ticket.save(update_fields=["type", "llm_answer", "status", "updated_at"])
            return

        if result["type"] == Ticket.TYPE_PRIVACY:
            service = CONTACT_SERVICES["privacy"]
            ticket.llm_answer = (
                f"For privacy and account concerns, please contact us directly at "
                f"{service}@service.neverdue.ca and we'll get back to you."
            )
            ticket.status = Ticket.STATUS_AWAITING
            ticket.save(update_fields=["type", "llm_answer", "status", "updated_at"])
            return

        gh_url = create_issue(result["title"], result["body"], result["labels"] or [])
        ticket.gh_url = gh_url
        ticket.status = Ticket.STATUS_OPEN
        ticket.save(update_fields=["type", "gh_url", "status", "updated_at"])

    except LLMAPIError as exc:
        logger.error("support.process_ticket: LLM error for %s: %s", ticket_id, exc)
    except Exception as exc:
        logger.error("support.process_ticket: unexpected error for %s: %s", ticket_id, exc)