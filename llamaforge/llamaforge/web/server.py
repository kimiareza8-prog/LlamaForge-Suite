from __future__ import annotations

import dataclasses
import json
import hashlib
import mimetypes
import os
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..core.config import AppConfig, APP_DIR
from ..core.hardware import detect_hardware
from ..core.models import ModelManager, LocalModel, local_model_from_path
from ..core.net import get_status, stream_chat_events, count_chat_tokens, apply_chat_template, chat_completion_with_tools
from ..core.planner import make_plan, server_args, PROFILES, MEMORY_MODES
from ..core.processes import ManagedProcess, ProcessPolicy
from ..core.runtime import RuntimeManager
from ..core.smart_core import assess_model, candidate_plans
from ..core.smart_chat import choose_profile, response_quality, recovery_hint
from ..core.system_metrics import cpu_percent, memory_gb, process_cpu_percent
from ..core.native_dialogs import pick_file as native_pick_file, pick_folder as native_pick_folder
from ..core.personal_brain import PersonalBrain, BrainCancelled, BRAIN_LOG_DIR
from ..core.learning_data import validate_examples, obvious_non_teaching
from ..core.trainable_models import TrainableModelManager, portable_training_models_root
from ..core.app_logging import get_logger, LOG_FILE
from ..core.agent_tools import AgentRuntime, AgentPermissions
from ..core.remote_apps import RemoteAppManager, RemoteTaskCancelled
from contextlib import nullcontext
from ..core.cluster import ClusterManager
from ..core.autotune import AdaptiveTuner
from ..core.redaction import redact
from ..core.request_tracing import TraceStore, current_trace, record, logged_stream

APP_VERSION = "0.34.3-hotfix"
STATIC_ROOT = Path(__file__).parent / "static"

# Curated one-click bundles intentionally bind one chat artifact to one exact
# trainable source.  The user sees one model; GGUF is only the efficient
# inference representation and the Transformers checkpoint is the training
# representation of the same upstream model.
QUICK_MODELS = {
    "qwen2.5-1.5b-instruct": {
        "id": "qwen2.5-1.5b-instruct",
        "name": "Qwen2.5 1.5B Instruct",
        "size_label": "1.5B",
        "architecture": "qwen2",
        # Bartowski's imatrix Q4_K_M is ~986 MB: under the requested 1 GB
        # while retaining a materially better quant than the sub-1GB Q3 choices
        # available for larger ~1.7B models. The trainable source is the exact
        # official Qwen checkpoint, so chat and LoRA stay on one logical model.
        "chat_repo": "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        "training_repo": "Qwen/Qwen2.5-1.5B-Instruct",
        "quant_preferences": ["q4_k_m", "iq4_xs", "q4_k_s", "q3_k_l"],
        "chat_size": "~986 MB",
        "max_chat_bytes": 1_000_000_000,
        "training_size": "~3.1 GB",
        "language_note": "Multilingual · strong small-model Persian/Arabic-script behavior",
        "description": "Light one-click model for chat + Personal Brain learning",
        "learning_defaults": {
            "rank": 4, "alpha": 8, "micro_steps": 3, "replay_samples": 4,
            "max_length": 128, "learning_rate": 0.00015,
        },
    },
}


def _jsonable(obj: Any):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return obj


def _friendly_model(model: LocalModel | None) -> dict | None:
    if not model:
        return None
    return _jsonable(model)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(x for x in parts if x)
    return str(content or "")


def _messages_have_image_attachments(messages: list[dict]) -> bool:
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for att in message.get("attachments") or []:
            if isinstance(att, dict) and str(att.get("kind") or "").lower() == "image":
                return True
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(x, dict) and str(x.get("type") or "") == "image_url" for x in content):
            return True
    return False


def _safe_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except Exception:
        return default


def _canonical_model_slug(value: str) -> str:
    """Normalize repo/model names so an inference quant can pair with its source."""
    raw=str(value or '').replace('\\','/').split('/')[-1].lower()
    raw=re.sub(r'\.gguf$','',raw)
    bits=[x for x in re.split(r'[^a-z0-9]+',raw) if x]
    drop={'gguf','imatrix','imat','quant','quantized','bnb','bitsandbytes','safetensors','fp16','f16','bf16'}
    out=[]
    for bit in bits:
        if bit in drop or re.fullmatch(r'i\d+',bit) or re.fullmatch(r'q\d+',bit) or re.fullmatch(r'iq\d+',bit):
            continue
        if re.fullmatch(r'\d+bit',bit):
            continue
        # Quant suffix pieces commonly survive underscore/hyphen splitting.
        if bit in {'k','s','m','l','xs'} and out and re.fullmatch(r'q\d+',out[-1] if out else ''):
            continue
        out.append(bit)
    return '-'.join(out)


class EventBroker:
    """Ordered bounded history shared by SSE reconnect and long polling."""
    def __init__(self):
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._subs: set[queue.Queue] = set()
        self._revision = 0
        self._history = deque(maxlen=256)

    def _since(self, after: int) -> list[dict]:
        if after > self._revision or (self._history and after < self._history[0]["revision"] - 1):
            return [{"type": "resync", "revision": self._revision}]
        return [dict(x) for x in self._history if x["revision"] > after]

    def poll(self, after: int, timeout: float = 15) -> list[dict]:
        with self._changed:
            self._changed.wait_for(lambda: self._revision != after, timeout=max(0, min(timeout, 20)))
            return self._since(after)

    def subscribe(self, after: int | None = None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            if after is not None:
                for event in self._since(after): q.put_nowait(event)
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, kind: str, payload: dict | None = None) -> None:
        with self._changed:
            self._revision += 1
            event = {**(payload or {}), "type": kind, "revision": self._revision, "at": time.time()}
            self._history.append(event)
            for q in self._subs:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    while not q.empty():
                        try: q.get_nowait()
                        except queue.Empty: break
                    q.put_nowait({"type": "resync", "revision": self._revision})
            self._changed.notify_all()


