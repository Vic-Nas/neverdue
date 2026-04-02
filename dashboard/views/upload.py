# dashboard/views/upload.py
import base64
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from dashboard.models import Category

logger = logging.getLogger(__name__)


@login_required
def upload(request):
    try:
        from emails.tasks import process_uploaded_file, process_text_as_upload
        from emails.models import ScanJob
        if request.method == 'POST':
            files = request.FILES.getlist('files')
            context = request.POST.get('context', '').strip()
            if not files and not context:
                return render(request, 'dashboard/upload.html', {
                    'categories': Category.objects.filter(user=request.user).order_by('name'),
                    'error': 'Please provide a file or a prompt.',
                })
            if files:
                attachments = []
                for uploaded in files:
                    content_type = uploaded.content_type or 'application/octet-stream'
                    file_b64 = base64.b64encode(uploaded.read()).decode('utf-8')
                    filename = uploaded.name or ''
                    attachments.append([file_b64, content_type, filename])
                job = ScanJob.objects.create(
                    user=request.user, source=ScanJob.SOURCE_UPLOAD,
                    status=ScanJob.STATUS_QUEUED,
                    file_b64=json.dumps(attachments),
                    upload_context=context,
                )
                process_uploaded_file.defer(
                    job_id=job.id, user_id=request.user.pk,
                    attachments=attachments, context=context,
                )
            else:
                job = ScanJob.objects.create(
                    user=request.user, source=ScanJob.SOURCE_UPLOAD,
                    status=ScanJob.STATUS_QUEUED, upload_text=context,
                )
                process_text_as_upload.defer(job_id=job.id, user_id=request.user.pk, text=context)
            return redirect('dashboard:queue')
        return render(request, 'dashboard/upload.html', {
            'categories': Category.objects.filter(user=request.user).order_by('name'),
        })
    except Exception:
        logger.exception("upload error for user=%s", request.user.pk)
        return HttpResponse('Upload unavailable.', status=500)
