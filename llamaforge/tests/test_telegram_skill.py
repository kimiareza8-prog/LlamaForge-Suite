import asyncio
import json
from types import SimpleNamespace as NS
import pytest
from llamaforge.core.agent_tools import AgentPermissions
from llamaforge.core.skill_system import SkillRegistry
from llamaforge.core.skill_contracts import operation_policy
from llamaforge.core.redaction import redact
from test_skill_audit import runtime


class MemoryVault:
    value = None
    def load(self): return self.value
    def save(self, value): self.value = value
    def clear(self): self.value = None


class FakeClient:
    def __init__(self):
        self.calls=[];self.session=NS(save=lambda:'SECRET_SESSION');self.authorized=True
        self.peers=[NS(id=i,first_name='Ali',last_name='',username=f'user{i}',title=None,bot=False) for i in (1,2)]
    async def connect(self): self.calls.append(('connect',))
    async def disconnect(self): self.calls.append(('disconnect',))
    async def is_user_authorized(self): return self.authorized
    async def get_me(self): return self.peers[0]
    async def get_dialogs(self,limit):
        self.calls.append(('dialogs',limit));return [NS(entity=e,id=e.id,name='Ali',unread_count=1) for e in self.peers]
    async def get_entity(self,peer):return self.peers[0]
    async def get_messages(self,peer,**kwargs):
        self.calls.append(('messages',peer.id,kwargs))
        if 'ids' in kwargs:return NS(id=kwargs['ids'],raw_text='hello',out=True,date=None,media=None)
        return [NS(id=i,raw_text='x'*3000,out=False,date=None,media=None) for i in range(kwargs['limit'])]
    async def send_message(self,peer,text,**kwargs):
        self.calls.append(('send',peer.id,text,kwargs));return NS(id=12,raw_text=text,out=True,date=None,media=None)
    async def send_code_request(self,phone):return NS(phone_code_hash='SECRET_CODE_HASH')
    async def sign_in(self,**kwargs):self.authorized=True
    async def log_out(self):self.calls.append(('logout',));return True


@pytest.fixture
def service():
    from llamaforge.core.telegram_skill import TelegramService
    client=FakeClient();vault=MemoryVault();vault.value={'api_id':1,'api_hash':'SECRET_HASH','session':'SECRET_SESSION'}
    svc=TelegramService(vault=vault,client_factory=lambda *_:client)
    yield svc,client,vault
    svc.close()


def test_telegram_metadata_does_not_read_history_and_ambiguity_is_explicit(service):
    svc,client,_=service
    out=svc.tool({'operation':'resolve_person','query':'Ali'},AgentPermissions(),'owner')
    assert len(out['matches'])==2 and out['ambiguous']
    assert not any(c[0]=='messages' for c in client.calls)
    assert 'SECRET' not in json.dumps(out)


def test_context_budget_peer_scope_and_send_without_history(service):
    svc,client,_=service;p=AgentPermissions(allow_write=False)
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p,'owner')['matches'][0]['chat_ref']
    with pytest.raises(ValueError,match='scope|expired'):svc.tool({'operation':'messages','chat_ref':ref},p,'other')
    out=svc.tool({'operation':'messages','chat_ref':ref,'limit':15},p,'owner')
    assert len(out['messages'])<=15 and len(json.dumps(out))<14000 and out['truncated']
    client.calls.clear()
    receipt=svc.tool({'operation':'send','chat_ref':ref,'text':'hello','request_key':'synthetic-one'},p,'owner')
    assert receipt['message_id']==12 and receipt['verification']['verified']
    assert not any(c[0]=='messages' and 'ids' not in c[2] for c in client.calls)
    again=svc.tool({'operation':'send','chat_ref':ref,'text':'hello','request_key':'synthetic-one'},p,'owner')
    assert again['message_id']==12 and len([c for c in client.calls if c[0]=='send'])==1


