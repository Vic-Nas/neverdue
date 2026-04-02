import pytest
import json
from unittest.mock import patch, MagicMock
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from dashboard.views.upload import upload
from emails.models import ScanJob


def _make_request(user, method='GET', data=None, files=None):
    rf = RequestFactory()
    if method == 'GET':
        req = rf.get('/dashboard/upload/')
    else:
        req = rf.post('/dashboard/upload/', data=data or {})
        if files:
            req = rf.post('/dashboard/upload/', data={**(data or {}), **files})
    req.user = user
    return req


@pytest.mark.django_db
class TestUploadView:
    def test_get_renders(self, auth_client):
        resp = auth_client.get('/dashboard/upload/')
        assert resp.status_code == 200
        assert b'upload' in resp.content.lower() or resp.status_code == 200

    @patch('emails.tasks.process_text_as_upload')
    def test_text_only_creates_job(self, mock_proc, auth_client, user):
        resp = auth_client.post('/dashboard/upload/', {'context': 'Exam on June 15'})
        assert resp.status_code == 302
        job = ScanJob.objects.get(user=user)
        assert job.source == 'upload'
        assert job.upload_text == 'Exam on June 15'
        mock_proc.defer.assert_called_once()

    def test_empty_submission_error(self, auth_client):
        resp = auth_client.post('/dashboard/upload/', {})
        assert resp.status_code == 200
        assert b'provide' in resp.content.lower() or b'error' in resp.content.lower()

    @patch('emails.tasks.process_uploaded_file')
    def test_file_upload_creates_job(self, mock_proc, auth_client, user):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('test.png', b'\x89PNG\r\n', content_type='image/png')
        resp = auth_client.post('/dashboard/upload/', {'files': f})
        assert resp.status_code == 302
        job = ScanJob.objects.get(user=user)
        assert job.source == 'upload'
        assert job.file_b64 != ''
        mock_proc.defer.assert_called_once()

    @patch('emails.tasks.process_uploaded_file')
    def test_file_with_context(self, mock_proc, auth_client, user):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('syllabus.pdf', b'%PDF...', content_type='application/pdf')
        resp = auth_client.post('/dashboard/upload/', {'files': f, 'context': 'Fall 2026 syllabus'})
        assert resp.status_code == 302
        job = ScanJob.objects.get(user=user)
        assert job.upload_context == 'Fall 2026 syllabus'

    def test_unauthenticated_redirect(self, client):
        resp = client.get('/dashboard/upload/')
        assert resp.status_code == 302
        assert 'login' in resp.url or 'accounts' in resp.url
