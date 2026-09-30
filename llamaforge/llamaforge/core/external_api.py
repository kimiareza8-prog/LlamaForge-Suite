from __future__ import annotations

import base64
import ctypes
import json
import os
import http.client
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any, Iterator

from .network_policy import direct_opener

PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models_path": "/models",
        "chat_path": "/responses",
        "protocol": "responses",
    },
    "gemini": {
        "name": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "protocol": "chat_completions",
    },
    "cerebras": {
        "name": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "protocol": "chat_completions",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "protocol": "chat_completions",
    },
    "mistral": {
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "protocol": "chat_completions",
    },
    "alibaba": {
        "name": "Alibaba Cloud Model Studio",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "protocol": "chat_completions",
    },
}

ALIBABA_BASE_URLS = {
    "Singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "China (Beijing)": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "US (Virginia)": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "China (Hong Kong)": "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1",
}

DEFAULT_REASONING_TAG_PAIRS = (
    ("<thought>", "</thought>"),
    ("<think>", "</think>"),
    ("<analysis>", "</analysis>"),
    ("<reasoning>", "</reasoning>"),
)
_FINAL_ANSWER_LABEL = re.compile(
    r"(?im)^[ \t>*#-]*(?:\*{1,2}[ \t]*)?(?:output|final(?:[ \t]+answer)?|answer|"
    r"جواب(?:[ \t]+نهایی)?|پاسخ(?:[ \t]+نهایی)?)\s*[:：][ \t]*"
)


class _ReasoningTagStreamParser:
    """Split tagged reasoning out of API text while preserving chunk boundaries."""

    def __init__(self, output_syntax: dict[str, str] | None = None):
        custom = output_syntax if isinstance(output_syntax, dict) else {}
        opening = str(custom.get("open_marker") or "").strip()
        closing = str(custom.get("close_marker") or "").strip()
        pairs = list(DEFAULT_REASONING_TAG_PAIRS)
        if opening and closing and len(opening) <= 80 and len(closing) <= 80:
            if (opening, closing) not in pairs:
                pairs.insert(0, (opening, closing))
        self.open_pairs = pairs
        self.buffer = ""
        self.in_reasoning = False
        self.close_marker = ""
        self.text_parts: list[str] = []
        self.reasoning_parts: list[str] = []

    def feed(self, chunk: str = "", *, final: bool = False) -> list[tuple[str, str]]:
        self.buffer += str(chunk or "")
        emitted: list[tuple[str, str]] = []

        def emit(kind: str, value: str) -> None:
            if not value:
                return
            emitted.append((kind, value))
            (self.reasoning_parts if kind == "reasoning" else self.text_parts).append(value)

        while self.buffer:
            if self.in_reasoning:
                end = self.buffer.find(self.close_marker)
                if end >= 0:
                    emit("reasoning", self.buffer[:end])
                    self.buffer = self.buffer[end + len(self.close_marker):]
                    self.in_reasoning = False
                    self.close_marker = ""
                    continue
                if final:
                    emit("reasoning", self.buffer)
                    self.buffer = ""
                    break
                keep = max(
                    (n for n in range(1, len(self.close_marker)) if self.buffer.endswith(self.close_marker[:n])),
                    default=0,
                )
                safe_len = len(self.buffer) - keep
                if safe_len:
                    emit("reasoning", self.buffer[:safe_len])
                    self.buffer = self.buffer[safe_len:]
                break

            hits = []
            for opening, closing in self.open_pairs:
                index = self.buffer.find(opening)
                if index >= 0:
                    hits.append((index, -len(opening), opening, closing))
            if hits:
                index, _length, opening, closing = min(hits)
                emit("text", self.buffer[:index])
                self.buffer = self.buffer[index + len(opening):]
                self.in_reasoning = True
                self.close_marker = closing
                continue
            if final:
                emit("text", self.buffer)
                self.buffer = ""
                break
            keep = max(
                (n for opening, _closing in self.open_pairs for n in range(1, len(opening)) if self.buffer.endswith(opening[:n])),
                default=0,
            )
            safe_len = len(self.buffer) - keep
            if safe_len:
                emit("text", self.buffer[:safe_len])
                self.buffer = self.buffer[safe_len:]
            break
        return emitted

    def final_answer_from_reasoning(self) -> str:
        if "".join(self.text_parts).strip():
            return ""
        reasoning = "".join(self.reasoning_parts)
        matches = list(_FINAL_ANSWER_LABEL.finditer(reasoning))
        if not matches:
            return ""
        return reasoning[matches[-1].end():].strip()


