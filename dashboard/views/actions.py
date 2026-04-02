# dashboard/views/actions.py
import json as _json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from dashboard.models import Event
from dashboard.ical import build_ics

logger = logging.getLogger(__name__)


def _build_reprocess_text(events_qs, prompt: str) -> str:
    blocks = [e.serialize_as_text() for e in events_qs]
    return "\n\n---\n\n".join(blocks) + f"\n\nUser instruction: {prompt}"


@login_required
def event_prompt_edit(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.tasks import process_text_as_upload
        from emails.models import ScanJob
        event = get_object_or_404(Event, pk=pk, user=request.user)
        data = _json.loads(request.body)
        prompt = data.get('prompt', '').strip()
        if not prompt:
            return JsonResponse({'ok': False, 'error': 'Prompt is required.'}, status=400)

        full_text = _build_reprocess_text([event], prompt)
        event.delete()

        job = ScanJob.objects.create(
            user=request.user, source=ScanJob.SOURCE_UPLOAD,
            status=ScanJob.STATUS_QUEUED, upload_text=full_text,
        )
        process_text_as_upload.defer(job_id=job.id, user_id=request.user.pk, text=full_text)
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("event_prompt_edit error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def events_bulk_action(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        from emails.tasks import process_text_as_upload
        from emails.models import ScanJob
        data = _json.loads(request.body)
        event_ids = [int(i) for i in data.get('event_ids', [])]
        prompt = data.get('prompt', '').strip()
        action = data.get('action', 'delete')

        events = Event.objects.filter(pk__in=event_ids, user=request.user)

        if action == 'delete' or not prompt:
            count = events.count()
            events.delete()
            return JsonResponse({'ok': True, 'deleted': count})

        full_text = _build_reprocess_text(events, prompt)
        events.delete()

        job = ScanJob.objects.create(
            user=request.user, source=ScanJob.SOURCE_UPLOAD,
            status=ScanJob.STATUS_QUEUED, upload_text=full_text,
        )
        process_text_as_upload.defer(job_id=job.id, user_id=request.user.pk, text=full_text)
        return JsonResponse({'ok': True, 'queued': len(event_ids)})
    except Exception:
        logger.exception("events_bulk_action error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def export_events(request):
    try:
        ids_param = request.GET.get('ids', '')
        if ids_param == 'all':
            events = Event.objects.filter(user=request.user, status='active').select_related('category')
        elif ids_param:
            try:
                id_list = [int(i) for i in ids_param.split(',') if i.strip()]
            except ValueError:
                return HttpResponse('Invalid ids parameter.', status=400)
            if not id_list:
                return HttpResponse('No event IDs provided.', status=400)
            events = Event.objects.filter(pk__in=id_list, user=request.user, status='active').select_related('category')
        else:
            return HttpResponse('No event IDs provided.', status=400)

        if not events.exists():
            return HttpResponse('No active events found for the given IDs.', status=404)

        ics_content = build_ics(events)
        response = HttpResponse(ics_content, content_type='text/calendar')
        response['Content-Disposition'] = 'attachment; filename="neverdue-events.ics"'
        return response
    except Exception:
        logger.exception("export_events error for user=%s", request.user.pk)
        return HttpResponse('Export unavailable.', status=500)
