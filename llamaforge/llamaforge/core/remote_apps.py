from __future__ import annotations
from .request_tracing import record, current_trace
from contextvars import copy_context

import hashlib
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR

REMOTE_APPS_PATH = APP_DIR / "agent" / "remote_apps.json"
KEYRING_SERVICE = "LlamaForge.RemoteApps"
UA = "LlamaForge-RemoteSync/0.26"
BRIDGE_PAYLOAD_DIR = Path(__file__).resolve().parents[2] / "bridge_payload" / "web-bridge"


def _keyring_get(app_id: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, app_id)
    except Exception:
        return None


def _keyring_set(app_id: str, token: str) -> bool:
    try:
        import keyring
        if token:
            keyring.set_password(KEYRING_SERVICE, app_id, token)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, app_id)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _clean_connect_url(url: str) -> tuple[str, str]:
    raw = str(url or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Connection URL must be an http:// or https:// URL")
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    token = ""
    kept = []
    for key, value in pairs:
        if key.lower() in {"token", "agent_token", "aib_token"} and not token:
            token = value
        else:
            kept.append((key, value))
    clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(kept), parsed.fragment))
    return clean, token


def _public_text(value: Any, limit: int = 320) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]{12,}", r"\1[redacted]", text)
    text = re.sub(r"(?i)([?&](?:token|agent_token|aib_token|key)=)[^&#\s]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(x-aib-token\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)
    return text[:limit]


class RemoteTaskCancelled(RuntimeError):
    pass


class RemoteAppManager:
    """Background site/app bridge for the local LlamaForge Agent.

    A remote app publishes a small connection descriptor. LlamaForge stores that
    descriptor, polls for claimed tasks only while a local model is ready, runs the
    normal AgentEngine, and pushes activity/partial/final output back to the app.
    """

    def __init__(
        self,
        task_runner: Callable[[dict[str, Any], dict[str, Any], Callable[[dict[str, Any]], None]], str],
        runtime_info: Callable[[], dict[str, Any]],
        control_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        workspace_importer: Callable[[str, dict[str, Any]], None] | None = None,
        workspace_exporter: Callable[[str], dict[str, Any]] | None = None,
        log: Callable[[str], None] | None = None,
        app_version: str = "0.33.0-adaptive-engine",
        request_traces=None,
    ):
        self.request_traces = request_traces
        self.task_runner = task_runner
        self.runtime_info = runtime_info
        self.control_handler = control_handler
        self.workspace_importer = workspace_importer
        self.workspace_exporter = workspace_exporter
        self.log = log or (lambda _line: None)
        self.app_version = app_version
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.workers: dict[str, threading.Thread] = {}
        self.live: dict[str, dict[str, Any]] = {}
        self.workspace_revisions: dict[str, int] = {}
        self.workspace_last_sync: dict[str, float] = {}
        REMOTE_APPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not REMOTE_APPS_PATH.exists():
            REMOTE_APPS_PATH.write_text("[]", encoding="utf-8")
        self.supervisor = threading.Thread(target=self._supervisor_loop, name="remote-app-supervisor", daemon=True)
        self.supervisor.start()

    def _load(self) -> list[dict[str, Any]]:
        try:
            rows = json.loads(REMOTE_APPS_PATH.read_text(encoding="utf-8"))
            return rows if isinstance(rows, list) else []
        except Exception:
            return []

    def _save(self, rows: list[dict[str, Any]]) -> None:
        tmp = REMOTE_APPS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(REMOTE_APPS_PATH)

    def _token(self, row: dict[str, Any]) -> str:
        app_id = str(row.get("id") or "")
        return str(_keyring_get(app_id) or row.get("token_fallback") or "")

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        app_id = str(row.get("id") or "")
        state = dict(self.live.get(app_id) or {})
        return {
            "id": app_id,
            "name": str(row.get("name") or "Connected app"),
            "base_url": str(row.get("base_url") or ""),
            "chat_url": str(row.get("chat_url") or ""),
            "connect_url": str(row.get("connect_url") or ""),
            "protocol": str(row.get("protocol") or ""),
            "protocol_version": str(row.get("protocol_version") or ""),
            "remote_version": str(row.get("remote_version") or ""),
            "bridge_update_supported": bool(str((row.get("endpoints") or {}).get("bridge_update") or "")),
            "enabled": bool(row.get("enabled", True)),
            "token_configured": bool(self._token(row)),
            "added_at": row.get("added_at"),
            "state": state.get("state", "starting" if row.get("enabled", True) else "disabled"),
            "last_seen": state.get("last_seen"),
            "last_error": state.get("last_error", ""),
            "active_message_id": state.get("active_message_id"),
            "tasks_completed": int(state.get("tasks_completed") or 0),
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            rows = self._load()
            return {
                "ready": True,
                "storage": str(REMOTE_APPS_PATH),
                "apps": [self._public(r) for r in rows],
                "active_workers": sum(1 for t in self.workers.values() if t.is_alive()),
            }

    def _request_json(
        self,
        url: str,
        token: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
        max_bytes: int = 2_000_000,
    ) -> dict[str, Any]:
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if token:
            headers["X-AIB-Token"] = token
            headers["Authorization"] = "Bearer " + token
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=max(2.0, min(float(timeout), 45.0))) as resp:
                limit = max(1024, min(int(max_bytes or 2_000_000), 100_000_000))
                raw = resp.read(limit + 1)
                if len(raw) > limit:
                    raise RuntimeError("Remote app response exceeded the supported size")
                obj = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                if not isinstance(obj, dict):
                    raise RuntimeError("Remote app returned non-object JSON")
                if int(getattr(resp, "status", 200)) >= 400:
                    raise RuntimeError(str(obj.get("error") or f"HTTP {resp.status}"))
                return obj
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read(200_000).decode("utf-8", errors="replace") or "{}")
                msg = body.get("error") if isinstance(body, dict) else None
            except Exception:
                msg = None
            raise RuntimeError(str(msg or f"HTTP {exc.code}")) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Connection failed: {exc}") from exc

    def _request_bytes_json(
        self,
        url: str,
        token: str,
        data: bytes,
        *,
        method: str = "PUT",
        headers: dict[str, str] | None = None,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        request_headers = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/zip"}
        if token:
            request_headers["X-AIB-Token"] = token
            request_headers["Authorization"] = "Bearer " + token
        request_headers.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=max(3.0, min(float(timeout), 90.0))) as resp:
                raw = resp.read(2_000_000)
                obj = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                if not isinstance(obj, dict):
                    raise RuntimeError("Remote app returned non-object JSON")
                if int(getattr(resp, "status", 200)) >= 400:
                    raise RuntimeError(str(obj.get("error") or f"HTTP {resp.status}"))
                return obj
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read(200_000).decode("utf-8", errors="replace") or "{}")
                msg = body.get("error") if isinstance(body, dict) else None
            except Exception:
                msg = None
            raise RuntimeError(str(msg or f"HTTP {exc.code}")) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Connection failed: {exc}") from exc

    @staticmethod
    def _bridge_payload_version() -> str:
        version_file = BRIDGE_PAYLOAD_DIR / "VERSION"
        try:
            return version_file.read_text(encoding="utf-8").strip() or "unknown"
        except Exception:
            return "unknown"

    @classmethod
    def _bridge_update_package(cls) -> tuple[bytes, dict[str, Any]]:
        if not BRIDGE_PAYLOAD_DIR.is_dir():
            raise RuntimeError(f"Embedded Web Bridge payload is missing: {BRIDGE_PAYLOAD_DIR}")
        files: dict[str, str] = {}
        rows: list[tuple[str, bytes]] = []
        for path in sorted(BRIDGE_PAYLOAD_DIR.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(BRIDGE_PAYLOAD_DIR).as_posix()
            if rel in {"data/config.php"} or rel.startswith("data/messages/") or rel.startswith("data/ratelimits/") or rel.startswith(".aib-updates/"):
                continue
            data = path.read_bytes()
            files[rel] = hashlib.sha256(data).hexdigest()
            rows.append((rel, data))
        manifest = {
            "format": "llamaforge-web-bridge-update-v1",
            "version": cls._bridge_payload_version(),
            "files": files,
        }
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for rel, data in rows:
                zf.writestr(rel, data)
            zf.writestr("bridge-manifest.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        blob = bio.getvalue()
        if len(blob) > 12_000_000:
            raise RuntimeError("Embedded Web Bridge update package exceeds the supported size")
        return blob, manifest

    def add(self, connect_url: str, token: str = "") -> dict[str, Any]:
        clean_url, embedded = _clean_connect_url(connect_url)
        token = str(token or embedded or "").strip()
        parsed = urllib.parse.urlsplit(clean_url)
        path = parsed.path or "/"
        directory = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
        sibling = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, directory + "connect.php", "", ""))
        original = str(connect_url or "").strip()
        basename = path.rstrip("/").rsplit("/", 1)[-1].lower()
        # Never probe a legacy agent.php URL without an explicit action: its
        # default action claims a pending message. Discover sibling connect.php
        # first so adding a connection is side-effect free.
        if basename == "agent.php":
            candidates = [sibling]
        elif basename == "connect.php":
            candidates = [original, clean_url]
        elif path.endswith("/") or not basename:
            candidates = [sibling, original]
        else:
            candidates = [original, clean_url, sibling]
        candidates = list(dict.fromkeys(x for x in candidates if x))
        desc = None
        selected_url = clean_url
        last_error = ""
        for probe_url in candidates:
            try:
                candidate = self._request_json(probe_url, token, timeout=15)
                if str(candidate.get("protocol") or "") == "LlamaForge Remote App Sync":
                    desc = candidate
                    selected_url, _ = _clean_connect_url(probe_url)
                    break
                last_error = "endpoint returned a different protocol"
            except Exception as exc:
                last_error = str(exc)
        if not isinstance(desc, dict):
            raise RuntimeError("Could not discover a LlamaForge Remote App Sync endpoint. " + last_error)
        clean_url = selected_url
        app = desc.get("app") if isinstance(desc.get("app"), dict) else {}
        endpoints = desc.get("endpoints") if isinstance(desc.get("endpoints"), dict) else {}
        required = {"poll", "heartbeat", "activity", "typing", "reply"}
        missing = sorted(k for k in required if not str(endpoints.get(k) or ""))
        if missing:
            raise RuntimeError("Connection descriptor is missing endpoints: " + ", ".join(missing))
        app_id = str(app.get("id") or ("remote_" + uuid.uuid4().hex[:16]))
        row = {
            "id": app_id,
            "name": str(app.get("name") or "Connected website"),
            "base_url": str(app.get("base_url") or ""),
            "chat_url": str(app.get("chat_url") or ""),
            "connect_url": clean_url,
            "protocol": str(desc.get("protocol") or ""),
            "protocol_version": str(desc.get("protocol_version") or ""),
            "remote_version": str(app.get("version") or ""),
            "capabilities": desc.get("capabilities") if isinstance(desc.get("capabilities"), dict) else {},
            "endpoints": {str(k): str(v) for k, v in endpoints.items()},
            "enabled": True,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "token_fallback": "",
        }
        if not _keyring_set(app_id, token):
            row["token_fallback"] = token
        with self.lock:
            rows = [r for r in self._load() if str(r.get("id") or "") != app_id]
            rows.append(row)
            self._save(rows)
            self.live[app_id] = {"state": "connected", "last_seen": time.time(), "last_error": "", "tasks_completed": 0}
        self._ensure_worker(row)
        self.log(f"[remote-app] added {row['name']} id={app_id} base={row['base_url']}")
        return self._public(row)

    def remove(self, app_id: str) -> bool:
        app_id = str(app_id or "")
        with self.lock:
            rows = self._load()
            new_rows = [r for r in rows if str(r.get("id") or "") != app_id]
            if len(new_rows) == len(rows):
                return False
            self._save(new_rows)
            self.live.pop(app_id, None)
        _keyring_set(app_id, "")
        self.log(f"[remote-app] removed {app_id}")
        return True

    def set_enabled(self, app_id: str, enabled: bool) -> dict[str, Any]:
        app_id = str(app_id or "")
        target = None
        with self.lock:
            rows = self._load()
            for row in rows:
                if str(row.get("id") or "") == app_id:
                    row["enabled"] = bool(enabled)
                    target = row
                    break
            if target is None:
                raise RuntimeError("Connected app not found")
            self._save(rows)
            self.live.setdefault(app_id, {})["state"] = "starting" if enabled else "disabled"
        if enabled:
            self._ensure_worker(target)
        return self._public(target)

    def test(self, app_id: str) -> dict[str, Any]:
        row = next((r for r in self._load() if str(r.get("id") or "") == str(app_id or "")), None)
        if not row:
            raise RuntimeError("Connected app not found")
        desc = self._request_json(str(row.get("connect_url") or ""), self._token(row), timeout=15)
        ok = str(desc.get("protocol") or "") == "LlamaForge Remote App Sync"
        if not ok:
            raise RuntimeError("Unexpected connection protocol")
        app = desc.get("app") if isinstance(desc.get("app"), dict) else {}
        endpoints = desc.get("endpoints") if isinstance(desc.get("endpoints"), dict) else {}
        with self.lock:
            rows = self._load()
            for saved in rows:
                if str(saved.get("id") or "") == str(app_id or ""):
                    saved["remote_version"] = str(app.get("version") or saved.get("remote_version") or "")
                    saved["protocol_version"] = str(desc.get("protocol_version") or saved.get("protocol_version") or "")
                    saved["capabilities"] = desc.get("capabilities") if isinstance(desc.get("capabilities"), dict) else saved.get("capabilities", {})
                    if endpoints:
                        saved["endpoints"] = {str(k): str(v) for k, v in endpoints.items()}
                    row = saved
                    break
            self._save(rows)
            self.live.setdefault(str(row.get("id")), {}).update({"state": "connected", "last_seen": time.time(), "last_error": ""})
        return {"ok": True, "app": self._public(row), "remote": app}

    def bridge_update_status(self, app_id: str) -> dict[str, Any]:
        row = next((r for r in self._load() if str(r.get("id") or "") == str(app_id or "")), None)
        if not row:
            raise RuntimeError("Connected app not found")
        endpoint = str((row.get("endpoints") or {}).get("bridge_update") or "")
        if not endpoint:
            raise RuntimeError("This website Bridge does not expose the remote updater yet. Install the updater-capable Bridge once, then future updates can be done from LlamaForge.")
        remote = self._request_json(endpoint + (("&" if "?" in endpoint else "?") + "action=status"), self._token(row), timeout=15)
        return {"ok": True, "local_bridge_version": self._bridge_payload_version(), "remote": remote, "app": self._public(row)}

    def update_bridge(self, app_id: str) -> dict[str, Any]:
        app_id = str(app_id or "")
        log = getattr(self, "log", lambda _line: None)
        row = next((r for r in self._load() if str(r.get("id") or "") == app_id), None)
        if not row:
            raise RuntimeError("Connected app not found")
        endpoint = str((row.get("endpoints") or {}).get("bridge_update") or "")
        if not endpoint:
            raise RuntimeError("This website Bridge is too old for one-click update. Replace it once with the updater-capable Bridge included in this LlamaForge build.")
        if str((getattr(self, "live", {}).get(app_id) or {}).get("active_message_id") or ""):
            raise RuntimeError("The connected website is processing a message. Finish or stop that response before updating the Web Bridge.")

        token = self._token(row)
        was_enabled = bool(row.get("enabled", False))
        before_status: dict[str, Any] = {}
        try:
            before_status = self._request_json(endpoint + (("&" if "?" in endpoint else "?") + "action=status"), token, timeout=15)
        except Exception:
            before_status = {}

        # The legacy updater deleted live PHP files before copying replacements.
        # Pause the background long-poll worker first so the one-time migration to
        # the atomic updater cannot race against our own connector traffic.
        if was_enabled:
            log(f"[bridge:update] pausing connector app={app_id} before ZIP deployment")
            self.set_enabled(app_id, False)
            worker = getattr(self, "workers", {}).get(app_id)
            if worker and worker.is_alive():
                worker.join(timeout=40.0)
            if worker and worker.is_alive():
                self.set_enabled(app_id, True)
                raise RuntimeError("Could not pause the website connector safely; Bridge update was not started")

        blob, manifest = self._bridge_update_package()
        sha = hashlib.sha256(blob).hexdigest()
        log(f"[bridge:update] uploading ZIP bytes={len(blob)} sha256={sha[:12]} target={manifest.get('version')}")
        result: dict[str, Any]
        after_status: dict[str, Any] = {}
        try:
            result = self._request_bytes_json(
                endpoint + (("&" if "?" in endpoint else "?") + "action=install"),
                token, blob, method="PUT",
                headers={"X-AIB-Action": "install", "X-AIB-Package-SHA256": sha, "X-AIB-Target-Version": str(manifest.get("version") or "")},
                timeout=90,
            )
            # A successful authenticated status call using the same saved token is
            # itself a strong check that the Connection token did not rotate. New
            # updater versions also expose non-secret credential fingerprints.
            status_error: Exception | None = None
            for attempt in range(4):
                try:
                    after_status = self._request_json(endpoint + (("&" if "?" in endpoint else "?") + "action=status"), token, timeout=20)
                    status_error = None
                    break
                except Exception as exc:
                    status_error = exc
                    if attempt < 3:
                        time.sleep(0.4 * (attempt + 1))
            if status_error is not None:
                log(f"[bridge:update] post-install credential verification deferred: {status_error}")
                after_status = {}
            before_agent = str(before_status.get("agent_token_id") or "")
            after_agent = str(after_status.get("agent_token_id") or "")
            before_owner = str(before_status.get("owner_key_id") or "")
            after_owner = str(after_status.get("owner_key_id") or "")
            if before_agent and after_agent and before_agent != after_agent:
                raise RuntimeError("Web Bridge update changed the connection-token identity; update rejected")
            if before_owner and after_owner and before_owner != after_owner:
                raise RuntimeError("Web Bridge update changed the management-key identity; update rejected")
            log(f"[bridge:update] activated={after_status.get('bridge_version') or manifest.get('version')} credentials=preserved")
        finally:
            if was_enabled:
                try:
                    self.set_enabled(app_id, True)
                    log(f"[bridge:update] connector resumed app={app_id}")
                except Exception as exc:
                    log(f"[bridge:update] connector resume failed app={app_id}: {exc}")

        # Re-probe the descriptor so the UI immediately reflects the new version/endpoints.
        try:
            refreshed = self.test(app_id)
        except Exception:
            latest = next((r for r in self._load() if str(r.get("id") or "") == app_id), row)
            refreshed = {"app": self._public(latest)}
        return {
            "ok": bool(result.get("ok", True)),
            "install": result,
            "package_version": manifest.get("version"),
            "credential_identity_preserved": bool(after_status) or bool(result.get("credentials_preserved", False)),
            "update_pipeline": str(after_status.get("update_pipeline") or result.get("activation") or "zip_upload -> staged_extract -> atomic_activate"),
            "app": refreshed.get("app"),
        }

    def rollback_bridge(self, app_id: str, backup_id: str) -> dict[str, Any]:
        row = next((r for r in self._load() if str(r.get("id") or "") == str(app_id or "")), None)
        if not row:
            raise RuntimeError("Connected app not found")
        endpoint = str((row.get("endpoints") or {}).get("bridge_update") or "")
        if not endpoint:
            raise RuntimeError("This website Bridge does not support remote rollback")
        result = self._request_json(
            endpoint + (("&" if "?" in endpoint else "?") + "action=rollback"),
            self._token(row), method="POST", payload={"backup_id": str(backup_id or "")}, timeout=60,
        )
        try:
            refreshed = self.test(app_id)
        except Exception:
            refreshed = {"app": self._public(row)}
        return {"ok": bool(result.get("ok", True)), "rollback": result, "app": refreshed.get("app")}

    def stop(self) -> None:
        self.stop_event.set()

    def _supervisor_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                for row in self._load():
                    if row.get("enabled", True):
                        self._ensure_worker(row)
            except Exception as exc:
                self.log(f"[remote-app:supervisor] {exc}")
            self.stop_event.wait(2.0)

    def _ensure_worker(self, row: dict[str, Any]) -> None:
        app_id = str(row.get("id") or "")
        if not app_id:
            return
        with self.lock:
            existing = self.workers.get(app_id)
            if existing and existing.is_alive():
                return
            worker = threading.Thread(target=self._worker_loop, args=(app_id,), name=f"remote-app-{app_id[-8:]}", daemon=True)
            self.workers[app_id] = worker
            worker.start()

    def _get_row(self, app_id: str) -> dict[str, Any] | None:
        return next((r for r in self._load() if str(r.get("id") or "") == app_id), None)

    def _set_live(self, app_id: str, **updates: Any) -> None:
        with self.lock:
            row = self.live.setdefault(app_id, {"tasks_completed": 0})
            row.update(updates)
            row["last_seen"] = time.time()

    def _heartbeat(self, row: dict[str, Any], state: str, message_id: str = "", control_result: dict[str, Any] | None = None) -> dict[str, Any]:
        info = self.runtime_info() or {}
        payload = {
            "action": "heartbeat",
            "agent_name": "LlamaForge Local Agent",
            "state": state,
            "model": str(info.get("model") or ""),
            "model_id": str(info.get("model_id") or ""),
            "model_ready": bool(info.get("ready")),
            "model_loading": bool(info.get("loading")),
            "models": info.get("models") if isinstance(info.get("models"), list) else [],
            "version": self.app_version,
            "message_id": message_id,
        }
        if isinstance(control_result, dict):
            payload["control_result"] = control_result
        return self._request_json(str((row.get("endpoints") or {}).get("heartbeat") or ""), self._token(row), method="POST", payload=payload, timeout=12)

    def _handle_remote_control(self, row: dict[str, Any], heartbeat: dict[str, Any]) -> bool:
        request = heartbeat.get("model_request") if isinstance(heartbeat, dict) and isinstance(heartbeat.get("model_request"), dict) else None
        if not request or not self.control_handler:
            return False
        request_id = str(request.get("request_id") or "")
        action = str(request.get("action") or "load").strip().lower()
        model_id = str(request.get("model_id") or "")
        if not request_id or (action != "unload" and not model_id):
            return False
        self.log(f"[remote-model] request={request_id} action={action} model_id={model_id}")
        self._set_live(str(row.get("id") or ""), state="loading_model", last_error="")
        try:
            result = self.control_handler(request)
            if not isinstance(result, dict):
                result = {"ok": False, "request_id": request_id, "action": action, "model_id": model_id, "error": "Invalid control result"}
        except Exception as exc:
            result = {"ok": False, "request_id": request_id, "action": action, "model_id": model_id, "error": str(exc)}
        try:
            self._heartbeat(row, "loading_model" if result.get("ok") and result.get("state") == "loading" else ("waiting_model" if result.get("ok") and result.get("state") in {"stopped","unloaded"} else ("polling" if result.get("ok") else "model_error")), control_result=result)
        except Exception as exc:
            self.log(f"[remote-model:ack] {exc}")
        return True

    @staticmethod
    def _activity_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
        if event.get("type") == "meta" and isinstance(event.get("trace"),dict):
            return {"type":"phase","phase":"diagnostic","label":"گزارش عیب‌یابی این درخواست در Logs برنامه ذخیره می‌شود",
                    "detail":str(event["trace"].get("id") or ""),"status":"done"}
        kind = str(event.get("event") or "")
        if event.get("type") != "agent":
            return None
        if kind == "route_decision":
            route = str(event.get("route") or "direct")
            sec = float(event.get("model_seconds") or 0.0)
            conf = int(event.get("confidence") or 0)
            detail = _public_text(event.get("summary") or "", 760)
            if sec: detail = (detail + (" · " if detail else "") + f"تصمیم مدل: {sec:.1f}s")
            if conf: detail = (detail + (" · " if detail else "") + f"اطمینان {conf}%")
            return {"type":"route_decision","phase":"route","label":"مدل: استفاده از Skill" if route == "skills" else "مدل: پاسخ مستقیم","detail":detail,"status":"done","ok":True}
        if kind == "route":
            route = str(event.get("route") or "agent")
            if route == "direct":
                return {"type":"route","phase":"route","label":"پاسخ مستقیم مدل؛ Skill لازم نیست","detail":"این پیام بدون Web/API/Browser مستقیماً به مدل محلی فرستاده شد.","status":"done","ok":True}
            return {"type":"route","phase":"route","label":"درخواست به Agent سپرده شد","detail":"درخواست به داده یا عملیات خارجی نیاز دارد؛ Skillهای مرتبط فعال شدند.","status":"done","ok":True}
        if kind == "context_policy":
            detail = f"Context {int(event.get('context_limit') or 0):,} · {int(event.get('skill_limit') or 0)} Skill · نگهداری {int(event.get('observation_keep') or 0)} Observation"
            return {"type":"context","phase":"context","label":"بودجه Context بهینه شد","detail":detail,"status":"done","ok":True}
        if kind == "direct_complete":
            sec = float(event.get("model_seconds") or 0.0)
            return {"type":"direct_complete","phase":"finalize","label":"پاسخ مستقیم مدل آماده شد","detail":f"زمان مدل: {sec:.1f} ثانیه" if sec else "بدون استفاده از Skill","status":"done","ok":True}
        if kind == "phase":
            return {"type": "phase", "phase": str(event.get("phase") or ""), "label": _public_text(event.get("label") or "Agent is working"), "status": "active"}
        if kind == "capabilities":
            skills = [str(x) for x in (event.get("skills") or [])[:8]]
            families = [str(x) for x in (event.get("families") or [])[:5]]
            cats = [str(x) for x in (event.get("categories") or [])[:5]]
            pieces = []
            if families: pieces.append("خانواده Skill: " + ", ".join(families))
            elif cats: pieces.append("دسته‌ها: " + ", ".join(cats))
            if skills: pieces.append("Skillها: " + ", ".join(skills))
            if event.get("model_seconds") is not None: pieces.append(f"تصمیم مدل: {float(event.get('model_seconds') or 0):.1f}s")
            return {"type": "capabilities", "phase": "capabilities", "label": "Skillهای مرتبط محدود و انتخاب شدند", "detail": _public_text(" · ".join(pieces), 1100), "status": "done", "ok": True}
        if kind == "thinking":
            step, max_steps = int(event.get("step") or 0), int(event.get("max_steps") or 0)
            return {"type": "thinking", "phase": "planning", "label": f"مدل در حال تصمیم‌گیری برای مرحله {step} از {max_steps}", "detail":"بررسی Observationها و انتخاب اقدام بعدی","step":step,"max_steps":max_steps,"status":"active"}
        if kind == "decision" and event.get("action") == "tool":
            skill = str(event.get("skill") or "")
            sec = float(event.get("model_seconds") or 0.0)
            detail = _public_text(event.get("summary") or "", 760)
            if sec: detail = (detail + (" · " if detail else "") + f"تصمیم مدل: {sec:.1f}s")
            return {"type":"decision","phase":"planning","label":"انتخاب Skill: " + skill,"detail":detail,"skill":skill,"status":"done","ok":True}
        if kind == "decision" and event.get("action") == "final":
            return {"type":"decision","phase":"finalize","label":"اطلاعات کافی است؛ ساخت پاسخ نهایی","detail":_public_text(event.get("summary") or "",760),"status":"active"}
        if kind == "tool_start":
            skill = str(event.get("tool") or "")
            args = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
            safe_parts = []
            for key in ("method","url","query","target","direction","index","amount","wait_seconds"):
                if key in args and args.get(key) not in (None, ""):
                    safe_parts.append(f"{key}={_public_text(args.get(key), 260)}")
            return {"type":"tool_start","phase":"execute","label":"اجرای " + skill,"detail":_public_text(" · ".join(safe_parts),900),"skill":skill,"status":"active"}
        if kind == "tool_result":
            skill = str(event.get("tool") or "")
            ok = bool(event.get("ok"))
            details = []
            if event.get("tool_seconds") is not None: details.append(f"{float(event.get('tool_seconds') or 0):.1f}s")
            if event.get("observation_chars"): details.append(f"{int(event.get('observation_chars') or 0):,} chars observation")
            if event.get("failure_kind"): details.append("خطا: " + str(event.get("failure_kind")))
            if event.get("fallbacks"): details.append("fallback: " + ", ".join(str(x) for x in (event.get("fallbacks") or [])[:4]))
            if event.get("error_preview"): details.append(_public_text(event.get("error_preview"), 420))
            return {"type":"tool_result","phase":"observe","label":("نتیجه " + skill + " دریافت شد") if ok else ("اجرای " + skill + " ناموفق بود"),"detail":_public_text(" · ".join(details),1000),"skill":skill,"status":"done" if ok else "error","ok":ok,"error":_public_text(event.get("error_preview") or "",900) if not ok else ""}
        if kind == "policy":
            return {"type":"policy","phase":"policy","label":_public_text(event.get("label") or "قانون اجرایی اعمال شد"),"detail":_public_text(event.get("policy") or "",500),"status":"done","ok":True}
        if kind == "preflight_failed":
            return {"type":"error","phase":"preflight","label":"پیش‌نیاز Skill تأیید نشد","skill":str(event.get("tool") or ""),"error":_public_text(event.get("error") or "",900),"status":"error","ok":False}
        if kind == "decision_error":
            return {"type":"error","phase":"planning","label":"خروجی کنترل مدل معتبر نبود","detail":"Runtime به مسیر بازیابی پاسخ رفت.","status":"error","ok":False}
        return None

    def _post_activity(self, row: dict[str, Any], message_id: str, event: dict[str, Any], *, visible: bool = True) -> dict[str, Any]:
        info = self.runtime_info() or {}
        payload = {
            "action": "activity",
            "message_id": message_id,
            "event": event,
            "visible": bool(visible),
            "agent_name": "LlamaForge Local Agent",
            "model": str(info.get("model") or ""),
            "version": self.app_version,
        }
        result = self._request_json(str((row.get("endpoints") or {}).get("activity") or ""), self._token(row), method="POST", payload=payload, timeout=12)
        if result.get("cancel_requested"):
            raise RemoteTaskCancelled("Remote user stopped this response")
        return result

    def _post_typing(self, row: dict[str, Any], message_id: str, text: str) -> None:
        payload = {
            "action": "typing", "message_id": message_id, "partial_answer": text[-50000:],
            "agent_name": "LlamaForge Local Agent",
        }
        result = self._request_json(str((row.get("endpoints") or {}).get("typing") or ""), self._token(row), method="POST", payload=payload, timeout=12)
        if result.get("cancel_requested"):
            raise RemoteTaskCancelled("Remote user stopped this response")

    def _post_reply(self, row: dict[str, Any], message_id: str, answer: str) -> None:
        payload = {
            "action": "reply", "message_id": message_id, "answer": answer,
            "agent_name": "LlamaForge Local Agent",
        }
        self._request_json(str((row.get("endpoints") or {}).get("reply") or ""), self._token(row), method="POST", payload=payload, timeout=15)

    @staticmethod
    def _workspace_scope_id(row: dict[str, Any], owner_hash: str) -> str:
        app_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(row.get("id") or "remote"))[:48]
        owner = re.sub(r"[^a-fA-F0-9]+", "", str(owner_hash or ""))[:64]
        return f"remote_{app_id}_{owner[:24]}"

    def _workspace_pull(self, row: dict[str, Any], owner_hash: str, revision: int | None = None, *, force: bool = False) -> tuple[str, int]:
        endpoint = str((row.get("endpoints") or {}).get("workspace_sync") or "")
        owner = str(owner_hash or "").strip().lower()
        scope = self._workspace_scope_id(row, owner)
        if not endpoint or not self.workspace_importer or not re.fullmatch(r"[a-f0-9]{64}", owner):
            return scope, int(revision or 0)
        key = str(row.get("id") or "") + ":" + owner
        known = int(self.workspace_revisions.get(key, 0) or 0)
        if not force and revision is not None and int(revision) == known:
            return scope, known
        result = self._request_json(endpoint, self._token(row), method="POST", payload={"action":"pull","owner_hash":owner}, timeout=60, max_bytes=96_000_000)
        snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else None
        if snapshot is None:
            raise RuntimeError("Workspace sync returned no snapshot")
        record("workspace.sync_pull", scope=scope, revision=result.get("revision"), snapshot=snapshot)
        self.workspace_importer(scope, snapshot)
        rev = int(result.get("revision") or revision or 0)
        self.workspace_revisions[key] = rev
        self.workspace_last_sync[key] = time.monotonic()
        return scope, rev

    def _workspace_push(self, row: dict[str, Any], owner_hash: str, scope: str, expected_revision: int) -> int:
        endpoint = str((row.get("endpoints") or {}).get("workspace_sync") or "")
        owner = str(owner_hash or "").strip().lower()
        if not endpoint or not self.workspace_exporter or not re.fullmatch(r"[a-f0-9]{64}", owner):
            return expected_revision
        snapshot = self.workspace_exporter(scope)
        record("workspace.sync_push",scope=scope,expected_revision=expected_revision,snapshot=snapshot)
        result = self._request_json(endpoint, self._token(row), method="POST", payload={"action":"push","owner_hash":owner,"expected_revision":int(expected_revision or 0),"snapshot":snapshot}, timeout=90, max_bytes=2_000_000)
        rev = int(result.get("revision") or expected_revision or 0)
        key = str(row.get("id") or "") + ":" + owner
        self.workspace_revisions[key] = rev
        self.workspace_last_sync[key] = time.monotonic()
        return rev

    def _sync_remote_workspaces(self, row: dict[str, Any]) -> None:
        endpoint = str((row.get("endpoints") or {}).get("workspace_sync") or "")
        if not endpoint or not self.workspace_importer:
            return
        app_id = str(row.get("id") or "")
        # Manifest is tiny. Only changed browser workspaces cause a content pull.
        manifest = self._request_json(endpoint + (("&" if "?" in endpoint else "?") + "action=manifest"), self._token(row), timeout=12)
        owners = manifest.get("owners") if isinstance(manifest.get("owners"), list) else []
        for item in owners[:30]:
            if not isinstance(item, dict):
                continue
            owner = str(item.get("owner_hash") or "").lower()
            rev = int(item.get("revision") or 0)
            key = app_id + ":" + owner
            if rev > 0 and rev != int(self.workspace_revisions.get(key, 0) or 0):
                try:
                    self._workspace_pull(row, owner, rev)
                except Exception as exc:
                    self.log(f"[remote-workspace:pull] app={app_id} owner={owner[:8]} error={exc}")

    def _run_task(self, row: dict[str, Any], item: dict[str, Any]) -> None:
        store=getattr(self,"request_traces",None)
        if store is None:
            return self._run_task_impl(row,item)
        messages=list(item.get("conversation_history") or [])
        messages.append({"role":"user","content":item.get("user_message",""),"attachments":item.get("attachments",[])})
        with store.request(origin="remote", app_id=str(row.get("id") or ""), message_id=str(item.get("message_id") or ""),
                           messages=messages, requested_model_id=item.get("requested_model_id"), version=self.app_version):
            return self._run_task_impl(row,item)

    def _run_task_impl(self, row: dict[str, Any], item: dict[str, Any]) -> None:
        app_id = str(row.get("id") or "")
        message_id = str(item.get("message_id") or "")
        owner_hash = str(item.get("workspace_owner") or "").strip().lower()
        workspace_scope = self._workspace_scope_id(row, owner_hash)
        workspace_revision = 0
        if owner_hash:
            try:
                workspace_scope, workspace_revision = self._workspace_pull(row, owner_hash, force=True)
            except Exception as exc:
                record("workspace.sync_error",phase="before",error=str(exc))
                self.log(f"[remote-workspace:pre-task] app={app_id} owner={owner_hash[:8]} error={exc}")
        item = dict(item)
        item["workspace_scope"] = workspace_scope
        self._set_live(app_id, state="working", active_message_id=message_id, last_error="")
        self._post_activity(row, message_id, {"type": "phase", "phase": "received", "label": "درخواست به LlamaForge رسید", "status": "done", "ok": True})
        lease_stop = threading.Event()
        task_cancel = threading.Event()
        item["_cancel"] = task_cancel

        def lease_loop():
            while not lease_stop.wait(18.0):
                try:
                    self._post_activity(row, message_id, {"type": "heartbeat", "label": "working"}, visible=False)
                except RemoteTaskCancelled:
                    task_cancel.set()
                    record("remote.cancel", source="heartbeat")
                    return
                except Exception:
                    pass

        lease_thread = threading.Thread(target=copy_context().run, args=(lease_loop,), name=f"remote-lease-{message_id[-6:]}", daemon=True)
        lease_thread.start()
        answer = ""
        last_typing = 0.0

        def emit(event: dict[str, Any]) -> None:
            nonlocal answer, last_typing
            if not isinstance(event, dict):
                return
            if event.get("type") == "text" and event.get("delta"):
                answer += str(event.get("delta") or "")
                now = time.monotonic()
                if now - last_typing >= (0.22 if len(answer) < 1600 else 0.35) or len(answer) < 120:
                    last_typing = now
                    try:
                        self._post_typing(row, message_id, answer)
                    except RemoteTaskCancelled:
                        raise
                    except Exception as exc:
                        self.log(f"[remote-app:typing] {exc}")
                return
            activity = self._activity_from_event(event)
            if activity:
                try:
                    self._post_activity(row, message_id, activity)
                except RemoteTaskCancelled:
                    raise
                except Exception as exc:
                    self.log(f"[remote-app:activity] {exc}")

        try:
            final = self.task_runner(row, item, emit)
            if str(final or "").strip():
                answer = str(final).strip()
            if not answer.strip():
                answer = "LlamaForge Agent completed the task but did not produce a text response."
            if owner_hash and self.workspace_exporter:
                try:
                    workspace_revision = self._workspace_push(row, owner_hash, workspace_scope, workspace_revision)
                except Exception as exc:
                    # Never discard the user's answer because a sync transport failed;
                    # the owner-scoped local copy remains available for the next retry.
                    record("workspace.sync_error",phase="after",error=str(exc))
                    self.log(f"[remote-workspace:post-task] app={app_id} owner={owner_hash[:8]} error={exc}")
            self._post_typing(row, message_id, answer)
            self._post_reply(row, message_id, answer)
            record("remote.delivery", message_id=message_id, status="delivered", answer=answer)
            current = int((self.live.get(app_id) or {}).get("tasks_completed") or 0) + 1
            self._set_live(app_id, state="connected", active_message_id=None, tasks_completed=current, last_error="")
            self.log(f"[remote-app] completed app={app_id} message={message_id}")
        except RemoteTaskCancelled as exc:
            if current_trace(): current_trace().meta.update(outcome="cancelled",error=str(exc))
            record("remote.cancel", error=str(exc))
            self.log(f"[remote-app:cancel] app={app_id} message={message_id} reason={exc}")
            self._set_live(app_id, state="connected", active_message_id=None, last_error="")
        except Exception as exc:
            error = str(exc)
            if current_trace(): current_trace().meta.update(outcome="error",error=error)
            record("remote.error",error=error)
            self.log(f"[remote-app:error] app={app_id} message={message_id} error={error}")
            try:
                self._post_activity(row, message_id, {"type": "error", "phase": "error", "label": "اجرای Agent متوقف شد", "error": error[:280], "status": "error", "ok": False})
                self._post_reply(row, message_id, "اجرای Agent با خطا متوقف شد: " + error[:1200])
            except Exception:
                pass
            self._set_live(app_id, state="error", active_message_id=None, last_error=error[:500])
        finally:
            lease_stop.set()
            task_cancel.set()

    def _worker_loop(self, app_id: str) -> None:
        backoff = 1.0
        last_idle_heartbeat = 0.0
        while not self.stop_event.is_set():
            row = self._get_row(app_id)
            if not row or not row.get("enabled", True):
                self._set_live(app_id, state="disabled")
                return
            info = self.runtime_info() or {}
            if not bool(info.get("ready")):
                state = "loading_model" if bool(info.get("loading")) else "waiting_model"
                self._set_live(app_id, state=state, last_error="")
                try:
                    hb = self._heartbeat(row, state)
                    self._handle_remote_control(row, hb)
                    last_idle_heartbeat = time.monotonic()
                except Exception as exc:
                    self._set_live(app_id, state="offline", last_error=str(exc)[:500])
                    self.stop_event.wait(min(4.0, backoff))
                    backoff = min(15.0, backoff * 1.5)
                    continue
                self.stop_event.wait(1.5)
                continue
            try:
                self._set_live(app_id, state="polling", last_error="")
                hb = self._heartbeat(row, "polling")
                if self._handle_remote_control(row, hb):
                    self.stop_event.wait(0.4)
                    continue
                sync_key = app_id + ":manifest"
                if time.monotonic() - float(self.workspace_last_sync.get(sync_key, 0.0) or 0.0) >= 8.0:
                    try:
                        self._sync_remote_workspaces(row)
                        self.workspace_last_sync[sync_key] = time.monotonic()
                    except Exception as exc:
                        self.log(f"[remote-workspace:manifest] app={app_id} error={exc}")
                poll_url = str((row.get("endpoints") or {}).get("poll") or "")
                data = self._request_json(poll_url, self._token(row), timeout=35)
                self._set_live(app_id, state="connected", last_error="")
                pending = data.get("pending_messages") if isinstance(data.get("pending_messages"), list) else []
                if pending:
                    for item in pending[:1]:
                        if isinstance(item, dict):
                            self._run_task(row, item)
                backoff = 1.0
            except Exception as exc:
                self._set_live(app_id, state="offline", last_error=str(exc)[:500])
                self.log(f"[remote-app:poll] app={app_id} error={exc}")
                self.stop_event.wait(backoff)
                backoff = min(15.0, backoff * 1.8)
