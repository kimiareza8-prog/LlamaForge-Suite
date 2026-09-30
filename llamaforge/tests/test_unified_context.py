"""Focused offline checks for the shared context/output budget."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llamaforge.core.external_api import ExternalAPIService
from llamaforge.core.smart_chat import choose_profile
from llamaforge.web.server import LlamaForgeState


class Response:
    headers = {}

    def __init__(self, lines):
        self.lines = lines

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Opener:
    def __init__(self, lines):
        self.response = Response(lines)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return self.response


class UnifiedContextTests(unittest.TestCase):
    def state(self, *, backend='gemini', context=8192, output=6000):
        state = object.__new__(LlamaForgeState)
        state.cfg = SimpleNamespace(inference_backend=backend, default_context_size=context,
                                    generation_max_tokens=output, generation_overrides_enabled=False)
        state.server_ready = backend == 'local'
        state.active_plan = SimpleNamespace(ctx_size=1024)
        state.active_model = None
        state._materialize_chat_messages = lambda messages: [dict(m) for m in messages]
        return state

    def test_smart_mode_does_not_shrink_saved_answer_cap(self):
        profile = choose_profile(None, [{'role': 'user', 'content': 'سلام'}], max_tokens=6000)
        self.assertEqual(profile.max_tokens, 6000)

    def test_api_profile_ignores_stale_client_cap_and_local_plan(self):
        state = self.state()
        profile = state.chat_profile({'messages': [{'role': 'user', 'content': 'سلام'}], 'max_tokens': 16})
        self.assertEqual(profile['max_tokens'], 6000)
        self.assertEqual(state._apply_generation_overrides(choose_profile(None, [], max_tokens=16)).max_tokens, 6000)

    def test_api_context_budget_trims_old_turns_and_preserves_latest(self):
        state = self.state(context=1024, output=600)
        messages = [{'role': 'system', 'content': 'system'}]
        for n in range(8):
            messages += [{'role': 'user', 'content': f'old {n} ' + 'x' * 200},
                         {'role': 'assistant', 'content': 'y' * 200}]
        messages += [{'role': 'user', 'content': 'latest'}]
        prepared, meta = state._prepare_chat_messages(messages, choose_profile(None, messages, max_tokens=600))
        self.assertGreater(meta['trimmed_turns'], 0)
        self.assertTrue(meta['estimated'])
        self.assertEqual(prepared[0]['role'], 'system')
        self.assertEqual(prepared[-1]['content'], 'latest')
        self.assertEqual(len(messages), 18)
        self.assertEqual(state.count_tokens(messages), state._estimate_api_tokens(messages))

    def test_local_output_cap_reflects_loaded_window(self):
        state = self.state(backend='local', context=8192, output=6000)
        profile = state._apply_generation_overrides(choose_profile(None, [], max_tokens=6000))
        self.assertEqual(profile.max_tokens, 320)

    def test_api_output_cap_respects_shared_context_budget(self):
        state = self.state(context=2048, output=6000)
        profile = state.chat_profile({'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(profile['max_tokens'], 1344)

    def test_api_length_reason_is_exposed_with_saved_output_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ExternalAPIService(Path(directory))
            service.secrets = SimpleNamespace(get=lambda _: 'test-key')
            lines = [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
                     b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                     b'data: [DONE]\n']
            opener = Opener(lines)
            with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False})):
                events = list(service.stream_chat('gemini', 'test-model',
                                                  [{'role': 'user', 'content': 'hi'}], max_tokens=6000))
            self.assertEqual(json.loads(opener.requests[0].data)['max_tokens'], 6000)
            self.assertIn({'type': 'meta', 'finish_reason': 'length', 'output_limit': 6000}, events)


if __name__ == '__main__':
    unittest.main()
