from __future__ import annotations

import hashlib
import json
import uuid
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import platform
import urllib.request
import urllib.parse
import venv
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Any
from collections import deque

from .config import APP_DIR
from .system_metrics import memory_gb, process_memory_mb
from .learning_data import (deterministic_examples, validate_examples, curriculum,
                            lesson_fingerprint, question_key, fact_key, normalized)
from .redaction import redact as redact_text

BRAIN_ROOT = APP_DIR / "brain"
BRAIN_CONFIG = BRAIN_ROOT / "config.json"
BRAIN_ARCHIVE = BRAIN_ROOT / "training_archive.jsonl"
BRAIN_ENV = BRAIN_ROOT / "trainer-env"
BRAIN_TOOLCHAIN = BRAIN_ROOT / "llama-toolchain"
BRAIN_BATCH = BRAIN_ROOT / "current_batch.json"
BRAIN_READY_MARKER = BRAIN_ENV / ".llamaforge-ready"
BRAIN_LOG_DIR = BRAIN_ROOT / "logs"
BRAIN_LAST_TRAINER = BRAIN_ROOT / "last-trainer.json"


def _decode_trainer_exit_code(code: int, system_name: str | None = None) -> dict:
    """Decode subprocess termination codes into useful cross-platform diagnostics."""
    code=int(code)
    system=(system_name or platform.system()).lower()
    result={"code":code,"hex":"","name":"","native_crash":False,"signal":None}
    if code == 0:
        result.update(name="SUCCESS", hex="0x00000000")
        return result
    if system == "windows":
        unsigned=code & 0xFFFFFFFF
        names={
            0xC0000005:"STATUS_ACCESS_VIOLATION",
            0xC0000017:"STATUS_NO_MEMORY",
            0xC00000FD:"STATUS_STACK_OVERFLOW",
            0xC0000374:"STATUS_HEAP_CORRUPTION",
            0xC0000409:"STATUS_STACK_BUFFER_OVERRUN",
            0xC0000135:"STATUS_DLL_NOT_FOUND",
            0xC0000139:"STATUS_ENTRYPOINT_NOT_FOUND",
            0xC000001D:"STATUS_ILLEGAL_INSTRUCTION",
        }
        result["hex"]=f"0x{unsigned:08X}"
        result["name"]=names.get(unsigned,"WINDOWS_NTSTATUS" if unsigned >= 0x80000000 else "WINDOWS_EXIT")
        result["native_crash"]=bool(unsigned >= 0x80000000)
        return result
    if code < 0:
        result["signal"]=-code
        result["name"]=f"SIGNAL_{-code}"
        result["native_crash"]=True
    else:
        result["name"]="PROCESS_EXIT"
    return result


def _weight_load_progress(line: str) -> float | None:
    m=re.search(r"Loading weights:\s*(\d{1,3})%", str(line or ""))
    if not m:
        return None
    pct=max(0,min(100,int(m.group(1))))
    return .14 + .06 * (pct/100.0)


def _read_json_dict(path: Path) -> dict:
    try:
        obj=json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj,dict) else {}
    except Exception:
        return {}



