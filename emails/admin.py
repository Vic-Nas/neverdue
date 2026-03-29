# emails/admin.py
import logging
from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages

from emails.models import ScanJob

logger = logging.getLogger(__name__)


def _reenqueue(modeladmin, request, queryset):
    """
    Admin bulk action: reset selected failed jobs to queued and re-enqueue them.
    Only acts on failed jobs — skips others silently.
    """
    from emails.tasks import _reenqueue_jobs
    failed = list(queryset.filter(status=ScanJob.STATUS_FAILED))
    if not failed:
        modeladmin.message_user(request, "No failed jobs in selection.", messages.WARNING)
        return
    count = _reenqueue_jobs(failed)
    modeladmin.message_user(request, f"Re-enqueued {count} job(s).", messages.SUCCESS)


_reenqueue.short_description = "Retry selected failed jobs"


@admin.register(ScanJob)
class ScanJobAdmin(admin.ModelAdmin):
    list_display = (
        'pk', 'user', 'source', 'status_badge', 'failure_reason',
        'failure_signature_short', 'notes_short', 'created_at', 'updated_at',
    )
    list_filter = ('status', 'source', 'failure_reason')
    search_fields = ('user__email', 'from_address', 'notes', 'failure_signature')
    readonly_fields = ('created_at', 'updated_at', 'duration_seconds')
    ordering = ('-created_at',)
    actions = [_reenqueue]

    # Optimise list queries
    raw_id_fields = ('user',)

    def status_badge(self, obj):
        colours = {
            ScanJob.STATUS_QUEUED: '#6b7280',
            ScanJob.STATUS_PROCESSING: '#2563eb',
            ScanJob.STATUS_NEEDS_REVIEW: '#d97706',
            ScanJob.STATUS_DONE: '#16a34a',
            ScanJob.STATUS_FAILED: '#dc2626',
        }
        colour = colours.get(obj.status, '#6b7280')
        return format_html(
            '<span style="color:{};font-weight:600;">{}</span>',
            colour,
            obj.get_status_display(),
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def failure_signature_short(self, obj):
        if not obj.failure_signature:
            return '—'
        return obj.failure_signature[:60] + ('…' if len(obj.failure_signature) > 60 else '')
    failure_signature_short.short_description = 'Signature'

    def notes_short(self, obj):
        if not obj.notes:
            return '—'
        return obj.notes[:60] + ('…' if len(obj.notes) > 60 else '')
    notes_short.short_description = 'Notes'
