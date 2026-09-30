import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_skill_audit import runtime


def tracing():
    from llamaforge.core.request_tracing import TraceStore, record, logged_model, logged_stream
    return TraceStore, record, logged_model, logged_stream


def test_full_input_output_export_and_secret_redaction(tmp_path):
    TraceStore, record, logged_model, _ = tracing()
    store = TraceStore(tmp_path)
    prompt = 'پرامپت طولانی\n' * 3000
    with store.request(origin='local', messages=[{'role':'user','content':prompt}]) as trace:
        record('settings', owner_key='PRIVATE_OWNER', max_tokens=1000, prompt_tokens=90)
        out=logged_model(lambda: {'content':'done', 'usage':{'completion_tokens':7}},
                         [{'role':'user','content':'password="TWO WORDS"\nAuthorization: Bearer PRIVATE_BEARER'}], stage='planner')
        assert out['content']=='done'
        record('tool.result', result={'headers':{'Cookie':'session=PRIVATE_COOKIE; other=SECRET2'},'url':'https://example.com/?agent_token=PRIVATE_QUERY'})
    events=store.events(trace.id)
    assert [r['seq'] for r in events]==list(range(1,len(events)+1))
    assert events[0]['data']['messages'][0]['content']==prompt
    serialized=json.dumps(events,ensure_ascii=False)
    for secret in ('PRIVATE_OWNER','TWO WORDS','PRIVATE_BEARER','PRIVATE_COOKIE','SECRET2','PRIVATE_QUERY'):
        assert secret not in serialized
    assert '"max_tokens": 1000' in serialized and '"prompt_tokens": 90' in serialized
    with zipfile.ZipFile(io.BytesIO(store.export(trace.id))) as z:
        assert {'events.jsonl','report.md','manifest.json'} <= set(z.namelist())
        assert prompt in z.read('report.md').decode()
        assert json.loads(z.read('manifest.json'))['status']=='completed'


def test_live_stream_is_not_buffered_and_partial_secret_is_redacted_on_close(tmp_path):
    TraceStore, _, _, logged_stream=tracing()
    store=TraceStore(tmp_path);consumed=[]
    def source():
        consumed.append(1);yield {'type':'text','delta':'api_key=SE'}
        consumed.append(2);yield {'type':'text','delta':'CRET'}
        consumed.append(3);yield {'type':'text','delta':' more'}
    with store.request(origin='local') as trace:
        it=logged_stream(source(), [{'role':'user','content':'hello'}],stage='final')
        assert next(it)['delta']=='api_key=SE' and consumed==[1]
        assert next(it)['delta']=='CRET'
        it.close()
    events=store.events(trace.id)
    assert 'SECRET' not in json.dumps(events)
    result=next(x for x in events if x['event']=='model.end')
    assert result['data']['status']=='disconnected' and result['data']['delta_count']==2
    assert result['data']['ttft_ms'] is not None


def test_parallel_requests_do_not_mix_and_export_rejects_paths(tmp_path):
    TraceStore, record, _, _=tracing()
    store=TraceStore(tmp_path);barrier=threading.Barrier(2)
    def run(name):
        with store.request(origin=name) as t:
            barrier.wait();record('marker',name=name)
        return t.id,name
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,['A','B']))
    for tid,name in results:
        assert [x['data']['name'] for x in store.events(tid) if x['event']=='marker']==[name]
    with pytest.raises(ValueError):store.export('../outside')
    assert len(store.list())==2


def test_trace_limit_is_explicit_and_retention_keeps_active_trace(tmp_path):
    TraceStore, record, _, _=tracing()
    store=TraceStore(tmp_path,max_traces=2,max_trace_bytes=2000)
    with store.request(origin='active') as active:
        for i in range(3):
            with store.request(origin=str(i)):record('big',text='x'*4000)
        assert store.events(active.id)
    rows=store.list()
    assert len(rows)<=2
    limited=next(r for r in rows if r['id']!=active.id)
    assert limited['complete'] is False
    assert any(x['event']=='trace.limit' for x in store.events(limited['id']))


