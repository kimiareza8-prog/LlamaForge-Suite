import asyncio
import io
import json
import threading
import zipfile
from types import SimpleNamespace as NS

import pytest

from llamaforge.core.agent_tools import AgentPermissions
from llamaforge.core.request_tracing import TraceStore
from llamaforge.core.telegram_skill import TELEGRAM_CANCEL
from test_telegram_skill import service
from test_skill_audit import runtime


def unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return json.loads(z.read('manifest.json')), [json.loads(s) for s in z.read('events.jsonl').splitlines()]


def test_export_cannot_mark_an_older_snapshot_complete(tmp_path,monkeypatch):
    store=TraceStore(tmp_path)
    with store.request(origin='test') as trace:
        original=store.events
        def finishing(trace_id):
            rows=original(trace_id)
            trace.finish('completed')
            return rows
        monkeypatch.setattr(store,'events',finishing)
        manifest,rows=unpack(store.export(trace.id))
        assert not manifest['complete'] or rows[-1]['event']=='request.end'
        assert manifest['events']==len(rows)


def test_interrupted_partial_jsonl_can_be_downloaded(tmp_path):
    store=TraceStore(tmp_path)
    with store.request(origin='test') as trace:trace.emit('test.result',text='recover me')
    with trace.path.open('ab') as f:f.write(b'{"schema":1,"event":"partial')
    manifest,rows=unpack(store.export(trace.id))
    assert not manifest['complete'] and manifest['read_errors']
    assert any(r['data'].get('text')=='recover me' for r in rows)
    assert store.events(trace.id)==rows


@pytest.mark.parametrize('metadata',['[]','{"id":"wrong"}','{bad json','{"status":"completed"}'])
def test_broken_sidecar_does_not_hide_other_traces(tmp_path,metadata):
    store=TraceStore(tmp_path)
    with store.request(origin='good') as good:pass
    with store.request(origin='broken') as broken:pass
    broken.meta_path.write_text(metadata)
    rows={r['id']:r for r in store.list()}
    assert good.id in rows and broken.id in rows
    assert not rows[broken.id]['complete']
    manifest,events=unpack(store.export(broken.id))
    assert not manifest['complete'] and events


def test_orphan_log_is_recoverable_without_metadata(tmp_path):
    store=TraceStore(tmp_path)
    with store.request(origin='test') as trace:pass
    trace.meta_path.unlink()
    manifest,rows=unpack(store.export(trace.id))
    assert not manifest['complete'] and rows


@pytest.mark.parametrize('revoke',[False,True])
def test_failed_disconnect_still_disables_local_account(service,revoke):
    svc,client,_=service
    svc.tool({'operation':'recent_chats'},AgentPermissions())
    async def failed():raise OSError('network down')
    if revoke:client.log_out=failed
    else:client.disconnect=failed
    with pytest.raises(RuntimeError):svc.disconnect(revoke=revoke)
    assert not svc.status()['connected'] and svc.status()['paused']
    assert not svc.refs and not svc.pending and svc.credentials is None
    before=list(client.calls)
    with pytest.raises(RuntimeError,match='disconnected'):svc.tool({'operation':'recent_chats'},AgentPermissions())
    assert client.calls==before


def test_cancelled_send_is_never_submitted(service,monkeypatch):
    svc,client,_=service;p=AgentPermissions()
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p)['matches'][0]['chat_ref']
    cancel=threading.Event();cancel.set();token=TELEGRAM_CANCEL.set(cancel)
    original=asyncio.run_coroutine_threadsafe
    # A legal scheduler interleaving: the loop finishes the RPC before the
    # submitting thread polls its cancellation event.
    def immediate(coro,loop):
        future=original(coro,loop)
        try:future.result(timeout=1)
        except Exception:pass
        return future
    monkeypatch.setattr(asyncio,'run_coroutine_threadsafe',immediate)
    try:
        with pytest.raises(RuntimeError,match='cancel'):
            svc.tool({'operation':'send','chat_ref':ref,'text':'hello','request_key':'cancelled'},p)
    finally:TELEGRAM_CANCEL.reset(token)
    assert not any(c[0]=='send' for c in client.calls)


def test_telegram_flood_wait_is_reported_and_applies_to_following_calls(service):
    svc,client,_=service;p=AgentPermissions()
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p)['matches'][0]['chat_ref']
    class FloodWaitError(Exception):seconds=30
    async def flood(*a,**k):client.calls.append(('flood',));raise FloodWaitError()
    client.send_message=flood
    with pytest.raises(RuntimeError,match='30 seconds'):
        svc.tool({'operation':'send','chat_ref':ref,'text':'hello','request_key':'flood'},p)
    before=list(client.calls)
    with pytest.raises(RuntimeError,match='cooldown'):svc.tool({'operation':'messages','chat_ref':ref},p)
    assert client.calls==before


