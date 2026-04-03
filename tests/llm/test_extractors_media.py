import json
from unittest.mock import patch, MagicMock
from llm.extractor.image import extract_events_from_image
from llm.extractor.email import extract_events_from_email


def _mock_api_response(events):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(events))]
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    return msg


SAMPLE_EVENT = {
    'title': 'Exam', 'start': '2026-06-15T09:00:00',
    'end': '2026-06-15T11:00:00', 'description': 'Final exam',
    'category_hint': 'Exams', 'recurrence_freq': '',
    'recurrence_until': '', 'status': 'active',
    'concern': '', 'expires_at': '',
}


class TestExtractImage:
    @patch('llm.extractor.image.call_api')
    def test_returns_events(self, mock_api):
        mock_api.return_value = _mock_api_response([SAMPLE_EVENT])
        events, inp, out = extract_events_from_image(b'fake', 'image/jpeg')
        assert len(events) == 1

    @patch('llm.extractor.image.call_api')
    def test_pdf_uses_document_block(self, mock_api):
        mock_api.return_value = _mock_api_response([])
        extract_events_from_image(b'fake', 'application/pdf')
        call_args = mock_api.call_args
        content = call_args[1]['messages'][0]['content']
        assert any(c.get('type') == 'document' for c in content)


class TestExtractEmail:
    @patch('llm.extractor.email.call_api')
    def test_visual_only_skips_reconcile(self, mock_api):
        mock_api.return_value = _mock_api_response([SAMPLE_EVENT])
        events, _, _ = extract_events_from_email(
            body='Context', attachments=[(b'img', 'image/jpeg', 'cal.jpg')],
        )
        assert len(events) == 1
        assert mock_api.call_count == 1

    @patch('llm.extractor.email.call_api')
    def test_text_attachment_triggers_reconcile(self, mock_api):
        mock_api.return_value = _mock_api_response([SAMPLE_EVENT])
        events, _, _ = extract_events_from_email(
            body='Email body',
            attachments=[(b'text content', 'text/plain', 'notes.txt')],
        )
        assert mock_api.call_count == 1
