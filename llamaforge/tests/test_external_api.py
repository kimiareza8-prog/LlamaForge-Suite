import io
import json
import http.client
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from llamaforge.core.external_api import ExternalAPIService, external_api_opener
from llamaforge.web.server import LlamaForgeState


class DummySecrets:
    def __init__(self, key='test-key'):
        self.key = key
    def get(self, provider):
        return self.key
    def set(self, provider, key):
        self.key = key


class FakeResponse:
    def __init__(self, body=b'', lines=None, headers=None, status=200):
        self._body = body
        self._lines = list(lines or [])
        self.headers = headers or {}
        self.status = status
    def read(self, *args):
        return self._body
    def __iter__(self):
        return iter(self._lines)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def close(self):
        pass


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []
    def open(self, request, timeout=None):
        self.requests.append(request)
        return self.response


def service(tmp_path: Path):
    svc = ExternalAPIService(tmp_path)
    svc.secrets = DummySecrets()
    return svc


def test_openai_uses_responses_api_and_parses_usage(tmp_path):
    obj = {
        'status': 'completed',
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'hello'}]}],
        'usage': {'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15},
    }
    opener = FakeOpener(FakeResponse(json.dumps(obj).encode(), headers={'x-ratelimit-remaining-requests': '99'}))
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False, 'mode': 'direct'})):
        result = svc.chat_completion('openai', 'gpt-test', [{'role': 'user', 'content': 'hi'}], max_tokens=50)
    assert opener.requests[0].full_url.endswith('/v1/responses')
    payload = json.loads(opener.requests[0].data.decode())
    assert payload['input'][0]['content'] == 'hi'
    assert payload['max_output_tokens'] == 50
    assert result['content'] == 'hello'
    assert result['usage']['total_tokens'] == 15
    assert svc.usage('openai')['rate_limits']['x-ratelimit-remaining-requests'] == '99'


def test_gemini_uses_openai_compatible_chat_completions(tmp_path):
    obj = {
        'choices': [{'message': {'content': 'gemini ok'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 7, 'completion_tokens': 2, 'total_tokens': 9},
    }
    opener = FakeOpener(FakeResponse(json.dumps(obj).encode()))
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': True, 'type': 'http', 'host': '127.0.0.1', 'port': 8080})):
        result = svc.chat_completion('gemini', 'gemini-test', [{'role': 'user', 'content': 'hi'}], max_tokens=40)
    assert opener.requests[0].full_url.endswith('/v1beta/openai/chat/completions')
    payload = json.loads(opener.requests[0].data.decode())
    assert payload['messages'][0]['content'] == 'hi'
    assert payload['max_tokens'] == 40
    assert result['content'] == 'gemini ok'
    assert result['route']['detected'] is True


@pytest.mark.parametrize(('provider','base_url'), [
    ('cerebras', 'https://api.cerebras.ai/v1'),
    ('groq', 'https://api.groq.com/openai/v1'),
    ('mistral', 'https://api.mistral.ai/v1'),
    ('alibaba', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'),
])
def test_added_providers_use_chat_completions_and_parse_models(tmp_path, provider, base_url):
    model_list = FakeOpener(FakeResponse(json.dumps({'data':[{'id':'model-live'}]}).encode()))
    chat = FakeOpener(FakeResponse(json.dumps({
        'choices':[{'message':{'content':'provider ok'},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5},
    }).encode()))
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(model_list, {'detected':False,'mode':'direct'})):
        models=svc.list_models(provider)
    assert model_list.requests[0].full_url == base_url + '/models'
    assert models['models'] == [{'id':'model-live','name':'model-live','owned_by':''}]
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(chat, {'detected':False,'mode':'direct'})):
        result=svc.chat_completion(provider,'model-live',[{'role':'user','content':'hi'}])
    assert chat.requests[0].full_url == base_url + '/chat/completions'
    payload=json.loads(chat.requests[0].data.decode())
    assert payload['messages'][0]['content']=='hi'
    assert result['content']=='provider ok'
    assert result['usage']['total_tokens']==5


def test_alibaba_region_override_is_used_for_model_and_chat_urls(tmp_path):
    region='https://dashscope-us.aliyuncs.com/compatible-mode/v1'
    svc=ExternalAPIService(tmp_path,base_url_provider=lambda provider:region if provider=='alibaba' else '')
    svc.secrets=DummySecrets()
    assert svc._url('alibaba','models_path')==region+'/models'
    assert svc._url('alibaba','chat_path')==region+'/chat/completions'


def test_gemini_stream_splits_thought_tags_and_recovers_explicit_output(tmp_path):
    thought = '<thought>Input: greeting\nLanguage: Persian\n* Output: سلام! چطور می‌توانم کمک کنم؟</thought>'
    lines = [
        b'data: ' + json.dumps({'choices':[{'delta':{'content':'<tho'}}]}).encode() + b'\n',
        b'data: ' + json.dumps({'choices':[{'delta':{'content':thought[4:]}}]}, ensure_ascii=False).encode() + b'\n',
        b'data: {"choices":[{"finish_reason":"stop","delta":{}}]}\n',
        b'data: [DONE]\n',
    ]
    svc = service(tmp_path)
    opener = FakeOpener(FakeResponse(lines=lines))
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False, 'mode': 'direct'})):
        events = list(svc.stream_chat('gemini', 'gemma-test', [{'role': 'user', 'content': 'سلام'}]))
    visible = ''.join(event.get('delta', '') for event in events if event.get('type') == 'text')
    reasoning = ''.join(event.get('delta', '') for event in events if event.get('type') == 'reasoning')
    assert visible == 'سلام! چطور می‌توانم کمک کنم؟'
    assert 'Input: greeting' in reasoning