def _split_tagged_reasoning(content: str, output_syntax: dict[str, str] | None = None) -> tuple[str, str]:
    parser = _ReasoningTagStreamParser(output_syntax)
    parser.feed(content, final=True)
    text = "".join(parser.text_parts)
    reasoning = "".join(parser.reasoning_parts)
    if not text.strip():
        answer = parser.final_answer_from_reasoning()
        if answer:
            match = list(_FINAL_ANSWER_LABEL.finditer(reasoning))[-1]
            reasoning = reasoning[:match.start()].rstrip()
            text = answer
    return text, reasoning


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _parse_proxy_endpoint(value: str) -> tuple[str, int] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    if "@" in raw:
        raw = raw.rsplit("@", 1)[1]
    if raw.startswith("[") and "]:" in raw:
        host, port = raw[1:].split("]:", 1)
    elif ":" in raw:
        host, port = raw.rsplit(":", 1)
    else:
        return None
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return None
    host = host.strip()
    if not host or not 1 <= port_i <= 65535:
        return None
    return host, port_i


def windows_http_proxy() -> dict[str, Any] | None:
    """Return the current WinINet HTTP proxy used by Psiphon, if enabled.

    External model APIs intentionally follow the Windows proxy dynamically. Local
    llama.cpp traffic does not use this helper and remains direct/loopback-only.
    """
    if os.name != "nt":
        return None
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
    except Exception:
        return None
    if not enabled or not server:
        return None

    entries: dict[str, tuple[str, int]] = {}
    if "=" in server:
        for item in server.split(";"):
            if "=" not in item:
                continue
            kind, endpoint = item.split("=", 1)
            parsed = _parse_proxy_endpoint(endpoint)
            if parsed:
                entries[kind.strip().lower()] = parsed
    else:
        parsed = _parse_proxy_endpoint(server)
        if parsed:
            entries["generic"] = parsed

    # HTTPS APIs connect through an HTTP CONNECT listener. Psiphon exposes one
    # through Windows System Proxy Settings. Prefer the explicit HTTPS/HTTP entry.
    endpoint = entries.get("https") or entries.get("http") or entries.get("generic")
    if endpoint:
        host, port = endpoint
        return {
            "detected": True,
            "supported": True,
            "type": "http",
            "host": host,
            "port": port,
            "source": "Windows system proxy",
            "url": f"http://{host}:{port}",
        }
    # Never leak an external-model request directly when Windows says a proxy is
    # enabled but only exposes a SOCKS listener. urllib cannot safely tunnel
    # HTTPS over SOCKS without an additional transport. Report it instead.
    socks = entries.get("socks") or entries.get("socks5")
    if socks:
        host, port = socks
        return {
            "detected": True,
            "supported": False,
            "type": "socks5",
            "host": host,
            "port": port,
            "source": "Windows system proxy",
            "reason": "SOCKS-only system proxy; an HTTP/HTTPS Psiphon listener is required for external model APIs",
        }
    return {
        "detected": True,
        "supported": False,
        "type": "unknown",
        "source": "Windows system proxy",
        "reason": "System proxy is enabled but no usable HTTP/HTTPS proxy endpoint was found",
    }