def test_telegram_permissions_and_profile_enforced_at_executor(runtime,service):
    runtime.telegram=service[0]
    no_read=AgentPermissions(allow_telegram_read=False)
    result=json.loads(runtime.execute('telegram',{'operation':'recent_chats'},no_read))
    assert not result['ok'] and not service[1].calls
    only=AgentPermissions(skill_profile='telegram_only')
    assert {d['function']['name'] for d in runtime.tool_definitions(only)}=={'telegram','automation'}
    result=json.loads(runtime.execute('calendar',{'operation':'now'},only))
    assert not result['ok']
    assert operation_policy('telegram',{'operation':'send'}).effect=='external_write'
    assert not operation_policy('telegram',{'operation':'send'}).parallel_safe
    assert not SkillRegistry(runtime,AgentPermissions(allow_telegram_write=False)).validate_call('telegram',{'operation':'send','chat_ref':'x','text':'hi','request_key':'one'})[0]


def test_auth_secrets_vault_only_and_logout_clears(service):
    svc,client,vault=service
    svc.login({'api_id':123,'api_hash':'a'*32,'phone':'+12025550123'})
    out=svc.login({'code':'12345'})
    assert out['connected'] and vault.value['session']=='SECRET_SESSION'
    assert 'SECRET' not in json.dumps(svc.status()) and '12345' not in json.dumps(out)
    svc.disconnect(revoke=True);assert vault.value is None


def test_telegram_credentials_redacted_but_chat_identifiers_kept():
    out=redact({'api_hash':'secret-a','phone_code_hash':'secret-b','session_string':'secret-c','chat_ref':'chat_123','session_id':'trace-name'})
    assert 'secret-' not in json.dumps(out)
    assert out['chat_ref']=='chat_123' and out['session_id']=='trace-name'


@pytest.mark.parametrize('task', ['تلگرام پیام‌های علی رو بخون', 'Read my Telegram messages', 'پیام Telegram علی رو جواب بده'])
def test_telegram_family_guard(task):assert 'telegram' in SkillRegistry.hinted_families(task)


@pytest.mark.parametrize('task',['Download the Telegram voice note','تلگرام این فایل رو دانلود کن','Pin this Telegram message'])
def test_telegram_media_and_management_requests_route_to_telegram(task):assert 'telegram' in SkillRegistry.hinted_families(task)


def test_write_timeout_is_not_retried(service):
    svc,client,_=service;p=AgentPermissions()
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p,'owner')['matches'][0]['chat_ref']
    async def uncertain(*args,**kwargs):client.calls.append(('send-timeout',));raise TimeoutError('uncertain')
    client.send_message=uncertain
    args={'operation':'send','chat_ref':ref,'text':'hello','request_key':'same'}
    with pytest.raises(RuntimeError,match='unknown|uncertain'):svc.tool(args,p,'owner')
    with pytest.raises(RuntimeError,match='unknown|uncertain'):svc.tool(args,p,'owner')
    assert len([c for c in client.calls if c[0]=='send-timeout'])==1


def test_ambiguous_names_and_recent_lists_cannot_authorize_send(service):
    svc,client,_=service;p=AgentPermissions()
    matches=svc.tool({'operation':'resolve_person','query':'Ali'},p)['matches']
    assert all('chat_ref' not in m for m in matches)
    ref=svc.tool({'operation':'recent_chats'},p)['matches'][0]['chat_ref']
    with pytest.raises(ValueError,match='resolve'):
        svc.tool({'operation':'send','chat_ref':ref,'text':'hello','request_key':'one'},p)
    assert not any(c[0]=='send' for c in client.calls)


def test_cancel_during_telegram_read(service):
    import threading
    import time
    from llamaforge.core.telegram_skill import TELEGRAM_CANCEL
    svc,client,_=service;cancel=threading.Event();started=threading.Event()
    async def blocked(**kwargs):started.set();await asyncio.sleep(5)
    client.get_dialogs=blocked
    token=TELEGRAM_CANCEL.set(cancel)
    stopper=threading.Thread(target=lambda:(started.wait(1),cancel.set()));stopper.start()
    before=time.monotonic()
    try:
        with pytest.raises(RuntimeError,match='cancel'):svc.tool({'operation':'recent_chats'},AgentPermissions())
        assert time.monotonic()-before<1
    finally:TELEGRAM_CANCEL.reset(token);stopper.join()


