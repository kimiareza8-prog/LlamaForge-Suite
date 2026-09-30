from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_DIR = Path.home() / ".llamaforge"
CONFIG_PATH = APP_DIR / "config.json"
CONFIG_BACKUP_PATH = APP_DIR / "config.json.bak"
RUNTIME_DIR = APP_DIR / "runtime"
DEFAULT_MODEL_DIR = Path.home() / "LlamaForgeModels"
KEYRING_SERVICE = "LlamaForge"
KEYRING_USER = "huggingface-token"
API_PROVIDER_IDS = {"openai", "gemini", "cerebras", "groq", "mistral", "alibaba"}
ALIBABA_BASE_URLS = {
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1",
}


def _keyring_get() -> str | None:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        return None


def _keyring_store(token: str) -> bool:
    try:
        import keyring
        if token:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _load_json_file(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _atomic_write_json(path: Path, value: dict) -> None:
    """Durably replace one small JSON file without exposing partial contents.

    Older builds wrote config.json directly. If another extracted LlamaForge build
    started while that write was in progress, it could observe an empty/partial
    file, fall back to defaults and then persist those defaults. Atomic replace
    removes that cross-version failure mode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
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


@dataclass
class AppConfig:
    model_dirs: list[str] = field(default_factory=lambda: [str(DEFAULT_MODEL_DIR)])
    runtime_dir: str = str(RUNTIME_DIR)
    hf_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8080
    preferred_profile: str = "Balanced"
    cpu_only_default: bool = False
    accelerator_mode: str = "adaptive"
    gpu_layer_percent: int = 35
    max_ram_percent: int = 88
    model_memory_mode: str = "hybrid"
    custom_server_path: str = ""
    last_model_path: str = ""
    exit_unloads_model: bool = True
    ui_disconnect_shutdown_seconds: int = 12
    idle_unload_minutes: int = 0
    agent_enabled_default: bool = False
    agent_allow_write: bool = True
    agent_allow_workspace_write: bool = True
    agent_allow_private_network: bool = True
    agent_browser_headless: bool = False
    agent_allow_telegram_read: bool = True
    agent_allow_telegram_write: bool = True
    agent_allow_tool_creation: bool = False
    agent_allow_system_commands: bool = False
    agent_allow_code_execution: bool = False
    agent_skill_profile: str = "all"
    agent_max_steps: int = 16
    diagnostic_full_traces: bool = False
    default_context_size: int = 4096
    generation_overrides_enabled: bool = False
    generation_temperature: float = 0.70
    generation_top_p: float = 0.95
    generation_top_k: int = 40
    generation_min_p: float = 0.00
    generation_repeat_penalty: float = 1.03
    generation_max_tokens: int = 2048
    speculative_mode: str = "auto"
    adaptive_context: bool = True
    inference_backend: str = "local"
    external_model_id: str = ""
    api_output_syntax: dict = field(default_factory=dict)
    api_provider_base_urls: dict = field(default_factory=dict)
    audio_ffmpeg_path: str = ""
    audio_vosk_model_path: str = ""

    @classmethod
    def load(cls) -> "AppConfig":
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DEFAULT_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg

        raw = _load_json_file(CONFIG_PATH)
        if raw is None:
            # Recover the last known-good config instead of silently turning a
            # transient/partial read into a permanent reset of model/runtime paths.
            raw = _load_json_file(CONFIG_BACKUP_PATH)
            if raw is not None:
                try:
                    _atomic_write_json(CONFIG_PATH, raw)
                except Exception:
                    pass
        try:
            known = {k: v for k, v in (raw or {}).items() if k in cls.__dataclass_fields__}
            cfg = cls(**known)
        except Exception:
            cfg = cls()

        secret = _keyring_get()
        if secret is not None:
            cfg.hf_token = secret

        for p in cfg.model_dirs:
            try:
                Path(os.path.expanduser(p)).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        Path(cfg.runtime_dir).expanduser().mkdir(parents=True, exist_ok=True)
        cfg.max_ram_percent = min(95, max(50, int(cfg.max_ram_percent or 88)))
        if str(getattr(cfg, "accelerator_mode", "adaptive") or "adaptive").lower() not in {"adaptive", "cpu", "gpu", "hybrid", "max_both"}:
            cfg.accelerator_mode = "adaptive"
        else:
            cfg.accelerator_mode = str(cfg.accelerator_mode or "adaptive").lower()
        cfg.cpu_only_default = cfg.accelerator_mode == "cpu"
        cfg.gpu_layer_percent = min(95, max(5, int(getattr(cfg, "gpu_layer_percent", 35) or 35)))
        if cfg.model_memory_mode not in {"ram_only", "ssd_test", "hybrid"}:
            cfg.model_memory_mode = "hybrid"
        cfg.ui_disconnect_shutdown_seconds = min(300, max(6, int(cfg.ui_disconnect_shutdown_seconds or 12)))
        cfg.idle_unload_minutes = max(0, int(cfg.idle_unload_minutes or 0))
        cfg.agent_enabled_default = bool(cfg.agent_enabled_default)
        cfg.agent_allow_write = bool(cfg.agent_allow_write)
        cfg.agent_allow_workspace_write = bool(getattr(cfg, "agent_allow_workspace_write", True))
        cfg.agent_allow_private_network = bool(cfg.agent_allow_private_network)
        cfg.agent_browser_headless = bool(cfg.agent_browser_headless)
        cfg.agent_allow_telegram_read = bool(cfg.agent_allow_telegram_read)
        cfg.agent_allow_telegram_write = bool(cfg.agent_allow_telegram_write)
        cfg.agent_allow_tool_creation = bool(getattr(cfg, "agent_allow_tool_creation", False))
        cfg.agent_allow_system_commands = bool(getattr(cfg, "agent_allow_system_commands", False))
        cfg.agent_allow_code_execution = bool(getattr(cfg, "agent_allow_code_execution", False))
        cfg.audio_ffmpeg_path = str(getattr(cfg, "audio_ffmpeg_path", "") or "").strip()[:1000]
        cfg.audio_vosk_model_path = str(getattr(cfg, "audio_vosk_model_path", "") or "").strip()[:1000]
        if cfg.agent_skill_profile not in {"all", "telegram_only"}: cfg.agent_skill_profile = "all"
        cfg.agent_max_steps = min(24, max(1, int(cfg.agent_max_steps or 16)))
        cfg.default_context_size = min(262144, max(512, int(cfg.default_context_size or 4096)))
        cfg.generation_overrides_enabled = bool(cfg.generation_overrides_enabled)
        cfg.generation_temperature = min(2.0, max(0.0, float(cfg.generation_temperature if cfg.generation_temperature is not None else 0.70)))
        cfg.generation_top_p = min(1.0, max(0.0, float(cfg.generation_top_p if cfg.generation_top_p is not None else 0.95)))
        cfg.generation_top_k = min(500, max(0, int(cfg.generation_top_k if cfg.generation_top_k is not None else 40)))
        cfg.generation_min_p = min(1.0, max(0.0, float(cfg.generation_min_p if cfg.generation_min_p is not None else 0.0)))
        cfg.generation_repeat_penalty = min(1.30, max(0.80, float(cfg.generation_repeat_penalty if cfg.generation_repeat_penalty is not None else 1.03)))
        cfg.generation_max_tokens = min(32768, max(16, int(cfg.generation_max_tokens or 2048)))
        cfg.speculative_mode = str(getattr(cfg, "speculative_mode", "auto") or "auto").lower()
        if cfg.speculative_mode not in {"off", "auto", "ngram"}:
            cfg.speculative_mode = "auto"
        cfg.adaptive_context = bool(getattr(cfg, "adaptive_context", True))
        cfg.inference_backend = str(getattr(cfg, "inference_backend", "local") or "local").lower()
        if cfg.inference_backend not in {"local", *API_PROVIDER_IDS}:
            cfg.inference_backend = "local"
        cfg.external_model_id = str(getattr(cfg, "external_model_id", "") or "").strip()[:240]
        raw_base_urls = getattr(cfg, "api_provider_base_urls", {})
        cfg.api_provider_base_urls = {}
        if isinstance(raw_base_urls, dict):
            alibaba_url = str(raw_base_urls.get("alibaba") or "").strip().rstrip("/")
            if alibaba_url in {url.rstrip("/") for url in ALIBABA_BASE_URLS}:
                cfg.api_provider_base_urls["alibaba"] = alibaba_url
        raw_syntax = getattr(cfg, "api_output_syntax", {})
        cfg.api_output_syntax = {}
        if isinstance(raw_syntax, dict):
            for provider, models in raw_syntax.items():
                provider = str(provider or "").lower().strip()
                if provider not in API_PROVIDER_IDS or not isinstance(models, dict):
                    continue
                clean_models = {}
                for model, markers in models.items():
                    model = str(model or "").strip()[:240]
                    if not model or not isinstance(markers, dict):
                        continue
                    opening = str(markers.get("open_marker") or "").strip()
                    closing = str(markers.get("close_marker") or "").strip()
                    if (not opening and not closing) or not opening or not closing:
                        continue
                    if len(opening) > 80 or len(closing) > 80 or any(ord(ch) < 32 for ch in opening + closing):
                        continue
                    clean_models[model] = {"open_marker": opening, "close_marker": closing}
                if clean_models:
                    cfg.api_output_syntax[provider] = clean_models
        return cfg

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        raw = asdict(self)
        if _keyring_store(self.hf_token):
            raw["hf_token"] = ""

        # The primary file is replaced atomically, then mirrored to a known-good
        # backup. If an older build later performs a non-atomic write and is
        # interrupted, this backup can restore the latest settings.
        _atomic_write_json(CONFIG_PATH, raw)
        try:
            _atomic_write_json(CONFIG_BACKUP_PATH, raw)
        except Exception:
            pass
