# support/tasks.py
import logging
from django.core.mail import mail_admins
from procrastinate.contrib.django import app
from llm.extractor.client import LLMAPIError

logger = logging.getLogger(__name__)


@app.task
def process_ticket(ticket_id: str) -> None:
    from support.models import Ticket
    from support.llm import answer_howto, draft_issue
    from support.github import create_issue

    try:
        ticket = Ticket.objects.get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.warning("support.process_ticket: ticket %s not found", ticket_id)
        return

    try:
        if ticket.type == Ticket.TYPE_HOWTO:
            answer = answer_howto(ticket.body)
            ticket.llm_answer = answer
            ticket.status = Ticket.STATUS_AWAITING
            ticket.save(update_fields=["llm_answer", "status", "updated_at"])
            return

        if ticket.type == Ticket.TYPE_PRIVACY:
            mail_admins(
                subject=f"[Support] Privacy/account ticket {ticket.id}",
                message=f"User: {ticket.user_id}\n\n{ticket.body}",
            )
            ticket.status = Ticket.STATUS_CLOSED
            ticket.save(update_fields=["status", "updated_at"])
            return

        title, body, labels = draft_issue(ticket.type, ticket.body)
        gh_url = create_issue(title, body, labels)
        ticket.gh_url = gh_url
        ticket.status = Ticket.STATUS_OPEN
        ticket.save(update_fields=["gh_url", "status", "updated_at"])

    except LLMAPIError as exc:
        logger.error("support.process_ticket: LLM error for %s: %s", ticket_id, exc)
    except Exception as exc:
        logger.error("support.process_ticket: unexpected error for %s: %s", ticket_id, exc)