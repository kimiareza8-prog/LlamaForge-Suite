import threading
from types import SimpleNamespace

from llamaforge.web.server import LlamaForgeState


def test_log_does_not_publish_tokens_or_authorization():
    state = object.__new__(LlamaForgeState)
    state.lock = threading.RLock()
    state.logs = []
    lines = []
    state.file_logger = SimpleNamespace(info=lines.append)
    state.log('failed https://example.com/?agent_token=TEST_SECRET_A Authorization: Bearer TEST_SECRET_B')
    assert 'TEST_SECRET_A' not in str(lines + state.logs)
    assert 'TEST_SECRET_B' not in str(lines + state.logs)