def external_api_opener() -> tuple[urllib.request.OpenerDirector, dict[str, Any]]:
    proxy = windows_http_proxy()
    if proxy:
        if not proxy.get("supported", True):
            raise RuntimeError(str(proxy.get("reason") or "System proxy is enabled but unsupported for external model APIs"))
        url = str(proxy["url"])
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url})), proxy
    return direct_opener(), {"detected": False, "mode": "direct"}


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_encrypt(text: str) -> str:
    data = text.encode("utf-8")
    buf = ctypes.create_string_buffer(data)
    in_blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(value: str) -> str:
    raw = base64.b64decode(value.encode("ascii"))
    buf = ctypes.create_string_buffer(raw)
    in_blob = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class ProviderSecretStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def get(self, provider: str) -> str:
        provider = str(provider or "").lower()
        if provider not in PROVIDERS:
            return ""
        if os.name == "nt":
            with self.lock:
                raw = _load_json(self.path).get(provider)
            if not raw:
                return ""
            try:
                return _dpapi_decrypt(str(raw))
            except Exception:
                return ""
        try:
            import keyring
            return str(keyring.get_password("LlamaForge.ExternalAPI", provider) or "")
        except Exception:
            return ""

    def set(self, provider: str, key: str) -> None:
        provider = str(provider or "").lower()
        if provider not in PROVIDERS:
            raise ValueError("Unknown API provider")
        key = str(key or "").strip()
        if os.name == "nt":
            with self.lock:
                raw = _load_json(self.path)
                if key:
                    raw[provider] = _dpapi_encrypt(key)
                else:
                    raw.pop(provider, None)
                _atomic_write_json(self.path, raw)
            return
        try:
            import keyring
            if key:
                keyring.set_password("LlamaForge.ExternalAPI", provider, key)
            else:
                try:
                    keyring.delete_password("LlamaForge.ExternalAPI", provider)
                except Exception:
                    pass
        except Exception as exc:
            raise RuntimeError("Secure OS credential storage is unavailable for API keys") from exc