def test_failed_connect_can_be_retried_without_stale_client(service):
    svc,client,_=service;calls=[]
    async def connect():
        calls.append('connect')
        if len(calls)==1: raise OSError('network unavailable')
    client.connect=connect
    with pytest.raises(RuntimeError):svc.tool({'operation':'recent_chats'},AgentPermissions())
    assert svc.client is None and not svc.connected
    assert svc.tool({'operation':'recent_chats'},AgentPermissions())['matches']
    assert len(calls)==2


def test_proxy_endpoint_parser_accepts_psiphon_style_local_ports():
    from llamaforge.core.telegram_skill import _parse_proxy_endpoint
    assert _parse_proxy_endpoint('127.0.0.1:1080') == ('127.0.0.1',1080)
    assert _parse_proxy_endpoint('socks5://127.0.0.1:23456') == ('127.0.0.1',23456)
    assert _parse_proxy_endpoint('http://127.0.0.1:34567') == ('127.0.0.1',34567)
    assert _parse_proxy_endpoint('broken') is None


def test_login_code_rpc_is_not_retried_and_connection_retries_stay_bounded(monkeypatch):
    from llamaforge.core.telegram_skill import TelegramService
    client=FakeClient();vault=MemoryVault();seen=[]
    def factory(runtime):
        seen.append(dict(runtime));return client
    svc=TelegramService(vault=vault,client_factory=factory)
    try:
        svc.login({'api_id':123,'api_hash':'a'*32,'phone':'+12025550123'})
        assert seen and seen[0]['_request_retries']==0 and seen[0]['_connection_retries']==3
        assert seen[0]['_raise_last_call_error'] is True
    finally:
        svc.close()


def test_saved_session_survives_service_restart_without_new_login_code():
    from llamaforge.core.telegram_skill import TelegramService
    vault=MemoryVault();vault.value={'api_id':1,'api_hash':'a'*32,'session':'SECRET_SESSION'}
    first_client=FakeClient();first=TelegramService(vault=vault,client_factory=lambda *_:first_client)
    assert first.status()['saved_session'] is True
    first.close()
    assert vault.value and vault.value['session']=='SECRET_SESSION'
    second_client=FakeClient();second=TelegramService(vault=vault,client_factory=lambda *_:second_client)
    try:
        out=second.login({'resume':True})
        assert out['connected'] is True
        assert out['saved_session'] is True
        assert not any(c[0]=='send' for c in second_client.calls)
    finally:
        second.close()


def test_read_only_telegram_dashboard_is_bounded_and_never_sends(service):
    svc,client,_=service
    overview=svc.dashboard(limit=20)
    assert overview['connected'] is True
    assert overview['saved_session'] is True
    assert 1 <= len(overview['dialogs']) <= 20
    ref=overview['dialogs'][0]['chat_ref']
    messages=svc.dashboard_messages(ref,limit=20)
    assert len(messages['messages']) <= 20
    assert messages['chat_ref'] == ref
    assert not any(c[0]=='send' for c in client.calls)


def test_login_code_has_local_duplicate_guard(service):
    svc,client,_=service
    payload={'api_id':123,'api_hash':'a'*32,'phone':'+12025550123'}
    svc.login(payload)
    first_pending=dict(svc.pending)
    with pytest.raises(RuntimeError,match='already requested|wait'):
        svc.login(payload)
    assert svc.pending == first_pending
    assert len([c for c in client.calls if c[0]=='connect']) == 1


