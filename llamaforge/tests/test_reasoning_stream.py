import io
from unittest.mock import patch

from llamaforge.core.net import stream_chat_events


class FakeResponse:
    def __init__(self, lines):
        self._lines = [x.encode('utf-8') for x in lines]
        self.headers = {'Content-Type': 'text/event-stream'}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self._lines)


class FakeOpener:
    def __init__(self, response): self.response = response
    def open(self, req, timeout=None): return self.response


def test_think_tags_are_split_from_final_answer():
    lines = [
        'data: {"choices":[{"delta":{"content":"<thi"}}]}\n',
        'data: {"choices":[{"delta":{"content":"nk>internal plan"}}]}\n',
        'data: {"choices":[{"delta":{"content":"</think>Final answer"}}]}\n',
        'data: [DONE]\n',
    ]
    with patch('llamaforge.core.net._opener', return_value=FakeOpener(FakeResponse(lines))):
        events = list(stream_chat_events('127.0.0.1', 8080, [{'role':'user','content':'hi'}]))
    reasoning=''.join(x.get('delta','') for x in events if x.get('type')=='reasoning')
    text=''.join(x.get('delta','') for x in events if x.get('type')=='text')
    assert reasoning == 'internal plan'
    assert text == 'Final answer'
    assert '<think>' not in text
