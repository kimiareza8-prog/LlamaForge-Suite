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
    assert {d['function']['name'] for d in runtime.tool_definitions(only)}=={'telegram'}
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