class LlamaForgeState:
    def __init__(self):
        self.cfg = AppConfig.load()
        self.hw = detect_hardware()
        self.runtime = RuntimeManager(self.cfg.runtime_dir, self.cfg.custom_server_path)
        self.models = ModelManager(self.cfg.hf_token)
        self.autotuner = AdaptiveTuner()
        self.autotune_cancel = threading.Event()
        self.chat_cancellations: dict[str, threading.Event] = {}
        self.local_models: list[LocalModel] = []
        self.local_trainable_models: list[dict] = []
        self.active_model: LocalModel | None = None
        self.active_plan = None
        self.server_proc = ManagedProcess()
        self.server_ready = False
        self.server_error = ""
        self.server_started_at = 0.0
        self.vision_projector_loaded = False
        self.template_health = {"state": "unknown", "message": "Not checked"}
        self.logs: deque[str] = deque(maxlen=8000)
        self.lock = threading.RLock()
        # Model selection/start/stop can be triggered by both the desktop UI and
        # connected websites. Keep the complete lifecycle atomic.
        self._model_lifecycle_lock = threading.RLock()
        self._server_generation = 0
        self.job = {"kind": "", "state": "idle", "message": "", "done": 0, "total": 0, "error": ""}
        self.runtime_cancel = threading.Event()
        self.model_download_cancel = threading.Event()
        self.shutting_down = False
        self.client_seen = False
        self.last_client_at = time.monotonic()
        self._last_cpu = 0.0
        self._last_ram = (self.hw.ram_total_gb, self.hw.ram_available_gb)
        self._runtime_cache: tuple[float, dict] | None = None
        self.events = EventBroker()
        self.file_logger = get_logger()
        self.request_traces = TraceStore(APP_DIR / "logs" / "requests")
        self.request_traces.enabled = bool(getattr(self.cfg,"diagnostic_full_traces",True))
        self.app_root = Path(__file__).resolve().parents[2]
        self.agent = AgentRuntime(log=self.log)
        self.agent.permission_provider = self.agent_permissions
        # Keep downloaded trainable checkpoints portable and visible: one level
        # above the LlamaForge application folder, inside a sibling
        # ``LlamaForgeModels`` directory. Example:
        #   ...\New folder\LlamaForge-0.15.1\
        #   ...\New folder\LlamaForgeModels\<repo>\
        # Runtime/trainer internals still live under ~/.llamaforge; model weights do not.
        self.training_models_root = portable_training_models_root(self.app_root)
        self.training_models_root.mkdir(parents=True, exist_ok=True)
        self.legacy_training_models_root = APP_DIR / "brain" / "bases"
        self.brain = PersonalBrain(self.app_root, log=self.log)
        self.trainables = TrainableModelManager(self.training_models_root, self.cfg.hf_token, log=self.log)
        self.brain_download_cancel = threading.Event()
        self.brain_cancel = threading.Event()
        self.brain_catalog_cache: list[dict] = []
        self.last_launch_payload: dict[str, Any] = {}
        self._process_cpu = 0.0
        self._last_generation_tps = 0.0
        self._last_prompt_tps = 0.0
        self._inference_active = 0
        self._last_inference_at = time.monotonic()
        self._idle_unload_fired = False
        self._live_payload = {"cpu_percent": 0.0, "process_cpu_percent": 0.0, "ram_total_gb": self.hw.ram_total_gb, "ram_available_gb": self.hw.ram_available_gb, "ram_used_gb": max(0.0, self.hw.ram_total_gb - self.hw.ram_available_gb)}
        self._remote_inference_lock = threading.RLock()
        self.remote_apps = RemoteAppManager(
            task_runner=self.run_remote_app_task,
            runtime_info=self.remote_runtime_info,
            control_handler=self.handle_remote_model_control,
            workspace_importer=lambda scope, snapshot: self.agent.import_workspace_snapshot(snapshot, scope),
            workspace_exporter=lambda scope: self.agent.export_workspace_snapshot(scope),
            log=self.log,
            app_version=APP_VERSION,
            request_traces=self.request_traces,
        )
        self.log(f"LlamaForge {APP_VERSION} control plane started")
        self.log(f"[logging] persistent={LOG_FILE} ui_buffer=8000 trainer_sessions={APP_DIR / 'brain' / 'logs'}")
        self.log(f"[hardware] os={self.hw.os_name} {self.hw.os_version} cpu={self.hw.cpu} logical={self.hw.logical_cores} physical={self.hw.physical_cores} ram={self.hw.ram_total_gb:.2f}GB available={self.hw.ram_available_gb:.2f}GB gpus={len(self.hw.gpus)}")
        self.log(f"[brain:storage] trainable_models_root={self.training_models_root} mode=portable-sibling")
        def _thread_hook(args):
            try:
                self.log(f"[thread:error] name={getattr(args.thread,'name','?')} type={getattr(args.exc_type,'__name__',args.exc_type)} error={args.exc_value}")
                for line in traceback.format_exception(args.exc_type,args.exc_value,args.exc_traceback):
                    for sub in line.rstrip().splitlines(): self.log("[thread:trace] "+sub)
            except Exception:
                pass
        threading.excepthook=_thread_hook
        self.cluster = ClusterManager(
            runtime=self.runtime, hardware=self.hw, version=APP_VERSION, log=self.log,
            on_node_lost=self._on_cluster_node_lost,
        )
        self.cluster.start_background()
        threading.Thread(target=self._metrics_loop, name="metrics", daemon=True).start()
        threading.Thread(target=self.scan_models, name="model-scan", daemon=True).start()

    def _on_cluster_node_lost(self, node_id: str) -> None:
        """Fail closed on a Worker loss and rebuild after the active request ends.

        Browser chat history/context is not stored inside RPC Workers, so stopping
        the current llama-server invalidates stale token steps without deleting
        conversation state. Recovery then re-plans from the remaining Workers.
        """
        if self.shutting_down or not self.server_proc.running:
            return
        payload = dict(self.last_launch_payload or {})
        self.server_error = f"Cluster Worker {node_id} disconnected; generation was stopped safely and the cluster is rebalancing."
        self.events.publish("state", {"reason": "cluster-worker-lost", "node_id": node_id})

        def recover():
            try:
                with self._model_lifecycle_lock:
                    if self.server_proc.running:
                        self._stop_server_locked(reason=f"cluster Worker lost: {node_id}")
                if payload and not self.shutting_down and self.cluster.enabled:
                    time.sleep(0.6)
                    # last_launch_payload contains only launch settings; the model
                    # path remains local on the Master. Scheduler will exclude the
                    # offline Worker and can choose a smaller viable pool.
                    self.start_server(payload)
                    self.log("[cluster:recovery] cluster rebuilt after Worker loss")
            except Exception as exc:
                self.server_error = f"Cluster recovery needs attention: {exc}"
                self.log(f"[cluster:recovery:error] {exc}")
                self.events.publish("state", {"reason": "cluster-recovery-error"})
        threading.Thread(target=recover, name="cluster-recovery", daemon=True).start()

    def touch_client(self):
        self.client_seen = True
        self.last_client_at = time.monotonic()

    def log(self, line: str):
        text = redact(str(line)).rstrip()
        if not text:
            return
        generation_tps_sample = 0.0
        stamp = time.strftime("%H:%M:%S")
        rendered = f"[{stamp}] {text}"
        with self.lock:
            self.logs.append(rendered)
            low = text.lower()
            m = re.search(r"prompt eval time.*?\(\s*([0-9.]+)\s+tokens per second\)", low)
            if m:
                try: self._last_prompt_tps = float(m.group(1))
                except Exception: pass
            m = re.search(r"(?<!prompt )eval time.*?\(\s*([0-9.]+)\s+tokens per second\)", low)
            if m:
                try:
                    self._last_generation_tps = float(m.group(1))
                    generation_tps_sample = self._last_generation_tps
                except Exception:
                    pass
        if generation_tps_sample > 0:
            try:
                if getattr(self, "cluster", None) and self.cluster.active_plan and self.active_model:
                    self.cluster.record_runtime_result(str(self.active_model.path), generation_tps_sample)
            except Exception:
                pass
        try:
            if hasattr(self, "file_logger"):
                self.file_logger.info(text)
        except Exception:
            pass
        if hasattr(self, "events"):
            self.events.publish("log", {"line": rendered})

    def log_exception(self, scope: str, exc: BaseException):
        self.log(f"[{scope}:error] {type(exc).__name__}: {exc}")
        for line in traceback.format_exc().rstrip().splitlines():
            self.log(f"[{scope}:trace] {line}")

    def diagnostic_report(self) -> dict:
        """Return a copy/paste friendly snapshot with no secrets."""
        try:
            doctor=self.brain_doctor()
        except Exception as exc:
            doctor={"ok":False,"error":str(exc),"checks":[]}
        with self.lock:
            logs=list(self.logs)
        return {
            "llamaforge_version":APP_VERSION,
            "generated_at":time.time(),
            "hardware":_jsonable(self.hw),
            "runtime":self.runtime_status(),
            "server":self.server_status(),
            "active_model":_friendly_model(self.active_model),
            "brain":self.brain_status(),
            "brain_doctor":doctor,
            "cluster": self.cluster.snapshot() if hasattr(self, "cluster") else {},
            "logging":{
                "persistent_log":str(LOG_FILE),
                "ui_buffer_lines":8000,
                "trainer_sessions_dir":str(APP_DIR / "brain" / "logs"),
                "last_trainer":dict(getattr(self.brain,"last_trainer",{}) or {}),
            },
            "config":{
                "host":self.cfg.host,"port":self.cfg.port,"max_ram_percent":self.cfg.max_ram_percent,
                "model_memory_mode":self.cfg.model_memory_mode,
                "cpu_only_default":self.cfg.cpu_only_default,
                "accelerator_mode":self.cfg.accelerator_mode,
                "gpu_layer_percent":int(self.cfg.gpu_layer_percent),
                "speculative_mode":str(getattr(self.cfg, "speculative_mode", "auto")),
                "adaptive_context":bool(getattr(self.cfg, "adaptive_context", True)),
                "hf_token_configured":bool(self.cfg.hf_token),
                "agent_enabled_default":bool(self.cfg.agent_enabled_default),
                "agent_allow_write":bool(self.cfg.agent_allow_write),
                "agent_allow_workspace_write":bool(self.cfg.agent_allow_workspace_write),
                "agent_allow_private_network":bool(self.cfg.agent_allow_private_network),
                "agent_browser_headless":bool(self.cfg.agent_browser_headless),
                "agent_max_steps":int(self.cfg.agent_max_steps),
                "agent_allow_telegram_read":bool(self.cfg.agent_allow_telegram_read),
                "agent_allow_telegram_write":bool(self.cfg.agent_allow_telegram_write),
                "agent_skill_profile":self.cfg.agent_skill_profile,
                "model_dirs":list(self.cfg.model_dirs),
            },
            "logs":logs,
        }

    def refresh_hw(self):
        # Full hardware enumeration is comparatively expensive. For live cards,
        # only refresh CPU + memory and keep static capabilities from startup.
        try:
            self._last_cpu = float(cpu_percent())
        except Exception:
            self._last_cpu = 0.0
        try:
            self._last_ram = memory_gb()
        except Exception:
            pass

    def _metrics_loop(self):
        while not self.shutting_down:
            self.refresh_hw()
            self._process_cpu = process_cpu_percent(self.server_proc.pid, self.hw.logical_cores) if self.server_proc.running else 0.0
            total, avail = self._last_ram
            self._live_payload = {
                "cpu_percent": round(self._last_cpu, 1),
                "process_cpu_percent": round(self._process_cpu, 1),
                "ram_total_gb": round(total, 2),
                "ram_available_gb": round(avail, 2),
                "ram_used_gb": round(max(0, total - avail), 2),
            }
            if hasattr(self, "events"):
                self.events.publish("metrics", {"live": dict(self._live_payload), "performance": self.performance_status()})
            idle_minutes = max(0, int(getattr(self.cfg, "idle_unload_minutes", 0) or 0))
            if (
                idle_minutes > 0 and self.server_ready and self.server_proc.running
                and self._inference_active <= 0 and not self._idle_unload_fired
                and time.monotonic() - self._last_inference_at >= idle_minutes * 60
            ):
                self._idle_unload_fired = True
                try:
                    self.stop_server(reason=f"idle for {idle_minutes} min")
                except Exception as exc:
                    self.log("[server:idle-unload:error] " + str(exc))
            time.sleep(1.0 if self.server_proc.running else 2.0)

    def performance_status(self) -> dict:
        plan = self.active_plan
        proc = float(self._process_cpu or 0.0)
        target = int(getattr(plan, "cpu_target_percent", 0) or 0) if plan else 0
        status, detail = "idle", "Start a local model to measure CPU utilization."
        if self.server_proc.running and not self.server_ready:
            status, detail = "loading", "Model loading is using the resources required by llama.cpp."
        elif self.server_ready and plan:
            if self._inference_active <= 0:
                status, detail = "ready-idle", "The model is loaded but not generating. Low Task Manager CPU here is expected; judge CPU saturation while tokens are actively being generated."
            elif proc >= 88:
                status, detail = "cpu-saturated", "llama-server is consuming most of the machine's CPU capacity during active inference."
            elif getattr(plan, "oversized", False) and proc < 75:
                status, detail = "paging-bound", "CPU is waiting on mapped model pages; SSD/page-cache traffic is likely limiting utilization."
            elif getattr(plan, "accelerator_mode", "") == "max_both" and proc < 75:
                status, detail = "gpu-sync-bound", "Max Both already gives all logical CPU workers permission to run. Lower CPU usage means this token step is waiting on the GPU/shared-memory path; reduce GPU share further if you want more CPU work."
            elif getattr(plan, "cpu_saturation", False) and proc < 75:
                status, detail = "memory-bound", "All CPU workers are allowed; lower Task Manager usage now usually means RAM bandwidth or kernel synchronization is the bottleneck, not a thread limit."
            elif target >= 90 and proc < 70:
                status, detail = "not-cpu-bound", "The requested CPU budget is available, but this inference step is not CPU-bound. More threads may not increase tokens/sec."
            else:
                status, detail = "balanced", "CPU usage is consistent with the current llama.cpp workload and thread plan."
        return {
            "state": status, "detail": detail, "process_cpu_percent": round(proc, 1),
            "system_cpu_percent": round(float(self._last_cpu or 0.0), 1), "target_percent": target,
            "generation_tps": round(self._last_generation_tps, 2), "prompt_tps": round(self._last_prompt_tps, 2),
            "threads": int(getattr(plan, "threads", 0) or 0) if plan else 0,
            "threads_batch": int(getattr(plan, "threads_batch", 0) or 0) if plan else 0,
            "saturation": bool(getattr(plan, "cpu_saturation", False)) if plan else False,
        }

    def _schedule_legacy_training_model_relocation(self, model) -> None:
        """Relocate only LlamaForge's old managed Brain downloads.

        This runs after a model profile is activated so an already-downloaded
        checkpoint from 0.14.2 and earlier is not silently kept in the hidden
        ~/.llamaforge/brain/bases location. Manual external folders are ignored.
        """
        try:
            base = self.brain.training_base_for(model)
            bp = Path(os.path.expanduser(base)) if base else None
            if not bp or not bp.is_dir() or not self.trainables._path_is_under(bp, self.legacy_training_models_root):
                return
            if self.brain.job.get("state") in ("running", "cancelling"):
                return
        except Exception:
            return

        def work():
            try:
                with self.brain.lock:
                    self.brain.job = {"state":"running","stage":"move-base","message":"Moving the training model beside LlamaForge…","error":"","progress":0.01}
                self.events.publish("brain", {"brain": self.brain_status()})
                self.log(f"[brain:storage] legacy training source detected path={bp}")
                def progress(msg,pct,done,total):
                    with self.brain.lock:
                        self.brain.job.update(state="running",stage="move-base",message=str(msg),progress=max(0.01,min(0.99,float(pct))),error="",done=int(done),total=int(total))
                    self.events.publish("brain", {"brain": self.brain_status()})
                moved = self.trainables.relocate_training_source(bp, legacy_root=self.legacy_training_models_root, progress=progress)
                self.brain.set_training_base(model, moved)
                with self.brain.lock:
                    self.brain.job = {"state":"done","stage":"storage","message":"Training model moved beside LlamaForge","error":"","progress":1.0,"path":moved}
                self.log(f"[brain:storage] active profile now uses portable training source={moved}")
                self.events.publish("brain", {"brain": self.brain_status()})
                self.events.publish("state", {"reason":"brain-storage-relocated"})
            except Exception as exc:
                self.log_exception("brain:storage", exc)
                with self.brain.lock:
                    self.brain.job = {"state":"error","stage":"move-base","message":"Could not move the existing training model","error":str(exc),"progress":0.0}
                self.events.publish("brain", {"brain": self.brain_status()})
        threading.Thread(target=work, name="brain-storage-relocation", daemon=True).start()

    def _trainable_pair_score(self, model: LocalModel, row: dict) -> int:
        if not model or not row:
            return 0
        active_arch=str(getattr(model,'architecture','') or '').lower().replace('_','').replace('-','')
        cand_arch=str(row.get('architecture') or '').lower().replace('_','').replace('-','')
        if active_arch and cand_arch and active_arch not in cand_arch and cand_arch not in active_arch:
            return 0
        repo=str(row.get('repo_id') or '')
        cand=_canonical_model_slug(repo)
        if not cand:
            return 0
        quant_repo=self.brain.infer_quant_repo(model)
        quant=_canonical_model_slug(quant_repo)
        name=_canonical_model_slug(getattr(model,'name',''))
        path_name=_canonical_model_slug(Path(str(getattr(model,'path','') or '')).stem)
        score=0
        if quant and cand==quant: score=max(score,120)
        if name and cand==name: score=max(score,112)
        if path_name and (cand==path_name or cand in path_name or path_name in cand): score=max(score,102)
        if quant and len(cand)>=8 and (cand in quant or quant in cand): score=max(score,96)
        if name and len(cand)>=8 and (cand in name or name in cand): score=max(score,90)
        if row.get('checkpoint_kind')=='peft_adapter': score+=8
        if row.get('internal_dependency'): score-=45
        return score

    def _find_local_training_pair(self, model: LocalModel) -> dict | None:
        lock=getattr(self,'lock',None)
        if lock is None:
            rows=[dict(x) for x in getattr(self,'local_trainable_models',[]) or []]
        else:
            with lock:
                rows=[dict(x) for x in getattr(self,'local_trainable_models',[]) or []]
        ranked=sorted(((self._trainable_pair_score(model,r),r) for r in rows),key=lambda x:x[0],reverse=True)
        if not ranked or ranked[0][0] < 80:
            return None
        score,row=ranked[0]
        out=dict(row); out['match_score']=score
        return out

    def _auto_link_local_training_source(self, model: LocalModel) -> dict | None:
        """Bind the exact local training source whenever a chat model is selected.

        The user selects one logical model. GGUF remains the fast chat artifact;
        the matching Transformers/PEFT checkpoint is an implementation detail used
        by Personal Brain. This also repairs profiles that were accidentally linked
        to an underlying dependency checkpoint in older builds.
        """
        pair=self._find_local_training_pair(model)
        if not pair:
            return None
        path=str(pair.get('local_dir') or '').strip()
        if not path:
            return None
        current=str(self.brain.training_base_for(model) or '')
        try:
            same=bool(current) and Path(current).resolve()==Path(path).resolve()
        except Exception:
            same=current==path
        if not same:
            self.brain.set_training_base(model,path)
            self.log(f"[model:pair] chat={model.name} training={pair.get('repo_id') or path} score={pair.get('match_score')}")
        return pair

    def _unified_model_library(self) -> list[dict]:
        with self.lock:
            models=list(self.local_models)
        rows=[]
        for model in models:
            item=_friendly_model(model) or {}
            pair=self._find_local_training_pair(model)
            bst=self.brain.status(model)
            learning={
                'ready':bool(pair and self.trainables.local_checkpoint_ready(str(pair.get('local_dir') or ''))),
                'source_repo':str((pair or {}).get('repo_id') or ''),
                'source_path':str((pair or {}).get('local_dir') or ''),
                'source_kind':str((pair or {}).get('checkpoint_kind') or ''),
                'match_score':int((pair or {}).get('match_score') or 0),
                'generation':int(bst.get('generation') or 0),
                'adapter_ready':bool(bst.get('adapter_ready')),
                'enabled':bool(bst.get('enabled')),
            }
            item['learning']=learning
            rows.append(item)
        return rows

    def scan_models(self):
        try:
            dirs = list(dict.fromkeys(self.cfg.model_dirs + self._discover_common_model_dirs()))
            for d in dirs:
                if d not in self.cfg.model_dirs and Path(os.path.expanduser(d)).exists():
                    self.cfg.model_dirs.append(d)
            self.cfg.save()
            rows = self.models.scan(dirs, on_progress=lambda x: self.log("[scan] " + x))
            trainable_rows = self.trainables.local_models(dirs)
            with self.lock:
                self.local_models = rows
                self.local_trainable_models = trainable_rows
            self.log(f"[scan] Found {len(rows)} GGUF inference model(s) and {len(trainable_rows)} trainable checkpoint(s)")
            self.events.publish("models", {"count": len(rows), "trainable_count": len(trainable_rows)})
            if self.cfg.last_model_path and Path(self.cfg.last_model_path).exists():
                try:
                    self.active_model = local_model_from_path(self.cfg.last_model_path)
                    self.brain.activate_model(self.active_model)
                    self._auto_link_local_training_source(self.active_model)
                    self._schedule_legacy_training_model_relocation(self.active_model)
                except Exception:
                    pass
        except Exception as exc:
            self.log(f"[scan:error] {exc}")

    @staticmethod
    def _discover_common_model_dirs() -> list[str]:
        home = Path.home()
        candidates = [
            home / ".lmstudio" / "models",
            home / ".cache" / "huggingface" / "hub",
            home / ".ollama" / "models",
        ]
        return [str(x) for x in candidates if x.exists()]

    def select_model(self, path: str) -> dict:
        with self._model_lifecycle_lock:
            return self._select_model_locked(path)

    def _select_model_locked(self, path: str) -> dict:
        model = local_model_from_path(path)
        previous_path = str(getattr(self.active_model, "path", "") or "")
        # Never let the UI say model B is active while llama-server is still
        # serving model A.  Older builds changed ``active_model`` first, so a
        # ready old server made the new card look loaded even though chat still
        # hit the previous weights.
        if self.server_proc.running and previous_path and Path(previous_path) != Path(model.path):
            self.stop_server(reason="model switch")
        with self.lock:
            self.active_model = model
            self.brain.activate_model(model)
            self._schedule_legacy_training_model_relocation(model)
            self.cfg.last_model_path = model.path
            parent = str(Path(model.path).parent)
            if parent not in self.cfg.model_dirs:
                self.cfg.model_dirs.append(parent)
            self.cfg.save()
            if not any(Path(m.path) == Path(model.path) for m in self.local_models):
                self.local_models.append(model)
                self.local_models.sort(key=lambda m: m.name.lower())
        # One selection controls both chat and learning. If the exact training
        # source is already on disk, link it automatically instead of asking the
        # user to pick a second model from a separate list.
        pair = self._auto_link_local_training_source(model)
        pair_name = str((pair or {}).get("repo_id") or "")
        self.log(f"[model:select] chat={model.name} path={model.path} learning_pair={pair_name or 'pending'} server_ready={bool(self.server_ready and self.server_proc.running)}")
        self.events.publish("state", {"reason": "model-selected"})
        return self.model_analysis(model)

    @staticmethod
    def _gguf_source_marker(path: str | Path) -> dict:
        p = Path(path).expanduser()
        for parent in (p.parent, *p.parents):
            marker = parent / ".llamaforge-source.json"
            if not marker.is_file():
                continue
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def quick_models(self) -> list[dict]:
        """Return the small curated list used by the simple model screen."""
        with self.lock:
            local = list(self.local_models)
        rows = []
        for spec in QUICK_MODELS.values():
            row = dict(spec)
            chat_path = ""
            for model in local:
                marker = self._gguf_source_marker(model.path)
                if str(marker.get("bundle_id") or "") == spec["id"]:
                    chat_path = model.path
                    break
                training_repo = str(marker.get("training_repo") or marker.get("logical_model_id") or "")
                source_repo = str(marker.get("repo_id") or "")
                if (training_repo.lower() == str(spec["training_repo"]).lower()
                        or source_repo.lower() == str(spec["chat_repo"]).lower()):
                    chat_path = model.path
                    break
            train_dir = self.trainables.repo_local_dir(spec["training_repo"])
            row.update({
                "chat_path": chat_path,
                "chat_ready": bool(chat_path and Path(chat_path).is_file()),
                "training_path": str(train_dir),
                "training_ready": bool(self.trainables.local_checkpoint_ready(train_dir)),
                "installed": bool(chat_path and self.trainables.local_checkpoint_ready(train_dir)),
            })
            rows.append(row)
        return rows

    def _quick_quant(self, spec: dict) -> tuple[str, list[dict]]:
        files = self.models.list_gguf_files(str(spec["chat_repo"]))
        if not files:
            raise RuntimeError(f"No GGUF files were found in {spec['chat_repo']}")
        usable = [r for r in files if "mmproj" not in Path(str(r.get("name") or "")).name.lower()]
        max_bytes = int(spec.get("max_chat_bytes") or 0)
        if max_bytes:
            under_limit = [r for r in usable if 0 < int(r.get("size") or 0) <= max_bytes]
            if under_limit:
                usable = under_limit
            elif any(int(r.get("size") or 0) > 0 for r in usable):
                raise RuntimeError(f"No GGUF in {spec['chat_repo']} satisfies the {max_bytes / 1_000_000_000:.2f} GB chat-size limit")
        prefs = [str(x).lower() for x in spec.get("quant_preferences") or []]
        chosen = None
        for token in prefs:
            chosen = next((r for r in usable if token in Path(str(r.get("name") or "")).name.lower()), None)
            if chosen:
                break
        if chosen is None:
            chosen = min(usable or files, key=lambda r: int(r.get("size") or 2**63 - 1))
        name = str(chosen.get("name") or "")
        return name, self.models.shard_group(files, name)

    def download_quick_model_async(self, bundle_id: str) -> dict:
        """Install one logical model for both chat and continual LoRA learning.

        The compact GGUF is only the inference artifact. Its exact Transformers
        source is downloaded beside it and bound through metadata, so the user
        sees and selects only one logical model for both chat and learning.
        """
        key = str(bundle_id or "").strip()
        spec = QUICK_MODELS.get(key)
        if not spec:
            raise RuntimeError("Unknown quick model")
        with self.lock:
            if self.job.get("state") == "running":
                raise RuntimeError("Another download/setup job is already running")
            self.model_download_cancel.clear()
            self.job = {
                "kind": "model-bundle-download", "state": "running", "bundle_id": key,
                "message": f"Preparing {spec['name']}", "stage": "inspect",
                "progress": 0.01, "done": 0, "total": 0, "error": "",
                "result_path": "", "chat_ready": False,
            }

        def job_update(**kw):
            with self.lock:
                self.job.update(**kw)
                payload = dict(self.job)
            self.events.publish("job", {"job": payload})

        def work():
            model = None
            launch = None
            try:
                chat_repo = str(spec["chat_repo"]); training_repo = str(spec["training_repo"])
                job_update(stage="chat-download", message=f"1/2 · Downloading {spec['name']} for chat…", progress=0.03)
                filename, group = self._quick_quant(spec)
                safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "--", chat_repo)
                dest = self.training_models_root / safe_repo / "gguf"
                dest.mkdir(parents=True, exist_ok=True)
                started = time.time()
                def chat_progress(done: int, total: int):
                    elapsed = max(.001, time.time() - started)
                    frac = (float(done) / float(total)) if total else 0.0
                    job_update(stage="chat-download", message=f"1/2 · Downloading {Path(filename).name}",
                               progress=0.03 + 0.27 * max(0.0, min(1.0, frac)), done=int(done or 0), total=int(total or 0),
                               bytes_per_sec=int(done / elapsed) if done else 0)
                paths = self.models.download_many(chat_repo, group, str(dest), progress_cb=chat_progress, stop_event=self.model_download_cancel)
                if not paths:
                    raise RuntimeError(f"{spec['name']} chat download finished without a GGUF file")
                launch = next((p for p in paths if re.search(r"-00001-of-\d{5}\.gguf$", p.name, re.I)), paths[0])
                marker = {
                    "repo_id": chat_repo, "filename": filename, "downloaded_at": time.time(),
                    "kind": "logical-model-bundle", "bundle_id": key,
                    "logical_model_id": training_repo, "training_repo": training_repo,
                    "display_name": spec["name"],
                }
                (dest / ".llamaforge-source.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                dest_s = str(dest)
                if dest_s not in self.cfg.model_dirs:
                    self.cfg.model_dirs.append(dest_s); self.cfg.save()
                self.scan_models()
                self.select_model(str(launch))
                model = self.active_model
                # The explicit Chat + Learning action turns Personal Brain on with
                # the safe zero-context contract; no second training-model picker.
                learning = dict(spec.get("learning_defaults") or {})
                learning.update({"enabled": True, "zero_context": True, "strict_learning": True, "auto_synthesize": True})
                self.brain.update(learning, model)
                job_update(stage="training-download", message="Chat model ready · 2/2 downloading its learning weights…",
                           progress=0.31, result_path=str(launch), chat_ready=True, done=0, total=0, bytes_per_sec=0)
                with self.brain.lock:
                    self.brain.job = {"state":"running","stage":"download-base","message":f"Downloading this same {spec['name']} model's learning weights…","error":"","progress":0.01}
                self.events.publish("brain", {"brain": self.brain_status()})

                train_dir = self.trainables.repo_local_dir(training_repo)
                if not self.trainables.local_checkpoint_ready(train_dir):
                    train_started = time.time()
                    def train_progress(msg, pct, done, total):
                        elapsed = max(.001, time.time() - train_started)
                        frac = max(0.0, min(1.0, float(pct)))
                        job_update(stage="training-download", message="2/2 · " + str(msg), progress=0.31 + 0.64 * frac,
                                   done=int(done or 0), total=int(total or 0), bytes_per_sec=int(done / elapsed) if done else 0,
                                   result_path=str(launch), chat_ready=True)
                        with self.brain.lock:
                            self.brain.job.update(state="running", stage="download-base", message=str(msg), progress=frac, error="", done=int(done or 0), total=int(total or 0))
                        self.events.publish("brain", {"brain": self.brain_status()})
                    train_local = self.trainables.download_snapshot(training_repo, allow_remote_code=False, progress=train_progress, cancel=self.model_download_cancel)
                else:
                    train_local = str(train_dir)
                if self.model_download_cancel.is_set():
                    raise RuntimeError(f"{spec['name']} model download cancelled")
                if not self.trainables.local_checkpoint_ready(train_local):
                    raise RuntimeError(f"The {spec['name']} learning checkpoint failed verification")
                marker["training_local_dir"] = str(train_local)
                (dest / ".llamaforge-source.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                self.scan_models()
                if model is None:
                    model = local_model_from_path(str(launch))
                self.brain.activate_model(model)
                self.brain.set_training_base(model, str(train_local))
                with self.brain.lock:
                    self.brain.job = {"state":"done","stage":"download-base","message":f"{spec['name']} learning weights downloaded","error":"","progress":1.0}
                self.events.publish("brain", {"brain": self.brain_status()})
                job_update(state="done", stage="ready", message=f"{spec['name']} ready for chat + learning", progress=1.0,
                           result_path=str(launch), chat_ready=True, error="")
                self.log(f"[quick-model] ready bundle={key} chat={launch} training={train_local}")
                self.events.publish("state", {"reason":"quick-model-ready"})
                # Prepare trainer/toolchain immediately. The chat model can already
                # run, and the frontend keeps a completed turn pending until this
                # setup becomes ready.
                self.setup_brain_automatically()
            except Exception as exc:
                cancelled = self.model_download_cancel.is_set()
                with self.brain.lock:
                    if self.brain.job.get("state") == "running" and self.brain.job.get("stage") == "download-base":
                        self.brain.job = {"state":"cancelled" if cancelled else "error", "stage":"download-base",
                                          "message":f"{spec['name']} learning download stopped" if cancelled else f"{spec['name']} learning download failed",
                                          "error":"" if cancelled else str(exc), "progress":0.0}
                job_update(state="cancelled" if cancelled else "error",
                           message=f"{spec['name']} download cancelled" if cancelled else f"{spec['name']} download failed",
                           error="" if cancelled else str(exc))
                if cancelled:
                    self.log(f"[quick-model] cancelled bundle={key}")
                else:
                    self.log_exception("quick-model", exc)
                self.events.publish("brain", {"brain": self.brain_status()})
                self.events.publish("state", {"reason":"quick-model-cancelled" if cancelled else "quick-model-error"})

        threading.Thread(target=work, name="quick-model-download", daemon=True).start()
        return dict(self.job)

    def search_gguf_models(self, query: str, limit: int = 16) -> dict:
        q = str(query or "").strip()
        if not q:
            q = "instruct gguf"
        rows = self.models.search_hf(q, max(1, min(30, int(limit or 16))))
        return {"results": rows, "query": q}

    def gguf_model_files(self, repo_id: str) -> dict:
        repo = str(repo_id or "").strip()
        if "/" not in repo:
            raise RuntimeError("Choose a valid Hugging Face GGUF repository")
        rows = self.models.list_gguf_files(repo)

        def preference(name: str) -> tuple[int, int, str]:
            low = Path(name).name.lower()
            # CPU-friendly defaults first.  Q4_K_M is a strong general default,
            # followed by Q4_K_S/Q5_K_M.  F16/F32 and extreme quants are left
            # available but pushed down the list.
            order = [
                ("q4_k_m", 0), ("q4-k-m", 0),
                ("q4_k_s", 1), ("q4-k-s", 1),
                ("q5_k_m", 2), ("q5-k-m", 2),
                ("q5_k_s", 3), ("q5-k-s", 3),
                ("q6_k", 4), ("q8_0", 5),
                ("q3_k_m", 6), ("q3_k_s", 7),
                ("iq4", 8), ("iq3", 9), ("iq2", 10),
                ("f16", 40), ("fp16", 40), ("f32", 50),
            ]
            rank = 20
            for token, value in order:
                if token in low:
                    rank = value; break
            size = next((int(x.get("size") or 0) for x in rows if x.get("name") == name), 0)
            return (rank, size if size else 2**63 - 1, low)

        rows = sorted(rows, key=lambda r: preference(str(r.get("name") or "")))
        return {"repo": repo, "files": rows, "recommended": (rows[0]["name"] if rows else "")}

    def download_gguf_async(self, repo_id: str, filename: str) -> dict:
        repo = str(repo_id or "").strip(); name = str(filename or "").strip()
        if "/" not in repo or not name.lower().endswith(".gguf"):
            raise RuntimeError("Choose a GGUF repository and file first")
        with self.lock:
            if self.job.get("state") == "running":
                raise RuntimeError("Another download/setup job is already running")
            self.model_download_cancel.clear()
            self.job = {
                "kind": "model-download", "state": "running",
                "message": f"Preparing {Path(name).name}", "done": 0,
                "total": 0, "error": "", "repo": repo, "filename": name,
                "result_path": "",
            }

        def work():
            try:
                files = self.models.list_gguf_files(repo)
                group = self.models.shard_group(files, name)
                projector = self.models.associated_mmproj(files, name)
                if projector and all(str(x.get("name") or "") != str(projector.get("name") or "") for x in group):
                    group.append(projector)
                    self.log(f"[model:download] multimodal projector detected: {projector.get('name')}")
                safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "--", repo)
                dest = self.training_models_root / safe_repo / "gguf"
                dest.mkdir(parents=True, exist_ok=True)
                started = time.time()

                def progress(done: int, total: int):
                    elapsed = max(.001, time.time() - started)
                    with self.lock:
                        self.job.update(
                            done=int(done or 0), total=int(total or 0),
                            message=f"Downloading {Path(name).name}",
                            bytes_per_sec=int(done / elapsed) if done else 0,
                        )
                    self.events.publish("job", {"job": dict(self.job)})

                paths = self.models.download_many(
                    repo, group, str(dest), progress_cb=progress,
                    stop_event=self.model_download_cancel,
                )
                if not paths:
                    raise RuntimeError("Model download finished without a GGUF file")
                launch = next((p for p in paths if re.search(r"-00001-of-\d{5}\.gguf$", p.name, re.I)), paths[0])
                marker = {
                    "repo_id": repo,
                    "filename": name,
                    "downloaded_at": time.time(),
                    "kind": "gguf-inference",
                }
                (dest / ".llamaforge-source.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                dest_s = str(dest)
                if dest_s not in self.cfg.model_dirs:
                    self.cfg.model_dirs.append(dest_s)
                    self.cfg.save()
                # Refresh both inference and trainable discovery so the next
                # single click can pair chat+learning automatically.
                self.scan_models()
                with self.lock:
                    self.job.update(
                        state="done", message="Model downloaded",
                        result_path=str(launch), done=int(self.job.get("total") or self.job.get("done") or 0),
                        error="",
                    )
                self.log(f"[model:download] ready repo={repo} path={launch}")
                self.events.publish("state", {"reason": "model-downloaded"})
            except Exception as exc:
                cancelled = self.model_download_cancel.is_set()
                with self.lock:
                    self.job.update(
                        state="cancelled" if cancelled else "error",
                        message="Model download cancelled" if cancelled else "Model download failed",
                        error="" if cancelled else str(exc),
                    )
                if cancelled:
                    self.log("[model:download] cancelled")
                else:
                    self.log_exception("model:download", exc)
                self.events.publish("state", {"reason": "model-download-cancelled" if cancelled else "model-download-error"})

        threading.Thread(target=work, name="model-download", daemon=True).start()
        return dict(self.job)

    def model_analysis(self, model: LocalModel | None = None) -> dict:
        model = model or self.active_model
        if not model:
            return {"model": None, "assessment": None, "plans": []}
        # Keep available RAM current for planning.
        try:
            total, avail = memory_gb()
            self.hw.ram_total_gb = total
            self.hw.ram_available_gb = avail
        except Exception:
            pass
        assessment = assess_model(model, self.hw, self.cfg.max_ram_percent, self.cfg.cpu_only_default, self.cfg.model_memory_mode)
        plans = []
        for score, plan, note in candidate_plans(model, self.hw, self.cfg.max_ram_percent, self.cfg.cpu_only_default, self.cfg.model_memory_mode):
            row = plan.to_dict(); row.update({"score": score, "note": note})
            plans.append(row)
        return {"model": _friendly_model(model), "assessment": _jsonable(assessment), "plans": plans}

    def runtime_status(self, refresh: bool = False) -> dict:
        now = time.monotonic()
        if not refresh and self._runtime_cache and now - self._runtime_cache[0] < 8.0:
            return dict(self._runtime_cache[1])
        st = self.runtime.status()
        st["installed"] = bool(st.get("server"))
        self._runtime_cache = (now, dict(st))
        return st

    def install_runtime_async(self, prefer_vulkan: bool | None = None):
        with self.lock:
            if self.job.get("state") in {"running", "cancelling"}:
                return
            if prefer_vulkan is None:
                prefer_vulkan = bool(getattr(self.hw, "gpus", [])) and str(getattr(self.cfg, "accelerator_mode", "hybrid") or "hybrid") != "cpu"
            requested_backend = "Vulkan" if prefer_vulkan else "CPU"
            self.job = {
                "kind": "runtime-install", "state": "running",
                "stage": "resolve", "requested_backend": requested_backend,
                "message": f"Finding compatible {requested_backend} runtime…",
                "done": 0, "total": 0, "progress": 0.0,
                "bytes_per_sec": 0, "eta_seconds": None, "error": "",
            }
            self.runtime_cancel.clear()

        def work():
            started = time.time()
            last_bucket = -1
            last_stage = ""
            try:
                def progress(done: int, total: int, message: str):
                    nonlocal last_bucket, last_stage
                    done_i, total_i = int(done or 0), int(total or 0)
                    raw_message = str(message)
                    is_download = raw_message.lower().startswith("downloading ")
                    stage = "download" if is_download else ("extract" if "extract" in raw_message.lower() else "resolve")
                    frac = max(0.0, min(1.0, (done_i / total_i))) if total_i > 0 else 0.0
                    elapsed = max(0.001, time.time() - started)
                    bps = int(done_i / elapsed) if is_download and done_i > 0 else 0
                    eta = int(max(0, (total_i - done_i) / bps)) if is_download and total_i > done_i and bps > 0 else None
                    if is_download and total_i > 0:
                        pct = int(round(frac * 100))
                        msg = f"{raw_message} — {pct}% · {done_i / 1048576:.1f} / {total_i / 1048576:.1f} MB"
                        if bps > 0:
                            msg += f" · {bps / 1048576:.2f} MB/s"
                        if eta is not None:
                            msg += f" · ETA {eta}s"
                    else:
                        msg = raw_message
                    with self.lock:
                        self.job.update(
                            done=done_i, total=total_i, progress=frac, message=msg, stage=stage,
                            bytes_per_sec=bps, eta_seconds=eta, requested_backend=requested_backend,
                        )
                    self.events.publish("job", {"job": dict(self.job)})
                    bucket = int(frac * 20) if is_download and total_i > 0 else -1
                    if stage != last_stage or bucket != last_bucket:
                        self.log("[runtime] " + msg)
                        last_stage, last_bucket = stage, bucket

                # A GPU/Hybrid request must not silently fall back to CPU after a
                # Vulkan failure: the UI should surface the real problem instead
                # of pretending acceleration is available.
                if prefer_vulkan:
                    result = self.runtime.install_vulkan_release(progress_cb=progress, cancel=self.runtime_cancel)
                else:
                    result = self.runtime.install_cpu_release(progress_cb=progress, cancel=self.runtime_cancel)
                self.runtime.use_managed_runtime()
                self._runtime_cache = None
                self.cfg.custom_server_path = ""
                self.cfg.save()
                with self.lock:
                    self.job.update(state="done", stage="done", progress=1.0, message=f"Installed {result.get('tag', 'runtime')}", error="", eta_seconds=0)
                self.log(f"[runtime] Compatible {result.get('status', {}).get('backend', 'runtime')} build installed and activated")
                self.events.publish("state", {"reason": "runtime-installed"})
            except Exception as exc:
                cancelled = self.runtime_cancel.is_set() or "cancel" in str(exc).lower()
                with self.lock:
                    self.job.update(
                        state="cancelled" if cancelled else "error",
                        stage="cancelled" if cancelled else "error",
                        error="" if cancelled else str(exc),
                        message="Runtime installation cancelled" if cancelled else "Runtime installation failed",
                    )
                self.log("[runtime] installation cancelled" if cancelled else "[runtime:error] " + str(exc))
                self.events.publish("state", {"reason": "runtime-install-cancelled" if cancelled else "runtime-install-error"})
        threading.Thread(target=work, name="runtime-install", daemon=True).start()

    def cancel_runtime_install(self) -> dict:
        with self.lock:
            if self.job.get("kind") != "runtime-install" or self.job.get("state") not in {"running", "cancelling"}:
                return {"ok": True, "cancelled": False, "job": dict(self.job)}
            self.runtime_cancel.set()
            self.job.update(state="cancelling", message="Stopping runtime download…")
            snapshot = dict(self.job)
        self.events.publish("job", {"job": snapshot})
        return {"ok": True, "cancelled": True, "job": snapshot}

    def build_cluster_runtime_async(self):
        with self.lock:
            if self.job.get("state") == "running":
                return
            self.job = {"kind": "runtime-cluster-build", "state": "running", "message": "Building RPC-capable llama.cpp runtime…", "done": 0, "total": 0, "error": ""}

        def work():
            try:
                def progress(message: str):
                    with self.lock:
                        self.job.update(message=str(message))
                    self.events.publish("job", {"job": dict(self.job)})
                    self.log("[runtime:cluster] " + str(message))
                result = self.runtime.build_cpu_from_source(progress_cb=progress)
                self._runtime_cache = None
                self.cfg.custom_server_path = ""
                self.cfg.save()
                with self.lock:
                    self.job.update(state="done", message="RPC-capable llama.cpp runtime built and activated", error="")
                self.log("[runtime:cluster] RPC-capable runtime built and activated")
                self.events.publish("state", {"reason": "cluster-runtime-built", "runtime": result})
            except Exception as exc:
                with self.lock:
                    self.job.update(state="error", error=str(exc), message="Cluster runtime build failed")
                self.log_exception("runtime:cluster", exc)
                self.events.publish("state", {"reason": "cluster-runtime-build-error"})
        threading.Thread(target=work, name="runtime-cluster-build", daemon=True).start()

    def set_runtime_path(self, path: str):
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"llama-server not found: {p}")
        self.runtime.set_custom_server(str(p))
        self._runtime_cache = None
        self.cfg.custom_server_path = str(p)
        self.cfg.save()
        self.log(f"[runtime] Using custom llama-server: {p}")
        self.events.publish("state", {"reason": "runtime-selected"})

    def _tune_environment(self, *, context: int | None = None, load_mode: str | None = None) -> dict:
        return self.autotuner.environment(
            self.hw, self.runtime.find_binary("llama-bench") or "",
            str(self.runtime_status().get("backend") or ""),
            context=int(context or self.cfg.default_context_size),
            load_mode=str(load_mode or self.cfg.model_memory_mode),
        )

    def autotune_model_async(self, model_path: str = "", apply_and_start: bool = True) -> dict:
        path = str(model_path or (self.active_model.path if self.active_model else self.cfg.last_model_path) or "")
        if not path:
            raise RuntimeError("Choose a GGUF model before AutoTune")
        model = local_model_from_path(path)
        bench = self.runtime.find_binary("llama-bench")
        if not bench:
            raise RuntimeError("The active llama.cpp runtime does not include llama-bench. Update the managed Runtime first.")
        with self.lock:
            if self.job.get("state") in {"running", "cancelling"}:
                raise RuntimeError(f"Another job is already running: {self.job.get('kind') or 'unknown'}")
            self.job = {
                "kind": "autotune", "state": "running", "stage": "prepare",
                "message": "Preparing per-model AutoTune benchmark…", "progress": 0.0,
                "done": 0, "total": 0, "error": "", "model_path": model.path,
            }
            self.autotune_cancel.clear()

        def work():
            try:
                if self.server_proc.running:
                    self.log("[autotune] unloading llama-server so llama-bench can measure resources without contention")
                    self.stop_server(reason="AutoTune benchmark")
                backend = str(self.runtime_status(refresh=True).get("backend") or "")

                def progress(message: str, frac: float):
                    with self.lock:
                        self.job.update(stage="benchmark", message=str(message), progress=max(0.0, min(1.0, float(frac))))
                    self.events.publish("state", {"reason": "autotune-progress"})

                result = self.autotuner.benchmark(
                    model, self.hw, bench, backend=backend, progress_cb=progress, cancel=self.autotune_cancel,
                    environment=self._tune_environment(),
                )
                with self.lock:
                    self.job.update(
                        state="done", stage="complete", message="AutoTune complete — best measured plan saved",
                        progress=1.0, result=result, error="",
                    )
                self.log(
                    f"[autotune] best model={model.name} threads={result.get('threads')} gpu_layers={result.get('gpu_layers')} "
                    f"batch={result.get('batch_size')}/{result.get('ubatch_size')} pp={float(result.get('prompt_tps') or 0):.2f} "
                    f"tg={float(result.get('generation_tps') or 0):.2f} ttft≈{float(result.get('estimated_ttft_ms') or 0):.0f}ms"
                )
                self.events.publish("state", {"reason": "autotune-done"})
                if apply_and_start and not self.autotune_cancel.is_set():
                    self.start_server({
                        "model_path": model.path, "profile": self.cfg.preferred_profile or "Balanced",
                        "ctx": int(self.cfg.default_context_size), "accelerator_mode": "adaptive",
                        "memory_mode": self.cfg.model_memory_mode, "speculative_mode": self.cfg.speculative_mode,
                        "adaptive_context": self.cfg.adaptive_context,
                    })
            except Exception as exc:
                cancelled = self.autotune_cancel.is_set() or "cancel" in str(exc).lower()
                with self.lock:
                    self.job.update(
                        state="cancelled" if cancelled else "error",
                        stage="cancelled" if cancelled else "error",
                        message="AutoTune cancelled" if cancelled else "AutoTune failed",
                        error="" if cancelled else str(exc),
                    )
                if not cancelled:
                    self.log_exception("autotune", exc)
                self.events.publish("state", {"reason": "autotune-cancelled" if cancelled else "autotune-error"})

        threading.Thread(target=work, name="model-autotune", daemon=True).start()
        return {"ok": True, "job": dict(self.job)}

    def cancel_autotune(self) -> dict:
        self.autotune_cancel.set()
        with self.lock:
            if self.job.get("kind") == "autotune" and self.job.get("state") == "running":
                self.job["state"] = "cancelling"
                self.job["message"] = "Cancelling AutoTune…"
        self.events.publish("state", {"reason": "autotune-cancelling"})
        return {"ok": True, "job": dict(self.job)}

    def _ensure_vision_runtime(self, messages: list[dict]) -> None:
        if not _messages_have_image_attachments(messages):
            return
        model = self.active_model
        if not model or not bool(getattr(model, "vision_capable", False)):
            raise RuntimeError("The selected model is not image-capable or has no matching mmproj file")
        if self.vision_projector_loaded and self.server_ready:
            return
        if not self.server_ready:
            raise RuntimeError("The local model is not ready")
        retry = dict(self.last_launch_payload or {})
        retry["model_path"] = model.path
        retry["vision_enabled"] = True
        self.log("[vision:lazy] image detected — reloading once with mmproj enabled")
        self.start_server(retry)
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.server_ready and self.vision_projector_loaded:
                self.log("[vision:lazy] multimodal projector is ready")
                return
            if self.server_error:
                raise RuntimeError(self.server_error)
            if not self.server_proc.running:
                raise RuntimeError("Vision reload stopped before becoming ready")
            time.sleep(0.25)
        raise RuntimeError("Vision projector loading timed out")

    def start_server(self, payload: dict) -> dict:
        with self._model_lifecycle_lock:
            return self._start_server_locked(payload)

    def _start_server_locked(self, payload: dict) -> dict:
        path = str(payload.get("model_path") or (self.active_model.path if self.active_model else ""))
        if not path:
            raise RuntimeError("Choose a GGUF model first")
        model = local_model_from_path(path)
        if model.shard_count > 1 and model.shards_present != model.shard_count:
            raise RuntimeError(f"Model is incomplete: {model.shards_present}/{model.shard_count} shards are present")
        server = self.runtime.find_binary("llama-server")
        if not server:
            raise RuntimeError("llama-server is not installed. Install a compatible runtime first.")

        compat = self.runtime.model_compatibility(model.architecture)
        if not compat.get("ok", True):
            raise RuntimeError(compat.get("message") or "The active llama.cpp runtime is too old for this model")

        profile = str(payload.get("profile") or "")
        if profile not in PROFILES:
            profile = assess_model(model, self.hw, self.cfg.max_ram_percent, self.cfg.cpu_only_default, self.cfg.model_memory_mode).recommended_profile
        ctx = _safe_int(payload.get("ctx"), int(self.cfg.default_context_size), 512, int(model.context_length or 262144))
        requested_accelerator = str(payload.get("accelerator_mode") or "").strip().lower()
        if requested_accelerator not in {"adaptive", "cpu", "gpu", "hybrid", "max_both"}:
            requested_accelerator = "cpu" if bool(payload.get("cpu_only", self.cfg.cpu_only_default)) else str(self.cfg.accelerator_mode or "adaptive")
        if requested_accelerator not in {"adaptive", "cpu", "gpu", "hybrid", "max_both"}:
            requested_accelerator = "adaptive"
        cpu_only = requested_accelerator == "cpu"
        gpu_layer_percent = _safe_int(
            payload.get("gpu_layer_percent"), int(self.cfg.gpu_layer_percent), 5, 95
        )
        thread_mode = str(payload.get("thread_mode") or "auto").lower()
        if thread_mode not in ("auto", "performance", "manual", "target", "saturate"):
            thread_mode = "auto"
        threads_override = payload.get("threads")
        threads_batch_override = payload.get("threads_batch")
        cpu_target_percent = _safe_int(payload.get("cpu_target_percent"), 100, 25, 100) if thread_mode == "target" else None
        tune = self.autotuner.get(model, environment=self._tune_environment(
            context=ctx, load_mode=str(payload.get("memory_mode") or self.cfg.model_memory_mode)
        )) if requested_accelerator == "adaptive" else None
        tune_source = "llama-bench" if tune else "hardware-heuristic"
        if tune and thread_mode == "auto":
            thread_mode = "manual"
            threads_override = int(tune.get("threads") or self.hw.physical_cores or 1)
            threads_batch_override = int(tune.get("threads_batch") or threads_override)
            self.log(
                f"[autotune] applying saved result threads={threads_override}/{threads_batch_override} "
                f"gpu_layers={tune.get('gpu_layers')} batch={tune.get('batch_size')}/{tune.get('ubatch_size')} "
                f"tg={float(tune.get('generation_tps') or 0):.2f} tok/s"
            )
        plan = make_plan(
            model, self.hw, profile, cpu_only, ctx, self.cfg.max_ram_percent,
            thread_mode=thread_mode,
            threads_override=threads_override,
            threads_batch_override=threads_batch_override,
            cpu_target_percent=cpu_target_percent,
            cpu_saturation=bool(payload.get("cpu_saturation", thread_mode == "saturate")),
            memory_mode=str(payload.get("memory_mode") or self.cfg.model_memory_mode),
            gpu_layer_percent=gpu_layer_percent,
            accelerator_mode=requested_accelerator,
            gpu_layers_override=(int(tune.get("gpu_layers")) if tune and tune.get("gpu_layers") is not None else None),
            batch_size_override=(int(tune.get("batch_size")) if tune and tune.get("batch_size") else None),
            ubatch_size_override=(int(tune.get("ubatch_size")) if tune and tune.get("ubatch_size") else None),
            tuning_source=tune_source,
            speculative_mode=str(payload.get("speculative_mode") or getattr(self.cfg, "speculative_mode", "auto")),
            adaptive_context=bool(payload.get("adaptive_context", getattr(self.cfg, "adaptive_context", True))),
        )
        if plan.memory_mode == "ram_only" and plan.oversized:
            raise RuntimeError(plan.warning or "Full RAM mode requires the model and runtime to fit in the safe RAM budget")
        flags = self.runtime.supported_server_flags()
        if plan.memory_mode == "ram_only" and "--load-mode" not in flags and "--no-mmap" not in flags:
            raise RuntimeError("Full RAM requires a llama.cpp runtime with --load-mode none or legacy --no-mmap support. Update the Runtime first.")
        if plan.memory_mode == "ssd_test" and "--lazy-mode" not in flags:
            self.log("[memory] SSD Test fallback: this runtime has no --lazy-mode; mmap + no-warmup will still be used when supported")
        # Auto = respect embedded model template. Manual choices are exposed only
        # from Advanced, keeping the normal experience one-click.
        chat_template = str(payload.get("chat_template") or "").strip() or None
        brain_status = self.brain.status(model)
        brain_lora = brain_status.get("adapter_path") if brain_status.get("enabled") and brain_status.get("adapter_ready") else None
        vision_requested = bool(payload.get("vision_enabled", False))
        mmproj_available = str(getattr(model, "vision_projector", "") or "") if bool(getattr(model, "vision_capable", False)) else ""
        # Vision projectors can consume substantial RAM. Keep text-only launches
        # lean and attach mmproj only on the first request that actually contains
        # an image. The server is transparently restarted once at that point.
        mmproj = mmproj_available if vision_requested else ""
        # Invalidate every previous readiness watcher before changing either
        # the local llama-server or any RPC Worker.  An old model must be fully
        # detached before Worker RPC processes are restarted for the new plan.
        self._server_generation += 1
        generation = self._server_generation
        if self.server_proc.running:
            old_pid = self.server_proc.pid
            self.log(f"[server:switch] stopping previous llama-server PID {old_pid or 'n/a'}")
            self.server_proc.stop()
            if self.server_proc.running:
                raise RuntimeError(f"Previous llama-server PID {old_pid or 'unknown'} is still running")
            time.sleep(0.2)
        if hasattr(self, "cluster"):
            self.cluster.stop_cluster_runtime()

        cluster_plan = None
        if hasattr(self, "cluster") and self.cluster.enabled:
            # Current llama.cpp RPC transfers model tensors from the Master, so
            # the GGUF remains local while Worker RAM/compute is provisioned.
            cluster_plan = self.cluster.prepare_launch(
                model_size_gb=float(model.size_gb or 0.0),
                runtime_overhead_gb=float(getattr(plan, "estimated_runtime_gb", 0.8) or 0.8),
                model_key=str(model.path),
            )
        args = server_args(
            model.path, plan, self.cfg.host, self.cfg.port, flags, chat_template,
            lora_path=brain_lora, lora_scale=float(self.brain.cfg.adapter_scale or 1.0),
            mmproj_path=mmproj or None,
            rpc_servers=list(cluster_plan.rpc_servers) if cluster_plan else None,
            tensor_split=list(cluster_plan.tensor_split) if cluster_plan else None,
            distributed=bool(cluster_plan),
            split_mode=str(cluster_plan.split_mode) if cluster_plan else None,
        )
        command = [server] + args
        self.last_launch_payload = {
            "model_path": model.path, "profile": profile, "ctx": ctx, "cpu_only": bool(plan.cpu_only),
            "thread_mode": thread_mode, "threads": plan.threads, "threads_batch": plan.threads_batch,
            "cpu_target_percent": int(getattr(plan, "cpu_target_percent", 100) or 100),
            "cpu_saturation": bool(getattr(plan, "cpu_saturation", False)),
            "gpu_layer_percent": int(getattr(plan, "gpu_layer_percent", 0) or 0),
            "accelerator_mode": str(getattr(plan, "accelerator_mode", "cpu") or "cpu"),
            "memory_mode": plan.memory_mode,
            "speculative_mode": str(getattr(plan, "speculative_mode", "auto")),
            "adaptive_context": bool(getattr(self.cfg, "adaptive_context", True)),
            "chat_template": chat_template or "",
            "vision_capable": bool(mmproj_available),
            "vision_enabled": bool(mmproj),
            "cluster_plan_id": cluster_plan.plan_id if cluster_plan else "",
            "safe_memory_retry": bool(payload.get("_safe_memory_retry", False)),
            "safe_gpu_retry": bool(payload.get("_safe_gpu_retry", False)),
        }

        self.server_ready = False
        self.server_error = ""
        self.server_started_at = time.time()
        self.template_health = {"state": "loading", "message": "Waiting for model"}
        self.active_model = model
        self.vision_projector_loaded = bool(mmproj)
        self.brain.activate_model(model)
        self.active_plan = plan
        self.cfg.last_model_path = model.path
        self.cfg.save()
        self.log(f"[chat:start] model={model.name} size={model.size_gb:.2f}GB quant={model.quantization} learning_ready={bool(brain_status.get('setup_ready'))}")
        self.log("[launch] " + subprocess.list2cmdline(command))
        if brain_lora:
            self.log(f"[brain] Loading learned adapter: {brain_lora}")
        policy = ProcessPolicy(
            affinity_count=self.hw.logical_cores if plan.cpu_strict else 0,
            priority=2 if plan.cpu_saturation else plan.cpu_priority,
        )
        try:
            self.server_proc.start(command, log_cb=self.log, policy=policy)
        except Exception:
            if cluster_plan:
                try: self.cluster.stop_cluster_runtime()
                except Exception: pass
            raise
        self.events.publish("state", {"reason": "server-starting"})
        threading.Thread(target=self._watch_server_ready, args=(generation,), name=f"server-ready-{generation}", daemon=True).start()
        return {"state": "starting", "model": _friendly_model(model), "plan": plan.to_dict(), "generation": generation}

    def _watch_server_ready(self, generation: int):
        deadline = time.time() + 300
        while time.time() < deadline and not self.shutting_down:
            if generation != self._server_generation:
                return
            if not self.server_proc.running:
                tail = self.server_proc.tail_text(120)
                low = (tail or "").lower()
                gpu_failure = bool(self.active_plan and not self.active_plan.cpu_only and
                                   any(x in low for x in ("vulkan", "vk_error", "device lost", "failed to create device")))
                if gpu_failure and not (self.last_launch_payload or {}).get("safe_gpu_retry"):
                    retry = dict(self.last_launch_payload or {})
                    retry.update(accelerator_mode="cpu", cpu_only=True, _safe_gpu_retry=True)
                    retry.pop("safe_gpu_retry", None)
                    self.log("[runtime:fallback] GPU startup failed; retrying once with CPU offload disabled")
                    try:
                        self.start_server(retry)
                        return
                    except Exception as exc:
                        self.log(f"[runtime:fallback] CPU retry could not start: {exc}")
                full_ram_failure = bool(
                    self.active_plan
                    and str(getattr(self.active_plan, "load_mode", "")) == "none"
                    and any(x in low for x in (
                        "out of memory", "bad_alloc", "cannot allocate", "failed to allocate",
                        "ggml_assert(addr) failed", "llama-mmap.cpp", "allocation failed",
                    ))
                )
                already_retried = bool((self.last_launch_payload or {}).get("safe_memory_retry"))
                if full_ram_failure and not already_retried:
                    if generation != self._server_generation:
                        return
                    retry = dict(self.last_launch_payload or {})
                    # A no-mmap allocation failure needs an explicit disk-backed
                    # fallback, not Smart Hybrid (which may choose no-mmap again).
                    retry["memory_mode"] = "ssd_test"
                    retry["_safe_memory_retry"] = True
                    retry.pop("safe_memory_retry", None)
                    self.log("[memory:fallback] Full RAM allocation failed; retrying once with safe mmap/SSD-backed mode")
                    self.events.publish("state", {"reason": "memory-safe-retry", "from": "full_ram", "to": "ssd_test"})
                    try:
                        self.start_server(retry)
                        return
                    except Exception as exc:
                        self.log(f"[memory:fallback] safe mmap retry could not start: {exc}")
                message = self._diagnose_failure(tail)
                if generation != self._server_generation:
                    return
                self.server_error = message
                self.server_ready = False
                self.log("[server:error] " + message)
                self.events.publish("state", {"reason": "server-error"})
                return
            status = get_status(f"http://{self.cfg.host}:{self.cfg.port}/health", timeout=1.2)
            if status == 200:
                # Some recent llama.cpp builds can expose HTTP health before the
                # model-loading log has reached its terminal ready state. Avoid a
                # false-ready race by requiring both the health probe and loader
                # evidence from the child process.
                tail_low = self.server_proc.tail_text(160).lower()
                loader_ready = "model loaded" in tail_low and "listening on" in tail_low
                if loader_ready:
                    # One final probe after the loader has declared readiness.
                    if get_status(f"http://{self.cfg.host}:{self.cfg.port}/health", timeout=1.2) == 200:
                        if generation != self._server_generation:
                            return
                        self.server_ready = True
                        self.server_error = ""
                        self.template_health = self._check_template_health()
                        self.log("[server] Model is ready")
                        self.log("[template] " + self.template_health.get("message", "Template check finished"))
                        self.events.publish("state", {"reason": "server-ready"})
                        return
            time.sleep(0.55)
        if generation == self._server_generation and self.server_proc.running:
            self.server_error = "Model loading timed out. Check Runtime Logs for the last llama.cpp messages."
            self.log("[server:error] " + self.server_error)
            self.events.publish("state", {"reason": "server-timeout"})

    def _check_template_health(self) -> dict:
        """Best-effort chat-template preflight after the model is actually ready."""
        probe = "LLAMAFORGE_TEMPLATE_PROBE_71C9"
        prompt = apply_chat_template(self.cfg.host, self.cfg.port, [{"role": "user", "content": probe}], timeout=8.0)
        if prompt is None:
            return {"state": "unknown", "message": "Template endpoint unavailable; using llama.cpp/model defaults"}
        if probe not in prompt:
            return {"state": "warning", "message": "Chat template preflight did not preserve the user turn"}
        low = prompt.lower()
        leaked = any(x in low for x in ("undefined", "template error", "jinja error"))
        if leaked:
            return {"state": "warning", "message": "Chat template preflight returned suspicious template output"}
        source = "embedded GGUF template" if self.active_model and self.active_model.chat_template else "llama.cpp default template"
        return {"state": "ok", "message": f"Chat template verified ({source})"}

    def _diagnose_failure(self, tail: str) -> str:
        low = (tail or "").lower()
        if "unknown model architecture" in low:
            m = re.search(r"unknown model architecture:\s*['\"]?([^'\"\s]+)", tail or "", re.I)
            arch = m.group(1) if m else (self.active_model.architecture if self.active_model else "this architecture")
            return f"The active llama.cpp runtime does not support {arch}. Install a newer compatible CPU runtime and retry."
        if "ggml_assert(addr) failed" in low or "llama-mmap.cpp" in low:
            return "The model memory mapping/locking path crashed. LlamaForge attempted one safe mmap retry; if it still fails, use Hybrid Smart or SSD Test instead of RAM Only."
        if "failed to allocate" in low or "out of memory" in low or "bad_alloc" in low:
            return "The model ran out of memory. Try Low RAM, reduce context, or close other applications."
        if "address already in use" in low or "bind" in low and "failed" in low:
            return f"Port {self.cfg.port} is already in use. Stop the other local server or change the port in Settings."
        return "llama-server stopped before the model became ready. Open Logs to see the loader error."

    def stop_server(self, reason: str = "manual") -> dict:
        with self._model_lifecycle_lock:
            return self._stop_server_locked(reason)

    def _stop_server_locked(self, reason: str = "manual") -> dict:
        """Unload the inference model and synchronously terminate llama-server.

        The selected GGUF remains selected so it can be loaded again with one
        click, but no model process is intentionally left resident in memory.
        """
        self._server_generation += 1
        pid = self.server_proc.pid
        _, before = memory_gb()
        self.server_ready = False
        self.server_error = ""
        self.template_health = {"state": "unknown", "message": "Not checked"}
        self.server_proc.stop()
        self.vision_projector_loaded = False
        if hasattr(self, "cluster"):
            try: self.cluster.stop_cluster_runtime()
            except Exception as exc: self.log(f"[cluster:stop] {exc}")
        # Give Windows a brief moment to account for the destroyed process.
        time.sleep(0.15)
        total, after = memory_gb()
        self._last_ram = (total or self._last_ram[0], after or self._last_ram[1])
        self._process_cpu = 0.0
        self._inference_active = 0
        self._idle_unload_fired = False
        freed = max(0.0, (after or 0.0) - (before or 0.0))
        suffix = f"; available RAM +{freed:.2f} GB" if freed >= 0.05 else ""
        self.log(f"[server] Model unloaded ({reason}); llama-server PID {pid or 'n/a'} terminated{suffix}")
        self.events.publish("state", {"reason": "server-stopped", "unloaded": True})
        return {
            "ok": True, "unloaded": True, "pid": pid,
            "ram_available_before_gb": round(before or 0.0, 2),
            "ram_available_after_gb": round(after or 0.0, 2),
            "ram_delta_gb": round(freed, 2),
        }

    def unload_model(self, reason: str = "manual") -> dict:
        return self.stop_server(reason=reason)

    def server_status(self) -> dict:
        return {
            "running": self.server_proc.running,
            "ready": bool(self.server_ready and self.server_proc.running),
            "pid": self.server_proc.pid,
            "error": self.server_error,
            "uptime": max(0, int(time.time() - self.server_started_at)) if self.server_proc.running else 0,
            "model": _friendly_model(self.active_model),
            "plan": self.active_plan.to_dict() if self.active_plan else None,
            "template_health": dict(self.template_health),
            "performance": self.performance_status(),
        }

    def snapshot(self) -> dict:
        total, avail = self._last_ram
        with self.lock:
            models = [_friendly_model(x) for x in self.local_models]
            trainable_models = [dict(x) for x in self.local_trainable_models]
            job = dict(self.job)
        analysis = self.model_analysis(self.active_model) if self.active_model else {"assessment": None, "plans": []}
        return {
            "version": APP_VERSION,
            "hardware": _jsonable(self.hw),
            "live": dict(self._live_payload),
            "performance": self.performance_status(),
            "runtime": self.runtime_status(),
            "server": self.server_status(),
            "models": models,
            "trainable_models": trainable_models,
            "model_library": self._unified_model_library(),
            "quick_models": self.quick_models(),
            "active_model": _friendly_model(self.active_model),
            "assessment": analysis.get("assessment"),
            "autotune": self.autotuner.get(self.active_model, environment=self._tune_environment()) if self.active_model else None,
            "job": job,
            "config": {
                "model_dirs": list(self.cfg.model_dirs), "host": self.cfg.host, "port": self.cfg.port,
                "max_ram_percent": self.cfg.max_ram_percent, "model_memory_mode": self.cfg.model_memory_mode, "cpu_only_default": self.cfg.cpu_only_default,
                "accelerator_mode": self.cfg.accelerator_mode, "gpu_layer_percent": int(self.cfg.gpu_layer_percent),
                "speculative_mode": str(getattr(self.cfg, "speculative_mode", "auto")),
                "adaptive_context": bool(getattr(self.cfg, "adaptive_context", True)),
                "exit_unloads_model": bool(self.cfg.exit_unloads_model),
                "ui_disconnect_shutdown_seconds": int(self.cfg.ui_disconnect_shutdown_seconds),
                "idle_unload_minutes": int(self.cfg.idle_unload_minutes),
                "hf_token_configured": bool(self.cfg.hf_token),
                "agent_enabled_default": bool(self.cfg.agent_enabled_default),
                "agent_allow_write": bool(self.cfg.agent_allow_write),
                "agent_allow_workspace_write": bool(self.cfg.agent_allow_workspace_write),
                "agent_allow_private_network": bool(self.cfg.agent_allow_private_network),
                "agent_browser_headless": bool(self.cfg.agent_browser_headless),
                "agent_max_steps": int(self.cfg.agent_max_steps),
                "agent_allow_telegram_read":bool(self.cfg.agent_allow_telegram_read),
                "agent_allow_telegram_write":bool(self.cfg.agent_allow_telegram_write),
                "agent_skill_profile":self.cfg.agent_skill_profile,
                "default_context_size": int(self.cfg.default_context_size),
                "generation_overrides_enabled": bool(self.cfg.generation_overrides_enabled),
                "generation_temperature": float(getattr(self.cfg, "generation_temperature", 0.70)),
                "generation_top_p": float(getattr(self.cfg, "generation_top_p", 0.95)),
                "generation_top_k": int(getattr(self.cfg, "generation_top_k", 40)),
                "generation_min_p": float(getattr(self.cfg, "generation_min_p", 0.0)),
                "generation_repeat_penalty": float(getattr(self.cfg, "generation_repeat_penalty", 1.03)),
                "generation_max_tokens": int(getattr(self.cfg, "generation_max_tokens", 2048)),
            },
            "agent": self.agent_status(),
            "remote_apps": self.remote_apps_status(),
            "brain": self.brain_status(),
            "cluster": self.cluster.snapshot() if hasattr(self, "cluster") else {},
            "cpu_threading": {
                "physical": max(1, int(self.hw.physical_cores)),
                "logical": max(1, int(self.hw.logical_cores)),
                "recommended_generation": max(1, int(self.hw.physical_cores)),
                "recommended_prompt": min(max(1, int(self.hw.logical_cores)), max(max(1, int(self.hw.physical_cores)), int(round(max(1, int(self.hw.physical_cores)) * 1.5)))),
                "performance_generation": max(1, int(self.hw.logical_cores)) if int(self.hw.logical_cores) <= 2 else max(max(1, int(self.hw.physical_cores)), int(self.hw.logical_cores) - 1),
                "performance_prompt": max(1, int(self.hw.logical_cores)),
                "hard_max": max(1, int(self.hw.logical_cores)),
                "aggressive_max": max(1, int(self.hw.logical_cores) * 2),
                "target_min": 25,
                "target_max": 100,
                "target_step": 5,
            },
        }

    def _materialize_chat_messages(self, messages: list[dict]) -> list[dict]:
        """Convert LlamaForge attachment metadata into llama-server chat content.

        Keeping this in one place guarantees token counting, template preview and
        actual inference all see the same prompt shape.
        """
        clean: list[dict] = []
        vision_ready = bool(self.active_model and getattr(self.active_model, "vision_capable", False) and self.server_ready and getattr(self, "vision_projector_loaded", True))
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "user")
            raw_content = m.get("content")
            content: Any = raw_content if isinstance(raw_content, (str, list)) else str(raw_content or "")
            attachments = m.get("attachments") if isinstance(m.get("attachments"), list) else []
            text_parts: list[str] = []
            image_parts: list[dict] = []
            for raw in attachments[:8]:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("kind") or "").lower()
                name = str(raw.get("name") or "attachment")[:180]
                if kind == "text":
                    text = str(raw.get("text") or "")
                    if text:
                        text_parts.append(f"[Attached file: {name}]\n{text[:400000]}")
                elif kind == "image":
                    if not vision_ready:
                        raise RuntimeError("The selected model is not image-capable. Choose a vision model with its mmproj file, then load it again.")
                    data_url = str(raw.get("data_url") or "")
                    if data_url.startswith("data:image/") and len(data_url) <= 8 * 1024 * 1024:
                        image_parts.append({"type": "image_url", "image_url": {"url": data_url}})
            if text_parts:
                base = _content_text(content)
                content = (base + ("\n\n" if base else "") + "\n\n".join(text_parts)).strip()
            if image_parts:
                base = _content_text(content)
                content = ([{"type": "text", "text": base}] if base else []) + image_parts
            clean.append({"role": role, "content": content})
        return clean

    def _prepare_chat_messages(self, messages: list[dict], profile) -> tuple[list[dict], dict]:
        """Keep the latest conversation inside a safe context budget.

        We do not summarize silently. Oldest complete turns are removed only when
        llama-server reports that the prompt is close to overflowing the active
        context. The UI receives a meta event when this happens.
        """
        clean = self._materialize_chat_messages(messages)
        ctx = int(getattr(self.active_plan, "ctx_size", 0) or 4096)
        reserve = min(max(384, int(profile.max_tokens)), max(512, ctx // 3))
        target = max(512, ctx - reserve - 192)
        tokens = count_chat_tokens(self.cfg.host, self.cfg.port, clean)
        if tokens is None or tokens <= target:
            return clean, {"input_tokens": tokens, "trimmed_turns": 0, "target": target}

        prefix = []
        while clean and clean[0].get("role") == "system":
            prefix.append(clean.pop(0))
        trimmed = 0
        # Remove oldest complete conversational turns, always preserving the newest
        # user message. Re-count only after each pair to keep this architecture-safe.
        while tokens is not None and tokens > target and len(clean) > 2:
            cut = 1
            if len(clean) > 1 and clean[0].get("role") == "user" and clean[1].get("role") == "assistant":
                cut = 2
            del clean[:cut]
            trimmed += 1
            tokens = count_chat_tokens(self.cfg.host, self.cfg.port, prefix + clean)
        return prefix + clean, {"input_tokens": tokens, "trimmed_turns": trimmed, "target": target}

    def _apply_generation_overrides(self, profile):
        """Apply user-owned sampling settings after Smart Chat classification.

        Smart mode remains the default. When manual generation control is enabled,
        the user's saved values are authoritative for ordinary chat, Agent final
        answers, and remote-site tasks. Agent control/JSON turns may still clamp
        temperature/top-p locally so the planner remains parseable.
        """
        if not bool(getattr(self.cfg, "generation_overrides_enabled", False)):
            return profile
        profile.temperature = float(getattr(self.cfg, "generation_temperature", 0.70))
        profile.top_p = float(getattr(self.cfg, "generation_top_p", 0.95))
        profile.top_k = int(getattr(self.cfg, "generation_top_k", 40))
        profile.min_p = float(getattr(self.cfg, "generation_min_p", 0.0))
        profile.repeat_penalty = float(getattr(self.cfg, "generation_repeat_penalty", 1.03))
        profile.max_tokens = int(getattr(self.cfg, "generation_max_tokens", 2048))
        profile.source = "manual-settings"
        profile.notes = tuple(list(profile.notes) + ["Manual generation settings from LlamaForge Preferences"])
        return profile

    @staticmethod
    def _remote_model_id(path: str) -> str:
        return "mdl_" + hashlib.sha256(str(Path(path).expanduser()).encode("utf-8", errors="ignore")).hexdigest()[:18]

    def _remote_model_catalog(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = list(self.local_models)
        active_path = str(getattr(self.active_model, "path", "") or "")
        out = []
        for model in rows:
            path = str(getattr(model, "path", "") or "")
            if not path:
                continue
            out.append({
                "id": self._remote_model_id(path),
                "name": str(getattr(model, "name", "") or Path(path).name),
                "architecture": str(getattr(model, "architecture", "") or ""),
                "quantization": str(getattr(model, "quantization", "") or ""),
                "size_gb": round(float(getattr(model, "size_gb", 0.0) or 0.0), 2),
                "loaded": bool(active_path and Path(active_path) == Path(path) and self.server_ready),
                "selected": bool(active_path and Path(active_path) == Path(path)),
                "vision_capable": bool(getattr(model, "vision_capable", False)),
            })
        return out

    def handle_remote_model_control(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a model-selection request originating from a connected website.

        The website only receives opaque model ids; local filesystem paths never
        leave LlamaForge. A switch stops the previous llama-server, selects the
        requested GGUF, and loads it with the user's main-app context/memory/CPU
        defaults. Sampling settings remain local and are not exposed on the site.
        """
        action = str(request.get("action") or "load").strip().lower()
        requested_id = str(request.get("model_id") or "").strip()
        request_id = str(request.get("request_id") or "").strip()
        if action == "unload":
            try:
                result = self.unload_model(reason="remote website request")
                return {"ok": True, "request_id": request_id, "action": "unload", "model_id": "", "state": "stopped", "unloaded": bool(result.get("unloaded", True))}
            except Exception as exc:
                return {"ok": False, "request_id": request_id, "action": "unload", "error": str(exc)}
        if not requested_id:
            return {"ok": False, "request_id": request_id, "action": action, "error": "model_id is required"}
        with self.lock:
            models = list(self.local_models)
        target = next((m for m in models if self._remote_model_id(str(getattr(m, "path", "") or "")) == requested_id), None)
        if target is None:
            return {"ok": False, "request_id": request_id, "model_id": requested_id, "error": "Requested model is not available in LlamaForge"}
        target_path = str(target.path)
        current_path = str(getattr(self.active_model, "path", "") or "")
        if self.server_ready and current_path and Path(current_path) == Path(target_path):
            return {"ok": True, "request_id": request_id, "model_id": requested_id, "state": "ready", "model": target.name}
        try:
            if current_path != target_path:
                self.select_model(target_path)
            payload = {
                "model_path": target_path,
                "profile": self.cfg.preferred_profile if self.cfg.preferred_profile in PROFILES else "Balanced",
                "ctx": int(self.cfg.default_context_size),
                "cpu_only": bool(self.cfg.cpu_only_default),
                "gpu_layer_percent": int(self.cfg.gpu_layer_percent),
                "memory_mode": str(self.cfg.model_memory_mode),
            }
            self.start_server(payload)
            return {"ok": True, "request_id": request_id, "model_id": requested_id, "state": "loading", "model": target.name}
        except Exception as exc:
            self.log(f"[remote-model:error] id={requested_id} error={exc}")
            return {"ok": False, "request_id": request_id, "model_id": requested_id, "error": str(exc)}

    def remote_runtime_info(self) -> dict[str, Any]:
        model = self.active_model
        active_path = str(getattr(model, "path", "") or "") if model else ""
        return {
            "ready": bool(self.server_ready and model is not None),
            "loading": bool(self.server_proc.running and not self.server_ready),
            "model": str(model.name if model else ""),
            "model_id": self._remote_model_id(active_path) if active_path else "",
            "architecture": str(getattr(model, "architecture", "") or "") if model else "",
            "vision_capable": bool(getattr(model, "vision_capable", False)) if model else False,
            "models": self._remote_model_catalog(),
            "version": APP_VERSION,
        }

    def run_remote_app_task(self, app: dict[str, Any], item: dict[str, Any], emit) -> str:
        """Run one website task through the exact same local AgentEngine used by Chat.

        The bridge controls transport only. Planning, skill selection, web/API/browser
        work and recovery are all performed by the local model + Agent runtime.
        """
        requested_model_id = str(item.get("requested_model_id") or "").strip()
        current_path = str(getattr(self.active_model, "path", "") or "") if self.active_model else ""
        current_id = self._remote_model_id(current_path) if current_path else ""
        if requested_model_id and (requested_model_id != current_id or not self.server_ready):
            try:
                emit({"type":"agent","event":"phase","phase":"model","label":"در حال بارگذاری مدل انتخاب‌شده از سایت"})
            except Exception:
                pass
            result = self.handle_remote_model_control({"request_id":"task_" + str(item.get("message_id") or "")[:12], "model_id":requested_model_id})
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "Could not load the model selected on the website"))
            deadline = time.time() + 300
            while time.time() < deadline:
                active_path = str(getattr(self.active_model, "path", "") or "") if self.active_model else ""
                if self.server_ready and active_path and self._remote_model_id(active_path) == requested_model_id:
                    break
                if self.server_error:
                    raise RuntimeError(self.server_error)
                time.sleep(0.35)
            else:
                raise RuntimeError("Timed out while loading the model selected on the website")
        if not self.server_ready or self.active_model is None:
            raise RuntimeError("Local model is not ready")
        history = item.get("conversation_history") if isinstance(item.get("conversation_history"), list) else []
        messages: list[dict[str, Any]] = []
        for row in history[-24:]:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "user").lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(row.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        user_message = str(item.get("user_message") or "").strip()
        attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
        if user_message or attachments:
            if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != user_message:
                messages.append({"role": "user", "content": user_message, "attachments": attachments})
            elif attachments:
                messages[-1]["attachments"] = attachments
        if not messages:
            raise RuntimeError("Remote app task has no user message")
        payload = {
            "messages": messages,
            "mode": "auto",
            "reasoning": "auto",
            "reasoning_budget": -1,
            "max_tokens": 4096,
            "agent": True,
            "request_id": "remote_" + str(app.get("id") or "") + "_" + str(item.get("message_id") or uuid.uuid4().hex),
        }
        payload["_trace_metadata"] = {"app_id":str(app.get("id") or ""), "message_id":str(item.get("message_id") or ""),
                                      "workspace_scope":str(item.get("workspace_scope") or "local")}
        cancel = item.get("_cancel") or threading.Event()
        payload["_cancel"] = cancel
        parts: list[str] = []
        with self._remote_inference_lock:
            previous_scope = self.agent.workspace_scope()
            self.agent.set_workspace_scope(str(item.get("workspace_scope") or "local"))
            try:
                iterator = self.chat_stream(payload)
                for event in iterator:
                    if isinstance(event, dict):
                        if event.get("type") == "text" and event.get("delta"):
                            parts.append(str(event.get("delta") or ""))
                        try:
                            emit(event)
                        except RemoteTaskCancelled:
                            cancel.set()
                            raise
                        except Exception as exc:
                            record("remote.emit_error", error=str(exc))
                            self.log(f"[remote-app:emit] {exc}")
            except RuntimeError as exc:
                if cancel.is_set(): raise RemoteTaskCancelled("Remote user cancelled") from exc
                raise
            finally:
                if "iterator" in locals() and hasattr(iterator,"close"): iterator.close()
                self.agent.set_workspace_scope(previous_scope)
        return "".join(parts).strip()

    def remote_apps_status(self) -> dict[str, Any]:
        return self.remote_apps.status()

    def add_remote_app(self, payload: dict) -> dict[str, Any]:
        app = self.remote_apps.add(str(payload.get("connect_url") or payload.get("url") or ""), str(payload.get("token") or ""))
        self.events.publish("state", {"reason": "remote-app-added"})
        return {"ok": True, "app": app, "remote_apps": self.remote_apps.status()}

    def remove_remote_app(self, payload: dict) -> dict[str, Any]:
        ok = self.remote_apps.remove(str(payload.get("id") or ""))
        self.events.publish("state", {"reason": "remote-app-removed"})
        return {"ok": ok, "remote_apps": self.remote_apps.status()}

    def toggle_remote_app(self, payload: dict) -> dict[str, Any]:
        app = self.remote_apps.set_enabled(str(payload.get("id") or ""), bool(payload.get("enabled")))
        self.events.publish("state", {"reason": "remote-app-toggled"})
        return {"ok": True, "app": app, "remote_apps": self.remote_apps.status()}

    def test_remote_app(self, payload: dict) -> dict[str, Any]:
        return self.remote_apps.test(str(payload.get("id") or ""))

    def remote_bridge_update_status(self, payload: dict) -> dict[str, Any]:
        return self.remote_apps.bridge_update_status(str(payload.get("id") or ""))

    def update_remote_bridge(self, payload: dict) -> dict[str, Any]:
        result = self.remote_apps.update_bridge(str(payload.get("id") or ""))
        self.events.publish("state", {"reason": "remote-bridge-updated"})
        return result

    def rollback_remote_bridge(self, payload: dict) -> dict[str, Any]:
        result = self.remote_apps.rollback_bridge(str(payload.get("id") or ""), str(payload.get("backup_id") or ""))
        self.events.publish("state", {"reason": "remote-bridge-rolled-back"})
        return result

    def agent_permissions(self) -> AgentPermissions:
        return AgentPermissions(
            allow_write=bool(self.cfg.agent_allow_write),
            allow_workspace_write=bool(self.cfg.agent_allow_workspace_write),
            allow_private_network=bool(self.cfg.agent_allow_private_network),
            browser_headless=bool(self.cfg.agent_browser_headless),
            allow_telegram_read=bool(self.cfg.agent_allow_telegram_read),
            allow_telegram_write=bool(self.cfg.agent_allow_telegram_write),
            skill_profile=self.cfg.agent_skill_profile,
        )

    def agent_status(self) -> dict:
        return self.agent.status(self.agent_permissions())

    def add_agent_connector(self, payload: dict) -> dict:
        return self.agent.add_connector(
            str(payload.get("name") or ""),
            str(payload.get("schema_url") or ""),
            str(payload.get("token") or ""),
            self.agent_permissions(),
        )

    def remove_agent_connector(self, payload: dict) -> dict:
        removed = self.agent.remove_connector(str(payload.get("id") or ""))
        return {"ok": removed, "agent": self.agent_status()}

    def agent_chat_stream(self, payload: dict):
        if not self.server_ready:
            raise RuntimeError("The local model is not ready")
        messages = payload.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("No chat messages were provided")
        # Agent turns stage attachments into workspace references first. This is
        # the key to content-on-demand: organizing a file does not spend context
        # reading it, while an explicit inspect/read request can load it later.
        messages = self.agent.prepare_messages_for_agent(messages)
        record("attachments.prepared", messages=messages)
        receipts = [{"index":i, "attachments":m.pop("_attachment_refs")} for i,m in enumerate(messages) if m.get("_attachment_refs")]
        if receipts: yield {"type":"attachments", "messages":receipts}
        self.agent.vision_available = bool(self.active_model and getattr(self.active_model, "vision_capable", False) and self.server_ready)
        if self.brain.cfg.enabled and self.brain.cfg.zero_context:
            latest_user = next((dict(m) for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
            if not latest_user:
                raise RuntimeError("Zero-context Brain mode requires a user message")
            messages = [latest_user]
        profile = self._apply_generation_overrides(choose_profile(
            self.active_model, messages,
            mode=str(payload.get("mode") or payload.get("preset") or "auto"),
            max_tokens=_safe_int(payload.get("max_tokens"), int(getattr(self.cfg, "generation_max_tokens", 2048)), 16, 32768),
            reasoning=str(payload.get("reasoning") or "auto"),
            reasoning_budget=_safe_int(payload.get("reasoning_budget"), -1, -1, 32768),
        ))
        prepared, ctx_meta = self._prepare_chat_messages(messages, profile)
        record("context.prepared", messages=prepared, context=ctx_meta, profile=profile.to_dict())
        permissions = self.agent_permissions()
        user_text = next((_content_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
        answer_parts: list[str] = []
        request_id = str(payload.get("request_id") or "req_" + uuid.uuid4().hex)
        session_id = str(payload.get("session_id") or ("sess_" + uuid.uuid4().hex))
        generation_id = int(getattr(self, "_server_generation", 0) or 0)
        token_step = 0
        yield {"type": "profile", "profile": profile.to_dict()}
        yield {"type": "meta", "request": {"session_id": session_id, "request_id": request_id, "generation_id": generation_id, "cluster_plan_id": getattr(getattr(self, "cluster", None), "active_plan", None).plan_id if getattr(getattr(self, "cluster", None), "active_plan", None) else ""}}
        yield {"type": "agent", "event": "enabled", "permissions": {
            "write": permissions.allow_write, "private_network": permissions.allow_private_network,
            "browser_headless": permissions.browser_headless,
        }}
        if ctx_meta.get("trimmed_turns"):
            yield {"type": "meta", "context": ctx_meta}

        def call_model(agent_messages: list[dict], _tools: list[dict]) -> dict:
            nonlocal generation_id
            if generation_id != int(getattr(self, "_server_generation", 0) or 0):
                raise RuntimeError("Generation invalidated because the model/cluster changed")
            if _messages_have_image_attachments(agent_messages):
                self._ensure_vision_runtime(agent_messages)
                generation_id = int(getattr(self, "_server_generation", 0) or 0)
            # Agent v2 intentionally avoids llama.cpp native function parsing.
            # Each planning turn is plain chat with one JSON control decision,
            # which works across strict Gemma/Qwen/Mistral templates.
            first = str((agent_messages[0] if agent_messages else {}).get("content") or "")
            control_call = "RETURN EXACTLY ONE JSON OBJECT" in first or "agent-control output" in first
            token_cap = min(int(profile.max_tokens), 256 if "stage 0 of a local AI agent router" in first else 768) if control_call else int(profile.max_tokens)
            result = chat_completion_with_tools(
                self.cfg.host, self.cfg.port, agent_messages, tools=None,
                temperature=min(profile.temperature, 0.35) if control_call else profile.temperature,
                top_p=min(profile.top_p, 0.92) if control_call else profile.top_p, top_k=profile.top_k,
                min_p=profile.min_p, repeat_penalty=profile.repeat_penalty,
                max_tokens=token_cap, reasoning="off" if control_call else profile.effective_reasoning,
                reasoning_budget=0 if control_call else profile.reasoning_budget, json_mode=control_call,
                cancel=payload.get("_cancel"),
            )
            if generation_id != int(getattr(self, "_server_generation", 0) or 0):
                raise RuntimeError("Stale Agent result rejected after cluster/model generation changed")
            return result

        def stream_final(agent_messages: list[dict]):
            nonlocal generation_id
            if generation_id != int(getattr(self, "_server_generation", 0) or 0):
                raise RuntimeError("Generation invalidated because the model/cluster changed")
            if _messages_have_image_attachments(agent_messages):
                self._ensure_vision_runtime(agent_messages)
                generation_id = int(getattr(self, "_server_generation", 0) or 0)
            for streamed in stream_chat_events(
                self.cfg.host, self.cfg.port, agent_messages,
                temperature=profile.temperature, top_p=profile.top_p, top_k=profile.top_k,
                min_p=profile.min_p, repeat_penalty=profile.repeat_penalty,
                max_tokens=int(profile.max_tokens), reasoning=profile.effective_reasoning,
                reasoning_budget=profile.reasoning_budget, cancel=payload.get("_cancel"),
            ):
                if generation_id != int(getattr(self, "_server_generation", 0) or 0):
                    raise RuntimeError("Stale streamed Agent result rejected after cluster/model generation changed")
                yield streamed

        self._inference_active = int(getattr(self, "_inference_active", 0) or 0) + 1
        self._last_inference_at = time.monotonic(); self._idle_unload_fired = False
        try:
            for event in self.agent.run(
                prepared, call_model, permissions, max_steps=int(self.cfg.agent_max_steps), request_id=request_id,
                context_limit=int(getattr(self.active_plan, "ctx_size", 0) or getattr(self.cfg, "default_context_size", 8192) or 8192),
                stream_final=stream_final, cancel=payload.get("_cancel"),
            ):
                if generation_id != int(getattr(self, "_server_generation", 0) or 0):
                    raise RuntimeError("Stale Agent event rejected after cluster/model generation changed")
                if event.get("type") == "text" and event.get("delta"):
                    token_step += 1
                    answer_parts.append(str(event.get("delta") or ""))
                yield event
        finally:
            self._inference_active = max(0, int(getattr(self, "_inference_active", 1) or 1) - 1)
            self._last_inference_at = time.monotonic()
        yield {"type": "quality", "quality": response_quality(user_text, "".join(answer_parts), task=profile.task, language=profile.language)}

    def chat_profile(self, payload: dict) -> dict:
        messages = payload.get("messages") or []
        max_tokens = _safe_int(payload.get("max_tokens"), 2048, 16, 32768)
        mode = str(payload.get("mode") or payload.get("preset") or "auto")
        reasoning = str(payload.get("reasoning") or "auto")
        reasoning_budget = _safe_int(payload.get("reasoning_budget"), -1, -1, 32768)
        return self._apply_generation_overrides(choose_profile(
            self.active_model, messages, mode=mode, max_tokens=max_tokens,
            reasoning=reasoning, reasoning_budget=reasoning_budget,
        )).to_dict()

    def chat_stream(self, payload: dict):
        store = getattr(self, "request_traces", None)
        if store is None:
            yield from self._chat_stream_impl(payload)
            return
        payload = dict(payload)
        payload.setdefault("request_id", "req_" + uuid.uuid4().hex)
        metadata = dict(payload.get("_trace_metadata") or {})
        metadata.update(origin="remote" if metadata else "local", request_id=payload["request_id"],
                        session_id=payload.get("session_id", ""), messages=payload.get("messages", []),
                        agent=bool(payload.get("agent")), version=APP_VERSION)
        scope = nullcontext(current_trace()) if current_trace() is not None else store.request(**metadata)
        with scope as trace:
            if trace:
                record("runtime.context", model=_friendly_model(getattr(self,"active_model",None)),
                       hardware=_jsonable(getattr(self,"hw",None)), plan=_jsonable(getattr(self,"active_plan",None)),
                       launch=getattr(self,"last_launch_payload",{}),
                       generation=getattr(self,"_server_generation",0),
                       live=getattr(self,"_live_payload",{}),
                       runtime_cached=(getattr(self,"_runtime_cache",None) or (0,{}))[1],
                       options={k:v for k,v in payload.items() if k not in {"messages","_cancel","_trace_metadata"}})
                yield {"type":"meta", "trace":{"id":trace.id,"request_id":payload["request_id"]}}
            answer=[]
            iterator=self._chat_stream_impl(payload)
            try:
                for event in iterator:
                    if isinstance(event,dict) and event.get("type")=="text":answer.append(str(event.get("delta") or ""))
                    elif isinstance(event,dict) and event.get("type")!="reasoning":record("response.event", **event)
                    yield event
            finally:
                if hasattr(iterator,"close"):iterator.close()
                record("response.output", text="".join(answer), cancelled=bool(payload.get("_cancel") and payload["_cancel"].is_set()))
                record("runtime.snapshot",live=getattr(self,"_live_payload",{}),shared_log_tail=list(getattr(self,"logs",[]))[-250:])

    def _chat_stream_impl(self, payload: dict):
        raw_messages = payload.get("messages") or []
        if bool(payload.get("agent")):
            yield from self.agent_chat_stream(payload)
            return
        if _messages_have_image_attachments(raw_messages):
            self._ensure_vision_runtime(raw_messages)
        if not self.server_ready:
            raise RuntimeError("The local model is not ready")
        request_id = str(payload.get("request_id") or "req_" + uuid.uuid4().hex[:18])
        generation_id = int(getattr(self, "_server_generation", 0) or 0)
        token_step = 0
        messages = raw_messages
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("No chat messages were provided")
        if self.brain.cfg.enabled and self.brain.cfg.zero_context:
            latest_user = next((dict(m) for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
            if not latest_user:
                raise RuntimeError("Zero-context Brain mode requires a user message")
            messages = [latest_user]
        profile = self._apply_generation_overrides(choose_profile(
            self.active_model, messages,
            mode=str(payload.get("mode") or payload.get("preset") or "auto"),
            max_tokens=_safe_int(payload.get("max_tokens"), int(getattr(self.cfg, "generation_max_tokens", 2048)), 16, 32768),
            reasoning=str(payload.get("reasoning") or "auto"),
            reasoning_budget=_safe_int(payload.get("reasoning_budget"), -1, -1, 32768),
        ))
        repair_issues = [str(x) for x in (payload.get("repair_issues") or [])]
        if bool(payload.get("repair")):
            # Repair the failure we actually observed rather than applying the same
            # retry recipe to every bad response.
            if "repetition" in repair_issues or "echo" in repair_issues:
                profile.temperature = max(profile.temperature, 0.72)
                profile.top_p = max(profile.top_p, 0.94)
                profile.repeat_penalty = max(profile.repeat_penalty, 1.12)
            if "too_short" in repair_issues:
                profile.max_tokens = min(32768, max(profile.max_tokens, 3072))
            # In Personal Brain zero-context mode, the inference contract is
            # intentionally strict: the next request contains the user's latest
            # text and chat-template structure only. Recovery may alter sampling
            # parameters, but it must not append hidden textual hints.
            hint = None if (self.brain.cfg.enabled and self.brain.cfg.zero_context) else recovery_hint(repair_issues, profile.language)
            if hint:
                messages = [dict(m) for m in messages]
                for i in range(len(messages)-1, -1, -1):
                    if messages[i].get("role") == "user":
                        messages[i]["content"] = str(messages[i].get("content") or "") + "\n\n" + hint
                        break
            profile.notes = tuple(list(profile.notes) + ["Quality Guard recovery applied for: " + ", ".join(repair_issues or ["unknown"])])

        prepared, ctx_meta = self._prepare_chat_messages(messages, profile)
        record("context.prepared", messages=prepared, context=ctx_meta, profile=profile.to_dict())
        user_text = next((_content_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
        answer_parts: list[str] = []
        yield {"type": "profile", "profile": profile.to_dict()}
        yield {"type": "meta", "request": {"request_id": request_id, "generation_id": generation_id, "cluster_plan_id": getattr(getattr(self, "cluster", None), "active_plan", None).plan_id if getattr(getattr(self, "cluster", None), "active_plan", None) else ""}}
        if self.brain.cfg.enabled and self.brain.cfg.zero_context:
            yield {"type": "meta", "brain": {"zero_context": True, "inference_messages": 1}}
        if ctx_meta.get("trimmed_turns"):
            yield {"type": "meta", "context": ctx_meta}
        self._inference_active = int(getattr(self, "_inference_active", 0) or 0) + 1
        self._last_inference_at = time.monotonic()
        self._idle_unload_fired = False
        try:
            for event in logged_stream(stream_chat_events(
                self.cfg.host, self.cfg.port, prepared,
                temperature=profile.temperature, top_p=profile.top_p, top_k=profile.top_k,
                min_p=profile.min_p, repeat_penalty=profile.repeat_penalty,
                max_tokens=profile.max_tokens, reasoning=profile.effective_reasoning,
                reasoning_budget=profile.reasoning_budget, cancel=payload.get("_cancel"),
            ), prepared, stage="direct", profile=profile.to_dict()):
                if generation_id != int(getattr(self, "_server_generation", 0) or 0):
                    raise RuntimeError("Generation invalidated because the active cluster/model generation changed")
                if event.get("type") == "text" and event.get("delta"):
                    token_step += 1
                    answer_parts.append(str(event["delta"]))
                yield event
        finally:
            self._inference_active = max(0, int(getattr(self, "_inference_active", 1) or 1) - 1)
            self._last_inference_at = time.monotonic()
        yield {"type": "quality", "quality": response_quality(
            user_text, "".join(answer_parts), task=profile.task, language=profile.language
        )}

    def count_tokens(self, messages: list[dict]) -> int | None:
        if not self.server_ready:
            return None
        if self.brain.cfg.enabled and self.brain.cfg.zero_context:
            latest = next((dict(m) for m in reversed(messages or []) if isinstance(m, dict) and m.get("role") == "user"), None)
            messages = [latest] if latest else []
        return count_chat_tokens(self.cfg.host, self.cfg.port, self._materialize_chat_messages(messages))

    def template_preview(self, messages: list[dict]) -> str | None:
        if not self.server_ready:
            return None
        return apply_chat_template(self.cfg.host, self.cfg.port, self._materialize_chat_messages(messages))

    def brain_status(self) -> dict:
        st = self.brain.status(self.active_model)
        root = getattr(self, "training_models_root", None)
        if root is not None:
            st["training_models_root"] = str(root)
            st["training_models_storage"] = "portable-sibling"
        pair=self._find_local_training_pair(self.active_model) if self.active_model else None
        st["selected_model_name"] = str(getattr(self.active_model,"name","") or "") if self.active_model else ""
        st["training_source_repo"] = str((pair or {}).get("repo_id") or "")
        st["training_source_kind"] = str((pair or {}).get("checkpoint_kind") or "")
        st["training_source_auto"] = bool(pair)
        return st

    def update_brain(self, payload: dict) -> dict:
        before = bool(self.brain.cfg.enabled)
        self.brain.update(payload, self.active_model)
        after = bool(self.brain.cfg.enabled)
        if before and not after:
            self.brain_cancel.set()
            self.brain_download_cancel.set()
            with self.brain.lock:
                if self.brain.job.get("state") == "running":
                    self.brain.job.update(state="cancelling", message="Stopping the current Brain task safely…", error="")
            self.log("[brain:power] OFF · cancellation requested for active Brain work")
        elif not before and after:
            # Re-enabling affects future jobs; cancellation of the current one
            # remains sticky until that worker has reached a terminal state.
            with self.brain.lock:
                if self.brain.job.get("state") not in ("running", "cancelling"):
                    self.brain_cancel.clear(); self.brain_download_cancel.clear()
            self.log("[brain:power] ON")
        else:
            self.log(f"[brain] settings updated enabled={after}")
        st = self.brain_status()
        self.events.publish("brain", {"brain": st})
        self.events.publish("state", {"reason": "brain-settings"})
        return st

    def toggle_brain(self, enabled: bool) -> dict:
        requested = bool(enabled)
        before = bool(self.brain.cfg.enabled)
        self.log(f"[brain:toggle] requested={requested} previous={before}")
        self.brain.update({"enabled": requested}, self.active_model)
        after = bool(self.brain.cfg.enabled)
        if not after:
            self.brain_cancel.set()
            self.brain_download_cancel.set()
            with self.brain.lock:
                if self.brain.job.get("state") == "running":
                    self.brain.job.update(state="cancelling", message="Stopping the current Brain task safely…", error="")
        else:
            with self.brain.lock:
                if self.brain.job.get("state") not in ("running", "cancelling"):
                    self.brain_cancel.clear()
                    self.brain_download_cancel.clear()
        st = self.brain_status()
        self.log(f"[brain:toggle] effective={after} changed={before != after} setup_ready={bool(st.get('setup_ready'))} job={st.get('job',{}).get('state','idle')}")
        self.events.publish("brain", {"brain": st})
        self.events.publish("state", {"reason": "brain-toggle"})
        return st

    def cancel_brain_operation(self) -> dict:
        self.brain_cancel.set(); self.brain_download_cancel.set()
        with self.brain.lock:
            if self.brain.job.get("state") in ("running", "cancelling"):
                self.brain.job.update(state="cancelling", message="Stopping the current Brain task safely…", error="")
        self.log("[brain:cancel] user requested cancellation")
        st = self.brain_status()
        self.events.publish("brain", {"brain": st})
        return st

    def resolve_brain_base(self) -> dict:
        if not self.active_model:
            raise RuntimeError("Choose the GGUF model first")
        result = self.brain.resolve_training_base(self.active_model)
        self.log("[brain:base] " + str(result.get("message") or result))
        return result

    def brain_doctor(self) -> dict:
        try:
            self.hw = detect_hardware()
        except Exception:
            pass
        report = self.brain.doctor(self.active_model, self.hw)
        base=self.brain.training_base_for(self.active_model) if self.active_model else ""
        bp=Path(os.path.expanduser(base)) if base else None
        if bp and bp.exists():
            v=self.trainables.verify_local(bp)
            report.setdefault("checks",[]).append({"name":"checkpoint_integrity","ok":bool(v.get("ok")),"level":"ok" if v.get("ok") else "error","detail":"verified" if v.get("ok") else "; ".join(v.get("issues") or [])})
            report["ok"]=bool(report.get("ok")) and bool(v.get("ok"))
            report["checkpoint"] = v
        try:
            self.training_models_root.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.training_models_root).free
            storage_ok = os.access(self.training_models_root, os.W_OK)
            report.setdefault("checks",[]).append({"name":"training_storage","ok":bool(storage_ok),"level":"ok" if storage_ok else "error","detail":f"{self.training_models_root} · {free/(1024**3):.1f} GB free"})
            report["ok"] = bool(report.get("ok")) and bool(storage_ok)
            if bp and bp.exists() and not self.trainables.path_is_managed(bp):
                level = "warn" if self.trainables._path_is_under(bp, self.legacy_training_models_root) else "ok"
                report.setdefault("checks",[]).append({"name":"training_model_location","ok":level=="ok","level":level,"detail":f"Current training source: {bp}"})
        except Exception as exc:
            report.setdefault("checks",[]).append({"name":"training_storage","ok":False,"level":"error","detail":str(exc)})
            report["ok"] = False
        report["training_models_root"] = str(self.training_models_root)
        report["active_model"] = _friendly_model(self.active_model)
        report["runtime"] = self.runtime_status()
        return report

    def search_trainable_models(self, query: str, limit: int = 16) -> dict:
        self.log(f"[brain:catalog] search query={query!r} limit={limit}")
        rows = self.trainables.search(query, limit=limit)
        self.brain_catalog_cache = rows
        self.log(f"[brain:catalog] search completed results={len(rows)} trainable={sum(1 for r in rows if r.get('trainable'))}")
        return {"results": rows}

    def inspect_trainable_model(self, repo_id: str) -> dict:
        self.log(f"[brain:catalog] inspect {repo_id}")
        return self.trainables.inspect(repo_id)

    def _validate_trainable_base_match(self, value: str) -> dict:
        if not self.active_model:
            raise RuntimeError("Choose a GGUF model first")
        candidate_repo=""; candidate_arch=""
        p=Path(os.path.expanduser(value))
        if p.exists():
            if not self.trainables.local_checkpoint_ready(p):
                raise RuntimeError("That folder is not a complete trainable Transformers checkpoint")
            meta=p/".llamaforge-source.json"
            try:
                if meta.is_file(): candidate_repo=str(json.loads(meta.read_text(encoding="utf-8")).get("repo_id") or "")
            except Exception: pass
            if not candidate_repo:
                try: candidate_repo=str(self.trainables._repo_id_from_local_path(p) or "")
                except Exception: candidate_repo=""
            try:
                cfg=json.loads((p/"config.json").read_text(encoding="utf-8")); candidate_arch=str(cfg.get("model_type") or "")
            except Exception: pass
        else:
            candidate_repo=value if "/" in value else ""
            info=self.trainables.inspect(value) if candidate_repo else {}
            candidate_arch=str(info.get("architecture") or "")
            if candidate_repo and not info.get("trainable"):
                raise RuntimeError(info.get("reason") or "That repository is not a trainable checkpoint")
        expected=self.brain.resolve_training_base(self.active_model)
        expected_repo=str(expected.get("repo") or "") if expected.get("ok") else ""
        if expected_repo and candidate_repo and candidate_repo.lower()!=expected_repo.lower():
            raise RuntimeError(f"This GGUF was resolved to {expected_repo}, but you selected {candidate_repo}. Training a LoRA on a different base can corrupt behavior. Use the exact matching base.")
        active_arch=str(getattr(self.active_model,"architecture","") or "").lower().replace("_","").replace("-","")
        cand_arch=candidate_arch.lower().replace("_","").replace("-","")
        if active_arch and cand_arch and active_arch not in cand_arch and cand_arch not in active_arch:
            raise RuntimeError(f"Architecture mismatch: active GGUF is {self.active_model.architecture}, training checkpoint reports {candidate_arch}.")
        return {"ok":True,"expected_repo":expected_repo,"candidate_repo":candidate_repo,"candidate_architecture":candidate_arch}

    def use_trainable_base(self, value: str) -> dict:
        if not self.active_model:
            raise RuntimeError("Choose a GGUF model first")
        value = str(value or "").strip()
        if not value:
            raise RuntimeError("Training model path/repository is empty")
        match=self._validate_trainable_base_match(value)
        self.brain.set_training_base(self.active_model, value)
        self.log(f"[brain:base] linked {value} match={match}")
        self.events.publish("brain", {"brain": self.brain_status()})
        return self.brain_status()

    def download_trainable_async(self, repo_id: str) -> dict:
        if not self.active_model:
            raise RuntimeError("Choose a GGUF model first")
        repo_id = str(repo_id or "").strip()
        if not repo_id:
            raise RuntimeError("Repository id is empty")
        # Claim the Brain operation under the same lock used to set its state.
        # A separate status() check allowed two near-simultaneous API clicks to
        # both start multi-GB downloads/training work.
        with self.brain.lock:
            if self.brain.job.get("state") in ("running", "cancelling"):
                raise RuntimeError("Another Brain operation is already running")
            self.brain_download_cancel.clear(); self.brain_cancel.clear()
            self.brain.job={"state":"running","stage":"base-download","message":f"Inspecting {repo_id}…","error":"","progress":0.01}
        self.events.publish("brain", {"brain": self.brain_status()})
        model=self.active_model
        def work():
            try:
                info=self.trainables.inspect(repo_id)
                if not info.get("trainable"):
                    raise RuntimeError(info.get("reason") or "This repository is not trainable")
                self.log(f"[brain:download] repo={repo_id} weights={info.get('weight_gb',0)}GB total={info.get('total_gb',0)}GB gated={info.get('gated')} remote_code={info.get('requires_remote_code')}")
                progress_log={"bucket":-1,"message":""}
                started=time.time()
                def tracked_progress(msg,pct,done,total):
                    elapsed=max(0.001,time.time()-started)
                    bps=int(done/elapsed) if done else 0
                    eta=int(max(0,(total-done)/bps)) if total and bps else None
                    with self.brain.lock:
                        self.brain.job.update(state="running",stage="base-download",message=str(msg),progress=float(pct),error="",done=int(done),total=int(total),bytes_per_sec=bps,eta_seconds=eta)
                    self.events.publish("brain", {"brain":self.brain_status()})
                    bucket=int(max(0.0,min(1.0,float(pct)))*20)
                    if bucket!=progress_log["bucket"] or str(msg)!=progress_log["message"]:
                        progress_log.update(bucket=bucket,message=str(msg))
                        self.log(f"[brain:download:progress] {bucket*5}% done={int(done)} total={int(total)} bps={bps} eta={eta} {msg}")
                downloader=getattr(self.trainables,"download_training_bundle",self.trainables.download_snapshot)
                local=downloader(repo_id,allow_remote_code=bool(self.brain.cfg.allow_remote_code),progress=tracked_progress,cancel=self.brain_download_cancel)
                self._validate_trainable_base_match(local)
                self.brain.set_training_base(model,local)
                report=self.brain_doctor()
                with self.brain.lock:
                    self.brain.job={"state":"done","stage":"base-download","message":"Trainable model downloaded and linked","error":"","progress":1.0,"path":local,"doctor_ok":bool(report.get('ok'))}
                self.log(f"[brain:download] completed repo={repo_id} path={local}")
                self.events.publish("brain", {"brain":self.brain_status()}); self.events.publish("state", {"reason":"brain-base-downloaded"})
            except Exception as exc:
                cancelled = self.brain_download_cancel.is_set() or self.brain_cancel.is_set() or isinstance(exc, BrainCancelled)
                if cancelled:
                    self.log(f"[brain:download] cancelled: {exc}")
                    with self.brain.lock:
                        self.brain.job={"state":"cancelled","stage":"base-download","message":"Training model download stopped","error":"","progress":float(self.brain.job.get("progress") or 0.0)}
                else:
                    self.log_exception("brain:download",exc)
                    with self.brain.lock:
                        self.brain.job={"state":"error","stage":"base-download","message":"Training model download failed","error":str(exc),"progress":0.0}
                self.events.publish("brain", {"brain":self.brain_status()})
        threading.Thread(target=work,name="brain-base-download",daemon=True).start()
        return self.brain_status()

    def setup_brain_automatically(self) -> dict:
        """One-click Personal Brain setup for the active GGUF.

        Detects the trainable HF source, prepares the isolated trainer and arms
        zero-context weight learning. The potentially large downloads happen
        only after this explicit user action.
        """
        if not self.active_model:
            raise RuntimeError("Choose a GGUF model first")
        model = self.active_model
        # Claim auto-setup atomically so repeated double-clicks cannot create two
        # trainer/download workers. Safe defaults are saved while the claim is held.
        with self.brain.lock:
            if self.brain.job.get("state") in ("running", "cancelling"):
                return self.brain.status(model)
            self.brain_cancel.clear(); self.brain_download_cancel.clear()
            # Setup prepares infrastructure only. It must not silently change
            # the user's master power or advanced learning preferences.
            self.brain.update({}, model)
            self.brain.job = {"state":"running","stage":"auto-setup","message":"Checking this model…","error":"","progress":0.02}
        self.events.publish("brain", {"brain": self.brain_status()})
        proc = getattr(self, "server_proc", None)
        chat_ready = bool(getattr(self, "server_ready", False) and getattr(proc, "running", False))
        self.log(f"[brain:auto] background setup requested chat_server_ready={chat_ready} active={model.name}")

        def set_job(stage, message, progress):
            if self.brain_cancel.is_set():
                raise BrainCancelled("Automatic Brain setup cancelled by user")
            with self.brain.lock:
                self.brain.job.update(state="running", stage=stage, message=message, progress=float(progress), error="")
            self.events.publish("brain", {"brain": self.brain_status()})
            self.log("[brain:auto] " + message)

        def work():
            try:
                base = self.brain.training_base_for(model)
                bp = Path(os.path.expanduser(base)) if base else None
                # 0.15.1 portable storage: if an older LlamaForge version already
                # downloaded this checkpoint under ~/.llamaforge/brain/bases, move
                # that managed copy beside the application instead of downloading
                # several GB again. Arbitrary manually selected folders are never moved.
                if bp and bp.is_dir() and self.trainables._path_is_under(bp, self.legacy_training_models_root):
                    set_job("move-base", "Moving the existing training model beside LlamaForge…", 0.04)
                    def move_progress(msg,pct,done,total):
                        with self.brain.lock:
                            self.brain.job.update(state="running",stage="move-base",message=str(msg),progress=0.04+0.04*max(0.0,min(1.0,float(pct))),error="",done=int(done),total=int(total))
                        self.events.publish("brain", {"brain": self.brain_status()})
                    moved = self.trainables.relocate_training_source(bp, legacy_root=self.legacy_training_models_root, progress=move_progress)
                    if str(moved) != str(bp):
                        self.brain.set_training_base(model, moved)
                        base = moved
                        bp = Path(moved)
                        self.log(f"[brain:storage] active training source relocated to {moved}")
                local_ready = bool(bp and bp.exists() and self.trainables.local_checkpoint_ready(bp))
                if local_ready and (bp / "adapter_config.json").is_file():
                    local_ready = bool(self.trainables.training_bundle_info(bp).get("ok"))
                # A path can be structurally valid and still belong to the wrong
                # logical model. 0.16.0 could leave an old raw dependency linked
                # to a fine-tuned GGUF, which made setup look ready and then
                # trained/faulted against the wrong checkpoint. Revalidate the
                # lineage before accepting an existing path.
                if local_ready:
                    try:
                        self._validate_trainable_base_match(str(bp))
                    except Exception as exc:
                        self.log(f"[brain:auto] existing training source rejected: {exc}")
                        local_ready=False
                if not base or not local_ready:
                    set_job("detect-base", "Finding the exact trainable source for this model…", 0.06)
                    repo = base if base and "/" in base and not (bp and bp.exists()) else ""
                    if not repo or (bp and bp.exists() and not local_ready):
                        result = self.brain.resolve_training_base(model)
                        if not result.get("ok") or not result.get("repo"):
                            raise RuntimeError(result.get("message") or "Could not determine the trainable base automatically")
                        repo = result["repo"]
                    info = self.trainables.inspect(repo)
                    if not info.get("trainable"):
                        raise RuntimeError(info.get("reason") or f"{repo} is not a trainable Transformers checkpoint")
                    set_job("download-base", f"Preparing training bundle {repo}…", 0.12)
                    download_started=time.time()
                    def base_progress(msg,pct,done,total):
                        gp=0.12+max(0.0,min(1.0,float(pct)))*0.46
                        elapsed=max(0.001,time.time()-download_started)
                        bps=int(done/elapsed) if done else 0
                        eta=int(max(0,(total-done)/bps)) if total and bps else None
                        with self.brain.lock:
                            self.brain.job.update(state="running",stage="download-base",message=str(msg),progress=gp,error="",done=int(done),total=int(total),bytes_per_sec=bps,eta_seconds=eta)
                        self.events.publish("brain", {"brain": self.brain_status()})
                    downloader=getattr(self.trainables,"download_training_bundle",self.trainables.download_snapshot)
                    local = downloader(repo,allow_remote_code=bool(self.brain.cfg.allow_remote_code),progress=base_progress,cancel=self.brain_download_cancel)
                    self._validate_trainable_base_match(local)
                    self.brain.set_training_base(model, local)
                    set_job("detect-base", f"Learning source ready: {repo}", 0.60)
                else:
                    set_job("detect-base", "Trainable base already downloaded", 0.60)

                if not (self.brain.trainer_ready() and self.brain.toolchain_ready()):
                    set_job("trainer", "Preparing the learning engine…", 0.62)
                    def progress(msg, p):
                        set_job("trainer", str(msg), 0.62 + max(0.0, min(1.0, float(p))) * 0.30)
                    self.brain.prepare_environment(progress, force=False, cancel=self.brain_cancel)
                else:
                    set_job("trainer", "Learning engine already ready", 0.92)

                if not self.brain.training_base_ready(model):
                    raise RuntimeError("The trainable base could not be verified")
                if not self.brain.trainer_ready() or not self.brain.toolchain_ready():
                    raise RuntimeError("The learning engine could not be verified")
                set_job("doctor","Running Brain preflight checks…",0.95)
                report=self.brain_doctor()
                bad=[c for c in report.get("checks",[]) if not c.get("ok") and c.get("level")!="warn"]
                if bad:
                    names={str(c.get("name") or "") for c in bad}
                    # Autopilot gets one safe repair attempt for broken trainer/toolchain
                    # state. This is explicit setup work, never normal startup.
                    if names & {"trainer_python","trainer_marker","toolchain","dependencies"}:
                        set_job("repair","Repairing the learning engine…",0.96)
                        def repair_progress(msg,p): set_job("repair",str(msg),0.96+max(0.0,min(1.0,float(p)))*0.025)
                        self.brain.prepare_environment(repair_progress,force=True,cancel=self.brain_cancel)
                        report=self.brain_doctor(); bad=[c for c in report.get("checks",[]) if not c.get("ok") and c.get("level")!="warn"]
                    if bad:
                        raise RuntimeError("Brain preflight failed: "+"; ".join(f"{c.get('name')}: {c.get('detail')}" for c in bad[:4]))
                with self.brain.lock:
                    self.brain.job = {"state":"done","stage":"ready","message":"Personal Brain is ready — just chat","error":"","progress":1.0}
                self.events.publish("brain", {"brain": self.brain_status()})
                self.events.publish("state", {"reason":"brain-auto-ready"})
                self.log("[brain:auto] Personal Brain ready")
            except Exception as exc:
                cancelled = self.brain_cancel.is_set() or self.brain_download_cancel.is_set() or isinstance(exc, BrainCancelled)
                with self.brain.lock:
                    if cancelled:
                        self.brain.job = {"state":"cancelled","stage":"auto-setup","message":"Automatic Brain setup stopped","error":"","progress":float(self.brain.job.get("progress") or 0.0)}
                    else:
                        self.brain.job = {"state":"error","stage":"auto-setup","message":"Automatic setup needs attention","error":str(exc),"progress":0.0}
                if cancelled: self.log(f"[brain:auto] cancelled: {exc}")
                else: self.log_exception("brain:auto", exc)
                self.events.publish("brain", {"brain": self.brain_status()})
                self.events.publish("state", {"reason":"brain-auto-cancelled" if cancelled else "brain-auto-error"})
        threading.Thread(target=work, name="brain-auto-setup", daemon=True).start()
        return self.brain_status()

    def prepare_brain_async(self, force: bool = False) -> dict:
        with self.brain.lock:
            if self.brain.job.get("state") in ("running", "cancelling"):
                return self.brain.status(self.active_model)
            self.brain_cancel.clear()
            self.brain.job = {"state":"running","stage":"setup","message":"Preparing Brain Trainer…","error":"","progress":0.01}
        def work():
            try:
                def progress(msg, p):
                    if self.brain_cancel.is_set():
                        raise BrainCancelled("Trainer setup cancelled")
                    with self.brain.lock:
                        self.brain.job.update(state="running", stage="setup", message=str(msg), progress=float(p), error="")
                    self.events.publish("brain", {"brain": self.brain_status()})
                    self.log("[brain:setup] " + str(msg))
                self.brain.prepare_environment(progress, force=force, cancel=self.brain_cancel)
                if self.brain_cancel.is_set():
                    raise BrainCancelled("Trainer setup cancelled")
                with self.brain.lock:
                    self.brain.job = {"state":"done","stage":"setup","message":"Brain Trainer ready","error":"","progress":1.0}
                self.events.publish("brain", {"brain": self.brain_status()})
            except Exception as exc:
                cancelled = self.brain_cancel.is_set() or isinstance(exc, BrainCancelled)
                with self.brain.lock:
                    self.brain.job = {
                        "state":"cancelled" if cancelled else "error",
                        "stage":"setup",
                        "message":"Brain Trainer setup stopped" if cancelled else "Brain Trainer setup failed",
                        "error":"" if cancelled else str(exc),
                        "progress":float(self.brain.job.get("progress") or 0.0) if cancelled else 0.0,
                    }
                if cancelled: self.log(f"[brain:setup] cancelled: {exc}")
                else: self.log_exception("brain:setup", exc)
                self.events.publish("brain", {"brain": self.brain_status()})
        threading.Thread(target=work, name="brain-setup", daemon=True).start()
        return self.brain_status()

    def _brain_synthesize_examples(self, user_text: str, assistant_text: str = "", *, cancel: threading.Event | None = None) -> list[dict]:
        if not self.brain.cfg.auto_synthesize or not self.server_ready:
            return []
        prompt = (
            "You compile supervised training examples for a private personal language model.\n"
            "The USER MESSAGE is the only source of truth. Never use, repeat, or validate a previous assistant/model answer as a training target.\n"
            "Extract only durable personal facts, explicit corrections, stable preferences or writing style explicitly taught by the user.\n"
            "Do not turn a tool request, pending task, calendar event, temporary plan or a question into permanent model knowledge.\n"
            "If the user is merely asking a question and provides no answer/fact/correction, return an empty JSON array.\n"
            "Generate 1 to 8 short supervised question/answer examples that would help the model reproduce what the user explicitly taught.\n"
            "Return ONLY JSON in this exact form:\n"
            "[{\"user\":\"recall question\",\"assistant\":\"exact answer span from user text\",\"evidence\":\"exact supporting quote from user text\",\"kind\":\"fact|preference|style\"}]\n\n"
            f"USER MESSAGE:\n{user_text}"
        )
        parts=[]
        try:
            for ev in stream_chat_events(
                self.cfg.host, self.cfg.port, [{"role":"user","content":prompt}],
                temperature=0.12, top_p=0.85, top_k=24, min_p=0.0, repeat_penalty=1.02,
                max_tokens=1200, reasoning="off", reasoning_budget=0, timeout=300, cancel=cancel,
            ):
                if ev.get("type") == "text" and ev.get("delta"):
                    parts.append(str(ev["delta"]))
            raw="".join(parts).strip()
            if "```" in raw:
                raw=re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I|re.S).strip()
            a=raw.find("["); b=raw.rfind("]")
            if a<0 or b<a:
                return []
            data=json.loads(raw[a:b+1])
            return validate_examples(data, source_text=user_text, require_evidence=True)
        except Exception as exc:
            self.log("[brain:synth] Teacher synthesis skipped: " + str(exc))
            return []

    def preview_brain_lesson(self, payload: dict) -> dict:
        """Compile once while chat is loaded; this endpoint never starts a trainer."""
        text = str(payload.get("user") or "").strip()
        if not text or len(text) > 20000:
            raise ValueError("A lesson must contain 1 to 20000 characters")
        model = self.active_model
        model_key = self.brain.model_key(model)
        deterministic = self.brain.deterministic_examples(text)
        if payload.get("precheck") and not obvious_non_teaching(text) and not deterministic:
            return {"examples": [], "should_learn": True, "compiled": False, "reason": "needs-compilation",
                    "model_key": model_key}
        if obvious_non_teaching(text):
            examples = []
        else:
            examples = deterministic or self._brain_synthesize_examples(text)
        if self.brain.model_key(self.active_model) != model_key:
            raise RuntimeError("The selected model changed during compilation; review the lesson again")
        examples = validate_examples(examples)
        duplicate = bool(model and examples and self.brain.already_learned(model, examples))
        return {"examples": examples, "should_learn": bool(examples) and not duplicate, "compiled": True,
                "reason": "already-learned" if duplicate else "supervision" if examples else "no-supervision",
                "model_key": model_key}

    def learn_brain_async(self, payload: dict) -> dict:
        if not self.brain.cfg.enabled:
            raise RuntimeError("Brain Learning is disabled")
        if not self.active_model:
            raise RuntimeError("Choose and load a model before teaching it")
        user_text=str(payload.get("user") or "").strip()
        assistant_text=str(payload.get("assistant") or "").strip()
        if not user_text or len(user_text) > 20000:
            raise ValueError("A lesson must contain 1 to 20000 characters")
        if not assistant_text:
            assistant_text="Acknowledged."
        supplied = payload.get("examples")
        mode = str(payload.get("mode") or "automatic")
        if mode not in ("automatic", "compiled", "explicit"):
            raise ValueError("Unknown learning mode")
        if mode == "explicit":
            explicit = validate_examples(supplied)
            if not explicit:
                raise ValueError("An explicit lesson needs a valid question and user-provided answer")
        elif mode == "compiled":
            explicit = self.brain.deterministic_examples(user_text) + validate_examples(supplied, source_text=user_text, require_evidence=True)
        else:
            explicit = None
        if payload.get("model_key") and payload["model_key"] != self.brain.model_key(self.active_model):
            raise RuntimeError("The selected model changed; review this lesson for the new model")
        model=self.active_model
        launch_payload=dict(self.last_launch_payload or {"model_path": model.path, "profile":"Balanced", "ctx":4096, "cpu_only":bool(self.cfg.cpu_only_default), "gpu_layer_percent":int(self.cfg.gpu_layer_percent)})
        launch_payload["model_path"] = model.path
        def same_model():
            return self.active_model is not None and Path(self.active_model.path) == Path(model.path)
        def require_model():
            if not same_model():
                self.brain_cancel.set()
                raise BrainCancelled("The selected model changed; the old learning job was cancelled")
        with self.brain.lock:
            if self.brain.job.get("state") in ("running", "cancelling"):
                raise RuntimeError("The personal brain is already learning")
            self.brain_cancel.clear()
            self.brain.job={"state":"running","stage":"synthesize","message":"Compiling this turn into training examples…","error":"","progress":0.03}
        self.events.publish("brain", {"brain": self.brain_status()})

        def work():
            stopped_for_training = False
            try:
                require_model()
                run_id=f"{int(time.time())}-{threading.get_ident()}"
                self.log(f"[brain:run] id={run_id} model={model.name} base={self.brain.training_base_for(model)} device={self.brain.cfg.device} zero_context={self.brain.cfg.zero_context}")
                if self.brain_cancel.is_set():
                    raise BrainCancelled("Learning cancelled before compilation")
                deterministic = [] if mode == "explicit" else self.brain.deterministic_examples(user_text)
                examples = explicit if explicit is not None else (
                    [] if obvious_non_teaching(user_text) else deterministic or self._brain_synthesize_examples(user_text, cancel=self.brain_cancel))
                examples = validate_examples(examples)
                require_model()
                self.log(f"[brain:synth] grounded_examples={len(examples)} deterministic_examples={len(deterministic)}")
                # Questions with no explicit teaching signal should not make the
                # model reinforce its own answer.  We still inspect every user
                # message, but only actual user-provided supervision changes
                # weights.
                if not examples or self.brain.already_learned(model, examples):
                    with self.brain.lock:
                        self.brain.job={"state":"done","stage":"no-op","message":"No explicit fact or correction to learn from this message","error":"","progress":1.0}
                    self.events.publish("brain", {"brain": self.brain_status()})
                    self.log("[brain] no user-provided training target; weights unchanged")
                    return
                if self.brain_cancel.is_set():
                    raise BrainCancelled("Learning cancelled before model unload")
                report=self.brain_doctor()
                bad=[c for c in report.get("checks",[]) if not c.get("ok") and c.get("level")!="warn"]
                if bad:
                    raise RuntimeError("Brain preflight failed before training: "+"; ".join(f"{c.get('name')}: {c.get('detail')}" for c in bad[:5]))
                with self._model_lifecycle_lock:
                    require_model()
                    if self.server_proc.running:
                        self.stop_server(reason="brain-training")
                        stopped_for_training = True
                if stopped_for_training:
                    time.sleep(.35)
                # The worker admits CPU training using the actual checkpoint
                # size and selected backend, including CPU use on an Intel iGPU.
                # A model-name guess ("7b") cannot establish a memory budget.
                def progress(msg,p):
                    require_model()
                    self.events.publish("brain", {"brain": self.brain_status(), "message":str(msg), "progress":float(p)})
                    self.log("[brain] " + str(msg))
                result=self.brain.learn(model,"" if mode == "explicit" else user_text,assistant_text,examples,progress,cancel=self.brain_cancel)
                self.log(f"[brain] Candidate generation {result.get('generation')} · awaiting reload verification")
                with self._model_lifecycle_lock:
                    require_model()
                    self.start_server(launch_payload)
                deadline=time.time()+300
                while time.time()<deadline and self.server_proc.running and not self.server_ready and not self.server_error:
                    time.sleep(.5)
                if not self.server_ready:
                    raise RuntimeError(self.server_error or "The updated adapter was trained but the model did not become ready after reload")
                if self.brain_cancel.is_set():
                    raise BrainCancelled("Learning cancelled before confirmation")
                with self._model_lifecycle_lock:
                    require_model()
                    self.brain.confirm_learning(model)
                with self.brain.lock:
                    self.brain.job.update(state="done",stage="complete",message="Learned into weights · context remains zero",progress=1.0,error="")
                self.events.publish("brain", {"brain": self.brain_status()})
                self.events.publish("state", {"reason":"brain-learned"})
            except Exception as exc:
                cancelled = self.brain_cancel.is_set() or isinstance(exc, BrainCancelled)
                if cancelled: self.log(f"[brain:learn] cancelled: {exc}")
                else: self.log_exception("brain", exc)
                try:
                    if self.brain.rollback_unconfirmed_learning(model):
                        self.log("[brain:transaction] restored last confirmed Personal Brain adapter")
                        # If the failed reload process is still alive, restart it
                        # once with the restored adapter rather than leaving a
                        # potentially incompatible candidate resident.
                        with self._model_lifecycle_lock:
                            if same_model() and self.server_proc.running:
                                self.stop_server(reason="brain-rollback")
                except Exception as rb_exc:
                    self.log_exception("brain:rollback", rb_exc)
                with self.brain.lock:
                    self.brain.job.update(
                        state="cancelled" if cancelled else "error",
                        stage="cancelled" if cancelled else "failed",
                        message="Brain learning stopped; candidate weights were discarded" if cancelled else "Learning failed; weights were not confirmed",
                        error="" if cancelled else str(exc),
                        progress=float(self.brain.job.get("progress") or 0.0) if cancelled else 0.0,
                    )
                if stopped_for_training:
                    try:
                        with self._model_lifecycle_lock:
                            if same_model() and not self.server_proc.running:
                                self.start_server(launch_payload)
                    except Exception as rex:
                        self.log_exception("brain:restore", rex)
                self.events.publish("brain", {"brain": self.brain_status()})
                self.events.publish("state", {"reason":"brain-learn-cancelled" if cancelled else "brain-learn-error"})
        threading.Thread(target=work,name="brain-learn",daemon=True).start()
        return self.brain_status()

    def bake_brain_async(self, payload: dict) -> dict:
        if not self.active_model:
            raise RuntimeError("Choose the GGUF model to bake")
        export_bin=self.runtime.find_binary("llama-export-lora")
        if not export_bin:
            raise RuntimeError("The active llama.cpp runtime does not include llama-export-lora")
        if not self.brain.adapter_gguf(self.active_model).is_file():
            raise RuntimeError("No confirmed learned Personal Brain adapter exists to bake")
        out=str(payload.get("output") or "").strip() or None
        model=self.active_model
        launch_payload=dict(self.last_launch_payload or {"model_path":model.path,"profile":"Balanced","ctx":4096,"cpu_only":bool(self.cfg.cpu_only_default),"gpu_layer_percent":int(self.cfg.gpu_layer_percent)})
        with self.brain.lock:
            if self.brain.job.get("state") in ("running", "cancelling"):
                raise RuntimeError("Another Brain operation is already running")
            self.brain.job={"state":"running","stage":"bake","message":"Preparing standalone Brain GGUF…","error":"","progress":.05}
        self.events.publish("brain", {"brain":self.brain_status()})
        def work():
            was_running=bool(self.server_proc.running)
            try:
                if was_running:
                    self.stop_server(reason="brain-bake")
                with self.brain.lock:
                    self.brain.job.update(state="running",stage="bake",message="Merging personal weights into a new GGUF…",error="",progress=.15)
                self.events.publish("brain", {"brain":self.brain_status()})
                merged=self.brain.bake_merged(model,model.path,export_bin,out)
                with self.brain.lock:
                    self.brain.job={"state":"done","stage":"bake","message":"Merged brain GGUF created","error":"","progress":1.0,"output":merged}
                self.log("[brain] Merged brain written to " + merged)
                self.events.publish("brain", {"brain":self.brain_status()})
            except Exception as exc:
                with self.brain.lock:
                    self.brain.job={"state":"error","stage":"bake","message":"Brain merge failed","error":str(exc),"progress":0.0}
                self.log_exception("brain:bake", exc)
                self.events.publish("brain", {"brain":self.brain_status()})
            finally:
                if was_running and not self.server_proc.running:
                    try:
                        self.start_server(launch_payload)
                    except Exception as rex:
                        self.log_exception("brain:bake:restore", rex)
        threading.Thread(target=work,name="brain-bake",daemon=True).start()
        return self.brain_status()

    def pick_file(self, kind: str) -> str:
        path = native_pick_file(kind)
        if not path:
            self.log("[picker] Selection cancelled or native picker unavailable")
        return path

    def pick_folder(self) -> str:
        path = native_pick_folder()
        if not path:
            self.log("[picker] Folder selection cancelled or native picker unavailable")
        return path

    def add_model_folder(self, path: str):
        p = str(Path(path).expanduser())
        if not Path(p).is_dir():
            raise NotADirectoryError(p)
        if p not in self.cfg.model_dirs:
            self.cfg.model_dirs.append(p)
            self.cfg.save()
        threading.Thread(target=self.scan_models, name="model-scan", daemon=True).start()

    def update_settings(self, payload: dict):
        # Validate before any mutation: bool("false") must never enable access.
        for key in ("agent_enabled_default", "agent_allow_write", "agent_allow_workspace_write",
                    "agent_allow_private_network", "agent_browser_headless", "agent_allow_telegram_read", "agent_allow_telegram_write"):
            if key in payload and not isinstance(payload[key], bool):
                raise ValueError(f"{key} must be a JSON boolean")
        if "agent_skill_profile" in payload and payload["agent_skill_profile"] not in {"all", "telegram_only"}:
            raise ValueError("Invalid Agent skill profile")
        for key in ("agent_allow_telegram_read", "agent_allow_telegram_write", "agent_skill_profile"):
            if key in payload: setattr(self.cfg, key, payload[key])
        if "max_ram_percent" in payload:
            self.cfg.max_ram_percent = _safe_int(payload["max_ram_percent"], 88, 50, 95)
        if "port" in payload:
            self.cfg.port = _safe_int(payload["port"], 8080, 1024, 65535)
        if "model_memory_mode" in payload:
            mode = str(payload.get("model_memory_mode") or "hybrid").strip().lower()
            self.cfg.model_memory_mode = mode if mode in MEMORY_MODES else "hybrid"
            self.log(f"[settings] model memory mode = {self.cfg.model_memory_mode}")
        if "accelerator_mode" in payload:
            mode = str(payload.get("accelerator_mode") or "hybrid").strip().lower()
            self.cfg.accelerator_mode = mode if mode in {"adaptive", "cpu", "gpu", "hybrid", "max_both"} else "adaptive"
            self.cfg.cpu_only_default = self.cfg.accelerator_mode == "cpu"
        if "gpu_layer_percent" in payload:
            self.cfg.gpu_layer_percent = _safe_int(payload.get("gpu_layer_percent"), 35, 5, 95)
        if "exit_unloads_model" in payload:
            self.cfg.exit_unloads_model = bool(payload.get("exit_unloads_model"))
        if "ui_disconnect_shutdown_seconds" in payload:
            self.cfg.ui_disconnect_shutdown_seconds = _safe_int(payload["ui_disconnect_shutdown_seconds"], 12, 6, 300)
        if "idle_unload_minutes" in payload:
            self.cfg.idle_unload_minutes = _safe_int(payload["idle_unload_minutes"], 0, 0, 1440)
            self._idle_unload_fired = False
        if "hf_token" in payload:
            token=str(payload.get("hf_token") or "").strip()
            self.cfg.hf_token=token
            self.models.token=token
            self.trainables.token=token
            self.log(f"[settings] Hugging Face token {'configured' if token else 'cleared'}")
        if "agent_enabled_default" in payload:
            self.cfg.agent_enabled_default = bool(payload.get("agent_enabled_default"))
        if "agent_allow_write" in payload:
            self.cfg.agent_allow_write = bool(payload.get("agent_allow_write"))
        if "agent_allow_workspace_write" in payload:
            self.cfg.agent_allow_workspace_write = bool(payload.get("agent_allow_workspace_write"))
        if "agent_allow_private_network" in payload:
            self.cfg.agent_allow_private_network = bool(payload.get("agent_allow_private_network"))
        if "agent_browser_headless" in payload:
            self.cfg.agent_browser_headless = bool(payload.get("agent_browser_headless"))
        if "agent_max_steps" in payload:
            self.cfg.agent_max_steps = _safe_int(payload.get("agent_max_steps"), 8, 1, 16)
        if "default_context_size" in payload:
            self.cfg.default_context_size = _safe_int(payload.get("default_context_size"), 4096, 512, 262144)
        if "generation_overrides_enabled" in payload:
            self.cfg.generation_overrides_enabled = bool(payload.get("generation_overrides_enabled"))
        if "generation_temperature" in payload:
            self.cfg.generation_temperature = min(2.0, max(0.0, float(payload.get("generation_temperature") or 0.0)))
        if "generation_top_p" in payload:
            self.cfg.generation_top_p = min(1.0, max(0.0, float(payload.get("generation_top_p") or 0.0)))
        if "generation_top_k" in payload:
            self.cfg.generation_top_k = _safe_int(payload.get("generation_top_k"), 40, 0, 500)
        if "generation_min_p" in payload:
            self.cfg.generation_min_p = min(1.0, max(0.0, float(payload.get("generation_min_p") or 0.0)))
        if "generation_repeat_penalty" in payload:
            self.cfg.generation_repeat_penalty = min(1.30, max(0.80, float(payload.get("generation_repeat_penalty") or 1.03)))
        if "generation_max_tokens" in payload:
            self.cfg.generation_max_tokens = _safe_int(payload.get("generation_max_tokens"), 2048, 16, 32768)
        if "speculative_mode" in payload:
            mode = str(payload.get("speculative_mode") or "auto").lower()
            self.cfg.speculative_mode = mode if mode in {"off", "auto", "ngram"} else "auto"
        if "adaptive_context" in payload:
            self.cfg.adaptive_context = bool(payload.get("adaptive_context"))
        self.cfg.save()
        self.events.publish("state", {"reason": "settings-updated"})

    def shutdown(self):
        self.shutting_down = True
        try:
            if getattr(self, "agent", None): self.agent.telegram.close()
        except Exception:
            pass
        try:
            if getattr(self, "remote_apps", None):
                self.remote_apps.stop()
        except Exception:
            pass
        try:
            if self.cfg.exit_unloads_model:
                self.stop_server(reason="application exit")
        except Exception:
            pass
        try:
            if getattr(self, "cluster", None):
                self.cluster.shutdown()
        except Exception:
            pass


def _expected_client_disconnect(exc: BaseException | None) -> bool:
    """Return True for normal browser/socket disconnects.

    Windows commonly reports an app-mode browser closing an idle keep-alive
    connection as WinError 10053/10054.  Those are transport lifecycle events,
    not application failures, and must never produce a scary traceback.
    """
    if exc is None:
        return False
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in {10053, 10054, 10058}:
            return True
        # POSIX equivalents: ECONNRESET, ECONNABORTED, EPIPE.
        if getattr(exc, "errno", None) in {32, 53, 54, 103, 104}:
            return True
    return False


class LlamaForgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, state: LlamaForgeState):
        super().__init__(addr, handler)
        self.state = state

    def handle_error(self, request, client_address):
        # socketserver prints uncaught handler errors directly to stderr.  A web
        # client disappearing mid keep-alive is routine, especially Edge/Chrome
        # app-mode on Windows, so suppress only those expected disconnects.
        exc = sys.exc_info()[1]
        if _expected_client_disconnect(exc):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "LlamaForgeLocal/0.34.3-hotfix"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Keep the launcher console clean; actionable events go to Runtime Logs.
        pass

    def handle(self):
        # BaseHTTPRequestHandler may be waiting for the *next* keep-alive request
        # when Edge/Chrome closes the app window.  On Windows 3.13 this bubbles up
        # as WinError 10053 before do_GET/do_POST gets a chance to catch it.
        try:
            return super().handle()
        except BaseException as exc:
            if _expected_client_disconnect(exc):
                self.close_connection = True
                return
            raise

    def _write(self, data: bytes) -> bool:
        """Best-effort response write; False means the browser went away."""
        try:
            self.wfile.write(data)
            return True
        except BaseException as exc:
            if _expected_client_disconnect(exc):
                self.close_connection = True
                return False
            raise

    @property
    def state(self) -> LlamaForgeState:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, obj: Any, status: int = 200):
        data = json.dumps(_jsonable(obj), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._write(data)

    def _send_file(self, path: Path, filename: str = "download"):
        data = path.read_bytes()
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return self._send_download(data, filename, ctype)

    def _send_download(self, data: bytes, filename: str, ctype: str):
        safe_name = re.sub(r"[\r\n\"]+", "_", str(filename or "download"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 80 * 1024 * 1024:
            raise ValueError("Request body too large (80 MB maximum)")
        raw = self.rfile.read(length) if length else b"{}"
        obj = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        return obj if isinstance(obj, dict) else {}

    def _route_api_get(self, path: str, query: dict[str, list[str]]):
        if path.startswith("/api/logs/requests"):
            if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
                return self._send_json({"error":"Request transcripts are available only from the local control interface"},403)
            store=self.state.request_traces
            if path == "/api/logs/requests":
                return self._send_json({"traces":store.list(),"settings":store.status()})
            trace_id=str((query.get("id") or [""])[0])
            try:
                if path == "/api/logs/requests/export":
                    return self._send_download(store.export(trace_id),trace_id+".zip","application/zip")
                rows=store.events(trace_id)
                if path == "/api/logs/requests/event":
                    sequence=_safe_int((query.get("seq") or [0])[0],0,0,2**31-1)
                    row=next((r for r in rows if r["seq"]==sequence),None)
                    return self._send_json({"event":row},200 if row else 404)
                if path == "/api/logs/requests/detail":
                    return self._send_json({"events":[{k:v for k,v in row.items() if k!="data"} for row in rows]})
                return self._send_json({"error":"Unknown trace endpoint"},404)
            except (ValueError,FileNotFoundError) as exc:
                return self._send_json({"error":str(exc)},404)
        if path == "/api/events/poll":
            after = _safe_int((query.get("after") or [0])[0], 0, 0, 2**63 - 1)
            return self._send_json({"events": self.state.events.poll(after)})
        if path == "/api/state":
            return self._send_json(self.state.snapshot())
        if path == "/api/metrics":
            return self._send_json({"live": dict(self.state._live_payload), "performance": self.state.performance_status()})
        if path == "/api/server/status":
            return self._send_json(self.state.server_status())
        if path == "/api/logs":
            with self.state.lock:
                lines = list(self.state.logs)
            return self._send_json({"lines": lines, "persistent_log": str(LOG_FILE),
                                    "trainer_sessions_dir": str(BRAIN_LOG_DIR),
                                    "last_trainer": dict(getattr(self.state.brain,"last_trainer",{}) or {})})
        if path == "/api/logs/trainer":
            info=dict(getattr(self.state.brain,"last_trainer",{}) or {})
            raw_path=str(info.get("log_path") or "").strip()
            text=""
            if raw_path:
                try:
                    candidate=Path(raw_path).expanduser().resolve()
                    root=BRAIN_LOG_DIR.expanduser().resolve()
                    if candidate.is_file() and (candidate == root or root in candidate.parents):
                        data=candidate.read_bytes()
                        # API copy endpoint is intentionally bounded; the on-disk
                        # session log remains complete. Keep the most diagnostic tail.
                        if len(data) > 2*1024*1024: data=data[-2*1024*1024:]
                        text=data.decode("utf-8",errors="replace")
                except Exception as exc:
                    text=f"Could not read trainer session log: {exc}"
            return self._send_json({"info":info,"text":text})
        if path == "/api/diagnostics":
            return self._send_json(self.state.diagnostic_report())
        if path == "/api/model/analysis":
            return self._send_json(self.state.model_analysis())
        if path == "/api/brain/status":
            return self._send_json(self.state.brain_status())
        if path == "/api/brain/doctor":
            return self._send_json(self.state.brain_doctor())
        if path == "/api/brain/catalog/search":
            q=(query.get("q") or [""])[0]; limit=_safe_int((query.get("limit") or ["16"])[0],16,1,30)
            return self._send_json(self.state.search_trainable_models(q,limit))
        if path == "/api/brain/catalog/inspect":
            repo=(query.get("repo") or [""])[0]
            return self._send_json(self.state.inspect_trainable_model(repo))
        if path == "/api/brain/catalog/local":
            return self._send_json({"results": self.state.trainables.local_models(self.state.cfg.model_dirs)})
        if path == "/api/models/catalog/search":
            q=(query.get("q") or [""])[0]; limit=_safe_int((query.get("limit") or ["16"])[0],16,1,30)
            return self._send_json(self.state.search_gguf_models(q,limit))
        if path == "/api/models/catalog/files":
            repo=(query.get("repo") or [""])[0]
            return self._send_json(self.state.gguf_model_files(repo))
        if path == "/api/models/quick":
            return self._send_json({"results": self.state.quick_models()})
        if path == "/api/dialog/model":
            return self._send_json({"path": self.state.pick_file("model")})
        if path == "/api/dialog/runtime":
            return self._send_json({"path": self.state.pick_file("runtime")})
        if path == "/api/dialog/folder":
            return self._send_json({"path": self.state.pick_folder()})
        if path == "/api/agent/status":
            data = self.state.agent_status()
            data["remote_apps"] = self.state.remote_apps_status()
            return self._send_json(data)
        if path == "/api/agent/apps":
            return self._send_json(self.state.remote_apps_status())
        if path == "/api/calendar/now":
            return self._send_json({"ok": True, "now": self.state.agent.calendar.now()})
        if path == "/api/calendar/month":
            now = self.state.agent.calendar.now()
            year = _safe_int((query.get("year") or [now["jalali"][:4]])[0], int(now["jalali"][:4]), 1200, 1700)
            month = _safe_int((query.get("month") or [now["jalali"][5:7]])[0], int(now["jalali"][5:7]), 1, 12)
            return self._send_json({"ok": True, "month": self.state.agent.calendar.month(year, month), "now": now})
        if path == "/api/calendar/events":
            events = self.state.agent.calendar.list_events(
                str((query.get("start") or [""])[0]), str((query.get("end") or [""])[0]),
                str((query.get("q") or [""])[0]), _safe_int((query.get("limit") or ["100"])[0], 100, 1, 500),
                str((query.get("include_cancelled") or ["0"])[0]).lower() in {"1","true","yes"},
            )
            return self._send_json({"ok": True, "events": events})
        if path == "/api/workspace/files":
            q = str((query.get("q") or [""])[0]).strip()
            if q:
                return self._send_json({"ok": True, "matches": self.state.agent.workspace.search(q, _safe_int((query.get("limit") or ["80"])[0], 80, 1, 200))})
            return self._send_json({"ok": True, **self.state.agent.workspace.list(str((query.get("folder") or [""])[0]))})
        if path == "/api/cluster":
            return self._send_json(self.state.cluster.snapshot())
        if path == "/api/cluster/profiles":
            return self._send_json({"profiles": self.state.cluster.profiles()})
        if path == "/api/ping":
            return self._send_json({"ok": True, "version": APP_VERSION})
        return self._send_json({"error": "Not found"}, 404)

    def _route_api_post(self, path: str, body: dict):
        if path.startswith("/api/agent/telegram/"):
            if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
                return self._send_json({"error":"Use the local Agent settings to manage Telegram"},403)
            if path == "/api/agent/telegram/login":
                return self._send_json(self.state.agent.telegram.login(body))
            if path == "/api/agent/telegram/disconnect":
                return self._send_json(self.state.agent.telegram.disconnect(revoke=body.get("revoke") is True))
            if path == "/api/agent/telegram/install":
                return self._send_json(self.state.agent.install_telegram_skill_async())
            return self._send_json({"error":"Not found"},404)
        if path == "/api/logs/requests/settings":
            if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
                return self._send_json({"error":"Cross-site diagnostic changes are not allowed"},403)
            if not isinstance(body.get("enabled"),bool):
                return self._send_json({"error":"enabled must be a boolean"},400)
            self.state.request_traces.enabled=body["enabled"]
            self.state.cfg.diagnostic_full_traces=body["enabled"]
            self.state.cfg.save()
            return self._send_json(self.state.request_traces.status())
        if path == "/api/chat/cancel":
            event = self.state.chat_cancellations.get(str(body.get("request_id") or ""))
            if event: event.set()
            return self._send_json({"ok": True, "cancelled": bool(event)})
        if path == "/api/cluster/role":
            return self._send_json(self.state.cluster.set_role(str(body.get("role") or "standalone"), enabled=body.get("enabled") if "enabled" in body else None))
        if path == "/api/cluster/modes":
            return self._send_json(self.state.cluster.set_modes(body.get("selection_mode"), body.get("optimization_mode"), body.get("enabled") if "enabled" in body else None))
        if path == "/api/cluster/pair":
            return self._send_json({"ok": True, "node": self.state.cluster.pair_node(str(body.get("node_id") or ""), str(body.get("pairing_code") or ""))})
        if path == "/api/cluster/node":
            return self._send_json({"ok": True, "node": self.state.cluster.update_node(str(body.get("node_id") or ""), body)})
        if path == "/api/cluster/node/forget":
            self.state.cluster.forget_node(str(body.get("node_id") or "")); return self._send_json({"ok": True})
        if path == "/api/cluster/benchmark":
            return self._send_json({"ok": True, "benchmark": self.state.cluster.benchmark_node(str(body.get("node_id") or ""))})
        if path == "/api/cluster/profile/save":
            return self._send_json(self.state.cluster.save_profile(str(body.get("name") or "")))
        if path == "/api/cluster/profile/load":
            return self._send_json(self.state.cluster.load_profile(str(body.get("name") or "")))
        if path == "/api/cluster/worker-autostart":
            enabled = self.state.cluster.set_worker_autostart(bool(body.get("enabled")))
            return self._send_json({"ok": True, "enabled": enabled})
        if path == "/api/logs/clear":
            with self.state.lock:
                self.state.logs.clear()
            return self._send_json({"ok": True})
        if path == "/api/models/scan":
            threading.Thread(target=self.state.scan_models, daemon=True).start()
            return self._send_json({"ok": True})
        if path == "/api/model/select":
            return self._send_json(self.state.select_model(str(body.get("path") or "")))
        if path == "/api/models/add-folder":
            self.state.add_model_folder(str(body.get("path") or "")); return self._send_json({"ok": True})
        if path == "/api/models/catalog/download":
            return self._send_json(self.state.download_gguf_async(str(body.get("repo") or ""), str(body.get("filename") or "")))
        if path == "/api/models/quick/download":
            return self._send_json(self.state.download_quick_model_async(str(body.get("id") or "qwen2.5-1.5b-instruct")))
        if path == "/api/runtime/install":
            prefer = body.get("prefer_vulkan")
            self.state.install_runtime_async(None if prefer is None else bool(prefer)); return self._send_json({"ok": True, "job": self.state.job})
        if path == "/api/runtime/cancel":
            return self._send_json(self.state.cancel_runtime_install())
        if path == "/api/autotune/start":
            return self._send_json(self.state.autotune_model_async(str(body.get("model_path") or ""), bool(body.get("apply_and_start", True))))
        if path == "/api/autotune/cancel":
            return self._send_json(self.state.cancel_autotune())
        if path == "/api/autotune/clear":
            model = self.state.active_model
            self.state.autotuner.clear(model)
            self.state.events.publish("state", {"reason": "autotune-cleared"})
            return self._send_json({"ok": True})
        if path == "/api/runtime/build-cluster":
            self.state.build_cluster_runtime_async(); return self._send_json({"ok": True, "job": self.state.job})
        if path == "/api/runtime/select":
            self.state.set_runtime_path(str(body.get("path") or "")); return self._send_json(self.state.runtime_status())
        if path == "/api/server/start":
            return self._send_json(self.state.start_server(body))
        if path in ("/api/server/stop", "/api/model/unload"):
            return self._send_json(self.state.unload_model(reason="manual unload"))
        if path == "/api/settings":
            self.state.update_settings(body)
            cfg = self.state.cfg
            return self._send_json({
                "ok": True,
                "model_memory_mode": cfg.model_memory_mode,
                "accelerator_mode": cfg.accelerator_mode,
                "cpu_only_default": bool(cfg.cpu_only_default),
                "gpu_layer_percent": int(cfg.gpu_layer_percent),
                "speculative_mode": str(getattr(cfg, "speculative_mode", "auto")),
                "adaptive_context": bool(getattr(cfg, "adaptive_context", True)),
                "default_context_size": int(cfg.default_context_size),
                "generation_overrides_enabled": bool(cfg.generation_overrides_enabled),
                "generation_temperature": float(cfg.generation_temperature),
                "generation_top_p": float(cfg.generation_top_p),
                "generation_top_k": int(cfg.generation_top_k),
                "generation_min_p": float(cfg.generation_min_p),
                "generation_repeat_penalty": float(cfg.generation_repeat_penalty),
                "generation_max_tokens": int(cfg.generation_max_tokens),
                "agent": self.state.agent_status(),
            })
        if path == "/api/calendar":
            return self._send_json({"ok": True, "result": self.state.agent.calendar.tool(body, allow_write=True)})
        if path == "/api/workspace/files":
            self.state.agent.vision_available = bool(self.state.active_model and getattr(self.state.active_model, "vision_capable", False) and self.state.server_ready and self.state.vision_projector_loaded)
            return self._send_json({"ok": True, "result": self.state.agent.workspace.tool(body, allow_write=True, vision_available=bool(self.state.agent.vision_available))})
        if path == "/api/workspace/upload":
            name = str(body.get("name") or "file")
            result = self.state.agent.workspace.upload_data(
                name=name, folder=str(body.get("folder") or ""), data_url=str(body.get("data_url") or ""),
                text=str(body.get("text")) if body.get("text") is not None else None,
                description=str(body.get("description") or ""), tags=body.get("tags") if isinstance(body.get("tags"), list) else [],
            )
            return self._send_json({"ok": True, "file": result})
        if path == "/api/agent/browser/install":
            return self._send_json(self.state.agent.install_browser_skill_async())
        if path == "/api/agent/browser/close":
            result = self.state.agent.browser_close({}, self.state.agent_permissions())
            return self._send_json({"ok": True, **result, "agent": self.state.agent_status()})
        if path == "/api/agent/connector/add":
            return self._send_json({"ok": True, "connector": self.state.add_agent_connector(body), "agent": self.state.agent_status()})
        if path == "/api/agent/connector/remove":
            return self._send_json(self.state.remove_agent_connector(body))
        if path == "/api/agent/app/add":
            return self._send_json(self.state.add_remote_app(body))
        if path == "/api/agent/app/remove":
            return self._send_json(self.state.remove_remote_app(body))
        if path == "/api/agent/app/toggle":
            return self._send_json(self.state.toggle_remote_app(body))
        if path == "/api/agent/app/test":
            return self._send_json(self.state.test_remote_app(body))
        if path == "/api/agent/app/bridge-status":
            return self._send_json(self.state.remote_bridge_update_status(body))
        if path == "/api/agent/app/update-bridge":
            return self._send_json(self.state.update_remote_bridge(body))
        if path == "/api/agent/app/rollback-bridge":
            return self._send_json(self.state.rollback_remote_bridge(body))
        if path == "/api/chat/tokens":
            return self._send_json({"tokens": self.state.count_tokens(body.get("messages") or [])})
        if path == "/api/chat/profile":
            return self._send_json(self.state.chat_profile(body))
        if path == "/api/chat/template":
            return self._send_json({"prompt": self.state.template_preview(body.get("messages") or [])})
        if path == "/api/brain/settings":
            return self._send_json(self.state.update_brain(body))
        if path == "/api/brain/toggle":
            if "enabled" not in body:
                return self._send_json({"error":"enabled is required"}, 400)
            return self._send_json(self.state.toggle_brain(bool(body.get("enabled"))))
        if path == "/api/brain/cancel":
            return self._send_json(self.state.cancel_brain_operation())
        if path == "/api/brain/autosetup":
            return self._send_json(self.state.setup_brain_automatically())
        if path == "/api/brain/prepare":
            return self._send_json(self.state.prepare_brain_async(force=bool(body.get("force"))))
        if path == "/api/brain/resolve-base":
            return self._send_json(self.state.resolve_brain_base())
        if path == "/api/brain/preview":
            return self._send_json(self.state.preview_brain_lesson(body))
        if path == "/api/brain/learn":
            return self._send_json(self.state.learn_brain_async(body))
        if path == "/api/brain/bake":
            return self._send_json(self.state.bake_brain_async(body))
        if path == "/api/brain/catalog/download":
            return self._send_json(self.state.download_trainable_async(str(body.get("repo") or "")))
        if path == "/api/brain/catalog/use":
            return self._send_json(self.state.use_trainable_base(str(body.get("value") or "")))
        if path == "/api/app/exit":
            # This endpoint is an explicit user action: always unload regardless
            # of the background-mode preference.
            result = self.state.unload_model(reason="explicit Exit & unload")
            self._send_json(result)
            self.state.shutting_down = True
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/api/shutdown":
            result = self.state.unload_model(reason="application exit") if self.state.cfg.exit_unloads_model else {"ok": True, "unloaded": False}
            self._send_json(result)
            self.state.shutting_down = True
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        return self._send_json({"error": "Not found"}, 404)

    def _events_stream(self):
        after = self.headers.get("Last-Event-ID")
        q = self.state.events.subscribe(after=_safe_int(after, 0, 0, 2**63 - 1) if after else None)
        self.state.touch_client()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            initial = json.dumps({"type": "connected", "version": APP_VERSION}, ensure_ascii=False)
            self.wfile.write(("data: " + initial + "\n\n").encode("utf-8")); self.wfile.flush()
            while not self.state.shutting_down:
                try:
                    event = q.get(timeout=12.0)
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write((f"id: {event['revision']}\n" + "data: " + payload + "\n\n").encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush(); self.state.touch_client()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state.events.unsubscribe(q)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/workspace/download":
            self.state.touch_client()
            try:
                query = urllib.parse.parse_qs(parsed.query)
                file_id = str((query.get("id") or [""])[0])
                row, file_path = self.state.agent.workspace._resolve_id(file_id)
                return self._send_file(file_path, str(row.get("name") or file_path.name))
            except Exception as exc:
                return self._send_json({"error": str(exc)}, 404)
        if parsed.path == "/api/events":
            return self._events_stream()
        if parsed.path.startswith("/api/"):
            self.state.touch_client()
            try:
                return self._route_api_get(parsed.path, urllib.parse.parse_qs(parsed.query))
            except Exception as exc:
                self.state.log_exception("api:get", exc)
                return self._send_json({"error": str(exc)}, 500)
        return self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            self.state.touch_client()
        if parsed.path == "/api/chat/stream":
            return self._chat_stream()
        try:
            body = self._read_json()
            return self._route_api_post(parsed.path, body)
        except Exception as exc:
            self.state.log_exception("api:post", exc)
            return self._send_json({"error": str(exc)}, 500)

    def _chat_stream(self):
        headers_sent = False
        iterator = None
        cancel = threading.Event()
        active = getattr(self.state, "chat_cancellations", {})
        request_id = ""
        registered = False
        try:
            body = self._read_json()
            request_id = str(body.get("request_id") or "req_" + uuid.uuid4().hex)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
                raise ValueError("Invalid request_id")
            if request_id in active:
                raise RuntimeError("Request is already running")
            active[request_id] = cancel
            registered = True
            body["request_id"] = request_id
            body["_cancel"] = cancel
            iterator = self.state.chat_stream(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            headers_sent = True
            for sequence, event in enumerate(iterator, 1):
                if cancel.is_set():
                    raise RuntimeError("Generation cancelled")
                chunk = json.dumps(event if isinstance(event, dict) else {"type": "text", "delta": str(event)}, ensure_ascii=False)
                self.wfile.write((f"id: {sequence}\n" + "data: " + chunk + "\n\n").encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as exc:
            if _expected_client_disconnect(exc):
                return
            try:
                if not headers_sent:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.end_headers()
                msg = json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False)
                self.wfile.write(("data: " + msg + "\n\ndata: [DONE]\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            if not cancel.is_set():
                self.state.log_exception("chat", exc)
        finally:
            cancel.set()
            if iterator is not None and hasattr(iterator, "close"):
                iterator.close()
            if registered:
                active.pop(request_id, None)

    def _serve_static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        rel = urllib.parse.unquote(rel)
        target = (STATIC_ROOT / rel).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            return self.send_error(403)
        if not target.is_file():
            # SPA fallback for /chat, /models, etc.
            target = STATIC_ROOT / "index.html"
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js": ctype = "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/javascript")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self';")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._write(data)


def choose_port(host: str = "127.0.0.1", preferred: int = 8765) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 31)):
        with socket.socket() as s:
            try:
                s.bind((host, port)); return port
            except OSError:
                continue
    with socket.socket() as s:
        s.bind((host, 0)); return int(s.getsockname()[1])


def create_server(host: str = "127.0.0.1", port: int | None = None) -> tuple[LlamaForgeHTTPServer, LlamaForgeState, str]:
    state = LlamaForgeState()
    port = port or choose_port(host)
    server = LlamaForgeHTTPServer((host, port), Handler, state)
    return server, state, f"http://{host}:{port}/"