@pytest.mark.parametrize('uncertain',[False,True])
def test_agent_turn_deduplicates_even_if_model_changes_request_key(runtime,service,uncertain):
    svc,client,_=service;runtime.telegram=svc;p=AgentPermissions()
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p)['matches'][0]['chat_ref']
    if uncertain:
        async def timeout(*a,**k):client.calls.append(('send',));raise TimeoutError('unknown')
        client.send_message=timeout
    def run_turn():
        calls=iter([
            {'route':'skills','families':['telegram']},
            {'action':'tool','skill':'telegram','arguments':{'operation':'send','chat_ref':ref,'text':'hello','request_key':'model-one'}},
            {'action':'tool','skill':'telegram','arguments':{'operation':'send','chat_ref':ref,'text':'hello','request_key':'model-two'}},
            {'action':'final','answer':'انجام شد'}])
        list(runtime.run([{'role':'user','content':'تلگرام به علی hello بفرست'}],lambda *_:{'content':json.dumps(next(calls))},p,max_steps=3))
    run_turn()
    assert len([c for c in client.calls if c[0]=='send'])==1
    # A new user instruction may intentionally send the same text again.
    run_turn()
    assert len([c for c in client.calls if c[0]=='send'])==2


def test_failed_relogin_cannot_leave_an_old_phone_code_hash(service):
    svc,client,_=service
    svc.login({'api_id':123,'api_hash':'a'*32,'phone':'+12025550123'})
    async def failed(_):raise OSError('send code failed')
    client.send_code_request=failed
    with pytest.raises(RuntimeError):svc.login({'api_id':124,'api_hash':'b'*32,'phone':'+12025550124'})
    assert not svc.pending
    with pytest.raises(ValueError,match='not requested'):svc.login({'code':'12345'})


def test_generated_http_request_id_is_shared_with_stream_and_cancel():
    from llamaforge.web.server import Handler
    state=NS(chat_cancellations={},log=lambda *_:None,log_exception=lambda *_:None)
    observed=[]
    def stream(payload):
        request_id=payload.get('request_id')
        observed.append(request_id)
        assert request_id in state.chat_cancellations
        assert payload['_cancel'] is state.chat_cancellations[request_id]
        yield {'type':'text','delta':'ok'}
    state.chat_stream=stream
    handler=object.__new__(Handler);handler.server=NS(state=state)
    handler._read_json=lambda:{'messages':[{'role':'user','content':'hello'}]}
    handler.send_response=lambda *_:None;handler.send_header=lambda *_:None;handler.end_headers=lambda:None
    handler.wfile=io.BytesIO();handler._send_json=lambda *_:None
    handler._chat_stream()
    assert observed and observed[0]
    assert not state.chat_cancellations


@pytest.mark.parametrize('permission,tool,args',[
    ('allow_workspace_write','calendar',{'operation':'create','title':'Blocked','start':'2026-09-27T10:00'}),
    ('allow_write','http_request',{'method':'POST','url':'https://example.com/endpoint'}),
    ('allow_telegram_read','telegram',{'operation':'recent_chats'}),
    ('allow_telegram_write','telegram',{'operation':'send','chat_ref':'unused','text':'hello'}),
])
def test_new_tool_call_honors_permissions_revoked_after_turn_started(runtime,monkeypatch,permission,tool,args):
    initial=AgentPermissions(allow_write=True)
    live=AgentPermissions(allow_write=True)
    setattr(live,permission,False)
    runtime.permission_provider=lambda:live
    touched=[]
    monkeypatch.setattr(runtime,'_http',lambda *_a,**_k:touched.append('network'))
    monkeypatch.setattr(runtime.telegram,'_submit',lambda coro:(coro.close(),touched.append('telegram'))[-1])
    result=json.loads(runtime.execute(tool,args,initial))
    assert not result['ok'] and not touched
    assert getattr(initial,permission) is True  # A request snapshot is not mutated.
    assert not runtime.calendar.snapshot()['events']


def test_live_permission_provider_cannot_expand_a_request_grant(runtime):
    runtime.permission_provider=lambda:AgentPermissions(allow_workspace_write=True)
    result=json.loads(runtime.execute('calendar',{'operation':'create','title':'Denied','start':'2026-09-27T10:00'},AgentPermissions(allow_workspace_write=False)))
    assert not result['ok']