def test_dashboard_reuses_verified_session_without_redundant_account_rpcs():
    from llamaforge.core.telegram_skill import TelegramService
    class CountingClient(FakeClient):
        def __init__(self):
            super().__init__(); self.auth_checks=0; self.me_reads=0
        async def is_user_authorized(self):
            self.auth_checks += 1
            return await super().is_user_authorized()
        async def get_me(self):
            self.me_reads += 1
            return await super().get_me()
    vault=MemoryVault(); vault.value={'api_id':1,'api_hash':'a'*32,'session':'SECRET_SESSION'}
    client=CountingClient(); svc=TelegramService(vault=vault,client_factory=lambda *_:client)
    try:
        svc.login({'resume':True})
        assert (client.auth_checks, client.me_reads) == (1, 1)
        svc.dashboard(limit=20)
        # Dashboard refresh only reads the bounded dialog page once; it does not
        # re-check authorization or re-read the account when already verified.
        assert (client.auth_checks, client.me_reads) == (1, 1)
        assert len([c for c in client.calls if c[0]=='dialogs']) == 1
    finally:
        svc.close()


def test_model_selects_from_compact_candidates_before_send(service):
    svc,client,_=service;p=AgentPermissions()
    resolved=svc.tool({'operation':'resolve_person','query':'علی'},p,'owner')
    assert resolved['selection_required'] is True
    assert 1 <= len(resolved['matches']) <= 20
    assert all(set(row).issubset({'name','username','candidate_ref'}) for row in resolved['matches'])
    assert all('chat_ref' not in row and 'id' not in row for row in resolved['matches'])
    # Persian query can locally rank an English Ali label, but the model still
    # performs the final semantic selection explicitly.
    chosen=resolved['matches'][0]
    selected=svc.tool({'operation':'select_person','candidate_ref':chosen['candidate_ref']},p,'owner')
    ref=selected['selected']['chat_ref']
    sent=svc.tool({'operation':'send','chat_ref':ref,'text':'hello'},p,'owner')
    assert sent['message_id']==12


def test_telegram_media_download_and_file_send_are_bounded_and_scoped(service,tmp_path):
    from llamaforge.core.workspace import FileWorkspace
    svc,client,_=service
    p=AgentPermissions()
    workspace=FileWorkspace(tmp_path/'workspace')
    async def get_media(peer,**kwargs):
        client.calls.append(('media-message',peer.id,kwargs))
        return NS(id=kwargs['ids'],raw_text='',out=False,date=None,media='TELEGRAM_MEDIA',file=NS(name='../voice.ogg',size=3))
    async def iter_download(media,**kwargs):
        client.calls.append(('iter-download',media,kwargs))
        yield b'abc'
    async def send_file(peer,path,**kwargs):
        client.calls.append(('send-file',peer.id,path,kwargs))
        return NS(id=33,raw_text='',out=True,date=None,media='SENT_MEDIA')
    client.get_messages=get_media
    client.iter_download=iter_download
    client.send_file=send_file
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p,'owner')['matches'][0]['chat_ref']
    result=svc.tool({'operation':'download_media','chat_ref':ref,'message_id':9},p,'owner',workspace)
    assert result['downloaded'] and result['file']['name']=='tg-9-voice.ogg'
    stored,path=workspace._resolve_id(result['file']['id'])
    assert path.read_bytes()==b'abc' and stored['source']=='telegram'
    saved=workspace.tool({'operation':'write_text','name':'outgoing.txt','text':'outbound'},allow_write=True)
    sent=svc.tool({'operation':'send_file','chat_ref':ref,'file_id':saved['id'],'caption':'note'},p,'owner',workspace)
    assert sent['sent'] and sent['message_id']==33 and sent['verification']['verified']
    assert len([row for row in client.calls if row[0]=='send-file'])==1


