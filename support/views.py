# support/views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from llm.extractor.client import LLMAPIError
from support.models import Ticket
from support.tasks import process_ticket

logger = logging.getLogger(__name__)


@login_required
def submit(request):
    if request.method == "POST":
        body = request.POST.get("body", "").strip()
        if not body:
            return render(request, "support/submit.html", {
                "error": "Please describe your issue.",
            })
        ticket = Ticket.objects.create(user=request.user, type=Ticket.TYPE_BUG, body=body)
        process_ticket.defer(ticket_id=str(ticket.id))
        return redirect("support:detail", pk=ticket.id)

    return render(request, "support/submit.html", {})


@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, user=request.user)
    return render(request, "support/ticket_detail.html", {"ticket": ticket})


@login_required
@require_POST
def resolve(request, pk):
    """AJAX — howto satisfied/unsatisfied branch."""
    ticket = get_object_or_404(Ticket, pk=pk, user=request.user)
    if ticket.status != Ticket.STATUS_AWAITING:
        return JsonResponse({"error": "Invalid state."}, status=400)

    try:
        data = json.loads(request.body)
        satisfied = bool(data.get("satisfied"))
    except (ValueError, KeyError):
        return JsonResponse({"error": "Bad request."}, status=400)

    if satisfied:
        ticket.status = Ticket.STATUS_CLOSED
        ticket.save(update_fields=["status", "updated_at"])
        return JsonResponse({"status": "closed"})

    from support.llm import triage
    from support.github import create_issue
    try:
        result = triage(ticket.body)
        gh_url = create_issue(result["title"], result["body"], result["labels"] or [])
        ticket.gh_url = gh_url
        ticket.status = Ticket.STATUS_OPEN
        ticket.save(update_fields=["gh_url", "status", "updated_at"])
        return JsonResponse({"status": "open", "gh_url": gh_url})
    except LLMAPIError as exc:
        logger.error("support.resolve: LLM error %s", exc)
        return JsonResponse({"error": "Could not process. Try again later."}, status=500)
    except Exception as exc:
        logger.error("support.resolve: error %s", exc)
        return JsonResponse({"error": "Could not open issue. Try again later."}, status=500)


@login_required
def my_tickets(request):
    tickets = Ticket.objects.filter(user=request.user)
    return render(request, "support/my_tickets.html", {"tickets": tickets})