def test_api_output_syntax_can_be_overridden_per_model(tmp_path):
    lines = [
        b'data: {"choices":[{"delta":{"content":"BEGIN_PRIVATEinternal notesEND_PRIVATEvisible answer"}}]}\n',
        b'data: {"choices":[{"finish_reason":"stop","delta":{}}]}\n',
        b'data: [DONE]\n',
    ]
    svc = service(tmp_path)
    opener = FakeOpener(FakeResponse(lines=lines))
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False, 'mode': 'direct'})):
        events = list(svc.stream_chat(
            'gemini', 'custom-model', [{'role': 'user', 'content': 'hi'}],
            output_syntax={'open_marker': 'BEGIN_PRIVATE', 'close_marker': 'END_PRIVATE'},
        ))
    assert ''.join(e.get('delta', '') for e in events if e['type'] == 'text') == 'visible answer'
    assert ''.join(e.get('delta', '') for e in events if e['type'] == 'reasoning') == 'internal notes'


def test_api_output_syntax_is_saved_per_provider_and_model():
    saved = []
    events = []
    state = object.__new__(LlamaForgeState)
    state.cfg = SimpleNamespace(api_output_syntax={}, save=lambda: saved.append(True))
    state.events = SimpleNamespace(publish=lambda *args: events.append(args))
    result = state.set_api_output_syntax({
        'provider': 'gemini', 'model': 'gemma-test',
        'open_marker': '<thought>', 'close_marker': '</thought>',
    })
    assert result['output_syntax'] == {'open_marker': '<thought>', 'close_marker': '</thought>'}
    assert state.cfg.api_output_syntax['gemini']['gemma-test'] == result['output_syntax']
    assert saved == [True]
    assert events[-1][1]['reason'] == 'api-output-syntax-updated'
    cleared = state.set_api_output_syntax({'provider': 'gemini', 'model': 'gemma-test', 'open_marker': '', 'close_marker': ''})
    assert cleared['output_syntax'] == {}
    assert state.cfg.api_output_syntax == {}


def test_api_output_syntax_requires_a_complete_marker_pair():
    state = object.__new__(LlamaForgeState)
    state.cfg = SimpleNamespace(api_output_syntax={}, save=lambda: None)
    state.events = SimpleNamespace(publish=lambda *args: None)
    with pytest.raises(ValueError, match='both the opening and closing'):
        state.set_api_output_syntax({'provider': 'openai', 'model': 'gpt-test', 'open_marker': '<analysis>', 'close_marker': ''})


def test_model_probe_sends_one_short_greeting_without_switching_models(tmp_path):
    obj = {
        'status': 'completed',
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'سلام!'}]}],
        'usage': {'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5},
    }
    opener = FakeOpener(FakeResponse(json.dumps(obj).encode()))
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False, 'mode': 'direct'})):
        result = svc.test_model('openai', 'gpt-test')
    payload = json.loads(opener.requests[0].data.decode())
    assert payload['model'] == 'gpt-test'
    assert payload['input'] == [{'role': 'user', 'content': 'سلام'}]
    assert payload['max_output_tokens'] == 24
    assert result['ok'] is True
    assert result['response'] == 'سلام!'
    assert result['usage']['total_tokens'] == 5


def test_model_probe_reports_empty_response_as_failed_test(tmp_path):
    obj = {'status': 'completed', 'output': []}
    opener = FakeOpener(FakeResponse(json.dumps(obj).encode()))
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False, 'mode': 'direct'})):
        with pytest.raises(RuntimeError, match='returned no text'):
            svc.test_model('openai', 'gpt-test')