class ExternalAPIService:
    def __init__(self, app_dir: Path, *, log=None, base_url_provider=None):
        self.app_dir = Path(app_dir)
        self.log = log or (lambda _x: None)
        self.base_url_provider = base_url_provider or (lambda _provider: "")
        self.secrets = ProviderSecretStore(self.app_dir / "api-secrets.json")
        self.usage_path = self.app_dir / "api-usage.json"
        self.models_path = self.app_dir / "api-models-cache.json"
        self.lock = threading.RLock()
        self._models_cache = _load_json(self.models_path)
        self._usage = _load_json(self.usage_path)

    @staticmethod
    def provider_name(provider: str) -> str:
        return str(PROVIDERS.get(provider, {}).get("name") or provider)

    def key_configured(self, provider: str) -> bool:
        return bool(self.secrets.get(provider))

    def set_key(self, provider: str, key: str) -> None:
        provider = str(provider or "").lower().strip()
        key = str(key or "").strip()
        self.secrets.set(provider, key)
        # A successful write is not enough: verify the exact secret can be read
        # back before the UI is told that it was saved. This catches DPAPI /
        # credential-store failures immediately instead of failing later during
        # model discovery. Never log or return the secret itself.
        stored = self.secrets.get(provider)
        if key and stored != key:
            raise RuntimeError("API key could not be read back from secure storage")
        if not key and stored:
            raise RuntimeError("API key could not be removed from secure storage")
        self.log(f"[api:{provider}] API key {'configured' if key else 'cleared'} in OS-protected storage")

    def route_status(self) -> dict[str, Any]:
        proxy = windows_http_proxy()
        if proxy:
            return {k: v for k, v in proxy.items() if k != "url"}
        return {"detected": False, "mode": "direct"}

    def cached_models(self, provider: str) -> list[dict[str, Any]]:
        row = self._models_cache.get(provider) if isinstance(self._models_cache, dict) else None
        models = row.get("models") if isinstance(row, dict) else []
        return [dict(x) for x in models if isinstance(x, dict)]

    def status(self, active_provider: str = "", active_model: str = "") -> dict[str, Any]:
        providers = {}
        for pid, meta in PROVIDERS.items():
            cached = self.cached_models(pid)
            try:
                configured_base = str(self.base_url_provider(pid) or "").strip()
            except Exception:
                configured_base = ""
            providers[pid] = {
                "id": pid,
                "name": meta["name"],
                "base_url": configured_base or meta["base_url"],
                "base_url_options": ({label: url for label, url in ALIBABA_BASE_URLS.items()} if pid == "alibaba" else {}),
                "configured": self.key_configured(pid),
                "models": cached,
                "model_count": len(cached),
                "cache_updated_at": float((self._models_cache.get(pid) or {}).get("updated_at") or 0) if isinstance(self._models_cache, dict) else 0,
                "usage": self.usage(pid),
            }
        return {
            "providers": providers,
            "route": self.route_status(),
            "active_provider": str(active_provider or ""),
            "active_model": str(active_model or ""),
        }

    def _headers(self, provider: str, key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LlamaForge/0.36.5-unified-context",
        }

    def _url(self, provider: str, path_key: str) -> str:
        meta = PROVIDERS.get(provider)
        if not meta:
            raise ValueError("Unknown API provider")
        try:
            configured_base = str(self.base_url_provider(provider) or "").strip()
        except Exception:
            configured_base = ""
        base_url = configured_base or meta["base_url"]
        return base_url.rstrip("/") + meta[path_key]

    @staticmethod
    def _rate_headers(headers) -> dict[str, str]:
        out = {}
        try:
            for key, value in headers.items():
                low = str(key).lower()
                if low.startswith("x-ratelimit-") or low.startswith("ratelimit-") or low in {"retry-after"}:
                    out[low] = str(value)
        except Exception:
            pass
        return out

    def _record_usage(self, provider: str, model: str, usage: dict | None, *, sent: int = 0, received: int = 0, rate_headers: dict | None = None, error: bool = False) -> None:
        provider = str(provider or "")
        model = str(model or "")
        now = time.time()
        with self.lock:
            root = self._usage if isinstance(self._usage, dict) else {}
            prov = root.setdefault(provider, {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "bytes_sent": 0, "bytes_received": 0, "models": {}})
            prov["requests"] = int(prov.get("requests") or 0) + (0 if error else 1)
            prov["errors"] = int(prov.get("errors") or 0) + (1 if error else 0)
            prov["bytes_sent"] = int(prov.get("bytes_sent") or 0) + max(0, int(sent or 0))
            prov["bytes_received"] = int(prov.get("bytes_received") or 0) + max(0, int(received or 0))
            normalized = self.normalize_usage(usage or {})
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                prov[key] = int(prov.get(key) or 0) + int(normalized.get(key) or 0)
            prov["last_used_at"] = now
            if rate_headers:
                prov["rate_limits"] = dict(rate_headers)
            models = prov.setdefault("models", {})
            if model:
                row = models.setdefault(model, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "bytes_sent": 0, "bytes_received": 0})
                row["requests"] = int(row.get("requests") or 0) + (0 if error else 1)
                row["bytes_sent"] = int(row.get("bytes_sent") or 0) + max(0, int(sent or 0))
                row["bytes_received"] = int(row.get("bytes_received") or 0) + max(0, int(received or 0))
                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    row[key] = int(row.get(key) or 0) + int(normalized.get(key) or 0)
                row["last_used_at"] = now
            self._usage = root
            _atomic_write_json(self.usage_path, root)

    @staticmethod
    def normalize_usage(usage: dict) -> dict[str, int]:
        if not isinstance(usage, dict):
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        inp = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        out = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        total = usage.get("total_tokens", 0) or 0
        try: inp = int(inp)
        except Exception: inp = 0
        try: out = int(out)
        except Exception: out = 0
        try: total = int(total)
        except Exception: total = 0
        if total <= 0:
            total = max(0, inp + out)
        return {"input_tokens": max(0, inp), "output_tokens": max(0, out), "total_tokens": max(0, total)}

    def usage(self, provider: str) -> dict[str, Any]:
        with self.lock:
            row = dict((self._usage or {}).get(provider) or {})
            if isinstance(row.get("models"), dict):
                row["models"] = {k: dict(v) for k, v in row["models"].items() if isinstance(v, dict)}
        row.setdefault("requests", 0)
        row.setdefault("errors", 0)
        row.setdefault("input_tokens", 0)
        row.setdefault("output_tokens", 0)
        row.setdefault("total_tokens", 0)
        row.setdefault("bytes_sent", 0)
        row.setdefault("bytes_received", 0)
        row.setdefault("rate_limits", {})
        row["quota_note"] = (
            "Standard API keys do not expose a reliable account-wide remaining quota here. "
            "LlamaForge shows provider response usage and any rate-limit headers that the API returns."
        )
        return row

    def list_models(self, provider: str) -> dict[str, Any]:
        provider = str(provider or "").lower()
        key = self.secrets.get(provider)
        if not key:
            raise RuntimeError(f"{self.provider_name(provider)} API key is not configured")
        url = self._url(provider, "models_path")
        opener, route = external_api_opener()
        req = urllib.request.Request(url, method="GET", headers=self._headers(provider, key))
        started = time.monotonic()
        transient_http = {408, 425, 429, 500, 502, 503, 504}
        network_errors = (
            urllib.error.URLError, socket.timeout, TimeoutError,
            http.client.RemoteDisconnected, http.client.IncompleteRead, http.client.BadStatusLine,
            ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
        )
        raw = b""; headers = {}; last_exc = None
        for attempt, delay in enumerate((0.0, 0.35, 0.9)):
            if delay:
                time.sleep(delay)
            try:
                with opener.open(req, timeout=30) as response:
                    raw = response.read()
                    headers = self._rate_headers(response.headers)
                last_exc = None
                break
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in transient_http and attempt < 2:
                    try: exc.read(200_000)
                    except Exception: pass
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:1800]
                self._record_usage(provider, "", None, received=len(detail.encode("utf-8")), error=True)
                raise RuntimeError(f"{self.provider_name(provider)} HTTP {exc.code}: {detail or exc.reason}") from exc
            except network_errors as exc:
                last_exc = exc
                if attempt < 2:
                    continue
                self._record_usage(provider, "", None, error=True)
                route_text = "through Psiphon/system proxy" if route.get("detected") else "directly"
                raise RuntimeError(f"Could not reach {self.provider_name(provider)} {route_text}: {exc}") from exc
        if last_exc is not None:
            raise RuntimeError(f"Could not refresh {self.provider_name(provider)} models: {last_exc}")
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except Exception as exc:
            raise RuntimeError(f"{self.provider_name(provider)} returned invalid JSON") from exc
        rows = (obj.get("data") or obj.get("models")) if isinstance(obj, dict) else None
        models = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or row.get("name") or "").strip()
            if not mid:
                continue
            if mid.startswith("models/"):
                mid = mid.split("/", 1)[1]
            models.append({"id": mid, "name": mid, "owned_by": str(row.get("owned_by") or "")})
        models.sort(key=lambda x: x["id"].casefold())
        with self.lock:
            self._models_cache[provider] = {"updated_at": time.time(), "models": models}
            _atomic_write_json(self.models_path, self._models_cache)
        self.log(f"[api:{provider}] models refreshed count={len(models)} route={'proxy' if route.get('detected') else 'direct'} latency_ms={int((time.monotonic()-started)*1000)}")
        return {"ok": True, "provider": provider, "models": models, "route": {k: v for k, v in route.items() if k != "url"}, "rate_limits": headers}

    @staticmethod
    def _clean_messages(messages: list[dict]) -> list[dict]:
        out = []
        for row in messages or []:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "user")
            if role not in {"system", "user", "assistant", "developer", "tool"}:
                role = "user"
            content = row.get("content")
            if isinstance(content, (str, list)):
                out.append({"role": role, "content": content})
            else:
                out.append({"role": role, "content": str(content or "")})
        return out

    def _protocol(self, provider: str) -> str:
        meta = PROVIDERS.get(provider) or {}
        return str(meta.get("protocol") or "chat_completions")

    @staticmethod
    def _responses_input(messages: list[dict]) -> list[dict]:
        out = []
        for row in ExternalAPIService._clean_messages(messages):
            role = str(row.get("role") or "user")
            if role == "tool":
                role = "user"
            content = row.get("content")
            # The current LlamaForge text chat passes strings. Keep the adapter
            # conservative; unsupported multimodal payloads are not synthesized.
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content or "")
            out.append({"role": role, "content": content})
        return out

    def _payload(self, provider: str, model: str, messages: list[dict], *, temperature: float, top_p: float, max_tokens: int, stream: bool, json_mode: bool = False, minimal: bool = False) -> dict[str, Any]:
        if self._protocol(provider) == "responses":
            payload: dict[str, Any] = {
                "model": model,
                "input": self._responses_input(messages),
                "stream": bool(stream),
                "max_output_tokens": int(max_tokens),
            }
            # Some reasoning models intentionally do not accept sampling knobs.
            # Try them once for compatible models, then retry a 400 without them.
            if not minimal:
                payload["temperature"] = float(temperature)
                payload["top_p"] = float(top_p)
            return payload
        payload = {
            "model": model,
            "messages": self._clean_messages(messages),
            "stream": bool(stream),
            "max_tokens": int(max_tokens),
        }
        if not minimal:
            payload["temperature"] = float(temperature)
            payload["top_p"] = float(top_p)
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
        return payload

    def _open_chat(self, provider: str, key: str, payload: dict, timeout: int):
        url = self._url(provider, "chat_path")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        opener, route = external_api_opener()
        req = urllib.request.Request(url, data=body, method="POST", headers=self._headers(provider, key))
        return opener, req, body, route

    @staticmethod
    def _extract_response_text(obj: dict) -> str:
        pieces: list[str] = []
        for item in obj.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "text"} and part.get("text") is not None:
                    pieces.append(str(part.get("text") or ""))
        return "".join(pieces)

    def chat_completion(self, provider: str, model: str, messages: list[dict], *, temperature: float = 0.2, top_p: float = 0.9, max_tokens: int = 2048, json_mode: bool = False, timeout: int = 180, cancel: threading.Event | None = None, output_syntax: dict[str, str] | None = None) -> dict[str, Any]:
        provider = str(provider or "").lower(); model = str(model or "").strip()
        key = self.secrets.get(provider)
        if not key: raise RuntimeError(f"{self.provider_name(provider)} API key is not configured")
        if not model: raise RuntimeError("No external API model is selected")
        last_error = None
        retry_delays = (0.35, 0.9)
        transient_http = {408, 425, 429, 500, 502, 503, 504}
        transient_network = (
            urllib.error.URLError, socket.timeout, TimeoutError,
            http.client.RemoteDisconnected, http.client.IncompleteRead, http.client.BadStatusLine,
            ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
        )

        def retry_wait(delay: float) -> None:
            if cancel is not None:
                if cancel.wait(delay):
                    raise RuntimeError("Generation cancelled")
            else:
                time.sleep(delay)

        for minimal in (False, True):
            payload = self._payload(provider, model, messages, temperature=temperature, top_p=top_p, max_tokens=max_tokens, stream=False, json_mode=json_mode, minimal=minimal)
            body = b""; route: dict[str, Any] = {}
            fall_back_to_minimal = False
            for attempt in range(len(retry_delays) + 1):
                opener, req, body, route = self._open_chat(provider, key, payload, timeout)
                try:
                    if cancel and cancel.is_set(): raise RuntimeError("Generation cancelled")
                    with opener.open(req, timeout=timeout) as response:
                        raw = response.read()
                        rate = self._rate_headers(response.headers)
                    if cancel and cancel.is_set(): raise RuntimeError("Generation cancelled")
                    obj = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                    if obj.get("error"):
                        raise RuntimeError(str(obj.get("error")))
                    usage_raw = obj.get("usage") or {}
                    usage = self.normalize_usage(usage_raw)
                    if self._protocol(provider) == "responses":
                        content = self._extract_response_text(obj)
                        if not content and str(obj.get("status") or "") not in {"completed", ""}:
                            raise RuntimeError(f"{self.provider_name(provider)} response status: {obj.get('status')}")
                        finish_reason = str(obj.get("status") or "completed")
                        reasoning_content = ""
                        tool_calls = []
                    else:
                        choices = obj.get("choices") or []
                        if not choices: raise RuntimeError(f"{self.provider_name(provider)} returned no chat choices")
                        msg = choices[0].get("message") or {}
                        content = msg.get("content") or ""
                        reasoning_content = msg.get("reasoning_content") or ""
                        tool_calls = msg.get("tool_calls") or []
                        finish_reason = choices[0].get("finish_reason")
                    if isinstance(content, str):
                        content, tagged_reasoning = _split_tagged_reasoning(content, output_syntax)
                        if tagged_reasoning:
                            reasoning_content = "\n".join(x for x in (str(reasoning_content or "").strip(), tagged_reasoning) if x)
                    self._record_usage(provider, model, usage_raw, sent=len(body), received=len(raw), rate_headers=rate)
                    return {
                        "content": content,
                        "reasoning_content": reasoning_content,
                        "tool_calls": tool_calls,
                        "finish_reason": finish_reason,
                        "usage": {"prompt_tokens": usage["input_tokens"], "completion_tokens": usage["output_tokens"], "total_tokens": usage["total_tokens"], "provider": provider, "model": model},
                        "route": {k: v for k, v in route.items() if k != "url"},
                    }
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:2400]
                    last_error = RuntimeError(f"{self.provider_name(provider)} HTTP {exc.code}: {detail or exc.reason}")
                    if exc.code == 400 and not minimal:
                        fall_back_to_minimal = True
                        break
                    if exc.code in transient_http and attempt < len(retry_delays):
                        retry_wait(retry_delays[attempt])
                        continue
                    self._record_usage(provider, model, None, sent=len(body), received=len(detail.encode("utf-8")), error=True)
                    raise last_error from exc
                except transient_network as exc:
                    route_text = "through Psiphon/system proxy" if route.get("detected") else "directly"
                    last_error = RuntimeError(f"Could not reach {self.provider_name(provider)} {route_text}: {exc}")
                    if attempt < len(retry_delays):
                        retry_wait(retry_delays[attempt])
                        continue
                    self._record_usage(provider, model, None, sent=len(body), error=True)
                    raise last_error from exc
            if fall_back_to_minimal:
                continue
        if last_error:
            raise last_error
        raise RuntimeError("External API request failed")

    def test_model(self, provider: str, model: str, *, output_syntax: dict[str, str] | None = None) -> dict[str, Any]:
        """Send one small, explicit greeting request without changing the active model."""
        result = self.chat_completion(
            provider,
            model,
            [{"role": "user", "content": "سلام"}],
            temperature=0.1,
            top_p=1.0,
            max_tokens=24,
            timeout=45,
            output_syntax=output_syntax,
        )
        response = str(result.get("content") or "").strip()
        if not response:
            raise RuntimeError(f"{self.provider_name(provider)} returned no text for the سلام test")
        return {
            "ok": True,
            "provider": str(provider),
            "model": str(model),
            "prompt": "سلام",
            "response": response,
            "usage": result.get("usage") or {},
            "route": result.get("route") or {},
        }

    def stream_chat(self, provider: str, model: str, messages: list[dict], *, temperature: float = 0.7, top_p: float = 0.95, max_tokens: int = 2048, timeout: int = 900, cancel: threading.Event | None = None, output_syntax: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
        provider = str(provider or "").lower(); model = str(model or "").strip()
        key = self.secrets.get(provider)
        if not key: raise RuntimeError(f"{self.provider_name(provider)} API key is not configured")
        if not model: raise RuntimeError("No external API model is selected")
        response = None; body = b""; route = {}; minimal = False
        retry_delays = (0.35, 0.9)
        transient_http = {408, 425, 429, 500, 502, 503, 504}
        transient_network = (
            urllib.error.URLError, socket.timeout, TimeoutError,
            http.client.RemoteDisconnected, http.client.IncompleteRead, http.client.BadStatusLine,
            ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
        )

        def retry_wait(delay: float) -> None:
            if cancel is not None:
                if cancel.wait(delay):
                    raise RuntimeError("Generation cancelled")
            else:
                time.sleep(delay)

        for attempt_minimal in (False, True):
            payload = self._payload(provider, model, messages, temperature=temperature, top_p=top_p, max_tokens=max_tokens, stream=True, minimal=attempt_minimal)
            fall_back_to_minimal = False
            for attempt in range(len(retry_delays) + 1):
                opener, req, body, route = self._open_chat(provider, key, payload, timeout)
                try:
                    if cancel and cancel.is_set(): raise RuntimeError("Generation cancelled")
                    response = opener.open(req, timeout=timeout)
                    minimal = attempt_minimal
                    break
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:2400]
                    if exc.code == 400 and not attempt_minimal:
                        fall_back_to_minimal = True
                        break
                    if exc.code in transient_http and attempt < len(retry_delays):
                        retry_wait(retry_delays[attempt])
                        continue
                    self._record_usage(provider, model, None, sent=len(body), received=len(detail.encode("utf-8")), error=True)
                    raise RuntimeError(f"{self.provider_name(provider)} HTTP {exc.code}: {detail or exc.reason}") from exc
                except transient_network as exc:
                    route_text = "through Psiphon/system proxy" if route.get("detected") else "directly"
                    if attempt < len(retry_delays):
                        retry_wait(retry_delays[attempt])
                        continue
                    self._record_usage(provider, model, None, sent=len(body), error=True)
                    raise RuntimeError(f"Could not reach {self.provider_name(provider)} {route_text}: {exc}") from exc
            if response is not None:
                break
            if fall_back_to_minimal:
                continue
        if response is None:
            raise RuntimeError("External API request failed")

        rate = self._rate_headers(response.headers)
        received = 0
        usage_raw: dict[str, Any] = {}
        completed = False
        finish_reason = ""
        protocol = self._protocol(provider)
        reasoning_parser = _ReasoningTagStreamParser(output_syntax)
        try:
            with response:
                for raw_line in response:
                    received += len(raw_line)
                    if cancel and cancel.is_set():
                        try: response.close()
                        except Exception: pass
                        raise RuntimeError("Generation cancelled")
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        completed = True
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise RuntimeError(str(obj.get("error")))
                    if protocol == "responses":
                        etype = str(obj.get("type") or "")
                        if etype == "response.output_text.delta" and obj.get("delta"):
                            for kind, text in reasoning_parser.feed(str(obj.get("delta"))):
                                yield {"type": kind, "delta": text}
                        elif etype == "response.completed":
                            response_obj = obj.get("response") or {}
                            if isinstance(response_obj.get("usage"), dict):
                                usage_raw = response_obj["usage"]
                            completed = True
                            finish_reason = "stop"
                        elif etype == "response.incomplete":
                            response_obj = obj.get("response") or {}
                            if isinstance(response_obj.get("usage"), dict):
                                usage_raw = response_obj["usage"]
                            finish_reason = str((response_obj.get("incomplete_details") or {}).get("reason") or "incomplete")
                            completed = True
                        elif etype == "response.failed":
                            raise RuntimeError(str((obj.get("response") or {}).get("error") or "OpenAI response failed"))
                    else:
                        if isinstance(obj.get("usage"), dict):
                            usage_raw = obj["usage"]
                        choice = (obj.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            yield {"type": "reasoning", "delta": str(reasoning)}
                        text = delta.get("content")
                        if text:
                            for kind, text_part in reasoning_parser.feed(str(text)):
                                yield {"type": kind, "delta": text_part}
                        if choice.get("finish_reason") is not None:
                            finish_reason = str(choice["finish_reason"])
                            completed = True
            if not completed:
                raise RuntimeError("External API stream ended before completion")
            for kind, text_part in reasoning_parser.feed(final=True):
                yield {"type": kind, "delta": text_part}
            final_answer = reasoning_parser.final_answer_from_reasoning()
            if final_answer:
                yield {"type": "text", "delta": final_answer}
            usage = self.normalize_usage(usage_raw)
            self._record_usage(provider, model, usage_raw, sent=len(body), received=received, rate_headers=rate)
            yield {"type": "meta", "usage": {"prompt_tokens": usage["input_tokens"], "completion_tokens": usage["output_tokens"], "total_tokens": usage["total_tokens"], "provider": provider, "model": model}}
            if finish_reason:
                yield {"type": "meta", "finish_reason": finish_reason, "output_limit": int(max_tokens)}
            yield {"type": "meta", "provider": {"id": provider, "model": model, "route": {k: v for k, v in route.items() if k != "url"}, "compatibility_fallback": minimal}}
        except Exception:
            self._record_usage(provider, model, None, sent=0, received=received, error=True)
            raise
