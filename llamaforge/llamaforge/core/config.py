from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_DIR = Path.home() / ".llamaforge"
CONFIG_PATH = APP_DIR / "config.json"
RUNTIME_DIR = APP_DIR / "runtime"
DEFAULT_MODEL_DIR = Path.home() / "LlamaForgeModels"
KEYRING_SERVICE = "LlamaForge"
KEYRING_USER = "huggingface-token"


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
    agent_skill_profile: str = "all"
    agent_max_steps: int = 8
    diagnostic_full_traces: bool = True
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

    @classmethod
    def load(cls) -> "AppConfig":
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DEFAULT_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
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
        # accelerator_mode supersedes the legacy CPU-only default. Existing
        # 0.29.0 configs therefore migrate to Hybrid automatically instead of
        # silently preserving the old CPU-only behavior forever.
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
        if cfg.agent_skill_profile not in {"all", "telegram_only"}: cfg.agent_skill_profile = "all"
        cfg.agent_max_steps = min(16, max(1, int(cfg.agent_max_steps or 8)))
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
        return cfg

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        raw = asdict(self)
        # Prefer the operating-system credential vault. If no usable keyring
        # backend exists, retain backwards-compatible local config behavior.
        if _keyring_store(self.hf_token):
            raw["hf_token"] = ""
        CONFIG_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")
