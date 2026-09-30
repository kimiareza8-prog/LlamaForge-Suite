"""Bounded personal-account Telegram adapter. Credentials never enter tool schemas.

One optional Telethon client/event loop is shared by requests. A lightweight incoming-message
listener can publish bounded events to the Automation Engine; it never starts an extra LLM or
automatic reply by itself. History cache and automatic media download are not started.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import difflib
from contextvars import ContextVar
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import sys
from pathlib import Path


TELEGRAM_VENDOR_DIR = Path(__file__).resolve().parents[2] / '.telegram-deps'
if TELEGRAM_VENDOR_DIR.is_dir():
    _vendor = str(TELEGRAM_VENDOR_DIR)
    if _vendor not in sys.path:
        sys.path.insert(0, _vendor)

TELEGRAM_CANCEL = ContextVar('telegram_cancel', default=None)
TELEGRAM_TURN = ContextVar('telegram_turn', default='')

READS = {'status', 'account_info', 'recent_chats', 'list_channels', 'list_bots', 'channel_info', 'bot_info', 'chat_info', 'participants', 'resolve_person', 'select_person', 'messages', 'my_messages', 'search', 'global_search', 'download_media'}
WRITES = {'send', 'reply', 'forward', 'edit', 'delete_message', 'pin', 'unpin', 'mark_read', 'react', 'send_file'}
MAX_TELEGRAM_MEDIA_BYTES = 20 * 1024 * 1024
TELEGRAM_MEDIA_CHUNK_BYTES = 512 * 1024
SCHEMA = {'type':'object', 'required':['operation'], 'additionalProperties':False, 'properties':{
    'operation':{'type':'string','enum':sorted(READS | WRITES)},
    'query':{'type':'string','description':'Name/nickname/@username for resolve_person or channel_info/bot_info; phrase for search/global_search'},
    'candidate_ref':{'type':'string','description':'Opaque candidate returned by resolve_person; use select_person before send/reply'},
    'chat_ref':{'type':'string','description':'Opaque reference returned by resolve_person/recent_chats; never invent one'},
    'to_chat_ref':{'type':'string','description':'Resolved and explicitly selected destination chat for forward'},
    'source_chat_ref':{'type':'string','description':'Source chat for forward'},
    'file_id':{'type':'string','description':'File Manager ID for send_file'},
    'kind':{'type':'string','enum':['all','private','group','channel','bot'],'description':'Optional recent dialog filter'},
    'limit':{'type':'integer','minimum':1,'maximum':100,'description':'List/search maximum; message context is still capped at 15'},
    'text':{'type':'string','description':'Plain message text, maximum 4096 characters; edit only edits your own message'},
    'message_id':{'type':'integer','minimum':1,'description':'Telegram message ID in the selected chat'},
    'caption':{'type':'string','description':'Optional caption for send_file (maximum 1024 characters)'},
    'reaction':{'type':'string','description':'One Telegram emoji reaction'},
    'request_key':{'type':'string','description':'Optional compatibility key; the runtime deduplicates sends within the current user request'},
}}


_PERSIAN_LATIN = str.maketrans({
    'آ':'a','ا':'a','أ':'a','إ':'a','ع':'a','ب':'b','پ':'p','ت':'t','ث':'s','ج':'j','چ':'ch',
    'ح':'h','خ':'kh','د':'d','ذ':'z','ر':'r','ز':'z','ژ':'zh','س':'s','ش':'sh','ص':'s','ض':'z',
    'ط':'t','ظ':'z','غ':'gh','ف':'f','ق':'gh','ك':'k','ک':'k','گ':'g','ل':'l','م':'m','ن':'n',
    'و':'o','ؤ':'o','ه':'h','ة':'h','ي':'i','ى':'i','ی':'i','ئ':'i','ء':'',
})


def _compact_name(value: str) -> str:
    """Normalize human labels for local ranking only; original labels go to the model."""
    text = str(value or '').casefold().replace('ك','ک').replace('ي','ی')
    # Emoji/punctuation/decorative symbols disappear from the ranking key without
    # altering what the user/model sees.
    return ''.join(ch for ch in text if ch.isalnum())


def _latin_key(value: str) -> str:
    return _compact_name(str(value or '').translate(_PERSIAN_LATIN))


def _candidate_score(query: str, person: dict, recency_index: int) -> tuple[float, int]:
    q = _compact_name(query.lstrip('@'))
    qlatin = _latin_key(query.lstrip('@'))
    name = _compact_name(person.get('name') or '')
    username = _compact_name(person.get('username') or '')
    name_latin = _latin_key(person.get('name') or '')
    values = [v for v in (name, username, name_latin) if v]
    score = 0.0
    for value in values:
        if q and value == q: score = max(score, 100.0)
        if q and (q in value or value in q): score = max(score, 88.0)
        if qlatin and value == qlatin: score = max(score, 98.0)
        if qlatin and (qlatin in value or value in qlatin): score = max(score, 84.0)
        if q: score = max(score, difflib.SequenceMatcher(None, q, value).ratio() * 70.0)
        if qlatin: score = max(score, difflib.SequenceMatcher(None, qlatin, value).ratio() * 78.0)
    # Recency is only a tie/fallback signal. It keeps useful recent contacts in the
    # 20-candidate window when spelling/language/emoji prevents deterministic match.
    score += max(0.0, 12.0 - min(recency_index, 99) * 0.12)
    return score, -recency_index


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
    proxy = credentials.get('_proxy')
    if isinstance(proxy, dict):
        # Internal metadata is useful to the UI but must never be passed to python-socks.
        proxy = {k: v for k, v in proxy.items() if not str(k).startswith('_')}
    return TelegramClient(
        StringSession(credentials.get('session', '')), int(credentials['api_id']), credentials['api_hash'],
        receive_updates=bool(credentials.get('_receive_updates', False)),
        request_retries=int(credentials.get('_request_retries', 0)),
        connection_retries=int(credentials.get('_connection_retries', 1)),
        retry_delay=float(credentials.get('_retry_delay', 1)),
        raise_last_call_error=bool(credentials.get('_raise_last_call_error', True)),
        flood_sleep_threshold=0, timeout=float(credentials.get('_timeout', 10)), proxy=proxy,
        device_model='LlamaForge', app_version='0.36.2',
    )


def _parse_proxy_endpoint(value):
    value = str(value or '').strip()
    if not value:
        return None
    if '://' in value:
        value = value.split('://', 1)[1]
    # Psiphon's local proxy has no authentication. Ignore user-info from unrelated
    # system proxies rather than exposing it anywhere in status/diagnostics.
    if '@' in value:
        value = value.rsplit('@', 1)[1]
    if value.startswith('[') and ']:' in value:
        host, port = value[1:].split(']:', 1)
    elif ':' in value:
        host, port = value.rsplit(':', 1)
    else:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not host or not (1 <= port <= 65535):
        return None
    return host.strip(), port


def _windows_system_proxy_candidates():
    """Return current WinINet proxies, preferring SOCKS (Psiphon-friendly).

    Psiphon for Windows publishes local HTTP/HTTPS and SOCKS listeners and normally
    points Windows' System Proxy Settings at them. Ports may change each launch, so
    detection is intentionally dynamic instead of hard-coding 1080/8080.
    """
    if os.name != 'nt':
        return []
    try:
        import winreg
        path = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled = int(winreg.QueryValueEx(key, 'ProxyEnable')[0] or 0)
            server = str(winreg.QueryValueEx(key, 'ProxyServer')[0] or '').strip()
    except Exception:
        return []
    if not enabled or not server:
        return []

    entries = {}
    if '=' in server:
        for item in server.split(';'):
            if '=' not in item:
                continue
            kind, endpoint = item.split('=', 1)
            parsed = _parse_proxy_endpoint(endpoint)
            if parsed:
                entries[kind.strip().lower()] = parsed
    else:
        parsed = _parse_proxy_endpoint(server)
        if parsed:
            entries['generic'] = parsed

    result = []
    seen = set()
    for key, proxy_type in (('socks', 'socks5'), ('https', 'http'), ('http', 'http'), ('generic', 'http')):
        endpoint = entries.get(key)
        if not endpoint:
            continue
        host, port = endpoint
        sig = (proxy_type, host.lower(), port)
        if sig in seen:
            continue
        seen.add(sig)
        result.append({
            'proxy_type': proxy_type, 'addr': host, 'port': port, 'rdns': True,
            '_source': 'Windows system proxy',
        })
    return result


def _environment_proxy_candidates():
    result = []
    seen = set()
    for name in ('ALL_PROXY', 'all_proxy', 'HTTPS_PROXY', 'https_proxy'):
        value = str(os.environ.get(name) or '').strip()
        if not value:
            continue
        low = value.lower()
        kind = 'socks5' if low.startswith(('socks5://', 'socks5h://')) else 'http' if low.startswith(('http://', 'https://')) else None
        parsed = _parse_proxy_endpoint(value)
        if not kind or not parsed:
            continue
        host, port = parsed
        sig = (kind, host.lower(), port)
        if sig in seen:
            continue
        seen.add(sig)
        result.append({'proxy_type':kind, 'addr':host, 'port':port, 'rdns':True, '_source':name})
    return result


def _proxy_candidates():
    # LlamaForge intentionally follows Windows System Proxy Settings. Psiphon
    # updates these dynamically, so stale shell environment proxies cannot hijack
    # Telegram traffic.
    rows = _windows_system_proxy_candidates()
    out, seen = [], set()
    for row in rows:
        sig = (row.get('proxy_type'), str(row.get('addr')).lower(), int(row.get('port') or 0))
        if sig in seen:
            continue
        seen.add(sig); out.append(row)
    return out


def _public_proxy(proxy):
    if not proxy:
        return {'detected': False, 'mode': 'direct'}
    return {
        'detected': True, 'mode': 'proxy', 'type': str(proxy.get('proxy_type') or ''),
        'host': str(proxy.get('addr') or ''), 'port': int(proxy.get('port') or 0),
        'source': str(proxy.get('_source') or 'proxy'),
    }


class TelegramService:
    def __init__(self, *, vault=None, client_factory=None):
        self.vault = vault or CredentialVault()
        self.client_factory = client_factory or _client
        self.client = None
        self.credentials = None
        self.pending = None
        self.connected = False
        self.active_proxy = None
        self.account = {}
        self.refs = {}
        self.receipts = {}
        self._loop = None
        self._thread = None
        self._start_lock = threading.Lock()
        self._serial = None
        self._paused = False
        self._cooldown_until = 0.0
        self._login_code_cooldown_until = 0.0
        # None means 'not checked yet'.  Only cache the boolean, never the vault payload.
        self._saved_session = None
        # Disconnect is a user preference, not merely a live socket state.  Persist it
        # beside the encrypted StringSession so an app restart cannot silently
        # reconnect an account the user explicitly paused.
        self._vault_state_loaded = False
        self._event_callback = None
        self._live_handler_clients = set()

    def set_event_callback(self, callback):
        """Publish bounded incoming-message events without performing any reply."""
        self._event_callback = callback

    async def _install_live_handler(self, client):
        if not self._event_callback or id(client) in self._live_handler_clients:
            return
        try:
            from telethon import events
        except Exception:
            return

        async def on_new_message(event):
            try:
                message = getattr(event, 'message', None)
                if message is None or bool(getattr(message, 'out', False)):
                    return
                chat = await event.get_chat()
                sender = await event.get_sender()
                if chat is None:
                    return
                if bool(getattr(event, 'is_private', False)):
                    kind = 'bot' if bool(getattr(sender, 'bot', False)) else 'private'
                elif bool(getattr(event, 'is_channel', False)) and not bool(getattr(event, 'is_group', False)):
                    kind = 'channel'
                elif bool(getattr(event, 'is_group', False)):
                    kind = 'group'
                else:
                    kind = 'unknown'
                msg_id = int(getattr(message, 'id', 0) or 0)
                chat_id = int(getattr(chat, 'id', 0) or 0)
                sender_info = self._person(sender) if sender is not None else {}
                chat_info = self._person(chat)
                payload = {
                    'event_id': f'telegram:{chat_id}:{msg_id}',
                    'message_id': msg_id,
                    'chat_id': chat_id,
                    'chat_kind': kind,
                    'chat_name': str(chat_info.get('name') or '')[:160],
                    'chat_ref': self._reference(chat, 'local', True),
                    'text': str(getattr(message, 'raw_text', '') or '')[:4096],
                    'has_media': bool(getattr(message, 'media', None)),
                    'date': getattr(message, 'date', None).isoformat() if getattr(message, 'date', None) else None,
                    'outgoing': False,
                    'sender': {k: sender_info.get(k) for k in ('name','username','kind') if sender_info.get(k) is not None},
                }
                callback = self._event_callback
                if callback:
                    threading.Thread(target=callback, args=('telegram.message.received', payload, payload['event_id']),
                                     name='telegram-automation-event', daemon=True).start()
            except Exception:
                # Telegram event ingestion is best-effort. The account connection
                # must never be killed merely because an automation event failed.
                return

        client.add_event_handler(on_new_message, events.NewMessage(incoming=True))
        self._live_handler_clients.add(id(client))

    def _prune_receipts(self) -> None:
        """Bound delivery receipts without forcing reconnect after long sessions."""
        now = time.time()
        for key, row in list(self.receipts.items()):
            if not isinstance(row, dict):
                self.receipts.pop(key, None)
                continue
            age = now - float(row.get("created_at") or now)
            ttl = 6 * 3600 if row.get("result") is not None else 24 * 3600
            if age > ttl:
                self.receipts.pop(key, None)
        if len(self.receipts) > 900:
            safe = sorted((float(v.get("created_at") or 0), k) for k, v in self.receipts.items() if isinstance(v, dict) and v.get("result") is not None)
            for _, key in safe[:max(0, len(self.receipts) - 800)]:
                self.receipts.pop(key, None)

    def _load_vault_state(self):
        """Load saved-login metadata without contacting Telegram."""
        try:
            row = self.vault.load()
        except Exception:
            return None
        if not isinstance(row, dict):
            row = None
        if not self._vault_state_loaded:
            self._paused = bool(row.get('paused', False)) if row else False
            self._vault_state_loaded = True
        present = bool(row and row.get('session') and row.get('api_id') and row.get('api_hash'))
        self._saved_session = present
        return row

    def _saved_session_present(self):
        # This check is local-only.  It never contacts Telegram and never exposes
        # the StringSession or API credentials to the browser/UI.  A saved pause
        # state is restored at the same time so Disconnect survives app restarts.
        if self._saved_session is True and self._vault_state_loaded:
            return True
        row = self._load_vault_state()
        return bool(row and row.get('session') and row.get('api_id') and row.get('api_hash'))

    def _transport_connected(self):
        """Return the local Telethon transport state without issuing an RPC."""
        if self.client is None:
            return False
        probe = getattr(self.client, 'is_connected', None)
        if not callable(probe):
            # Lightweight test/custom clients may not implement Telethon's local
            # is_connected() helper; reaching this point still means a client exists.
            return True
        try:
            return bool(probe())
        except Exception:
            return False

    def _sync_transport_state(self):
        # `connected` means usable now, not merely "was connected once".  This is
        # intentionally a local socket check and never consumes a Telegram request.
        if self.connected and not self._transport_connected():
            self.connected = False
            self.account = {}
        return self.connected

    def status(self):
        self._load_vault_state()
        self._sync_transport_state()
        proxy_support = bool(importlib.util.find_spec('python_socks'))
        detected = _proxy_candidates()
        proxy = _public_proxy(self.active_proxy or (detected[0] if detected else None))
        proxy['support_installed'] = proxy_support
        proxy['candidate_count'] = len(detected)
        return {'installed':bool(importlib.util.find_spec('telethon')) and bool(importlib.util.find_spec('keyring')) and proxy_support,
                'telethon_installed':bool(importlib.util.find_spec('telethon')),
                'proxy_support_installed':proxy_support,
                'vault_installed':bool(importlib.util.find_spec('keyring')),
                'connected':self.connected, 'saved_session':self._saved_session_present(), 'account':dict(self.account),
                'login_pending':bool(self.pending), 'paused':self._paused, 'proxy':proxy,
                'operations':sorted(READS | WRITES), 'context_messages':5, 'context_limit':15,
                'media_limit_bytes':MAX_TELEGRAM_MEDIA_BYTES,
                'route_policy':'system_proxy_only_when_detected', 'live_events':bool(self._event_callback)}

    def _make_client(self, credentials, proxy=None, *, timeout=10, connection_retries=1, request_retries=0, retry_delay=1, receive_updates=None):
        runtime = dict(credentials)
        runtime['_proxy'] = proxy
        runtime['_timeout'] = timeout
        runtime['_connection_retries'] = connection_retries
        runtime['_request_retries'] = request_retries
        runtime['_retry_delay'] = retry_delay
        runtime['_raise_last_call_error'] = True
        runtime['_receive_updates'] = bool(self._event_callback) if receive_updates is None else bool(receive_updates)
        return self.client_factory(runtime)

    async def _connect_new_client(self, credentials, *, auth=False):
        candidates = _proxy_candidates()
        if candidates and not importlib.util.find_spec('python_socks'):
            raise RuntimeError('A system proxy/Psiphon was detected but python-socks is missing. Click Install Telegram support, then try again.')
        # Strict isolation: when Psiphon/System Proxy is detected, Telegram uses
        # only that tunnel. Do not silently leak Telegram traffic onto the device
        # direct route. Direct mode is used only when no Telegram proxy exists.
        attempts = list(candidates) if candidates else [None]
        errors = []
        for proxy in attempts:
            # Never automatically replay Telegram RPCs.  That is especially
            # important for auth.SendCode: a network error does not prove Telegram
            # did not receive the request, and blind retries can generate duplicate
            # login-code requests.  Connection establishment itself may retry a few
            # times because no account action has been sent yet.
            candidate = self._make_client(
                credentials, proxy,
                timeout=15 if auth else 10,
                connection_retries=3 if auth else 1,
                request_retries=0,
                retry_delay=1,
                receive_updates=bool(self._event_callback) and not auth,
            )
            try:
                await candidate.connect()
                if not auth:
                    await self._install_live_handler(candidate)
                self.active_proxy = proxy
                return candidate
            except BaseException as exc:
                errors.append((proxy, type(exc).__name__))
                try:
                    await candidate.disconnect()
                except Exception:
                    pass
        self.active_proxy = None
        if candidates:
            p = _public_proxy(candidates[0])
            raise RuntimeError(
                f"Telegram connection failed through {p['type']} {p['host']}:{p['port']} and directly. "
                'Keep Psiphon connected and press Ping Telegram to diagnose the tunnel.'
            )
        raise RuntimeError('Telegram connection failed directly. If Telegram is blocked on this network, connect Psiphon/VPN and press Ping Telegram.')

    def ensure_live_async(self):
        """Connect a saved, non-paused session so live Automation events can flow."""
        if not self._event_callback or self._paused or not self._saved_session_present():
            return False
        def work():
            try:
                self._submit(self._ensure_client(), timeout=45)
            except Exception:
                pass
        threading.Thread(target=work, name='telegram-live-connect', daemon=True).start()
        return True

    def ping(self, payload=None):
        return self._submit(self._ping(payload or {}), timeout=60)

    async def _ping(self, payload):
        if not importlib.util.find_spec('telethon'):
            return {'ok':False, 'message':'Telethon is not installed. Install Telegram support first.', 'tests':[]}
        candidates = _proxy_candidates()
        if candidates and not importlib.util.find_spec('python_socks'):
            return {'ok':False, 'message':'Proxy detected, but python-socks is missing. Reinstall Telegram support.',
                    'proxy':_public_proxy(candidates[0]), 'tests':[]}

        # A plain connect() only proves that the MTProto transport can be opened.
        # When API credentials are present in the local form, also run help.getConfig:
        # Telegram documents it as safe on an unauthenticated connection, and it
        # verifies a real RPC round-trip without sending a login code or message.
        raw_id = payload.get('api_id')
        raw_hash = str(payload.get('api_hash') or '').strip()
        try:
            api_id = int(raw_id) if raw_id not in (None, '') else 0
        except (TypeError, ValueError):
            api_id = 0
        full_rpc = api_id > 0 and bool(re.fullmatch(r'[a-fA-F0-9]{32}', raw_hash))
        probe_credentials = {'api_id':api_id, 'api_hash':raw_hash, 'session':''} if full_rpc else {'api_id':1, 'api_hash':'0'*32, 'session':''}

        # Same strict routing policy as real Telegram traffic: with a detected
        # Psiphon/System Proxy, Ping never performs a second direct Telegram probe.
        probes = [(p, 'proxy') for p in candidates[:2]] if candidates else [(None, 'direct')]
        tests = []
        for proxy, label in probes:
            started = time.monotonic()
            c = self._make_client(
                probe_credentials, proxy, timeout=8, connection_retries=1,
                request_retries=0, retry_delay=1, receive_updates=False,
            )
            row = {'mode':label, 'ok':False, 'transport_ok':False, 'rpc_tested':full_rpc, 'rpc_ok':False}
            if proxy: row['proxy'] = _public_proxy(proxy)
            try:
                await c.connect()
                row['transport_ok'] = True
                if full_rpc:
                    from telethon import functions
                    config = await asyncio.wait_for(c(functions.help.GetConfigRequest()), timeout=20)
                    row['rpc_ok'] = True
                    row['dc_id'] = int(getattr(config, 'this_dc', 0) or 0)
                row['ok'] = row['rpc_ok'] if full_rpc else True
            except BaseException as exc:
                row['error'] = type(exc).__name__
            finally:
                row['latency_ms'] = max(1, int((time.monotonic() - started) * 1000))
                tests.append(row)
                try: await c.disconnect()
                except Exception: pass

        good_proxy = next((x for x in tests if x['mode']=='proxy' and x['ok']), None)
        good_direct = next((x for x in tests if x['mode']=='direct' and x['ok']), None)
        transport_proxy = next((x for x in tests if x['mode']=='proxy' and x['transport_ok']), None)
        transport_direct = next((x for x in tests if x['mode']=='direct' and x['transport_ok']), None)
        ok = bool(good_proxy or good_direct)
        if good_proxy:
            p = good_proxy['proxy']
            prefix = 'Telegram RPC reachable' if full_rpc else 'Telegram transport reachable'
            message = f"{prefix} via {p['type']} {p['host']}:{p['port']} ({good_proxy['latency_ms']} ms)."
        elif good_direct:
            prefix = 'Telegram RPC reachable' if full_rpc else 'Telegram transport reachable'
            message = f"{prefix} directly ({good_direct['latency_ms']} ms)."
        elif full_rpc and (transport_proxy or transport_direct):
            row = transport_proxy or transport_direct
            route = ''
            if transport_proxy:
                p = transport_proxy['proxy']; route = f" through {p['type']} {p['host']}:{p['port']}"
            message = f"Telegram transport connects{route}, but the RPC test failed ({row.get('error') or 'unknown error'})."
        elif candidates:
            p = _public_proxy(candidates[0]); message = f"Telegram unreachable. Proxy detected at {p['host']}:{p['port']}, but its MTProto transport probe failed."
        else:
            message = 'Telegram unreachable and no Windows/system proxy was detected. Connect Psiphon and try again.'
        if not full_rpc and ok:
            message += ' Enter API ID and API Hash to let Ping test a real Telegram RPC round-trip too.'
        return {'ok':ok, 'message':message, 'proxy':_public_proxy(candidates[0] if candidates else None), 'tests':tests, 'rpc_tested':full_rpc}

    def _submit(self, coroutine, *, timeout=30):
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
                    return await asyncio.wait_for(coroutine, timeout=timeout)
            finally:
                coroutine.close()
        future = asyncio.run_coroutine_threadsafe(bounded(), self._loop)
        try:
            deadline = time.monotonic() + timeout + 5
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
        if not self._vault_state_loaded:
            self._load_vault_state()
        if self._paused: raise RuntimeError('Telegram is disconnected; use Reconnect saved session')
        if time.monotonic() < self._cooldown_until: raise RuntimeError('Telegram rate limit cooldown is active')
        # `connected` used to be a sticky memory flag.  If Telethon's socket dropped,
        # discard that stale client and rebuild it from the encrypted saved session
        # instead of forcing the user through Disconnect -> Reconnect manually.
        if self.client is not None and self.connected and self._transport_connected():
            return self.client
        if self.client is not None and not self._transport_connected():
            stale, self.client = self.client, None
            self.connected = False
            self.account = {}
            try:
                await stale.disconnect()
            except Exception:
                pass
        if self.client is None:
            self.credentials = self._load_vault_state()
            if not self.credentials: raise RuntimeError('Connect your Telegram account in Agent settings first')
            try:
                self.client = await self._connect_new_client(self.credentials)
            except BaseException:
                self.client = None
                self.connected = False
                raise
        if not await self.client.is_user_authorized():
            self.connected = False
            raise RuntimeError('Telegram session needs login in Agent settings')
        self.connected = True
        return self.client

    def login(self, payload):
        return self._submit(self._login(payload), timeout=75)

    async def _login(self, payload):
        if payload.get('resume'):
            # An explicit Resume overrides a persisted pause.  Only persist the
            # resumed state after Telegram has actually reconnected successfully.
            if not self._vault_state_loaded:
                self._load_vault_state()
            previous_paused = self._paused
            self._paused = False
            try:
                client = await self._ensure_client()
                self.account = self._person(await client.get_me())
                row = dict(self.credentials or self._load_vault_state() or {})
                if row.get('session'):
                    row['paused'] = False
                    self.vault.save(row)
                    self.credentials = row
                    self._vault_state_loaded = True
                    self._saved_session = True
                return self.status()
            except BaseException:
                self._paused = previous_paused
                raise
        if payload.get('api_id'):
            if self.connected: raise ValueError('Disconnect the current account before connecting another')
            api_id = int(payload['api_id']); api_hash = str(payload.get('api_hash') or '').strip()
            phone = str(payload.get('phone') or '').strip()
            if api_id <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash) or not re.fullmatch(r'\+[0-9]{7,16}', phone):
                raise ValueError('Enter a valid API ID, API Hash and phone number with country code')
            self.vault.load()  # Verify vault access before requesting a code.
            remaining = int(max(0.0, self._login_code_cooldown_until - time.monotonic()))
            same_pending = bool(
                self.pending and self.pending.get('phone') == phone and self.credentials and
                int(self.credentials.get('api_id') or 0) == api_id and
                str(self.credentials.get('api_hash') or '') == api_hash
            )
            if remaining > 0:
                if same_pending:
                    raise RuntimeError(f'A Telegram login code was already requested; use that code or wait {remaining + 1} seconds')
                # A different re-login must never retain the previous phone/code hash.
                self.pending = None
                raise RuntimeError(f'Wait {remaining + 1} seconds before requesting another Telegram login code')
            # Set the local guard before the RPC.  A timeout is ambiguous: Telegram
            # may already have accepted it, so immediate user retries are blocked.
            self._login_code_cooldown_until = time.monotonic() + 30.0
            # A failed second login must not reuse the previous phone/code hash.
            self.pending = None
            if self.client: await self.client.disconnect()
            self.refs.clear(); self.receipts.clear(); self._paused = False
            self.credentials = {'api_id':api_id, 'api_hash':api_hash, 'session':''}
            self.client = await self._connect_new_client(self.credentials, auth=True)
            try:
                sent = await self.client.send_code_request(phone)
            except ValueError as exc:
                if 'Request was unsuccessful' in str(exc):
                    raise RuntimeError('Telegram transport connected, but the login RPC did not complete. No automatic RPC retry was attempted. Press Ping Telegram with API ID/API Hash filled in, then try once more after the local cooldown.') from None
                raise
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
        self.credentials['paused'] = False
        self.vault.save(self.credentials)
        self._saved_session = True
        self._vault_state_loaded = True
        auth_client, self.client = self.client, None
        try:
            await auth_client.disconnect()
        except Exception:
            pass
        # Return to the conservative no-request-retry client after authentication so
        # normal Agent writes keep their existing uncertain-delivery protection.
        self.client = await self._connect_new_client(self.credentials)
        if not await self.client.is_user_authorized():
            self.connected = False
            raise RuntimeError('Telegram session was saved but could not be resumed; try Resume saved session')
        self.connected = True; self.pending = None
        self.account = self._person(await self.client.get_me())
        return self.status()

    def disconnect(self, *, revoke=False):
        return self._submit(self._disconnect(revoke))

    async def _disconnect(self, revoke):
        try:
            if revoke:
                if not self._vault_state_loaded:
                    self._load_vault_state()
                self._paused = False
                client = await self._ensure_client()
                if not await client.log_out(): raise RuntimeError('Telegram did not confirm session revocation')
                self.vault.clear()
                self._saved_session = False
                self._vault_state_loaded = True
            else:
                # Keep the encrypted StringSession but persist the user's explicit
                # pause so opening Telegram after an app restart cannot auto-login.
                row = dict(self.credentials or self._load_vault_state() or {})
                if row.get('session'):
                    row['paused'] = True
                    self.vault.save(row)
                    self._saved_session = True
                    self._vault_state_loaded = True
        finally:
            client, self.client = self.client, None
            self.credentials = None; self.pending = None
            self.connected = False; self._paused = True; self.active_proxy = None; self.account = {}
            self.refs.clear(); self.receipts.clear()
            if client: await client.disconnect()
        return self.status()

    def dashboard(self, limit=20):
        """Return a bounded, read-only Telegram overview for the local UI.

        One explicit dashboard refresh performs one get_me and one get_dialogs
        request.  There is intentionally no background polling, contact crawling,
        media download, or write operation here.
        """
        return self._submit(self._dashboard(limit), timeout=45)

    async def _dashboard(self, limit):
        limit = max(1, min(30, int(limit or 20)))
        client = await self._ensure_client()
        # Resume/login already reads the current account once. Reuse that local
        # snapshot instead of issuing another get_me on every dashboard refresh.
        if not self.account:
            self.account = self._person(await client.get_me())
        dialogs = await client.get_dialogs(limit=limit)
        rows = []
        for dialog in dialogs:
            entity = getattr(dialog, 'entity', None)
            if entity is None:
                continue
            person = self._person(entity)
            msg = getattr(dialog, 'message', None)
            preview = str(getattr(msg, 'raw_text', '') or '')[:240] if msg is not None else ''
            dt = getattr(dialog, 'date', None) or getattr(msg, 'date', None)
            if dt is not None and hasattr(dt, 'isoformat'):
                dt = dt.isoformat()
            elif dt is not None:
                dt = str(dt)
            rows.append({
                **person,
                'chat_ref': self._reference(entity, 'telegram-ui', False),
                'unread_count': int(getattr(dialog, 'unread_count', 0) or 0),
                'pinned': bool(getattr(dialog, 'pinned', False)),
                'archived': bool(getattr(dialog, 'archived', False)),
                'date': dt,
                'preview': preview,
            })
        return {
            'ok': True, 'connected': True, 'saved_session': self._saved_session_present(),
            'account': dict(self.account), 'dialogs': rows, 'limit': limit,
            'note': 'Read-only snapshot; no automatic polling or media download.',
        }

    def dashboard_messages(self, chat_ref, limit=20, before_id=0):
        return self._submit(self._dashboard_messages(chat_ref, limit, before_id), timeout=45)

    def ui_action(self, args, permissions, workspace=None):
        """Perform one explicit local UI action on a dialog selected in this tab.

        Agent references remain separate: the browser can authorize its selected
        dialog, but it cannot grant the model write access to that reference.
        Each button click gets its own receipt, so an intentional repeated text
        or attachment is possible without retrying an uncertain delivery.
        """
        op = str(args.get('operation') or '')
        allowed = {'send','reply','send_file','edit','delete_message','pin','unpin','mark_read','react','forward'}
        if op not in allowed: raise ValueError('Unsupported Telegram page action')
        if not permissions.allow_telegram_write: raise PermissionError('Enable Telegram messages in Agent settings first')
        if op == 'send_file' and not permissions.allow_workspace_write:
            raise PermissionError('Enable File Manager write access in Agent settings first')
        return self._submit(self._ui_action(args, workspace), timeout=120 if op=='send_file' else 45)

    async def _ui_action(self, args, workspace):
        scope = 'telegram-ui'
        ref = str(args.get('chat_ref') or '')
        row = self.refs.get(ref)
        if not row or row[1] != scope or row[2] < time.monotonic():
            raise ValueError('Select a recent dialog again; its reference expired')
        if args['operation'] == 'forward':
            target = self.refs.get(str(args.get('to_chat_ref') or ''))
            if not target or target[1] != scope or target[2] < time.monotonic():
                raise ValueError('Select a destination from recent dialogs again')
            self.refs[args['to_chat_ref']] = (target[0], scope, target[2], True)
            args = {**args, 'source_chat_ref':ref}
        else:
            self.refs[ref] = (row[0], scope, row[2], True)
        token = TELEGRAM_TURN.set('ui-' + uuid.uuid4().hex)
        try:
            return await self._tool(args, scope, workspace)
        finally:
            TELEGRAM_TURN.reset(token)

    async def _dashboard_messages(self, chat_ref, limit, before_id=0):
        limit = max(1, min(30, int(limit or 20)))
        before_id = int(before_id or 0)
        if before_id < 0: raise ValueError('Invalid message cursor')
        client = await self._ensure_client()
        row = self.refs.get(str(chat_ref or ''))
        if not row or row[1] != 'telegram-ui' or row[2] < time.monotonic():
            raise ValueError('Telegram chat reference expired; refresh the Telegram tab')
        peer = row[0]
        messages = await client.get_messages(peer, limit=limit, **({'max_id':before_id} if before_id else {}))
        out = []
        budget = 30000
        for msg in messages:
            text = str(getattr(msg, 'raw_text', '') or '')
            content = text[:min(4000, budget)]
            out.append({
                'message_id': int(getattr(msg, 'id', 0) or 0),
                'text': content,
                'outgoing': bool(getattr(msg, 'out', False)),
                'sender_name': str(getattr(getattr(msg,'sender',None),'title',None) or
                                   ' '.join(filter(None,[getattr(getattr(msg,'sender',None),'first_name',''),
                                                         getattr(getattr(msg,'sender',None),'last_name','')])) or
                                   getattr(getattr(msg,'sender',None),'username',None) or '')[:160],
                'date': getattr(msg, 'date', None).isoformat() if getattr(msg, 'date', None) else None,
                'has_media': bool(getattr(msg, 'media', None)),
                **self._message_extras(msg),
            })
            budget -= len(content)
            if budget <= 0:
                break
        return {
            'ok': True, 'chat_ref': str(chat_ref), 'chat': self._person(peer),
            'messages': out, 'limit': limit, 'has_more':len(messages)>=limit,
            'note': 'Read-only snapshot; no automatic polling or media download.',
        }

    @staticmethod
    def _person(entity):
        is_bot = bool(getattr(entity, 'bot', False))
        is_channel = bool(getattr(entity, 'broadcast', False))
        is_group = bool(getattr(entity, 'megagroup', False) or type(entity).__name__.lower().endswith(('chat','channelforbidden')))
        kind = 'bot' if is_bot else 'channel' if is_channel else 'group' if is_group else 'private'
        return {'id':entity.id, 'name':str(getattr(entity,'title',None) or ' '.join(filter(None,[getattr(entity,'first_name',''),getattr(entity,'last_name','')]))),
                'username':getattr(entity,'username',None), 'kind':kind, 'is_bot':is_bot,'is_group':is_group,
                'is_channel':is_channel, 'verified':bool(getattr(entity,'verified',False)),
                'participants_count':getattr(entity,'participants_count',None)}

    @staticmethod
    def _message_extras(message):
        out={}
        if getattr(message,'media',None):
            info=getattr(message,'file',None)
            media={'type':type(getattr(message,'media',None)).__name__[:80]}
            for key in ('name','size','mime_type'):
                value=getattr(info,key,None) if info is not None else None
                if value is not None:media[key]=value if key=='size' else str(value)[:300]
            mime = str(media.get('mime_type') or '').lower()
            media['kind'] = ('link' if 'WebPage' in media['type'] else
                             'location' if 'Geo' in media['type'] or 'Venue' in media['type'] else
                             'contact' if 'Contact' in media['type'] else
                             'poll' if 'Poll' in media['type'] else
                             'game' if 'Game' in media['type'] or 'Dice' in media['type'] else
                             'photo' if getattr(message,'photo',None) or 'Photo' in media['type'] else
                             'video' if getattr(message,'video',None) or mime.startswith('video/') else
                             'audio' if getattr(message,'voice',None) or getattr(message,'audio',None) or mime.startswith('audio/') else
                             'sticker' if getattr(message,'sticker',None) else
                             'file' if getattr(message,'file',None) is not None else 'unsupported')
            if media['kind']=='link':
                link=getattr(getattr(message,'media',None),'webpage',None)
                media['name']=str(getattr(link,'title',None) or getattr(link,'url',None) or 'Web link')[:300]
            elif media['kind']=='location':
                point=getattr(getattr(message,'media',None),'geo',None)
                lat,lon=getattr(point,'lat',None),getattr(point,'long',None)
                media['name']=f'{lat}, {lon}' if lat is not None and lon is not None else 'Location'
            elif media['kind']=='contact':
                contact=getattr(message,'media',None)
                media['name']=' '.join(filter(None,[str(getattr(contact,'first_name','') or ''),str(getattr(contact,'last_name','') or '')]))[:300] or 'Contact'
            out['media']=media
        reply=getattr(message,'reply_to_msg_id',None)
        if reply:out['reply_to_message_id']=int(reply)
        reactions=getattr(getattr(message,'reactions',None),'results',None) or []
        compact=[]
        for item in list(reactions)[:10]:
            reaction=getattr(item,'reaction',None)
            emoji=getattr(reaction,'emoticon',None)
            custom_id=getattr(reaction,'document_id',None)
            compact.append({'emoji':str(emoji)[:16] if emoji else None,'custom_emoji_id':str(custom_id) if custom_id else None,
                            'count':int(getattr(item,'count',0) or 0),'chosen':bool(getattr(item,'chosen_order',None) is not None)})
        if compact:out['reactions']=compact
        for key in ('views','forwards'):
            value=getattr(message,key,None)
            if isinstance(value,int) and value>=0:out[key]=value
        return out

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

    def tool(self, args, permissions, scope='local', workspace=None):
        op = args.get('operation')
        if op not in READS | WRITES: raise ValueError('Unknown Telegram operation')
        if op in READS and not permissions.allow_telegram_read: raise PermissionError('Telegram reads are disabled')
        if op in WRITES and not permissions.allow_telegram_write: raise PermissionError('Telegram messages are disabled')
        if op in {'download_media', 'send_file'} and not permissions.allow_workspace_write:
            raise PermissionError('Workspace file access is disabled')
        if op == 'status': return self.status()
        timeout = 120 if op in {'download_media', 'send_file'} else 30
        return self._submit(self._tool(args, scope, workspace), timeout=timeout)

    async def _tool(self, args, scope, workspace=None):
        op = args['operation']; limit = int(args.get('limit') or 5)
        max_limit = 15 if op in {'messages','my_messages','search'} else 100
        if not 1 <= limit <= max_limit: raise ValueError(f'Telegram limit must be 1..{max_limit}')
        client = await self._ensure_client()
        if op == 'account_info':
            if not self.account:self.account=self._person(await client.get_me())
            return {'account':dict(self.account),'connected':True,'history_read':False}
        if op in {'list_channels','list_bots'}:
            dialogs = await client.get_dialogs(limit=100)
            kind = 'channel' if op == 'list_channels' else 'bot'
            rows=[]
            for dialog in dialogs:
                entity=getattr(dialog,'entity',None)
                if entity is None:continue
                person=self._person(entity)
                if person['kind']!=kind:continue
                rows.append({'name':person['name'],'username':person.get('username'),'kind':person['kind'],
                             'chat_ref':self._reference(entity,scope,False),'preview':str(getattr(getattr(dialog,'message',None),'raw_text','') or '')[:160]})
                if len(rows)>=limit:break
            return {'kind':kind,'matches':rows,'limit':limit,'scanned_dialogs':len(dialogs),'truncated':len(rows)>=limit,'history_read':False}
        if op in {'channel_info','bot_info'}:
            query=str(args.get('query') or '').strip()
            if not query:raise ValueError('query is required')
            if len(query)>200:raise ValueError('query is too long')
            entity=await client.get_entity(query)
            person=self._person(entity)
            expected='channel' if op=='channel_info' else 'bot'
            if person['kind']!=expected:raise ValueError(f'The resolved account is not a {expected}')
            return {**person,'chat_ref':self._reference(entity,scope,False),'history_read':False}
        if op == 'global_search':
            query=str(args.get('query') or '').strip()
            if not query:raise ValueError('query is required for global_search')
            messages=await client.get_messages(None,search=query[:200],limit=limit)
            rows=[];budget=10000;truncated=False
            for msg in messages:
                text=str(getattr(msg,'raw_text','') or '')
                content=text[:min(2000,budget)]
                rows.append({'message_id':getattr(msg,'id',None),'text':content,'outgoing':bool(getattr(msg,'out',False)),
                             'date':getattr(msg,'date',None).isoformat() if getattr(msg,'date',None) else None,
                             'has_media':bool(getattr(msg,'media',None)),**self._message_extras(msg)})
                truncated |= len(content)<len(text)
                budget-=len(content)
                if budget<=0:truncated |= len(rows)<len(messages);break
            return {'query':query,'messages':rows,'truncated':truncated,'max_characters':10000,'search_scope':'global'}
        if op in {'chat_info','participants'}:
            row=self.refs.get(str(args.get('chat_ref') or ''))
            if not row or row[1]!=scope or row[2]<time.monotonic():
                raise ValueError('Unknown, expired or different-scope chat_ref; resolve the chat first')
            peer=row[0]
            person=self._person(peer)
            if op=='chat_info':
                return {**person,'chat_ref':args['chat_ref'],'history_read':False}
            if person['kind'] not in {'group','channel'}:
                raise ValueError('participants is available only for a group or channel')
            people=await client.get_participants(peer,limit=limit)
            return {'chat_ref':args['chat_ref'],'kind':person['kind'],'participants':[self._person(item) for item in people[:limit]],
                    'limit':limit,'truncated':len(people)>=limit,'history_read':False}
        if op == 'download_media':
            if workspace is None: raise RuntimeError('File Manager is unavailable')
            row=self.refs.get(str(args.get('chat_ref') or ''))
            if not row or row[1]!=scope or row[2]<time.monotonic():
                raise ValueError('Unknown, expired or different-scope chat_ref; resolve the chat first')
            message_id=args.get('message_id')
            if not isinstance(message_id,int) or isinstance(message_id,bool) or message_id<=0:
                raise ValueError('message_id is required')
            message=await client.get_messages(row[0],ids=message_id)
            media=getattr(message,'media',None) if message else None
            if not media or self._message_extras(message).get('media',{}).get('kind') in {'link','location','contact','poll','game','unsupported'}:
                raise ValueError('The selected Telegram message has no downloadable file')
            file_info=getattr(message,'file',None)
            size=getattr(file_info,'size',None)
            if not isinstance(size,int):
                document=getattr(media,'document',None)
                size=getattr(document,'size',None)
            if isinstance(size,int) and size>MAX_TELEGRAM_MEDIA_BYTES:
                raise ValueError('Telegram media exceeds the 20 MB download limit')
            media_kind=self._message_extras(message).get('media',{}).get('kind')
            media_mime=str(getattr(file_info,'mime_type',None) or '')
            suffix='.jpg' if media_kind=='photo' else (mimetypes.guess_extension(media_mime) or '.bin')
            filename=str(getattr(file_info,'name',None) or f'message-{message_id}{suffix}')
            filename=os.path.basename(filename.replace('\\','/'))
            filename=''.join(ch for ch in filename if ch.isprintable() and ch not in '/\\:')[:150].strip(' .') or f'message-{message_id}.bin'
            folder=workspace.files_root/'Telegram'
            folder.mkdir(parents=True,exist_ok=True)
            target=folder/f'tg-{message_id}-{filename}'
            suffix=1
            while target.exists():
                target=folder/f'tg-{message_id}-{suffix}-{filename}';suffix+=1
            partial=target.with_name(target.name+'.part')
            total=0;chunks=0;max_chunks=(MAX_TELEGRAM_MEDIA_BYTES//TELEGRAM_MEDIA_CHUNK_BYTES)+1
            try:
                async for chunk in client.iter_download(media,request_size=TELEGRAM_MEDIA_CHUNK_BYTES,limit=max_chunks):
                    if not chunk: break
                    total+=len(chunk);chunks+=1
                    if total>MAX_TELEGRAM_MEDIA_BYTES:
                        raise ValueError('Telegram media exceeds the 20 MB download limit')
                    with partial.open('ab' if chunks>1 else 'wb') as stream: stream.write(chunk)
                if total<=0: raise ValueError('Telegram returned an empty media file')
                if isinstance(size,int) and size>0 and total!=size:
                    raise RuntimeError('Telegram media download was incomplete; partial file removed')
                if chunks>=max_chunks and not (isinstance(size,int) and size>0 and total==size):
                    raise RuntimeError('Telegram media reached the safe chunk limit; partial file removed')
                partial.replace(target)
                with workspace.lock:
                    saved=workspace._register(target,source='telegram',description=f'Telegram message {message_id}',tags=['telegram','download'])
            except BaseException:
                try: partial.unlink(missing_ok=True)
                except Exception: pass
                try: target.unlink(missing_ok=True)
                except Exception: pass
                raise
            return {'downloaded':True,'file':saved,'chat_ref':args['chat_ref'],'message_id':message_id,
                    'verification':{'verified':True,'method':'local file size and SHA-256 indexed'},'limit_bytes':MAX_TELEGRAM_MEDIA_BYTES}
        if op == 'select_person':
            candidate_ref = str(args.get('candidate_ref') or '').strip()
            if not candidate_ref:
                raise ValueError('candidate_ref is required')
            row = self.refs.get(candidate_ref)
            if not row or row[1] != scope or row[2] < time.monotonic():
                raise ValueError('Unknown, expired or different-scope candidate_ref; resolve the person again')
            peer = row[0]
            self.refs[candidate_ref] = (peer, row[1], row[2], True)
            person = self._person(peer)
            return {'selected':{'name':person['name'],'username':person.get('username'),'chat_ref':candidate_ref},
                    'selection_confirmed':True, 'history_read':False}
        if op in {'resolve_person','recent_chats'}:
            query = str(args.get('query') or '').strip()
            if op == 'resolve_person' and not query: raise ValueError('query is required')
            if len(query) > 200: raise ValueError('query is too long')
            if op == 'resolve_person' and re.fullmatch(r'@[A-Za-z0-9_]{5,32}',query):
                entity = await client.get_entity(query)
                person = self._person(entity)
                return {'matches':[{'name':person['name'],'username':person.get('username'),
                                    'chat_ref':self._reference(entity,scope,True)}],
                        'ambiguous':False,'selection_required':False,'truncated':False,
                        'history_read':False,'search_scope':'exact @username'}
            dialogs = await client.get_dialogs(limit=100 if op in {'resolve_person','recent_chats'} else limit)
            peers = [d.entity for d in dialogs if getattr(d, 'entity', None) is not None]
            if op in {'recent_chats','list_channels','list_bots'}:
                rows=[]
                kind=str(args.get('kind') or 'all')
                for entity in peers:
                    person=self._person(entity)
                    if op=='list_channels' and person['kind']!='channel':continue
                    if op=='list_bots' and person['kind']!='bot':continue
                    if op=='recent_chats' and kind not in {'all',person['kind']}:continue
                    rows.append({'name':person['name'],'username':person.get('username'),'kind':person['kind'],
                                 'chat_ref':self._reference(entity,scope,False)})
                    if len(rows)>limit:break
                return {'matches':rows,'ambiguous':False,'selection_required':False,
                        'truncated':len(rows)>limit or len(peers)>=100,'history_read':False,'search_scope':f'up to 100 recent dialogs; filter={kind}'}

            # Do not dump a full contact/dialog list into the model. Rank up to 100
            # recent dialog labels locally, then expose at most 20 compact candidates.
            # The model makes the semantic choice (useful for emoji labels, nicknames,
            # Persian/English spellings, etc.) and must explicitly call select_person
            # before a write-capable chat_ref is authorized.
            ranked=[]; seen=set()
            for index, entity in enumerate(peers):
                sig=(type(entity).__name__, getattr(entity,'id',None))
                if sig in seen: continue
                seen.add(sig)
                person=self._person(entity)
                ranked.append((_candidate_score(query, person, index), index, entity, person))
            ranked.sort(key=lambda row:(row[0][0], row[0][1]), reverse=True)
            chosen=ranked[:20]
            rows=[]
            for _, _, entity, person in chosen:
                rows.append({'name':person['name'],'username':person.get('username'),
                             'candidate_ref':self._reference(entity,scope,False)})
            return {'query':query,'matches':rows,'ambiguous':len(rows)>1,
                    'selection_required':bool(rows),'candidate_limit':20,
                    'truncated':len(ranked)>20,'history_read':False,
                    'search_scope':'locally ranked from up to 100 recent dialog labels; max 20 exposed to model',
                    'instruction':'Choose the best candidate semantically, then call select_person with candidate_ref. Ask the user only if the remaining candidates are genuinely indistinguishable.'}
        if op == 'forward':
            source=self.refs.get(str(args.get('source_chat_ref') or ''))
            target=self.refs.get(str(args.get('to_chat_ref') or ''))
            message_id=args.get('message_id')
            if not source or source[1]!=scope or source[2]<time.monotonic():raise ValueError('Resolve the source chat again before forwarding')
            if not target or target[1]!=scope or target[2]<time.monotonic() or not target[3]:raise ValueError('Select the destination chat with select_person before forwarding')
            if not isinstance(message_id,int) or isinstance(message_id,bool) or message_id<=0:raise ValueError('message_id is required')
            signature=hashlib.sha256(json.dumps(['forward',type(source[0]).__name__,source[0].id,type(target[0]).__name__,target[0].id,message_id]).encode()).hexdigest()
            turn=TELEGRAM_TURN.get();receipt_key=(scope,turn,signature) if turn else (scope,signature)
            previous=self.receipts.get(receipt_key)
            if previous:
                if previous.get('result'):return previous['result']
                raise RuntimeError('Previous forward delivery is unknown; inspect the destination chat before retrying')
            self._prune_receipts()
            if len(self.receipts)>=1000:raise RuntimeError('Telegram receipt safety limit reached; retry after older receipts expire')
            self.receipts[receipt_key]={'signature':signature,'created_at':time.time()}
            try:
                forwarded=await client.forward_messages(target[0],message_id,from_peer=source[0])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if type(exc).__name__ in {'FloodWaitError','SlowModeWaitError'} and getattr(exc,'seconds',None):
                    self.receipts.pop(receipt_key,None);raise
                raise RuntimeError('Telegram forward delivery is unknown; inspect the destination chat before retrying') from None
            item=forwarded[0] if isinstance(forwarded,(list,tuple)) and forwarded else forwarded
            if item is None or not getattr(item,'id',None):raise RuntimeError('Telegram forward delivery is unknown; no message ID returned')
            result={'forwarded':True,'to_chat_ref':args['to_chat_ref'],'message_id':item.id,'verification':'Telegram returned forwarded message ID; do not retry automatically'}
            self.receipts[receipt_key]['result']=result
            return result
        row = self.refs.get(str(args.get('chat_ref') or ''))
        if not row or row[1] != scope or row[2] < time.monotonic(): raise ValueError('Unknown, expired or different-scope chat_ref; resolve the person first')
        peer = row[0]
        if op in WRITES:
            if not row[3]: raise ValueError('Select a person with select_person before sending; unresolved/recent chat refs are read-only')
            if op in {'send','reply'}:return await self._send(client, peer, args, scope)
            if op=='send_file':
                if workspace is None: raise RuntimeError('File Manager is unavailable')
                file_id=str(args.get('file_id') or '').strip()
                file_row,file_path=workspace._resolve_id(file_id)
                if int(file_row.get('size') or 0)>MAX_TELEGRAM_MEDIA_BYTES:
                    raise ValueError('Files larger than 20 MB cannot be sent through this Telegram tool')
                caption=str(args.get('caption') or '')
                if len(caption)>1024: raise ValueError('caption must be at most 1024 characters')
                reply=args.get('message_id')
                if reply is not None and (not isinstance(reply,int) or isinstance(reply,bool) or reply<=0):
                    raise ValueError('message_id must be a valid reply target')
                signature=hashlib.sha256(json.dumps(['file',type(peer).__name__,peer.id,file_row.get('sha256'),caption,reply],ensure_ascii=False).encode()).hexdigest()
                turn=TELEGRAM_TURN.get();receipt_key=(scope,turn,signature) if turn else (scope,signature)
                previous=self.receipts.get(receipt_key)
                if previous:
                    if previous['signature']!=signature: raise ValueError('request_key already belongs to another file send')
                    if previous.get('result'): return previous['result']
                    raise RuntimeError('Previous file delivery is unknown; inspect the chat before another send')
                self._prune_receipts()
                if len(self.receipts)>=1000: raise RuntimeError('Telegram receipt safety limit reached; retry after older receipts expire')
                self.receipts[receipt_key]={'signature':signature,'created_at':time.time()}
                try:
                    sent=await client.send_file(peer,str(file_path),caption=caption or None,reply_to=reply,parse_mode=None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if type(exc).__name__ in {'FloodWaitError','SlowModeWaitError'} and getattr(exc,'seconds',None):
                        self.receipts.pop(receipt_key,None);raise
                    raise RuntimeError('Telegram file delivery is unknown; inspect the chat, do not resend blindly') from None
                if isinstance(sent,(list,tuple)): sent=sent[0] if sent else None
                if sent is None: raise RuntimeError('Telegram did not return a file message ID; delivery may be unknown')
                result={'sent':True,'chat_ref':args['chat_ref'],'message_id':getattr(sent,'id',None),'file_id':file_id,
                        'verification':{'verified':False,'method':'Telegram message ID readback; do not resend automatically'}}
                self.receipts[receipt_key]['result']=result
                try:
                    check=await client.get_messages(peer,ids=sent.id)
                    result['verification']['verified']=bool(check and check.id==sent.id and getattr(check,'media',None))
                except Exception: result['verification']['detail']='Telegram acknowledged the ID; readback unavailable. Do not resend.'
                return result
            msg_id=args.get('message_id')
            if not isinstance(msg_id,int) or isinstance(msg_id,bool) or msg_id<=0:raise ValueError('message_id is required')
            if op=='edit':
                text=str(args.get('text') or '')
                if not text.strip() or len(text)>4096:raise ValueError('text must contain 1..4096 characters')
                result=await client.edit_message(peer,msg_id,text,parse_mode=None,link_preview=False)
                return {'edited':True,'chat_ref':args['chat_ref'],'message_id':getattr(result,'id',msg_id)}
            if op=='delete_message':
                deleted=await client.delete_messages(peer,[msg_id],revoke=True)
                return {'deleted':bool(deleted),'chat_ref':args['chat_ref'],'message_id':msg_id}
            if op=='pin':
                result=await client.pin_message(peer,msg_id,notify=False)
                return {'pinned':bool(result),'chat_ref':args['chat_ref'],'message_id':msg_id}
            if op=='unpin':
                result=await client.unpin_message(peer,message=msg_id)
                return {'unpinned':bool(result),'chat_ref':args['chat_ref'],'message_id':msg_id}
            if op=='mark_read':
                result=await client.send_read_acknowledge(peer,max_id=msg_id)
                return {'marked_read':bool(result),'chat_ref':args['chat_ref'],'message_id':msg_id}
            if op=='react':
                reaction=str(args.get('reaction') or '').strip()
                if not reaction or len(reaction)>8 or any(ch.isspace() for ch in reaction) or all(ord(ch)<128 for ch in reaction): raise ValueError('reaction must be one emoji')
                from telethon import functions, types
                input_peer=await client.get_input_entity(peer)
                result=await client(functions.messages.SendReactionRequest(
                    peer=input_peer,msg_id=msg_id,reaction=[types.ReactionEmoji(emoticon=reaction)]
                ))
                return {'reacted':bool(result),'chat_ref':args['chat_ref'],'message_id':msg_id,'reaction':reaction}
            raise ValueError('unsupported Telegram write')
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
                         'date':msg.date.isoformat() if msg.date else None, 'has_media':bool(msg.media),
                         **self._message_extras(msg)})
            budget -= len(content)
            if budget <= 0:
                truncated |= len(rows)<len(messages); break
        return {'chat_ref':args.get('chat_ref'),'messages':rows,'truncated':truncated,'max_characters':10000,'search_scope':'selected chat'}

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
        self._prune_receipts()
        if len(self.receipts)>=1000: raise RuntimeError('Telegram receipt safety limit reached; retry after older receipts expire')
        self.receipts[receipt_key] = {'signature':signature,'created_at':time.time()}
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
