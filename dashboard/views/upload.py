import base64
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
                for uploaded in files:
                    content_type = uploaded.content_type or 'application/octet-stream'
                    file_bytes = uploaded.read()
                    file_b64 = base64.b64encode(file_bytes).decode('utf-8')
                    filename = uploaded.name or ''
                    job = ScanJob.objects.create(
                        user=request.user, source=ScanJob.SOURCE_UPLOAD,
                        status=ScanJob.STATUS_QUEUED, file_b64=file_b64,
                        media_type=content_type, upload_context=context, filename=filename,
                    )
                    process_uploaded_file.defer(
                        job_id=job.id, user_id=request.user.pk,
                        file_b64=file_b64, media_type=content_type,
                        context=context, filename=filename,
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
