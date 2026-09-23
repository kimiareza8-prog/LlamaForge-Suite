from types import SimpleNamespace
from unittest.mock import Mock

from llamaforge.web.server import LlamaForgeState


def test_manual_generation_settings_override_smart_profile():
    state = object.__new__(LlamaForgeState)
    state.cfg = SimpleNamespace(
        generation_overrides_enabled=True,
        generation_temperature=0.33,
        generation_top_p=0.81,
        generation_top_k=17,
        generation_min_p=0.02,
        generation_repeat_penalty=1.11,
        generation_max_tokens=1234,
    )
    profile = SimpleNamespace(
        temperature=1.0, top_p=1.0, top_k=99, min_p=0.1,
        repeat_penalty=1.0, max_tokens=4000, source="smart", notes=(),
    )
    got = state._apply_generation_overrides(profile)
    assert got.temperature == 0.33
    assert got.top_p == 0.81
    assert got.top_k == 17
    assert got.min_p == 0.02
    assert got.repeat_penalty == 1.11
    assert got.max_tokens == 1234
    assert got.source == "manual-settings"


def test_remote_model_catalog_uses_opaque_ids_not_paths():
    state = object.__new__(LlamaForgeState)
    model = SimpleNamespace(path="C:/secret/models/model-a.gguf", name="Model A", architecture="gemma3", quantization="Q4_K_M", size_gb=3.25, vision_capable=True)
    state.local_models = [model]
    state.active_model = model
    state.server_ready = True
    state.lock = __import__('threading').RLock()
    rows = state._remote_model_catalog()
    assert len(rows) == 1
    assert rows[0]["name"] == "Model A"
    assert rows[0]["id"].startswith("mdl_")
    assert "path" not in rows[0]
    assert "secret" not in str(rows[0])
    assert rows[0]["loaded"] is True
    assert rows[0]["vision_capable"] is True


def test_attachment_materialization_is_shared_by_token_and_template_paths():
    state = object.__new__(LlamaForgeState)
    state.server_ready = True
    state.active_model = SimpleNamespace(vision_capable=True)
    messages = [{
        "role": "user",
        "content": "inspect",
        "attachments": [
            {"kind": "text", "name": "note.txt", "text": "hello file"},
            {"kind": "image", "name": "x.png", "data_url": "data:image/png;base64,AAAA"},
        ],
    }]
    clean = state._materialize_chat_messages(messages)
    assert set(clean[0]) == {"role", "content"}
    assert isinstance(clean[0]["content"], list)
    assert "hello file" in clean[0]["content"][0]["text"]
    assert clean[0]["content"][1]["type"] == "image_url"


def test_text_only_model_rejects_image_attachment():
    import pytest
    state = object.__new__(LlamaForgeState)
    state.server_ready = True
    state.active_model = SimpleNamespace(vision_capable=False)
    with pytest.raises(RuntimeError, match="not image-capable"):
        state._materialize_chat_messages([{
            "role": "user", "content": "x",
            "attachments": [{"kind": "image", "data_url": "data:image/png;base64,AAAA"}],
        }])


def test_settings_accept_exact_8000_context_and_gpu_mode():
    state = object.__new__(LlamaForgeState)
    state.cfg = SimpleNamespace(
        accelerator_mode="hybrid", cpu_only_default=False, gpu_layer_percent=35,
        default_context_size=4096, save=Mock(),
    )
    state.events = SimpleNamespace(publish=Mock())
    state.update_settings({"default_context_size": 8000, "accelerator_mode": "gpu", "gpu_layer_percent": 55})
    assert state.cfg.default_context_size == 8000
    assert state.cfg.accelerator_mode == "gpu"
    assert state.cfg.cpu_only_default is False
    assert state.cfg.gpu_layer_percent == 55
    state.cfg.save.assert_called_once()