def test_telegram_media_permissions_and_size_are_checked_before_transfer(service,tmp_path):
    from llamaforge.core.workspace import FileWorkspace
    svc,client,_=service;p=AgentPermissions();workspace=FileWorkspace(tmp_path/'workspace')
    ref=svc.tool({'operation':'resolve_person','query':'@user1'},p,'owner')['matches'][0]['chat_ref']
    calls=[]
    async def too_big(peer,**kwargs):
        return NS(id=kwargs['ids'],raw_text='',out=False,date=None,media='MEDIA',file=NS(name='large.bin',size=20*1024*1024+1))
    async def should_not_download(*args,**kwargs):
        calls.append('download')
        yield b''
    client.get_messages=too_big;client.iter_download=should_not_download
    with pytest.raises(PermissionError,match='Workspace file access'):
        svc.tool({'operation':'download_media','chat_ref':ref,'message_id':10},AgentPermissions(allow_workspace_write=False),'owner',workspace)
    with pytest.raises(ValueError,match='20 MB'):
        svc.tool({'operation':'download_media','chat_ref':ref,'message_id':10},p,'owner',workspace)
    assert calls==[]


def test_telegram_media_contracts_are_not_read_only():
    assert operation_policy('telegram',{'operation':'download_media'}).effect=='local_write'
    assert operation_policy('telegram',{'operation':'send_file'}).effect=='external_write'
    assert not operation_policy('telegram',{'operation':'send_file'}).parallel_safe


def test_telegram_classifies_broadcast_channels_separately_from_supergroups():
    from llamaforge.core.telegram_skill import TelegramService
    broadcast=TelegramService._person(NS(id=1,title='News',username='news',bot=False,broadcast=True,megagroup=False))
    group=TelegramService._person(NS(id=2,title='Chat',username='chat',bot=False,broadcast=False,megagroup=True))
    assert broadcast['kind']=='channel' and group['kind']=='group'


def test_telegram_group_details_and_participants_are_readable_but_bounded(service):
    svc,client,_=service
    client.peers=[NS(id=77,title='Project group',username='project_group',bot=False,broadcast=False,megagroup=True)]
    async def get_participants(peer,limit):
        client.calls.append(('participants',peer.id,limit))
        return [NS(id=5,first_name='Member',last_name='',username='member5',bot=False)]
    client.get_participants=get_participants
    p=AgentPermissions();ref=svc.tool({'operation':'recent_chats','kind':'group'},p,'owner')['matches'][0]['chat_ref']
    info=svc.tool({'operation':'chat_info','chat_ref':ref},p,'owner')
    members=svc.tool({'operation':'participants','chat_ref':ref,'limit':10},p,'owner')
    assert info['kind']=='group' and members['participants'][0]['username']=='member5'
    assert client.calls[-1]==('participants',77,10)


def test_telegram_reaction_uses_supported_emoji_request(monkeypatch):
    from types import ModuleType
    from llamaforge.core.telegram_skill import TelegramService
    telethon=ModuleType('telethon')
    telethon.functions=NS(messages=NS(SendReactionRequest=lambda **kw:NS(request=kw)))
    telethon.types=NS(ReactionEmoji=lambda **kw:NS(reaction=kw))
    monkeypatch.setitem(__import__('sys').modules,'telethon',telethon)
    class ReactionClient(FakeClient):
        async def get_input_entity(self,peer):return peer
        async def __call__(self,request):
            self.calls.append(('reaction',request.request))
            return NS(ok=True)
    client=ReactionClient();vault=MemoryVault();vault.value={'api_id':1,'api_hash':'a'*32,'session':'SECRET_SESSION'}
    svc=TelegramService(vault=vault,client_factory=lambda *_:client)
    try:
        ref=svc.tool({'operation':'resolve_person','query':'@user1'},AgentPermissions(),'owner')['matches'][0]['chat_ref']
        result=svc.tool({'operation':'react','chat_ref':ref,'message_id':4,'reaction':'👍'},AgentPermissions(),'owner')
        assert result['reacted'] and client.calls[-1][0]=='reaction'
        assert client.calls[-1][1]['msg_id']==4 and client.calls[-1][1]['reaction'][0].reaction['emoticon']=='👍'
    finally:svc.close()