class BrainCancelled(RuntimeError):
    """Raised when the user explicitly stops a Brain operation."""


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _python_in_venv(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _pip_in_venv(root: Path) -> list[str]:
    return [str(_python_in_venv(root)), "-m", "pip"]


def _safe_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class BrainConfig:
    enabled: bool = True
    zero_context: bool = True
    strict_learning: bool = True
    auto_synthesize: bool = True
    keep_training_archive: bool = True
    training_base: str = ""
    device: str = "auto"  # auto/cuda/cpu/xpu/mps
    rank: int = 8
    alpha: int = 16
    learning_rate: float = 0.00015
    micro_steps: int = 8
    replay_samples: int = 8
    max_length: int = 384
    adapter_scale: float = 1.0
    active_model_key: str = ""
    allow_remote_code: bool = False
    trainer_timeout: int = 3600


class PersonalBrain:
    """Weight-based continual learning control plane.

    The archive is a *training-only* replay source. It is never read by chat
    inference and never injected into model context. Normal zero-context chat is
    enforced separately in the web control plane.
    """

    def __init__(self, app_root: Path, log: Callable[[str], None] | None = None):
        self.app_root = Path(app_root)
        self._log_cb = log or (lambda _msg: None)
        BRAIN_ROOT.mkdir(parents=True, exist_ok=True)
        self.cfg = self._load()
        self.lock = threading.RLock()
        self.job = {"state": "idle", "stage": "", "message": "", "error": "", "progress": 0.0}
        self._pending_reload_key: str | None = None
        self.generation = 0
        # ``learned_packets`` means successfully committed learning runs, not
        # merely archived/attempted turns. Older builds counted failed attempts,
        # which made the Brain UI claim learning had happened when generation=0.
        self.learned_packets = 0
        self._archive_counts: dict[str, int] = {}
        self._replay_cache: tuple | None = None
        self.last_loss: float | None = None
        self.last_learned_at = 0.0
        self._trainer_probe_cache: tuple[float, bool] | None = None
        self._doctor_dependencies_cache: tuple[float, dict] | None = None
        self._doctor_qlora_cache: tuple[float, dict] | None = None
        BRAIN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.last_trainer: dict = _read_json_dict(BRAIN_LAST_TRAINER)


    def trace(self, message: str) -> None:
        try:
            self._log_cb(redact_text(str(message)))
        except Exception:
            pass

    def trace_exception(self, scope: str, exc: BaseException) -> None:
        self.trace(f"[brain:{scope}:error] {type(exc).__name__}: {exc}")
        for line in traceback.format_exc().rstrip().splitlines():
            self.trace(f"[brain:{scope}:trace] {line}")

    def _load(self) -> BrainConfig:
        if not BRAIN_CONFIG.exists():
            cfg = BrainConfig(); _atomic_json(BRAIN_CONFIG, asdict(cfg)); return cfg
        try:
            raw = json.loads(BRAIN_CONFIG.read_text(encoding="utf-8"))
            known = {k: v for k, v in raw.items() if k in BrainConfig.__dataclass_fields__}
            return BrainConfig(**known)
        except Exception:
            return BrainConfig()

    def save(self) -> None:
        _atomic_json(BRAIN_CONFIG, asdict(self.cfg))

    @staticmethod
    def model_key(model) -> str:
        if not model:
            return "none"
        identity = f"{getattr(model,'architecture','')}|{getattr(model,'name','')}|{getattr(model,'size_label','')}"
        return _safe_key(identity.lower())

    def profile_dir(self, model) -> Path:
        key = self.model_key(model)
        return BRAIN_ROOT / "profiles" / key

    def replay_path_for_key(self, key: str) -> Path:
        return BRAIN_ROOT / "profiles" / key / "replay.jsonl"

    def learned_ids_path_for_key(self, key: str) -> Path:
        return BRAIN_ROOT / "profiles" / key / "learned-packets.json"

    def transaction_path(self, model) -> Path:
        return self.profile_dir(model) / "learning-transaction.json"

    def profile_meta_path(self, model) -> Path:
        return self.profile_dir(model) / "brain-meta.json"

    def _profile_meta(self, model) -> dict:
        p=self.profile_meta_path(model)
        if not p.is_file(): return {"generation":0,"last_loss":None,"last_learned_at":0.0,"training_base":""}
        try:
            data=json.loads(p.read_text(encoding="utf-8")); return data if isinstance(data,dict) else {}
        except Exception:
            return {"generation":0,"last_loss":None,"last_learned_at":0.0,"training_base":""}

    def _save_profile_meta(self, model, data: dict) -> None:
        _atomic_json(self.profile_meta_path(model),data)

    def adapter_dir(self, model) -> Path:
        return self.profile_dir(model) / "peft-adapter"

    def adapter_gguf(self, model) -> Path:
        return self.profile_dir(model) / "personal-brain-lora.gguf"

    def candidate_adapter_dir(self, model) -> Path:
        return self.profile_dir(model) / "peft-adapter.candidate"

    def candidate_adapter_gguf(self, model) -> Path:
        return self.profile_dir(model) / "personal-brain-lora.candidate.gguf"

    def rollback_adapter_dir(self, model) -> Path:
        return self.profile_dir(model) / "peft-adapter.rollback"

    def rollback_adapter_gguf(self, model) -> Path:
        return self.profile_dir(model) / "personal-brain-lora.rollback.gguf"

    def merged_gguf(self, model) -> Path:
        return self.profile_dir(model) / f"{_safe_key(getattr(model,'name','model'))}-brain-merged.gguf"

    def _count_archive(self) -> int:
        if not BRAIN_ARCHIVE.exists(): return 0
        try:
            with BRAIN_ARCHIVE.open("r", encoding="utf-8", errors="ignore") as f:
                return sum(1 for x in f if x.strip())
        except Exception:
            return 0

    def _count_replay(self, key: str) -> int:
        if key in self._archive_counts:
            return self._archive_counts[key]
        n=len(self._learned_ids(key))
        self._archive_counts[key]=n
        return n

    def _learned_ids(self, key: str) -> set[str]:
        """Return packet ids that were fully trained, converted and reloaded.

        0.14.3 and earlier had no success ledger. For an old profile, generation
        is the only durable success count we can trust, so migrate at most the
        newest ``generation`` archive packets. A generation-0 profile therefore
        correctly migrates with zero learned packets even if it has failed
        attempts in replay.jsonl.
        """
        path=self.learned_ids_path_for_key(key)
        if path.is_file():
            try:
                data=json.loads(path.read_text(encoding="utf-8"))
                rows=data.get("ids") if isinstance(data,dict) else data
                return {str(x) for x in (rows or []) if str(x).strip()}
            except Exception:
                return set()
        meta_path=BRAIN_ROOT / "profiles" / key / "brain-meta.json"
        generation=0
        try:
            generation=int((json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}).get("generation") or 0)
        except Exception:
            generation=0
        rows=self._archive_rows(key)
        ids=[str(r.get("id") or "") for r in rows if str(r.get("id") or "")]
        learned=set(ids[-generation:]) if generation>0 else set()
        path.parent.mkdir(parents=True,exist_ok=True)
        _atomic_json(path,{"ids":sorted(learned),"migrated_from_generation":generation,"updated_at":time.time()})
        return learned

    def _set_learned_ids(self, key: str, ids: set[str]) -> None:
        path=self.learned_ids_path_for_key(key); path.parent.mkdir(parents=True,exist_ok=True)
        _atomic_json(path,{"ids":sorted(ids),"updated_at":time.time()})
        self._archive_counts[key]=len(ids)

    def _mark_packet_learned(self, key: str, packet_id: str) -> None:
        if not packet_id: return
        ids=self._learned_ids(key); ids.add(packet_id); self._set_learned_ids(key,ids)

    def trainer_python(self) -> Path:
        return _python_in_venv(BRAIN_ENV)

    def trainer_ready(self, refresh: bool = False) -> bool:
        # Normal UI snapshots must never import PyTorch; that can take seconds on
        # Windows. Setup writes this marker only after an explicit verification.
        if not refresh:
            return self.trainer_python().is_file() and BRAIN_READY_MARKER.is_file()
        py = self.trainer_python()
        if not py.is_file(): return False
        try:
            proc = subprocess.run(
                [str(py), "-c", "import torch,transformers,peft,safetensors; print('ok')"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45,
            )
            ok=proc.returncode == 0 and "ok" in proc.stdout
            if ok:
                BRAIN_READY_MARKER.parent.mkdir(parents=True,exist_ok=True); BRAIN_READY_MARKER.write_text("ready",encoding="utf-8")
            return ok
        except Exception:
            return False

    def toolchain_ready(self) -> bool:
        return (BRAIN_TOOLCHAIN / "convert_lora_to_gguf.py").is_file()

    def training_base_for(self, model=None) -> str:
        if model is not None:
            meta = self._profile_meta(model)
            locked = str(meta.get("training_base") or "").strip()
            if locked:
                return locked
            key = self.model_key(model)
            if self.cfg.active_model_key and self.cfg.active_model_key != key:
                return ""
        return str(self.cfg.training_base or "").strip()

    def training_base_ready(self, model=None) -> bool:
        val = self.training_base_for(model)
        if not val: return False
        if model is not None:
            # 0.18.1 migration: old profiles did not record which logical chat
            # model had verified this path. Force one cheap auto-setup pass so a
            # stale dependency (for example a raw Unsloth bnb base) cannot be
            # mistaken for the selected fine-tune's training source.
            meta=self._profile_meta(model)
            if str(meta.get('training_base_model_key') or '') != self.model_key(model):
                return False
        p = Path(os.path.expanduser(val))
        if not p.exists():
            # 0.15.1 intentionally requires a local, verified training source so
            # Transformers cannot silently download multi-GB weights while the
            # Brain UI appears stuck. Auto setup performs the download first.
            return False
        adapter_ready=(p / "adapter_config.json").is_file() and ((p / "adapter_model.safetensors").is_file() or (p / "adapter_model.bin").is_file())
        full_ready=(p / "config.json").is_file() and bool([x for x in list(p.rglob("*.safetensors")) + list(p.rglob("*.bin")) if "adapter_model" not in x.name.lower()])
        if full_ready:
            return True
        if adapter_ready:
            bundle=p / ".llamaforge-bundle.json"
            try:
                data=json.loads(bundle.read_text(encoding="utf-8"))
                under=Path(str(data.get("underlying_local_dir") or ""))
                under_ready=(under / "config.json").is_file() and bool([x for x in list(under.rglob("*.safetensors"))+list(under.rglob("*.bin")) if "adapter_model" not in x.name.lower()])
                return bool(under_ready)
            except Exception:
                return False
        return False

    def activate_model(self, model) -> None:
        """Switch Brain to the profile that belongs to *model*.

        This prevents a trainable base selected for model A from silently being
        reused for model B. Existing learned profiles remember their own base.
        """
        # A process crash during the small reload/verification window must never
        # leave an unconfirmed adapter active on next launch.
        key = self.model_key(model)
        if self._pending_reload_key != key:
            self.rollback_unconfirmed_learning(model, quiet=True)
        meta = self._profile_meta(model) if model is not None else {}
        locked = str(meta.get("training_base") or "").strip()
        changed = self.cfg.active_model_key != key
        self.cfg.active_model_key = key
        if locked:
            self.cfg.training_base = locked
        elif changed:
            self.cfg.training_base = ""
        self.save()

    def set_training_base(self, model, value: str) -> None:
        value = str(value or "").strip()
        self.cfg.training_base = value
        self.cfg.active_model_key = self.model_key(model)
        if model is not None:
            meta = self._profile_meta(model)
            meta["training_base"] = value
            meta["training_base_model_key"] = self.model_key(model) if value else ""
            self._save_profile_meta(model, meta)
        self.save()

    def status(self, model=None) -> dict:
        adapter = self.adapter_gguf(model) if model else None
        peft_dir = self.adapter_dir(model) if model else None
        meta = self._profile_meta(model) if model else {"generation":0,"last_loss":None,"last_learned_at":0.0}
        with self.lock:
            job = dict(self.job)
        return {
            "enabled": bool(self.cfg.enabled),
            "zero_context": bool(self.cfg.zero_context),
            "strict_learning": bool(self.cfg.strict_learning),
            "auto_synthesize": bool(self.cfg.auto_synthesize),
            "keep_training_archive": bool(self.cfg.keep_training_archive),
            "training_base": self.training_base_for(model),
            "device": self.cfg.device,
            "allow_remote_code": bool(self.cfg.allow_remote_code),
            "rank": self.cfg.rank,
            "alpha": self.cfg.alpha,
            "learning_rate": self.cfg.learning_rate,
            "micro_steps": self.cfg.micro_steps,
            "replay_samples": self.cfg.replay_samples,
            "max_length": self.cfg.max_length,
            "trainer_ready": self.trainer_ready(),
            "toolchain_ready": self.toolchain_ready(),
            "training_base_ready": self.training_base_ready(model),
            "adapter_ready": bool(adapter and adapter.is_file()),
            "adapter_path": str(adapter) if adapter else "",
            "peft_adapter_path": str(peft_dir) if peft_dir else "",
            "model_key": self.model_key(model) if model else "",
            "generation": int(meta.get("generation") or 0),
            "learned_packets": int(meta.get("generation") or 0) if model else self.learned_packets,
            "last_loss": meta.get("last_loss"),
            "validation": meta.get("validation", {}),
            "learning_contract": "user-supervision-then-validated-candidate",
            "trainer_timeout": self.cfg.trainer_timeout,
            "last_learned_at": float(meta.get("last_learned_at") or 0.0),
            "job": job,
            "setup_ready": bool(self.trainer_ready() and self.training_base_ready(model) and self.toolchain_ready()),
            "needs_setup": not bool(self.trainer_ready() and self.training_base_ready(model) and self.toolchain_ready()),
            "trainer_diagnostics": dict(self.last_trainer),
            "inference_contract": "latest-user-message-only" if self.cfg.enabled and self.cfg.zero_context else "normal-chat-history",
        }

    def update(self, payload: dict, model=None) -> None:
        bools = ("enabled", "zero_context", "strict_learning", "auto_synthesize", "keep_training_archive", "allow_remote_code")
        for k in bools:
            if k in payload: setattr(self.cfg, k, bool(payload[k]))
        if "training_base" in payload:
            self.set_training_base(model, str(payload.get("training_base") or "").strip())
        if "device" in payload:
            d = str(payload.get("device") or "auto").lower()
            self.cfg.device = d if d in ("auto","cpu","cuda","xpu","mps") else "auto"
        for k, lo, hi in (("rank",2,128),("alpha",2,256),("micro_steps",1,128),("replay_samples",0,128),("max_length",64,2048),("trainer_timeout",30,14400)):
            if k in payload:
                try: setattr(self.cfg, k, max(lo, min(hi, int(payload[k]))))
                except Exception: pass
        if "learning_rate" in payload:
            try: self.cfg.learning_rate = max(1e-6, min(0.01, float(payload["learning_rate"])))
            except Exception: pass
        if model is not None:
            self.cfg.active_model_key = self.model_key(model)
        self.save()


    def infer_quant_repo(self, model) -> str:
        """Best-effort Hugging Face repo inference from common local caches."""
        if not model:
            return ""
        path = Path(str(getattr(model, "path", "") or ""))
        # Models downloaded by LlamaForge are stored in ordinary local folders,
        # not necessarily in the Hugging Face cache layout.  Keep the source
        # repository beside the GGUF so Personal Brain can still resolve the
        # exact trainable lineage after a restart or after the folder is moved.
        for parent in (path.parent, *path.parents):
            marker = parent / ".llamaforge-source.json"
            if not marker.is_file():
                continue
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                # Curated Chat + Learning bundles write the exact trainable
                # source beside the GGUF. Prefer it over the quant repository so
                # lineage validation is deterministic and never asks the user to
                # choose a second model.
                repo = str(data.get("training_repo") or data.get("logical_model_id") or data.get("repo_id") or data.get("source_repo") or "").strip()
                if "/" in repo:
                    return repo
            except Exception:
                pass
        parts = list(path.parts)
        low = [x.lower() for x in parts]
        try:
            i = low.index("models", low.index(".lmstudio") + 1)
            if i + 2 < len(parts):
                return f"{parts[i+1]}/{parts[i+2]}"
        except Exception:
            pass
        # Hugging Face cache: .../hub/models--org--repo/snapshots/...
        for part in parts:
            if part.startswith("models--"):
                bits = part[len("models--"):].split("--")
                if len(bits) >= 2:
                    return bits[0] + "/" + "--".join(bits[1:])
        return ""

    def resolve_training_base(self, model) -> dict:
        """Resolve the exact trainable HF source for a local GGUF.

        Curated LlamaForge bundles carry an explicit ``training_repo`` and must
        stop there: following that repository's own model-card base would move
        learning to a different upstream checkpoint. Generic GGUFs still use the
        conservative model-card lineage walk below.
        """
        if model:
            path = Path(str(getattr(model, "path", "") or ""))
            for parent in (path.parent, *path.parents):
                marker = parent / ".llamaforge-source.json"
                if not marker.is_file():
                    continue
                try:
                    data = json.loads(marker.read_text(encoding="utf-8"))
                    exact = str(data.get("training_repo") or "").strip()
                    if "/" in exact:
                        source = str(data.get("repo_id") or "").strip()
                        chain = [x for x in (source, exact) if x]
                        return {"ok": True, "repo": exact, "source_repo": source, "chain": chain,
                                "explicit_bundle": True, "message": f"Exact bundled trainable source: {exact}"}
                except Exception:
                    pass
        repo = self.infer_quant_repo(model)
        if not repo:
            return {"ok": False, "repo": "", "message": "Could not infer a Hugging Face repository from this local path."}
        chain = [repo]
        current = repo
        try:
            for _ in range(3):
                url = "https://huggingface.co/api/models/" + urllib.parse.quote(current, safe="/")
                req = urllib.request.Request(url, headers={"User-Agent": "LlamaForge-Brain/0.34.2-diagnostics", "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
                card = data.get("cardData") or {}
                base = card.get("base_model") or data.get("baseModels") or data.get("base_model")
                if isinstance(base, list):
                    base = next((str(x) for x in base if isinstance(x, str) and x.strip()), "")
                base = str(base or "").strip()
                if not base:
                    # A non-GGUF Transformers repo is itself a valid training source.
                    if "gguf" not in current.lower():
                        return {"ok": True, "repo": current, "source_repo": repo, "chain": chain, "message": f"Suggested trainable source: {current}"}
                    break
                chain.append(base)
                # Stop at the first non-GGUF source. For an imatrix GGUF this is
                # normally the exact merged/fine-tuned Transformers checkpoint.
                if "gguf" not in base.lower():
                    return {"ok": True, "repo": base, "source_repo": repo, "chain": chain, "message": f"Suggested trainable source: {base}"}
                current = base
        except Exception as exc:
            return {"ok": False, "repo": "", "source_repo": repo, "chain": chain, "message": f"Could not resolve model card: {exc}"}
        return {"ok": False, "repo": "", "source_repo": repo, "chain": chain, "message": "The model card did not expose a trainable source."}

    def _run_logged(self, command: list[str], scope: str, timeout: int = 3600, cancel: threading.Event | None = None, env: dict | None = None) -> None:
        if cancel is not None and cancel.is_set():
            raise BrainCancelled(f"{scope} cancelled by user")
        self.trace(f"[brain:{scope}:cmd] "+" ".join(command))
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",bufsize=1,env=env)
        tail=deque(maxlen=80)
        assert proc.stdout
        watcher_stop=threading.Event()
        timed_out=threading.Event()
        deadline=time.monotonic()+timeout
        def cancel_watch():
            while not watcher_stop.wait(.05):
                if (time.monotonic() >= deadline or (cancel is not None and cancel.is_set())) and proc.poll() is None:
                    if time.monotonic() >= deadline: timed_out.set()
                    self.trace(f"[brain:{scope}] cancellation requested; terminating pid={proc.pid}")
                    try: proc.terminate()
                    except Exception: pass
                    if proc.poll() is None:
                        time.sleep(.5)
                    if proc.poll() is None:
                        try: proc.kill()
                        except Exception: pass
                    return
        threading.Thread(target=cancel_watch,name=f"brain-{scope}-cancel",daemon=True).start()
        try:
            for line in proc.stdout:
                line=line.rstrip()
                if line:
                    tail.append(line); self.trace(f"[brain:{scope}:out] {line}")
            code=proc.wait()
        finally:
            watcher_stop.set()
            if proc.poll() is None:
                proc.kill(); proc.wait()
            proc.stdout.close()
        self.trace(f"[brain:{scope}] exited code={code}")
        if cancel is not None and cancel.is_set():
            raise BrainCancelled(f"{scope} cancelled by user")
        if timed_out.is_set():
            raise RuntimeError(f"{scope} timed out after {timeout}s")
        if code!=0:
            raise RuntimeError(f"{scope} failed with code {code}: "+" | ".join(tail)[-4000:])

    def prepare_environment(self, progress: Callable[[str, float], None] | None = None, force: bool = False, cancel: threading.Event | None = None) -> None:
        """Create/repair the isolated trainer environment on explicit request.

        Normal repeated clicks are idempotent: once the Python environment and
        llama.cpp converter are verified, no package reinstall is performed.
        """
        def emit(msg, p):
            if cancel is not None and cancel.is_set(): raise BrainCancelled("Brain setup cancelled by user")
            if progress: progress(msg, p)
        self.trace(f"[brain:setup] prepare_environment force={bool(force)} python={self.trainer_python()}")
        if force:
            self._doctor_dependencies_cache=None
            self._doctor_qlora_cache=None
        if not force and self.trainer_ready() and self.toolchain_ready():
            emit("Brain Trainer is already ready; nothing to reinstall.", 1.0)
            return
        BRAIN_ROOT.mkdir(parents=True, exist_ok=True)
        if not self.trainer_python().exists():
            emit("Creating isolated Brain Trainer environment…", 0.05)
            venv.EnvBuilder(with_pip=True, clear=False).create(BRAIN_ENV)
        py = str(self.trainer_python())
        # Existing environments are verified before any network/package work.
        # This prevents a normal "Prepare" click from reinstalling gigabytes.
        if not force and self.trainer_ready(refresh=True):
            emit("Trainer dependencies verified.", 0.78)
            if not self.toolchain_ready():
                emit("Preparing llama.cpp LoRA conversion toolchain…", 0.86)
                self.prepare_toolchain()
            emit("Brain Trainer is ready.", 1.0)
            return
        emit("Updating pip in Brain Trainer environment…", 0.12)
        cmd=[py, "-m", "pip", "install", "--index-url", "https://pypi.org/simple", "--upgrade", "pip", "wheel"]
        self._run_logged(cmd,"setup-pip",timeout=1800,cancel=cancel)
        # Explicit pypi.org bypasses machine-wide mirrors that previously blocked LlamaForge startup.
        packages = ["torch", "transformers>=4.55", "peft>=0.17", "accelerate>=1.8", "safetensors", "sentencepiece", "bitsandbytes>=0.48"]
        emit("Installing PyTorch + PEFT/QLoRA dependencies…", 0.25)
        cmd=[py, "-m", "pip", "install", "--index-url", "https://pypi.org/simple", *packages]
        self._run_logged(cmd,"setup-packages",timeout=7200,cancel=cancel)
        emit("Preparing llama.cpp LoRA conversion toolchain…", 0.82)
        self.prepare_toolchain()
        if not self.trainer_ready(refresh=True):
            raise RuntimeError("Brain Trainer dependencies installed, but verification failed")
        self._doctor_dependencies_cache=None
        self._doctor_qlora_cache=None
        emit("Brain Trainer is ready.", 1.0)

    def prepare_toolchain(self) -> None:
        if self.toolchain_ready(): return
        self.trace("[brain:toolchain] downloading llama.cpp conversion scripts")
        BRAIN_ROOT.mkdir(parents=True, exist_ok=True)
        archive = BRAIN_ROOT / "llama-toolchain.zip"
        url = "https://github.com/ggml-org/llama.cpp/archive/refs/heads/master.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "LlamaForge-Brain/0.34.2-diagnostics"})
        with urllib.request.urlopen(req, timeout=180) as r, archive.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp = BRAIN_ROOT / "toolchain-extract"
        shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z: z.extractall(tmp)
        roots = [x for x in tmp.iterdir() if x.is_dir()]
        if not roots: raise RuntimeError("llama.cpp toolchain archive was empty")
        shutil.rmtree(BRAIN_TOOLCHAIN, ignore_errors=True)
        shutil.move(str(roots[0]), str(BRAIN_TOOLCHAIN))
        shutil.rmtree(tmp, ignore_errors=True)
        try: archive.unlink()
        except Exception: pass
        if not self.toolchain_ready():
            raise RuntimeError("LoRA conversion script was not found in downloaded llama.cpp toolchain")

    def _archive_rows(self, model_key: str | None = None, limit: int | None = None) -> list[dict]:
        source = self.replay_path_for_key(model_key) if model_key else BRAIN_ARCHIVE
        if not source.exists(): return []
        rows = deque(maxlen=limit) if limit else []
        try:
            with source.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if not line.strip(): continue
                    try: row=json.loads(line)
                    except Exception: continue
                    if not isinstance(row,dict): continue
                    if model_key and row.get("model_key") != model_key: continue
                    if limit: rows.append(row)
                    else: rows.append(row)
        except Exception:
            return []
        return list(rows)

    def append_packet(self, model, user_text: str, assistant_text: str, examples: list[dict]) -> dict:
        key = self.model_key(model)
        clean_examples=validate_examples(examples)
        # Deliberately do NOT train the model on its own just-produced answer.
        # A wrong answer must never become the supervised target merely because
        # the model said it.  Only examples derived from explicit user-provided
        # facts/corrections are allowed into the learning packet.
        packet={
            "id": uuid.uuid4().hex, "at": time.time(), "fingerprint": lesson_fingerprint(clean_examples),
            "model_key": key, "model_name": getattr(model,"name",""), "examples": clean_examples,
            "status": "pending",
        }
        # The archive is training-only. When disabled, keep this packet only in
        # memory for the current micro-training run and do not persist it.
        if self.cfg.keep_training_archive and clean_examples:
            BRAIN_ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
            rendered=json.dumps(packet,ensure_ascii=False)+"\n"
            with BRAIN_ARCHIVE.open("a",encoding="utf-8") as f:
                f.write(rendered)
            replay=self.replay_path_for_key(key); replay.parent.mkdir(parents=True,exist_ok=True)
            with replay.open("a",encoding="utf-8") as f:
                f.write(rendered)
        return packet

    deterministic_examples = staticmethod(deterministic_examples)

    def _confirmed_packets(self, model) -> list[dict]:
        key = self.model_key(model)
        replay = self.replay_path_for_key(key)
        ledger = self.learned_ids_path_for_key(key)
        def stamp(path):
            try:
                st = path.stat()
                return (st.st_mtime_ns, st.st_size)
            except OSError:
                return (0, 0)
        signature = (key, stamp(replay), stamp(ledger))
        if self._replay_cache and self._replay_cache[0] == signature:
            return self._replay_cache[1]
        learned = self._learned_ids(key)
        recent = deque(maxlen=256)
        if replay.is_file():
            with replay.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try: packet = json.loads(line)
                    except ValueError: continue
                    if isinstance(packet, dict) and packet.get("model_key") == key and packet.get("id") in learned:
                        recent.append({"id": packet["id"], "examples": validate_examples(packet.get("examples", []))})
        rows = list(reversed(recent))
        self._replay_cache = ((key, stamp(replay), stamp(ledger)), rows)
        return rows

    def already_learned(self, model, examples: list[dict]) -> bool:
        """Skip duplicate current knowledge, but allow reverting a later correction."""
        rows = validate_examples(examples)
        if not rows or not self.cfg.keep_training_archive:
            return False
        latest = {}
        for packet in self._confirmed_packets(model):
            for row in validate_examples(packet.get("examples", [])):
                latest.setdefault(fact_key(row), normalized(row["assistant"]))
        return all(latest.get(fact_key(r)) == normalized(r["assistant"]) for r in rows)

    def build_training_batch(self, model, new_packet: dict) -> list[dict]:
        packets = self._confirmed_packets(model) if self.cfg.replay_samples > 0 else []
        return curriculum(new_packet.get("examples", []), packets, self.cfg.replay_samples)

    def _prepare_learning_candidate(self, model) -> Path:
        """Create an isolated candidate adapter for one learning transaction."""
        candidate=self.candidate_adapter_dir(model)
        shutil.rmtree(candidate,ignore_errors=True)
        try:self.candidate_adapter_gguf(model).unlink(missing_ok=True)
        except Exception:pass
        current=self.adapter_dir(model)
        if current.is_dir():
            shutil.copytree(current,candidate)
        return candidate

    def _commit_learning_candidate(self, model, packet_id: str, previous_meta: dict, next_meta: dict | None = None) -> None:
        """Promote candidate PEFT+GGUF atomically enough to allow rollback.

        The transaction remains pending until llama.cpp has successfully loaded
        the new GGUF adapter. Server code then calls ``confirm_learning``. If the
        process dies first, ``activate_model`` rolls it back on next startup.
        """
        profile=self.profile_dir(model); profile.mkdir(parents=True,exist_ok=True)
        current_dir=self.adapter_dir(model); current_gguf=self.adapter_gguf(model)
        cand_dir=self.candidate_adapter_dir(model); cand_gguf=self.candidate_adapter_gguf(model)
        if not (cand_dir / "adapter_config.json").is_file() or not (cand_dir / "adapter_model.safetensors").is_file():
            raise RuntimeError("Candidate Personal Brain adapter failed verification before commit")
        if not cand_gguf.is_file():
            raise RuntimeError("Candidate GGUF adapter is missing before commit")
        rb_dir=self.rollback_adapter_dir(model); rb_gguf=self.rollback_adapter_gguf(model)
        shutil.rmtree(rb_dir,ignore_errors=True)
        try:rb_gguf.unlink(missing_ok=True)
        except Exception:pass
        had_dir=current_dir.is_dir(); had_gguf=current_gguf.is_file()
        tx={
            "packet_id":str(packet_id or ""),"model_key":self.model_key(model),
            "previous_meta":previous_meta,"next_meta":next_meta,"had_adapter":had_dir,"had_gguf":had_gguf,
            "state":"promoting","started_at":time.time(),
        }
        # Write the rollback intent before touching the confirmed adapter pair.
        # This closes the crash window between moving the old files aside and
        # recording enough information to restore them on the next launch.
        _atomic_json(self.transaction_path(model),tx)
        if had_dir: current_dir.replace(rb_dir)
        if had_gguf: current_gguf.replace(rb_gguf)
        try:
            cand_dir.replace(current_dir)
            cand_gguf.replace(current_gguf)
        except Exception:
            # Restore the previous pair if promotion itself fails halfway.
            shutil.rmtree(current_dir,ignore_errors=True)
            try:current_gguf.unlink(missing_ok=True)
            except Exception:pass
            if rb_dir.is_dir(): rb_dir.replace(current_dir)
            if rb_gguf.is_file(): rb_gguf.replace(current_gguf)
            try:self.transaction_path(model).unlink(missing_ok=True)
            except Exception:pass
            raise
        tx.update(state="pending-reload",committed_at=time.time())
        _atomic_json(self.transaction_path(model),tx)
        self._pending_reload_key = self.model_key(model)

    def confirm_learning(self, model) -> None:
        """Finalize a transaction only after llama.cpp reload is healthy."""
        txp=self.transaction_path(model)
        if not txp.is_file(): return
        try:tx=json.loads(txp.read_text(encoding="utf-8"))
        except Exception:tx={}
        # The durable commit point precedes cleanup. A crash afterward finishes
        # confirmation on startup; it must never roll a verified pair back.
        tx["state"] = "confirmed"
        _atomic_json(txp, tx)
        if isinstance(tx.get("next_meta"), dict):
            self._save_profile_meta(model, tx["next_meta"])
        packet_id=str(tx.get("packet_id") or "")
        if packet_id and self.cfg.keep_training_archive:
            self._mark_packet_learned(self.model_key(model),packet_id)
        shutil.rmtree(self.rollback_adapter_dir(model),ignore_errors=True)
        try:self.rollback_adapter_gguf(model).unlink(missing_ok=True)
        except Exception:pass
        try:txp.unlink(missing_ok=True)
        except Exception:pass
        self._pending_reload_key = None

    def rollback_unconfirmed_learning(self, model, quiet: bool=False) -> bool:
        """Restore the last confirmed adapter if a reload was never confirmed."""
        txp=self.transaction_path(model)
        if not txp.is_file(): return False
        try:tx=json.loads(txp.read_text(encoding="utf-8"))
        except Exception:tx={}
        if tx.get("state") == "confirmed":
            self.confirm_learning(model)
            return False
        self._pending_reload_key = None
        current_dir=self.adapter_dir(model); current_gguf=self.adapter_gguf(model)
        rb_dir=self.rollback_adapter_dir(model); rb_gguf=self.rollback_adapter_gguf(model)
        had_adapter=bool(tx.get("had_adapter")); had_gguf=bool(tx.get("had_gguf"))
        # Recovery is intentionally inferred from the backup paths as well as the
        # transaction state. The process can die at any instruction after the
        # intent file is written. If a previous confirmed object has NOT yet been
        # moved to its rollback path, the current object is still the confirmed
        # one and must be preserved. Older code deleted it unconditionally, which
        # could lose a healthy adapter during the tiny pre-promotion crash window.
        if had_adapter:
            if rb_dir.is_dir():
                shutil.rmtree(current_dir,ignore_errors=True)
                rb_dir.replace(current_dir)
            # no rollback dir => promotion of this component never started; keep current
        else:
            # There was no confirmed adapter before this transaction, so any
            # current one is an unconfirmed candidate and must be discarded.
            shutil.rmtree(current_dir,ignore_errors=True)
            shutil.rmtree(rb_dir,ignore_errors=True)
        if had_gguf:
            if rb_gguf.is_file():
                try:current_gguf.unlink(missing_ok=True)
                except Exception:pass
                rb_gguf.replace(current_gguf)
            # no rollback file => the old confirmed GGUF is still current; keep it
        else:
            try:current_gguf.unlink(missing_ok=True)
            except Exception:pass
            try:rb_gguf.unlink(missing_ok=True)
            except Exception:pass
        prev=tx.get("previous_meta")
        if isinstance(prev,dict): self._save_profile_meta(model,prev)
        shutil.rmtree(self.candidate_adapter_dir(model),ignore_errors=True)
        try:self.candidate_adapter_gguf(model).unlink(missing_ok=True)
        except Exception:pass
        try:txp.unlink(missing_ok=True)
        except Exception:pass
        if not quiet:self.trace("[brain:transaction] rolled back unconfirmed learning transaction")
        return True

    def _run_trainer(self, model, batch: list[dict], progress: Callable[[str,float],None] | None=None, adapter_path: Path | None=None, cancel: threading.Event | None=None) -> dict:
        if not self.trainer_ready(): raise RuntimeError("Brain Trainer is not prepared. Open Brain → Prepare Trainer first.")
        if not self.training_base_ready(model): raise RuntimeError("Link the exact trainable base model (HF folder or repo) before learning.")
        profile=self.profile_dir(model); profile.mkdir(parents=True,exist_ok=True)
        batch_path = profile / "current_batch.json"
        _atomic_json(batch_path, batch)
        worker=self.app_root / "llamaforge" / "trainer_worker.py"
        base=self.training_base_for(model)
        adapter_path=Path(adapter_path or self.adapter_dir(model))
        command=[str(self.trainer_python()),str(worker),"--base",base,"--data",str(batch_path),"--adapter",str(adapter_path),"--device",self.cfg.device,"--rank",str(self.cfg.rank),"--alpha",str(self.cfg.alpha),"--lr",str(self.cfg.learning_rate),"--steps",str(self.cfg.micro_steps),"--max-length",str(self.cfg.max_length),"--seed",str(int(self._profile_meta(model).get("generation") or 0))]
        bp=Path(os.path.expanduser(base))
        bundle=bp / ".llamaforge-bundle.json"
        if bundle.is_file():
            try:
                bdata=json.loads(bundle.read_text(encoding="utf-8"))
                underlying=str(bdata.get("underlying_local_dir") or "").strip()
                if underlying and Path(underlying).is_dir():
                    command += ["--underlying-base", underlying]
                    self.trace(f"[brain:trainer] using local underlying base={underlying}")
            except Exception as exc:
                self.trace(f"[brain:trainer] training bundle manifest warning: {exc}")
        if self.cfg.allow_remote_code: command.append("--trust-remote-code")

        BRAIN_LOG_DIR.mkdir(parents=True,exist_ok=True)
        stamp=time.strftime("%Y%m%d-%H%M%S")
        session_id=f"{stamp}-{self.model_key(model)}"
        session_log=BRAIN_LOG_DIR / f"trainer-{session_id}.log"
        # Keep logs useful without letting long-running installations grow forever.
        try:
            old=sorted(BRAIN_LOG_DIR.glob("trainer-*.log"),key=lambda x:x.stat().st_mtime,reverse=True)
            for stale in old[20:]: stale.unlink(missing_ok=True)
        except Exception: pass

        start_at=time.time(); deadline=time.monotonic()+self.cfg.trainer_timeout; timed_out=threading.Event(); tail=deque(maxlen=50); write_lock=threading.Lock()
        last={}; last_phase="boot"; peak_rss_mb=0.0; min_free_gb=None
        def raw(text: str) -> None:
            line=f"{time.strftime('%Y-%m-%d %H:%M:%S')} {redact_text(str(text))}"
            tail.append(line)
            try:
                with write_lock:
                    with session_log.open("a",encoding="utf-8",errors="replace") as fh:
                        fh.write(line+"\n")
            except Exception: pass

        self.trace(f"[brain:trainer] starting base={base} device={self.cfg.device} examples={len(batch)} steps={self.cfg.micro_steps} rank={self.cfg.rank} max_length={self.cfg.max_length}")
        self.trace(f"[brain:trainer:session] id={session_id} log={session_log}")
        self.trace("[brain:trainer:cmd] "+" ".join(command))
        raw("[session] "+json.dumps({"id":session_id,"base":base,"device":self.cfg.device,"examples":len(batch),"steps":self.cfg.micro_steps,"rank":self.cfg.rank,"max_length":self.cfg.max_length,"command":command},ensure_ascii=False))
        env=dict(os.environ)
        env["PYTHONFAULTHANDLER"]="1"
        env.setdefault("TOKENIZERS_PARALLELISM","false")
        try:
            proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",bufsize=1,env=env)
        except Exception:
            batch_path.unlink(missing_ok=True)
            raise
        raw(f"[process] pid={proc.pid}")
        assert proc.stdout
        watcher_stop=threading.Event()

        def watch_resources_and_cancel():
            nonlocal peak_rss_mb,min_free_gb
            next_ui=0.0
            while not watcher_stop.wait(2.0):
                if (time.monotonic() >= deadline or (cancel is not None and cancel.is_set())) and proc.poll() is None:
                    if time.monotonic() >= deadline: timed_out.set()
                    self.trace(f"[brain:trainer] cancellation requested; terminating pid={proc.pid}")
                    raw("[cancel] requested")
                    try:proc.terminate()
                    except Exception:pass
                    time.sleep(.5)
                    if proc.poll() is None:
                        try:proc.kill()
                        except Exception:pass
                    return
                total,free=memory_gb(); rss=process_memory_mb(proc.pid)
                peak_rss_mb=max(peak_rss_mb,float(rss or 0.0))
                if free:
                    min_free_gb=float(free) if min_free_gb is None else min(min_free_gb,float(free))
                snapshot=f"pid={proc.pid} rss_mb={rss:.1f} ram_free_gb={free:.2f} ram_total_gb={total:.2f}"
                raw("[resource] "+snapshot)
                now=time.monotonic()
                if now >= next_ui:
                    self.trace("[brain:trainer:resource] "+snapshot)
                    next_ui=now+8.0

        threading.Thread(target=watch_resources_and_cancel,name="brain-trainer-watch",daemon=True).start()
        code=-999
        try:
            for line in proc.stdout:
                line=line.rstrip()
                if not line: continue
                raw("[worker] "+line)
                try:
                    obj=json.loads(line)
                    if isinstance(obj,dict):
                        last.update(obj)
                        if obj.get("phase"): last_phase=str(obj.get("phase"))
                        self.trace("[brain:trainer:event] "+json.dumps(obj,ensure_ascii=False))
                        if progress:
                            try:p=float(obj.get("progress") if obj.get("progress") is not None else .5)
                            except Exception:p=.5
                            progress(str(obj.get("message") or "Training personal weights…"),p)
                        continue
                except Exception:
                    pass
                self.trace("[brain:trainer:out] "+line)
                wp=_weight_load_progress(line)
                if progress and wp is not None:
                    m=re.search(r"Loading weights:\s*(\d{1,3})%",line)
                    pct=m.group(1) if m else ""
                    progress(f"Loading training model weights… {pct}%",wp)
            code=proc.wait()
        finally:
            watcher_stop.set()
            if proc.poll() is None:
                proc.kill(); proc.wait()
            proc.stdout.close()
            batch_path.unlink(missing_ok=True)

        elapsed=round(time.time()-start_at,2)
        exit_info=_decode_trainer_exit_code(code)
        diag={
            "session_id":session_id,"log_path":str(session_log),"started_at":start_at,"elapsed_seconds":elapsed,
            "exit_code":int(code),"exit_hex":exit_info.get("hex") or "","exit_name":exit_info.get("name") or "",
            "native_crash":bool(exit_info.get("native_crash")),"last_phase":str(last.get("phase") or last_phase),
            "last_message":str(last.get("message") or ""),"last_error":str(last.get("error") or ""),
            "peak_worker_rss_mb":round(peak_rss_mb,1),"minimum_free_ram_gb":round(min_free_gb,2) if min_free_gb is not None else None,
            "tail":list(tail)[-20:],
        }
        self.last_trainer=dict(diag)
        try:_atomic_json(BRAIN_LAST_TRAINER,diag)
        except Exception:pass
        raw("[exit] "+json.dumps(diag,ensure_ascii=False))
        self.trace("[brain:trainer:exit] "+json.dumps({k:v for k,v in diag.items() if k!="tail"},ensure_ascii=False))
        if cancel is not None and cancel.is_set(): raise BrainCancelled("Brain training cancelled by user")
        if timed_out.is_set(): raise RuntimeError(f"Brain trainer timed out after {self.cfg.trainer_timeout}s")
        if code!=0:
            explicit=str(last.get("error") or "").strip()
            if explicit:
                message=explicit
            elif exit_info.get("name")=="STATUS_ACCESS_VIOLATION":
                message=("Brain Trainer crashed in native code with STATUS_ACCESS_VIOLATION (0xC0000005) "
                         f"during {diag['last_phase']}. Python could not catch the failing native extension. "
                         "LlamaForge 0.18.1 uses the Windows CPU safe backend to bypass bitsandbytes 4-bit model loading. ")
            elif exit_info.get("name")=="STATUS_NO_MEMORY":
                message="Brain Trainer was terminated by Windows with STATUS_NO_MEMORY (0xC0000017). "
            elif exit_info.get("native_crash"):
                message=f"Brain Trainer crashed in native code ({exit_info.get('name')} {exit_info.get('hex')}). "
            else:
                message=f"Brain Trainer exited with code {code}. "
            message += f"Full trainer session log: {session_log}"
            raise RuntimeError(message)
        if last.get("phase") != "complete" or not last.get("validation", {}).get("accepted"):
            raise RuntimeError("Trainer did not finish a validated candidate; adapter was not activated")
        return last

    def _convert_adapter(self, model, progress=None, adapter_path: Path | None=None, out_path: Path | None=None, cancel: threading.Event | None=None) -> Path:
        if not self.toolchain_ready(): raise RuntimeError("llama.cpp LoRA conversion toolchain is not prepared")
        script=BRAIN_TOOLCHAIN / "convert_lora_to_gguf.py"
        adapter_path=Path(adapter_path or self.adapter_dir(model))
        out=Path(out_path or self.adapter_gguf(model)); out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_suffix(".tmp.gguf")
        command=[str(self.trainer_python()),str(script),str(adapter_path),"--outfile",str(tmp),"--outtype","f16"]
        training_base=self.training_base_for(model)
        base=Path(os.path.expanduser(training_base))
        converter_base=base
        bundle=base / ".llamaforge-bundle.json"
        if bundle.is_file():
            try:
                bdata=json.loads(bundle.read_text(encoding="utf-8"))
                under=Path(str(bdata.get("underlying_local_dir") or ""))
                if under.is_dir(): converter_base=under
            except Exception: pass
        if converter_base.is_dir(): command += ["--base",str(converter_base)]
        env=dict(os.environ)
        env["PYTHONPATH"]=str(BRAIN_TOOLCHAIN)+os.pathsep+str(BRAIN_TOOLCHAIN/"gguf-py")+os.pathsep+env.get("PYTHONPATH","")
        if progress: progress("Converting learned LoRA to llama.cpp GGUF adapter…",0.88)
        self.trace("[brain:convert:cmd] "+" ".join(command))
        self._run_logged(command,"convert",timeout=900,cancel=cancel,env=env)
        if not tmp.exists(): raise RuntimeError("LoRA converter did not produce an adapter GGUF")
        tmp.replace(out)
        return out

    def learn(self, model, user_text: str, assistant_text: str, examples: list[dict], progress=None, cancel: threading.Event | None=None) -> dict:
        if not self.cfg.enabled: raise RuntimeError("Brain Learning is disabled")
        if cancel is not None and cancel.is_set(): raise BrainCancelled("Brain learning cancelled by user")
        if model is None: raise RuntimeError("No active model is linked to this Brain profile")
        with self.lock:
            if self.job.get("state") == "running" and self.job.get("stage") not in ("synthesize", "queued"):
                raise RuntimeError("A Brain learning job is already running")
            self.job={"state":"running","stage":"archive","message":"Building training packet…","error":"","progress":0.05}
        def emit(msg,p):
            with self.lock: self.job.update(message=msg,progress=float(p))
            if progress: progress(msg,p)
        try:
            meta=self._profile_meta(model)
            locked=str(meta.get("training_base") or "").strip()
            current=str(self.cfg.training_base or "").strip()
            if locked and locked != current and self.adapter_dir(model).exists():
                raise RuntimeError("This Brain profile is already bound to a different trainable base. Use the exact original base or reset the profile before changing it.")
            deterministic=self.deterministic_examples(user_text)
            packet=self.append_packet(model,user_text,assistant_text,deterministic+list(examples or []))
            if not packet["examples"] or self.already_learned(model, packet["examples"]):
                with self.lock:
                    self.job={"state":"done","stage":"no-op","message":"No new user supervision to train", "error":"", "progress":1.0}
                return {"ok": True, "skipped": True, "generation": int(meta.get("generation") or 0)}
            batch=self.build_training_batch(model,packet)
            emit(f"Micro-training on {len(batch)} examples…",0.18)
            previous_meta=dict(meta)
            candidate=self._prepare_learning_candidate(model)
            result=self._run_trainer(model,batch,emit,adapter_path=candidate,cancel=cancel)
            if cancel is not None and cancel.is_set(): raise BrainCancelled("Brain learning cancelled by user")
            validation = result.get("validation") or {}
            if not validation.get("accepted"):
                raise RuntimeError("Candidate quality check did not pass; weights unchanged")
            gguf=self._convert_adapter(model,emit,adapter_path=candidate,out_path=self.candidate_adapter_gguf(model),cancel=cancel)
            if cancel is not None and cancel.is_set(): raise BrainCancelled("Brain learning cancelled before weights were committed")
            if not self.cfg.enabled: raise BrainCancelled("Personal Brain was turned off; candidate weights were discarded")
            loss=None
            try: loss=float(result.get("loss"))
            except Exception: pass
            meta=self._profile_meta(model); generation=int(meta.get("generation") or 0)+1
            learned_at=time.time()
            next_meta={"generation":generation,"last_loss":loss,"last_learned_at":learned_at,
                       "training_base":str(self.training_base_for(model) or ""),
                       "training_base_model_key":self.model_key(model), "validation":validation}
            self._commit_learning_candidate(model,str(packet.get("id") or ""),previous_meta,next_meta)
            with self.lock:
                self.generation = generation; self.last_learned_at=learned_at; self.last_loss=loss
                self.job={"state":"running","stage":"reload","message":"Weights trained and converted. Verifying reload…","error":"","progress":0.94}
            return {"ok":True,"generation":generation,"adapter":str(self.adapter_gguf(model)),"examples":len(batch),"loss":loss,"packet_id":str(packet.get("id") or ""),"pending_confirmation":True,"validation":validation}
        except Exception as exc:
            shutil.rmtree(self.candidate_adapter_dir(model),ignore_errors=True)
            try:self.candidate_adapter_gguf(model).unlink(missing_ok=True)
            except Exception:pass
            self.rollback_unconfirmed_learning(model, quiet=True)
            self.trace_exception("learn", exc)
            with self.lock: self.job={"state":"error","stage":"failed","message":"Learning failed; weights were not marked as learned.","error":str(exc),"progress":0.0}
            raise

    def doctor(self, model=None, hw=None) -> dict:
        """Return a complete, non-destructive Brain readiness report."""
        checks=[]
        def add(name, ok, detail, level=None):
            checks.append({"name":name,"ok":bool(ok),"level":level or ("ok" if ok else "error"),"detail":str(detail)})
        py=self.trainer_python(); add("trainer_python", py.is_file(), py)
        add("trainer_marker", BRAIN_READY_MARKER.is_file(), BRAIN_READY_MARKER)
        add("toolchain", self.toolchain_ready(), BRAIN_TOOLCHAIN / "convert_lora_to_gguf.py")
        base=self.training_base_for(model); add("training_base", bool(base), base or "Not linked")
        if base:
            bp=Path(os.path.expanduser(base))
            effective_base=bp
            if bp.exists():
                adapter_cfg_path=bp/"adapter_config.json"
                adapter_weight=(bp/"adapter_model.safetensors").is_file() or (bp/"adapter_model.bin").is_file()
                effective_base=bp
                if adapter_cfg_path.is_file() and adapter_weight:
                    try:
                        acfg=json.loads(adapter_cfg_path.read_text(encoding="utf-8"))
                        declared=str(acfg.get("base_model_name_or_path") or "")
                        task_type=str(acfg.get("task_type") or "").strip().upper()
                        add("adapter_task", (not task_type) or task_type=="CAUSAL_LM", task_type or "Unspecified")
                        add("training_strategy", True, f"stacked-adapter · source fine-tune stays frozen · underlying base: {declared or 'unknown'}")
                        bundle_path=bp/".llamaforge-bundle.json"
                        if bundle_path.is_file():
                            bdata=json.loads(bundle_path.read_text(encoding="utf-8"))
                            under=Path(str(bdata.get("underlying_local_dir") or ""))
                            under_weights=[x for x in list(under.rglob("*.safetensors"))+list(under.rglob("*.bin")) if "adapter_model" not in x.name.lower()] if under.is_dir() else []
                            under_ok=bool(under.is_dir() and (under/"config.json").is_file() and under_weights)
                            add("underlying_base", under_ok, f"{under} · {len(under_weights)} weight file(s)" if under else "Missing local underlying base")
                            if under_ok: effective_base=under
                        else:
                            add("underlying_base", False, "PEFT source is missing .llamaforge-bundle.json; run automatic setup again")
                    except Exception as exc:
                        add("training_strategy", False, f"adapter/bundle parse failed: {exc}")
                else:
                    add("training_strategy", True, "standalone checkpoint")
                add("base_config", (bp/"config.json").is_file() or adapter_cfg_path.is_file(), (bp/"config.json") if (bp/"config.json").is_file() else adapter_cfg_path)
                weights=list(bp.rglob("*.safetensors"))+list(bp.rglob("*.bin"))
                add("base_weights", bool(weights), f"{len(weights)} weight/adapter file(s)")
                # A tokenizer_config.json names a tokenizer; it does not contain
                # its vocabulary/model. Metadata-only downloads are not ready.
                def tokenizer_assets(root):
                    return [root/name for name in ("tokenizer.json", "tokenizer.model", "spiece.model", "sentencepiece.model", "vocab.json", "vocab.txt") if (root/name).is_file() and (root/name).stat().st_size > 0]
                toks=tokenizer_assets(bp) or tokenizer_assets(effective_base)
                add("tokenizer", bool(toks), f"{len(toks)} tokenizer data file(s)" if toks else "Tokenizer data missing: repair/download tokenizer.json or the vocabulary/model files; tokenizer_config.json alone is insufficient")
                try:
                    cfg_path=(effective_base/"config.json") if (effective_base/"config.json").is_file() else (bp/"config.json")
                    cfg=json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
                    remote=bool(cfg.get("auto_map")); add("remote_code", (not remote) or self.cfg.allow_remote_code, "Required by model" if remote else "Not required", "warn" if remote and not self.cfg.allow_remote_code else "ok")
                except Exception as exc:
                    add("base_config_parse", False, exc)
                add("training_bundle_ready", self.training_base_ready(model), "Local source/base bundle verified" if self.training_base_ready(model) else "Local training bundle is incomplete; automatic setup must repair it")
            else:
                add("base_local", False, "Remote repo id is not locally ready; automatic setup must download and verify its training bundle before learning")
        if py.is_file():
            dep_result=None
            if self._doctor_dependencies_cache and time.time()-self._doctor_dependencies_cache[0] < 300:
                dep_result=dict(self._doctor_dependencies_cache[1])
            if dep_result is None:
                probe = (
                    "import json,sys,platform; out={'python':sys.version.split()[0],'platform':platform.platform()}; "
                    "mods=['torch','transformers','peft','accelerate','safetensors']; "
                    "import importlib,importlib.metadata as im; "
                    "[(out.__setitem__(m,getattr(importlib.import_module(m),'__version__','installed'))) for m in mods]; "
                    "out['bitsandbytes']=im.version('bitsandbytes') if True else ''; "
                    "import torch; out['cuda']=bool(torch.cuda.is_available()); out['mps']=bool(hasattr(torch.backends,'mps') and torch.backends.mps.is_available()); print(json.dumps(out))"
                )
                try:
                    pr=subprocess.run([str(py),"-c",probe],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
                    dep_result={"ok":pr.returncode==0,"detail":pr.stdout.strip().splitlines()[-1] if pr.returncode==0 and pr.stdout.strip() else (pr.stderr or pr.stdout)[-1200:]}
                except Exception as exc:
                    dep_result={"ok":False,"detail":str(exc)}
                self._doctor_dependencies_cache=(time.time(),dict(dep_result))
            if dep_result.get("ok"):
                try: detail=json.dumps(json.loads(dep_result.get("detail") or "{}"),ensure_ascii=False)
                except Exception: detail=str(dep_result.get("detail") or "ok")
                add("dependencies", True, detail)
            else:
                add("dependencies", False, dep_result.get("detail") or "dependency probe failed")
        # An Intel/display GPU is not a CUDA training backend. Match the worker's
        # effective device using its own dependency probe, before unloading chat.
        dependency_info = {}
        if py.is_file() and dep_result and dep_result.get("ok"):
            try: dependency_info = json.loads(dep_result.get("detail") or "{}")
            except (ValueError, TypeError): pass
        device = str(self.cfg.device or "auto").lower()
        cpu_training = device == "cpu" or (device == "auto" and not dependency_info.get("cuda") and not dependency_info.get("mps"))
        if base and cpu_training and str(getattr(hw, "os_name", platform.system())).lower() == "windows":
            try:
                source = effective_base
                config_file = source / "config.json"
                source_config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
                quantized = bool(source_config.get("quantization_config"))
                add("cpu_checkpoint_format", not quantized,
                    "Windows CPU training requires a full non-prequantized checkpoint; this source has quantization_config. Select the full training source; the GGUF chat copy is unchanged."
                    if quantized else "Full checkpoint format is compatible with Windows CPU safe LoRA.")
            except Exception as exc: add("cpu_checkpoint_format", False, str(exc))
        # For CPU-only PEFT bundles backed by bitsandbytes 4-bit weights, verify
        # the actual 4-bit kernel path with a tiny layer before we unload the
        # inference model and spend time loading several GB of checkpoint data.
        if py.is_file() and base and cpu_training and str(getattr(hw,"os_name",platform.system())).lower() != "windows":
            try:
                bp=Path(os.path.expanduser(base)); cfg_path=bp/"config.json"
                bundle=bp/".llamaforge-bundle.json"
                if bundle.is_file():
                    bd=json.loads(bundle.read_text(encoding="utf-8")); under=Path(str(bd.get("underlying_local_dir") or ""))
                    if under.is_dir(): cfg_path=under/"config.json"
                cfg=json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
                qcfg=cfg.get("quantization_config") if isinstance(cfg,dict) else None
                qmethod=str((qcfg or {}).get("quant_method") or "") if isinstance(qcfg,dict) else ""
                if "bitsandbytes" in qmethod.lower():
                    probe=(
                        "import json,torch,bitsandbytes as bnb; "
                        "m=bnb.nn.Linear4bit(16,16,bias=False,compute_dtype=torch.float32,quant_type='nf4',compress_statistics=True); "
                        "m=m.to('cpu'); w=m.weight; "
                        "ok=hasattr(w,'compress_statistics') and w.__class__.__name__=='Params4bit'; "
                        "x=torch.randn(2,16); y=m(x); "
                        "print(json.dumps({'ok':bool(ok),'weight_type':w.__class__.__name__,'shape':list(y.shape)})); "
                        "raise SystemExit(0 if ok else 7)"
                    )
                    qres=None
                    if self._doctor_qlora_cache and time.time()-self._doctor_qlora_cache[0] < 300:
                        qres=dict(self._doctor_qlora_cache[1])
                    if qres is None:
                        pr=subprocess.run([str(py),"-c",probe],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45)
                        detail=(pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else (pr.stderr or "probe failed")[-1000:])
                        qres={"ok":pr.returncode==0,"detail":detail}
                        self._doctor_qlora_cache=(time.time(),dict(qres))
                    add("cpu_qlora_backend",bool(qres.get("ok")),qres.get("detail") or "probe failed")
            except Exception as exc:
                add("cpu_qlora_backend",False,f"4-bit backend probe failed: {exc}")
        if hw is not None:
            try:
                add("ram", float(hw.ram_total_gb)>=12.0, f"{hw.ram_available_gb:.1f} GB free / {hw.ram_total_gb:.1f} GB total", "warn" if hw.ram_available_gb<6 else "ok")
                add("cpu", True, f"{hw.physical_cores} physical / {hw.logical_cores} logical · {hw.cpu}")
                add("gpu", True, ", ".join(g.name for g in hw.gpus) if hw.gpus else "CPU-only")
                if str(getattr(hw,"os_name","")).lower()=="windows" and cpu_training:
                    add("trainer_backend_policy", True,
                        "Windows CPU safe LoRA: bypass bitsandbytes native 4-bit model loading; use FP16 text-only causal loading, q/v LoRA, rank<=4 and max_length<=192 (Gemma3 gets a dedicated text-only loader when applicable).",
                        "ok")
            except Exception: pass
        if self.last_trainer:
            last=self.last_trainer
            last_ok=int(last.get("exit_code") or 0)==0
            detail=(f"session={last.get('session_id','')} exit={last.get('exit_name','')} {last.get('exit_hex','')} "
                    f"phase={last.get('last_phase','')} peak_rss={last.get('peak_worker_rss_mb',0)}MB log={last.get('log_path','')}")
            add("last_trainer_session", last_ok, detail, "ok" if last_ok else "warn")
        ok=all(c["ok"] or c["level"]=="warn" for c in checks)
        report={"ok":ok,"checks":checks,"base":base,"adapter":str(self.adapter_gguf(model)) if model else "","generation":self._profile_meta(model).get("generation",0) if model else 0,
                "last_trainer":dict(self.last_trainer)}
        self.trace("[brain:doctor] "+json.dumps(report,ensure_ascii=False))
        return report

    def bake_merged(self, model, base_gguf: str, export_binary: str, output: str | None=None) -> str:
        adapter=self.adapter_gguf(model)
        if not adapter.is_file(): raise RuntimeError("No learned GGUF adapter exists for this model")
        if not export_binary or not Path(export_binary).is_file(): raise RuntimeError("llama-export-lora is not installed in the active runtime")
        out=Path(output) if output else self.merged_gguf(model)
        out.parent.mkdir(parents=True,exist_ok=True)
        cmd=[export_binary,"--model",str(base_gguf),"--lora",str(adapter),"--output",str(out)]
        proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",timeout=3600)
        if proc.returncode!=0: raise RuntimeError("Brain merge failed: "+proc.stdout[-1800:])
        return str(out)