def test_agent_records_decisions_catalog_full_observation_and_verification(runtime,tmp_path):
    TraceStore, _, _, _=tracing()
    from llamaforge.core.agent_tools import AgentPermissions
    store=TraceStore(tmp_path/'traces')
    answers=iter([
        {'content':json.dumps({'route':'skills','families':['calendar'],'needs_write':True})},
        {'content':json.dumps({'action':'tool','skill':'calendar','arguments':{'operation':'create','title':'جلسه','jalali':'1405-07-06','time':'10:00'}})},
        {'content':json.dumps({'action':'final','answer':'جلسه ثبت شد.'})},
    ])
    with store.request(origin='remote',message_id='bridge-message') as trace:
        events=list(runtime.run([{'role':'user','content':'برای ۶ مهر ساعت ۱۰ جلسه بساز'}],lambda *a:next(answers),AgentPermissions(),max_steps=3))
    rows=store.events(trace.id);names=[r['event'] for r in rows]
    assert 'skills.catalog' in names and 'skills.shortlist' in names and 'planner.decision' in names
    assert names.count('model.start')==3
    tool=next(r for r in rows if r['event']=='tool.end')
    assert tool['data']['result']['result']['verification']['verified']
    assert 'observation' in names and any(e.get('delta') for e in events)


def test_failed_requests_have_terminal_error_and_disabled_trace_is_noop(tmp_path):
    TraceStore,record,_,_=tracing();store=TraceStore(tmp_path)
    with pytest.raises(RuntimeError):
        with store.request(origin='local') as t:raise RuntimeError('password=PRIVATE_FAILURE')
    assert store.list()[0]['status']=='error'
    assert 'PRIVATE_FAILURE' not in json.dumps(store.events(t.id))
    store.enabled=False
    with store.request(origin='local'):record('disabled')
    assert len(store.list())==1


def test_binary_is_represented_by_metadata_not_base64(tmp_path):
    TraceStore,_,_,_=tracing();store=TraceStore(tmp_path)
    with store.request(origin='local',messages=[{'attachments':[{'name':'photo.png','data_url':'data:image/png;base64,QUJDRA=='}]}]) as t:pass
    text=json.dumps(store.events(t.id))
    assert 'QUJDRA' not in text and 'sha256' in text and 'binary_omitted' in text


def test_chat_wrapper_correlates_request_and_exports_terminal_error(tmp_path):
    from types import SimpleNamespace
    from llamaforge.web.server import LlamaForgeState
    TraceStore,_,_,_=tracing()
    state=object.__new__(LlamaForgeState);state.request_traces=TraceStore(tmp_path)
    def failed(payload):
        yield {'type':'text','delta':'partial'}
        raise RuntimeError('backend failed')
    state._chat_stream_impl=failed
    events=[]
    with pytest.raises(RuntimeError,match='backend failed'):
        for event in state.chat_stream({'request_id':'client-123','messages':[{'role':'user','content':'hello'}]}):events.append(event)
    trace=state.request_traces.list()[0]
    assert trace['request_id']=='client-123' and trace['status']=='error'
    assert events[0]['trace']['id']==trace['id']
    assert any(r['data'].get('text')=='partial' for r in state.request_traces.events(trace['id']))