def test_resolve_person_candidate_window_is_capped_at_twenty():
    from llamaforge.core.telegram_skill import TelegramService
    client=FakeClient()
    client.peers=[NS(id=i,first_name=(f'⭐ Ali {i}' if i%2 else f'Contact {i}'),last_name='',username=f'user{i}',title=None,bot=False) for i in range(1,45)]
    vault=MemoryVault();vault.value={'api_id':1,'api_hash':'a'*32,'session':'SECRET_SESSION'}
    svc=TelegramService(vault=vault,client_factory=lambda *_:client)
    try:
        out=svc.tool({'operation':'resolve_person','query':'علی'},AgentPermissions(),'owner')
        assert len(out['matches'])==20
        assert out['truncated'] is True
        assert any('Ali' in row['name'] for row in out['matches'][:5])
    finally:
        svc.close()


def test_psiphon_detected_does_not_fallback_to_direct_telegram(monkeypatch):
    from llamaforge.core import telegram_skill as tg
    from llamaforge.core.telegram_skill import TelegramService
    seen=[]
    class FailingClient(FakeClient):
        async def connect(self):
            seen.append('connect')
            raise OSError('proxy failed')
    proxy={'proxy_type':'socks5','addr':'127.0.0.1','port':34567,'rdns':True,'_source':'Windows system proxy'}
    monkeypatch.setattr(tg,'_proxy_candidates',lambda:[proxy])
    monkeypatch.setattr(tg.importlib.util,'find_spec',lambda name: object() if name=='python_socks' else None)
    built=[]
    def factory(runtime):
        built.append(runtime.get('_proxy'))
        return FailingClient()
    svc=TelegramService(vault=MemoryVault(),client_factory=factory)
    try:
        with pytest.raises(RuntimeError,match='through socks5'):
            svc._submit(svc._connect_new_client({'api_id':1,'api_hash':'a'*32,'session':''}))
        assert built and all(row is not None for row in built)
        assert len(built)==1
    finally:
        svc.close()


def test_disconnect_pause_survives_restart_until_explicit_resume():
    from llamaforge.core.telegram_skill import TelegramService
    vault=MemoryVault(); vault.value={'api_id':1,'api_hash':'a'*32,'session':'SECRET_SESSION'}
    first_client=FakeClient(); first=TelegramService(vault=vault,client_factory=lambda *_:first_client)
    try:
        assert first.login({'resume':True})['connected'] is True
        out=first.disconnect(revoke=False)
        assert out['connected'] is False and out['paused'] is True and out['saved_session'] is True
        assert vault.value['paused'] is True
    finally:
        first.close()

    second_client=FakeClient(); second=TelegramService(vault=vault,client_factory=lambda *_:second_client)
    try:
        state=second.status()
        assert state['connected'] is False and state['paused'] is True and state['saved_session'] is True
        assert not any(call[0]=='connect' for call in second_client.calls)
        with pytest.raises(RuntimeError,match='Reconnect saved session'):
            second.tool({'operation':'recent_chats'},AgentPermissions())
        resumed=second.login({'resume':True})
        assert resumed['connected'] is True and resumed['paused'] is False
        assert vault.value['paused'] is False
    finally:
        second.close()


def test_stale_connected_flag_is_reconciled_and_transport_reconnects():
    from llamaforge.core.telegram_skill import TelegramService
    class TransportClient(FakeClient):
        def __init__(self):
            super().__init__(); self.transport=False; self.connects=0
        async def connect(self):
            self.connects += 1; self.transport=True; self.calls.append(('connect',))
        async def disconnect(self):
            self.transport=False; self.calls.append(('disconnect',))
        def is_connected(self):
            return self.transport
    vault=MemoryVault(); vault.value={'api_id':1,'api_hash':'a'*32,'session':'SECRET_SESSION'}
    client=TransportClient(); svc=TelegramService(vault=vault,client_factory=lambda *_:client)
    try:
        assert svc.login({'resume':True})['connected'] is True
        assert client.connects == 1
        client.transport=False  # simulate VPN/proxy/socket drop outside LlamaForge
        state=svc.status()
        assert state['connected'] is False
        out=svc.login({'resume':True})
        assert out['connected'] is True
        assert client.connects == 2
    finally:
        svc.close()
