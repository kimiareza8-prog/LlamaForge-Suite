"""Bounded personal-account Telegram adapter. Credentials never enter tool schemas.

One optional Telethon client/event loop is shared by requests. No inbox listener,
history cache, extra LLM, automatic media download or automatic reply is started.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
from contextvars import ContextVar
import hashlib
import importlib.util
import json
import re
import threading
import time
import uuid

TELEGRAM_CANCEL = ContextVar('telegram_cancel', default=None)
TELEGRAM_TURN = ContextVar('telegram_turn', default='')

READS = {'status', 'recent_chats', 'resolve_person', 'messages', 'my_messages', 'search'}
WRITES = {'send', 'reply'}
SCHEMA = {'type':'object', 'required':['operation'], 'additionalProperties':False, 'properties':{
    'operation':{'type':'string','enum':sorted(READS | WRITES)},
    'query':{'type':'string','description':'Name or exact @username for resolve_person; phrase for search'},
    'chat_ref':{'type':'string','description':'Opaque reference returned by resolve_person/recent_chats; never invent one'},
    'limit':{'type':'integer','minimum':1,'maximum':15,'description':'Default 5; history only for the chosen chat'},
    'text':{'type':'string','description':'Plain message text, maximum 4096 characters'},
    'message_id':{'type':'integer','minimum':1,'description':'Required for reply; from the selected chat'},
    'request_key':{'type':'string','description':'Optional compatibility key; the runtime deduplicates sends within the current user request'},
}}


class CredentialVault:
    """OS credential storage only; never silently fall back to plaintext."""
    def backend(self):
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if module not in {'keyring.backends.Windows', 'keyring.backends.macOS', 'keyring.backends.SecretService', 'keyring.backends.kwallet'}:
            raise RuntimeError('Telegram requires an OS credential vault (Windows Credential Manager, Keychain or Secret Service)')
        return backend

    def load(self):
        raw = self.backend().get_password('LlamaForge.Telegram', 'personal-account')
        return json.loads(raw) if raw else None

    def save(self, value):
        self.backend().set_password('LlamaForge.Telegram', 'personal-account', json.dumps(value))

    def clear(self):
        backend = self.backend()
        if backend.get_password('LlamaForge.Telegram', 'personal-account'):
            backend.delete_password('LlamaForge.Telegram', 'personal-account')


def _client(credentials):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(StringSession(credentials.get('session', '')), int(credentials['api_id']), credentials['api_hash'],
                          receive_updates=False, request_retries=0, connection_retries=1,
                          flood_sleep_threshold=0, timeout=10, device_model='LlamaForge', app_version='0.34.3')


class TelegramService:
    def __init__(self, *, vault=None, client_factory=None):
        self.vault = vault or CredentialVault()
        self.client_factory = client_factory or _client
        self.client = None
        self.credentials = None
        self.pending = None
        self.connected = False
        self.account = {}
        self.refs = {}
        self.receipts = {}
        self._loop = None
        self._thread = None
        self._start_lock = threading.Lock()
        self._serial = None
        self._paused = False
        self._cooldown_until = 0.0

    def status(self):
        return {'installed':bool(importlib.util.find_spec('telethon')),
                'vault_installed':bool(importlib.util.find_spec('keyring')),
                'connected':self.connected, 'account':dict(self.account),
                'login_pending':bool(self.pending), 'paused':self._paused,
                'operations':sorted(READS | WRITES), 'context_messages':5, 'context_limit':15}

    def _submit(self, coroutine):
        cancel = TELEGRAM_CANCEL.get()
        if cancel is not None and cancel.is_set():
            coroutine.close()
            raise RuntimeError('Telegram cancelled before execution')
        with self._start_lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._loop.run_forever, name='telegram-account', daemon=True)
                self._thread.start()
        async def bounded():
            if self._serial is None: self._serial = asyncio.Lock()
            try:
                async with self._serial:
                    return await asyncio.wait_for(coroutine, timeout=30)
            finally:
                coroutine.close()
        future = asyncio.run_coroutine_threadsafe(bounded(), self._loop)
        try:
            deadline = time.monotonic() + 35
            while True:
                cancel = TELEGRAM_CANCEL.get()
                if cancel is not None and cancel.is_set():
                    future.cancel()
                    raise RuntimeError('Telegram cancelled; verify any in-flight send before retrying')
                try: return future.result(timeout=0.1)
                except concurrent.futures.TimeoutError:
                    if future.done() or time.monotonic() >= deadline: raise
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError('Telegram timed out; delivery may be unknown. Do not blindly resend.') from None
        except (ValueError, PermissionError, RuntimeError):
            raise
        except Exception as exc:
            # SDK errors can include request objects. Export only type/retry time.
            seconds = getattr(exc, 'seconds', None)
            if seconds:
                self._cooldown_until = time.monotonic() + int(seconds)
                raise RuntimeError(f'Telegram rate limit; retry after {int(seconds)} seconds') from None
            raise RuntimeError('Telegram operation failed: ' + type(exc).__name__) from None

    async def _ensure_client(self):
        if self._paused: raise RuntimeError('Telegram is disconnected; use Resume in Agent settings')
        if time.monotonic() < self._cooldown_until: raise RuntimeError('Telegram rate limit cooldown is active')
        if self.client is None:
            self.credentials = self.vault.load()
            if not self.credentials: raise RuntimeError('Connect your Telegram account in Agent settings first')
            self.client = self.client_factory(self.credentials)
            try:
                await self.client.connect()
            except BaseException:
                failed, self.client = self.client, None
                self.connected = False
                try: await failed.disconnect()
                except Exception: pass
                raise
        if not await self.client.is_user_authorized():
            self.connected = False
            raise RuntimeError('Telegram session needs login in Agent settings')
        self.connected = True
        return self.client

    def login(self, payload):
        return self._submit(self._login(payload))

    async def _login(self, payload):
        if payload.get('resume'):
            self._paused = False
            client = await self._ensure_client()
            self.account = self._person(await client.get_me())
            return self.status()
        if payload.get('api_id'):
            if self.connected: raise ValueError('Disconnect the current account before connecting another')
            api_id = int(payload['api_id']); api_hash = str(payload.get('api_hash') or '').strip()
            phone = str(payload.get('phone') or '').strip()
            if api_id <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash) or not re.fullmatch(r'\+[0-9]{7,16}', phone):
                raise ValueError('Enter a valid API ID, API Hash and phone number with country code')
            self.vault.load()  # Verify vault access before requesting a code.
            # A failed second login must not reuse the previous phone/code hash.
            self.pending = None
            if self.client: await self.client.disconnect()
            self.refs.clear(); self.receipts.clear(); self._paused = False
            self.credentials = {'api_id':api_id, 'api_hash':api_hash, 'session':''}
            self.client = self.client_factory(self.credentials)
            await self.client.connect()
            sent = await self.client.send_code_request(phone)
            self.pending = {'phone':phone, 'phone_code_hash':sent.phone_code_hash, 'expires':time.monotonic()+300}
            return {'connected':False, 'login_pending':True, 'next':'code'}
        if not self.pending or self.pending['expires'] < time.monotonic():
            self.pending = None
            raise ValueError('Login code expired or not requested; request a new code')
        try:
            if payload.get('password'):
                await self.client.sign_in(password=str(payload['password']))
            else:
                await self.client.sign_in(phone=self.pending['phone'], code=str(payload.get('code') or ''),
                                          phone_code_hash=self.pending['phone_code_hash'])
        except Exception as exc:
            if type(exc).__name__ == 'SessionPasswordNeededError':
                return {'connected':False, 'login_pending':True, 'next':'password'}
            raise
        self.credentials['session'] = self.client.session.save()
        self.vault.save(self.credentials)
        self.connected = True; self.pending = None
        self.account = self._person(await self.client.get_me())
        return self.status()

    def disconnect(self, *, revoke=False):
        return self._submit(self._disconnect(revoke))

    async def _disconnect(self, revoke):
        try:
            if revoke:
                self._paused = False
                client = await self._ensure_client()
                if not await client.log_out(): raise RuntimeError('Telegram did not confirm session revocation')
                self.vault.clear()
        finally:
            client, self.client = self.client, None
            self.credentials = None; self.pending = None
            self.connected = False; self._paused = True; self.account = {}
            self.refs.clear(); self.receipts.clear()
            if client: await client.disconnect()
        return self.status()

    @staticmethod
    def _person(entity):
        return {'id':entity.id, 'name':str(getattr(entity,'title',None) or ' '.join(filter(None,[getattr(entity,'first_name',''),getattr(entity,'last_name','')]))),
                'username':getattr(entity,'username',None)}

    def _reference(self, entity, scope, resolved=False):
        now = time.monotonic()
        self.refs = {k:v for k,v in self.refs.items() if v[2] > now}
        for key,(peer,owner,expiry,was_resolved) in self.refs.items():
            if type(peer) is type(entity) and peer.id == entity.id and owner == scope:
                self.refs[key] = (peer,owner,expiry,was_resolved or resolved)
                return key
        while len(self.refs) >= 200: self.refs.pop(next(iter(self.refs)))
        key = 'chat_' + uuid.uuid4().hex
        self.refs[key] = (entity, scope, now+900, resolved)
        return key

    def tool(self, args, permissions, scope='local'):
        op = args.get('operation')
        if op not in READS | WRITES: raise ValueError('Unknown Telegram operation')
        if op in READS and not permissions.allow_telegram_read: raise PermissionError('Telegram reads are disabled')
        if op in WRITES and not permissions.allow_telegram_write: raise PermissionError('Telegram messages are disabled')
        if op == 'status': return self.status()
        return self._submit(self._tool(args, scope))

    async def _tool(self, args, scope):
        op = args['operation']; limit = int(args.get('limit') or 5)
        if not 1 <= limit <= 15: raise ValueError('Telegram context limit must be 1..15')
        client = await self._ensure_client()
        if op in {'resolve_person','recent_chats'}:
            query = str(args.get('query') or '').strip()
            if op == 'resolve_person' and not query: raise ValueError('query is required')
            if len(query) > 200: raise ValueError('query is too long')
            if op == 'resolve_person' and re.fullmatch(r'@[A-Za-z0-9_]{5,32}',query):
                peers = [await client.get_entity(query)]
            else:
                dialogs = await client.get_dialogs(limit=100 if op=='resolve_person' else limit)
                peers = [d.entity for d in dialogs if op=='recent_chats' or query.casefold() in self._person(d.entity)['name'].casefold()]
            ambiguous = op=='resolve_person' and len(peers)>1
            rows = [{**self._person(e), **({} if ambiguous else {'chat_ref':self._reference(e,scope,op=='resolve_person')})} for e in peers[:15]]
            return {'matches':rows, 'ambiguous':op=='resolve_person' and len(peers)>1,
                    'truncated':len(peers)>15, 'history_read':False, 'search_scope':'up to 100 recent dialogs or exact @username'}
        row = self.refs.get(str(args.get('chat_ref') or ''))
        if not row or row[1] != scope or row[2] < time.monotonic(): raise ValueError('Unknown, expired or different-scope chat_ref; resolve the person first')
        peer = row[0]
        if op in WRITES:
            if not row[3]: raise ValueError('Uniquely resolve_person before sending; recent chat listings only permit reading')
            return await self._send(client, peer, args, scope)
        kwargs = {'limit':limit}
        if op == 'my_messages': kwargs['from_user'] = 'me'
        if op == 'search':
            if not str(args.get('query') or '').strip(): raise ValueError('query is required for search')
            kwargs['search'] = str(args['query'])[:200]
        messages = await client.get_messages(peer, **kwargs)
        rows=[]; budget=10000; truncated=False
        for msg in messages:
            text = str(getattr(msg,'raw_text','') or '')
            content = text[:min(2000,budget)]
            truncated |= len(content)<len(text)
            rows.append({'message_id':msg.id, 'text':content, 'outgoing':bool(msg.out),
                         'date':msg.date.isoformat() if msg.date else None, 'has_media':bool(msg.media)})
            budget -= len(content)
            if budget <= 0:
                truncated |= len(rows)<len(messages); break
        return {'chat_ref':args['chat_ref'],'messages':rows,'truncated':truncated,'max_characters':10000}

    async def _send(self, client, peer, args, scope):
        text = str(args.get('text') or ''); key = str(args.get('request_key') or '')
        if not text.strip() or len(text)>4096: raise ValueError('text must contain 1..4096 characters')
        if len(key)>128: raise ValueError('request_key maximum is 128 characters')
        reply = args.get('message_id') if args['operation']=='reply' else None
        if args['operation']=='reply' and (not isinstance(reply,int) or isinstance(reply,bool) or reply<=0): raise ValueError('reply requires a message_id from this chat')
        # The model may change or reuse its key. Bind deduplication to the real
        # user turn and typed peer instead; a later user turn may repeat a send.
        signature = hashlib.sha256(json.dumps([scope,type(peer).__name__,peer.id,text,reply],ensure_ascii=False).encode()).hexdigest()
        turn = TELEGRAM_TURN.get()
        receipt_key = (scope,turn,signature) if turn else (scope,key or signature)
        previous = self.receipts.get(receipt_key)
        if previous:
            if previous['signature']!=signature: raise ValueError('request_key already belongs to another message')
            if previous.get('result'): return previous['result']
            raise RuntimeError('Previous delivery is unknown; inspect the chat before another send')
        if len(self.receipts)>=1000: raise RuntimeError('Telegram receipt limit reached; reconnect before starting new sends')
        self.receipts[receipt_key] = {'signature':signature}
        try:
            sent = await client.send_message(peer,text,reply_to=reply,parse_mode=None,link_preview=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if type(exc).__name__ in {'FloodWaitError', 'SlowModeWaitError'} and getattr(exc,'seconds',None):
                # Telegram explicitly rejected this RPC. Preserve the SDK wait
                # information so subsequent reads/writes respect the cooldown.
                self.receipts.pop(receipt_key, None)
                raise
            # Keep the uncertain receipt: a socket timeout is not proof of failure.
            raise RuntimeError('Telegram delivery is unknown; inspect the chat, do not resend blindly') from None
        result = {'chat_ref':args['chat_ref'],'message_id':sent.id,'verification':{'verified':False,'method':'message ID readback'}}
        self.receipts[receipt_key]['result'] = result
        try:
            check = await client.get_messages(peer,ids=sent.id)
            result['verification']['verified'] = bool(check and check.id==sent.id and check.raw_text==text)
        except Exception:
            result['verification']['detail'] = 'Telegram acknowledged the ID; readback unavailable. Do not resend.'
        return result

    def close(self):
        if self._loop is not None:
            try: self.disconnect()
            except Exception: pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
            if not self._thread.is_alive(): self._loop.close()
            self._loop = None
            self._serial = None