def test_transport_records_exact_fallback_payload(tmp_path,monkeypatch):
    import urllib.error
    from llamaforge.core.net import chat_completion_with_tools
    from test_reasoning_stream import FakeResponse
    TraceStore,_,logged_model,_=tracing();store=TraceStore(tmp_path)
    calls=[]
    class Opener:
        def open(self, req, **kwargs):
            data=json.loads(req.data);calls.append(data)
            if len(calls)==1:raise urllib.error.HTTPError(req.full_url,400,'bad',{},io.BytesIO(b'unknown field reasoning_format'))
            class Response:
                def read(self):return json.dumps({'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],'usage':{'prompt_tokens':9}}).encode()
                def close(self):pass
                def __enter__(self):return self
                def __exit__(self,*args):pass
            return Response()
    monkeypatch.setattr('llamaforge.core.net._opener',lambda:Opener())
    with store.request(origin='local') as t:
        logged_model(lambda:chat_completion_with_tools('localhost',8080,[{'role':'user','content':'full input'}]),[])
    rows=store.events(t.id)
    assert [r['data']['payload'] for r in rows if r['event']=='transport.request']==calls
    assert any(r['event']=='transport.error' for r in rows)


def test_trace_write_failure_never_breaks_chat(tmp_path,monkeypatch):
    TraceStore,record,_,_=tracing();store=TraceStore(tmp_path)
    from pathlib import Path
    original=Path.open
    def fail(path,*a,**kw):
        if path.suffix=='.jsonl':raise OSError('disk full')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',fail)
    with store.request(origin='local'):record('test',value='still running')
    assert store.last_error and store.list()[0]['complete'] is False


def test_diagnostic_http_download_and_cross_site_denial(tmp_path):
    from types import SimpleNamespace
    from llamaforge.web.server import Handler
    TraceStore,_,_,_=tracing();store=TraceStore(tmp_path)
    with store.request(origin='local',messages=[{'role':'user','content':'test'}]) as trace:pass
    handler=object.__new__(Handler);handler.server=SimpleNamespace(state=SimpleNamespace(request_traces=store))
    handler.headers={};handler.wfile=io.BytesIO();codes=[];headers={}
    handler.send_response=codes.append;handler.send_header=lambda k,v:headers.update({k:v});handler.end_headers=lambda:None
    handler._route_api_get('/api/logs/requests/export',{'id':[trace.id]})
    assert codes==[200] and headers['Content-Type']=='application/zip'
    assert headers['Cache-Control']=='no-store'
    assert zipfile.is_zipfile(io.BytesIO(handler.wfile.getvalue()))
    handler.headers={'Sec-Fetch-Site':'cross-site'};handler.wfile=io.BytesIO();codes.clear()
    handler._route_api_get('/api/logs/requests',{})
    assert codes==[403]


def test_active_picker_preview_redacts_credentials(tmp_path):
    TraceStore,_,_,_=tracing();store=TraceStore(tmp_path)
    with store.request(origin='local',messages=[{'role':'user','content':'owner_key=PRIVATE_PREVIEW'}]):
        assert 'PRIVATE_PREVIEW' not in json.dumps(store.list())


def test_parallel_tool_spans_keep_request_scope(runtime,tmp_path):
    TraceStore,_,_,_=tracing();store=TraceStore(tmp_path/'traces')
    from llamaforge.core.agent_tools import AgentPermissions
    replies=iter([
        {'content':json.dumps({'route':'skills','families':['calendar','files']})},
        {'content':json.dumps({'action':'parallel','actions':[{'skill':'calendar','arguments':{'operation':'now'}},{'skill':'workspace_files','arguments':{'operation':'list'}}]})},
        {'content':json.dumps({'action':'final','answer':'انجام شد.'})}])
    with store.request(origin='local') as t:
        list(runtime.run([{'role':'user','content':'تقویم و فایل‌ها را بررسی کن'}],lambda *a:next(replies),AgentPermissions(),max_steps=3))
    tools=[r for r in store.events(t.id) if r['event']=='tool.end']
    assert len(tools)==2 and all(r['thread'].startswith('lf-agent-read') for r in tools)


def test_remote_work_and_chat_share_one_transcript(tmp_path):
    from types import SimpleNamespace
    from llamaforge.core.remote_apps import RemoteAppManager
    from llamaforge.web.server import LlamaForgeState
    TraceStore,record,_,_=tracing();store=TraceStore(tmp_path)
    state=object.__new__(LlamaForgeState);state.request_traces=store
    state._chat_stream_impl=lambda p:iter([{'type':'text','delta':'answer'}])
    manager=object.__new__(RemoteAppManager);manager.request_traces=store;manager.app_version='test'
    def work(row,item):
        record('workspace.sync_pull',revision=1)
        list(state.chat_stream({'agent':True,'messages':[{'role':'user','content':'remote prompt'}]}))
        record('remote.delivery',status='delivered')
    manager._run_task_impl=work
    manager._run_task({'id':'app1'},{'message_id':'m1','user_message':'remote prompt'})
    assert len(store.list())==1
    rows=store.events(store.list()[0]['id'])
    assert rows[-1]['event']=='request.end'
    assert {'workspace.sync_pull','runtime.context','remote.delivery'} <= {r['event'] for r in rows}


def test_late_background_event_cannot_follow_terminal_marker(tmp_path):
    TraceStore,_,_,_=tracing();store=TraceStore(tmp_path)
    with store.request(origin='local') as t:pass
    t.emit('late.tool.result',result='late')
    assert store.events(t.id)[-1]['event']=='request.end'


def test_export_of_inflight_request_is_explicitly_incomplete(tmp_path):
    TraceStore,_,_,_=tracing();store=TraceStore(tmp_path)
    with store.request(origin='local') as t:
        with zipfile.ZipFile(io.BytesIO(store.export(t.id))) as z:
            manifest=json.loads(z.read('manifest.json'))
            assert manifest['status']=='running' and manifest['complete'] is False