def test_selected_gemini_chat_does_not_require_local_server(tmp_path):
    svc = service(tmp_path)
    svc._models_cache['gemini'] = {'models': [{'id': 'gemini-2.5-flash'}]}
    calls = []
    def fake_stream(provider, model, messages, **kwargs):
        calls.append((provider, model, messages, kwargs))
        yield {'type': 'text', 'delta': 'سلام!'}
    svc.stream_chat = fake_stream

    state = object.__new__(LlamaForgeState)
    state.cfg = SimpleNamespace(inference_backend='local', external_model_id='', api_output_syntax={'gemini': {'gemini-2.5-flash': {'open_marker': '<thought>', 'close_marker': '</thought>'}}}, save=lambda: None)
    state.external_api = svc
    state.events = SimpleNamespace(publish=lambda *args: None)
    state.log = lambda *args: None
    state._server_generation = 0
    state.server_ready = False
    state.server_proc = SimpleNamespace(running=False)
    state.active_model = None

    result = state.select_api_model({'provider': 'gemini', 'model': 'gemini-2.5-flash'})
    assert result['inference']['backend'] == 'gemini'
    assert result['inference']['ready'] is True
    events = list(state._backend_stream_events(
        [{'role': 'user', 'content': 'سلام'}], temperature=0.2, top_p=0.9,
        top_k=40, min_p=0.0, repeat_penalty=1.0, max_tokens=24,
        reasoning='auto', reasoning_budget=-1,
    ))
    assert calls[0][:2] == ('gemini', 'gemini-2.5-flash')
    assert calls[0][3]['output_syntax'] == {'open_marker': '<thought>', 'close_marker': '</thought>'}
    assert events == [{'type': 'text', 'delta': 'سلام!'}]


def test_openai_responses_stream_is_translated_to_llamaforge_events(tmp_path):
    lines = [
        b'data: {"type":"response.output_text.delta","delta":"hel"}\n',
        b'data: {"type":"response.output_text.delta","delta":"lo"}\n',
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2,"total_tokens":6}}}\n',
        b'data: [DONE]\n',
    ]
    opener = FakeOpener(FakeResponse(lines=lines))
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected': False, 'mode': 'direct'})):
        events = list(svc.stream_chat('openai', 'gpt-test', [{'role': 'user', 'content': 'hi'}]))
    assert ''.join(e.get('delta', '') for e in events if e.get('type') == 'text') == 'hello'
    usage = [e for e in events if e.get('type') == 'meta' and e.get('usage')][0]['usage']
    assert usage['total_tokens'] == 6


def test_proxy_enabled_but_unsupported_never_falls_back_direct():
    with patch('llamaforge.core.external_api.windows_http_proxy', return_value={
        'detected': True, 'supported': False, 'type': 'socks5', 'reason': 'unsupported proxy'
    }):
        with pytest.raises(RuntimeError, match='unsupported proxy'):
            external_api_opener()


def test_api_key_save_is_verified_by_readback(tmp_path):
    svc = service(tmp_path)
    svc.set_key('openai', 'sk-test-value')
    assert svc.key_configured('openai') is True
    assert svc.secrets.get('openai') == 'sk-test-value'


def test_api_key_save_fails_if_secure_store_cannot_read_back(tmp_path):
    class BrokenSecrets:
        def set(self, provider, key):
            self.written = key
        def get(self, provider):
            return ''
    svc = ExternalAPIService(tmp_path)
    svc.secrets = BrokenSecrets()
    with pytest.raises(RuntimeError, match='read back'):
        svc.set_key('gemini', 'AIza-test-value')


class SequenceOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
    def open(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _http_error(code, body=b'error'):
    return urllib.error.HTTPError('https://example.invalid', code, 'error', {}, io.BytesIO(body))


def test_chat_completion_retries_transient_http_500(tmp_path):
    success = FakeResponse(json.dumps({
        'choices':[{'message':{'content':'recovered'},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},
    }).encode())
    opener = SequenceOpener([_http_error(500, b'internal'), success])
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected':False,'mode':'direct'})), \
         patch('llamaforge.core.external_api.time.sleep', return_value=None):
        result = svc.chat_completion('gemini','gemini-test',[{'role':'user','content':'hi'}])
    assert result['content']=='recovered'
    assert len(opener.requests)==2


def test_chat_completion_retries_remote_disconnect(tmp_path):
    success = FakeResponse(json.dumps({
        'choices':[{'message':{'content':'recovered'},'finish_reason':'stop'}],
        'usage':{},
    }).encode())
    opener = SequenceOpener([http.client.RemoteDisconnected('proxy closed'), success])
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected':True,'type':'http'})), \
         patch('llamaforge.core.external_api.time.sleep', return_value=None):
        result = svc.chat_completion('gemini','gemini-test',[{'role':'user','content':'hi'}])
    assert result['content']=='recovered'
    assert len(opener.requests)==2


def test_stream_chat_retries_transient_open_failure_before_any_output(tmp_path):
    lines = [
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
        b'data: {"choices":[{"finish_reason":"stop","delta":{}}]}\n',
        b'data: [DONE]\n',
    ]
    opener = SequenceOpener([http.client.RemoteDisconnected('proxy closed'), FakeResponse(lines=lines)])
    svc = service(tmp_path)
    with patch('llamaforge.core.external_api.external_api_opener', return_value=(opener, {'detected':True,'type':'http'})), \
         patch('llamaforge.core.external_api.time.sleep', return_value=None):
        events=list(svc.stream_chat('gemini','gemini-test',[{'role':'user','content':'hi'}]))
    assert ''.join(e.get('delta','') for e in events if e.get('type')=='text')=='ok'
    assert len(opener.requests)==2
