"""Shared Ollama connection helpers for integration tests."""
import requests
from unittest.mock import MagicMock

OLLAMA_URL = 'http://localhost:11434'
OLLAMA_MODEL = 'qwen2.5:7b'


def ollama_available():
    try:
        return requests.get(f'{OLLAMA_URL}/api/tags', timeout=2).status_code == 200
    except Exception:
        return False


def ollama_call_api(**kwargs):
    system = kwargs.get('system', '')
    messages = kwargs.get('messages', [])
    user_text = messages[0]['content'] if messages else ''
    prompt = f"{system}\n\n{user_text}" if system else user_text

    resp = requests.post(f'{OLLAMA_URL}/api/generate', json={
        'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False,
        'options': {'temperature': 0, 'num_predict': 2000},
    }, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=data['response'])]
    mock_msg.usage = MagicMock(
        input_tokens=data.get('prompt_eval_count', 0),
        output_tokens=data.get('eval_count', 0),
    )
    return mock_msg
