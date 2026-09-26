import json
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

from llamaforge.web.server import STATIC_ROOT, create_server, choose_port


def test_web_assets_are_self_contained():
    assert (STATIC_ROOT / "index.html").is_file()
    assert (STATIC_ROOT / "styles.css").is_file()
    assert (STATIC_ROOT / "app.js").is_file()
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html


def test_choose_port_returns_bindable_local_port():
    port = choose_port("127.0.0.1", 18865)
    assert isinstance(port, int) and port >= 1024


def test_local_api_ping_and_index(tmp_path):
    # Keep AppConfig isolated from a real user's home directory while exercising
    # the actual stdlib HTTP server and static shell.
    with patch("llamaforge.web.server.AppConfig.load") as load:
        from llamaforge.core.config import AppConfig
        load.return_value = AppConfig(model_dirs=[str(tmp_path / "models")], runtime_dir=str(tmp_path / "runtime"))
        server, state, url = create_server(port=None)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=3) as r:
            data = json.loads(r.read().decode())
        assert data["ok"] is True
        assert data["version"] == "0.34.2-diagnostics"
        with urllib.request.urlopen(url, timeout=3) as r:
            html = r.read().decode("utf-8")
            assert "no-store" in (r.headers.get("Cache-Control") or "")
        assert "LlamaForge" in html and "app.js" in html
        with urllib.request.urlopen(url + "styles.css", timeout=3) as r:
            assert "no-store" in (r.headers.get("Cache-Control") or "")
    finally:
        state.shutdown()
        server.shutdown()
        server.server_close()


def test_template_health_accepts_embedded_probe(tmp_path):
    with patch("llamaforge.web.server.AppConfig.load") as load:
        from llamaforge.core.config import AppConfig
        load.return_value = AppConfig(model_dirs=[str(tmp_path / "models")], runtime_dir=str(tmp_path / "runtime"))
        server, state, url = create_server(port=None)
    try:
        state.active_model = type("M", (), {"chat_template": "x"})()
        with patch("llamaforge.web.server.apply_chat_template", return_value="<user>LLAMAFORGE_TEMPLATE_PROBE_71C9</user><assistant>"):
            h = state._check_template_health()
        assert h["state"] == "ok"
    finally:
        state.shutdown(); server.server_close()


def test_chat_streaming_uses_stable_dom_updates():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "text.textContent=m.content||''" in js
    assert "renderMarkdown(m.content)+(m.streaming" not in js
    assert "App.chatFollowTail" in js
    assert "streamPaintTimer=setTimeout" in js
    assert "scroll-behavior:auto!important" in css


def test_cpu_target_controls_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "homeCpuTarget" in js
    assert "cpu_target_percent" in js
    assert "data-mode=\"target\"" in js
    assert ".cpu-power-card" in css


def test_ui_is_event_driven_and_legacy_ui_is_removed():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    root = STATIC_ROOT.parent.parent.parent
    assert "new EventSource('/api/events')" in js
    assert "setInterval(()=>refreshState" not in js
    assert not (root / "legacy_app.py").exists()
    assert not (root / "ui").exists()

def test_saturation_controls_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    assert "data-mode=\"saturate\"" in js
    assert "cpu_saturation" in js
    assert "Saturate CPU" in js


def test_personal_brain_ui_and_zero_context_contract_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "['brain','Brain','brain']" in js
    assert "ZERO-CONTEXT" in js
    assert "/api/brain/learn" in js
    assert "/api/brain/autosetup" in js
    assert "Set up automatically" in js
    assert ".brain-simple-hero" in css


def test_model_lifecycle_controls_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    assert "/api/model/unload" in js
    assert "Unload model when LlamaForge closes" in js
    assert "Exit & unload" in js
    assert "idle_unload_minutes" in js


def test_default_config_unloads_model_on_exit(tmp_path):
    from llamaforge.core.config import AppConfig
    cfg = AppConfig(model_dirs=[str(tmp_path / "models")], runtime_dir=str(tmp_path / "runtime"))
    assert cfg.exit_unloads_model is True
    assert cfg.ui_disconnect_shutdown_seconds <= 20

def test_brain_trainable_library_and_diagnostics_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    assert "/api/brain/catalog/search" in js
    assert "/api/brain/catalog/download" in js
    assert "/api/brain/doctor" in js
    assert "Training models" in js
    assert "Download & use" in js

def test_full_diagnostics_endpoint_is_present_in_ui():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    assert "/api/diagnostics" in js
    assert "Copy system diagnostic" in js


def test_studio_theme_is_local_layered_and_covers_core_surfaces():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "macos.css").read_text(encoding="utf-8")
    assert (STATIC_ROOT / "macos.css").is_file()
    assert html.index('/styles.css') < html.index('/macos.css')
    assert 'http://' not in css and 'https://' not in css
    for selector in (
        '.sidebar', '.topbar', '.primary-button', '.field input', '.model-card',
        '.brain-simple-hero', '.brain-master-toggle>span', '.log-shell', '.modal',
        '.command-palette', '.studio-composer', '.studio-user .user-bubble'
    ):
        assert selector in css
    assert '@media (prefers-reduced-motion: reduce)' in css


def test_macos_theme_asset_is_served_with_no_store(tmp_path):
    with patch("llamaforge.web.server.AppConfig.load") as load:
        from llamaforge.core.config import AppConfig
        load.return_value = AppConfig(model_dirs=[str(tmp_path / "models")], runtime_dir=str(tmp_path / "runtime"))
        server, state, url = create_server(port=None)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(url + "macos.css", timeout=3) as r:
            css = r.read().decode("utf-8")
            assert "no-store" in (r.headers.get("Cache-Control") or "")
            assert "--panel:var(--surface)" in css
            assert "text/css" in (r.headers.get("Content-Type") or "")
    finally:
        state.shutdown(); server.shutdown(); server.server_close()


def test_brain_master_toggle_uses_atomic_button_and_dedicated_api():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    mac = (STATIC_ROOT / "macos.css").read_text(encoding="utf-8")
    assert 'id="brainPower"' in js
    assert 'role="switch"' in js
    assert "/api/brain/toggle" in js
    assert "brainTogglePending" in js
    assert "save({enabled:on" not in js
    assert ".brain-power-switch" in mac
    assert ".brain-power-track" in mac

def test_brain_cancel_api_route_is_present():
    src = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")
    assert 'if path == "/api/brain/cancel"' in src
    assert "cancel_brain_operation" in src
    assert 'if path == "/api/brain/toggle"' in src
    assert "toggle_brain" in src


def test_models_view_resets_chat_scroll_lock_and_download_flow_exists():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "view.className='view';" in js
    assert "/api/models/catalog/search" in js
    assert "/api/models/catalog/files" in js
    assert "/api/models/catalog/download" in js
    assert "overflow-y:auto" in css
    assert ".model-download-modal" in css
    assert ".download-model-results" in css


def test_extended_trainer_diagnostics_are_exposed_in_ui_and_api():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    src = (STATIC_ROOT.parent / "server.py").read_text(encoding="utf-8")
    assert "Copy trainer session" in js
    assert "/api/logs/trainer" in js
    assert 'if path == "/api/logs/trainer"' in src
    assert '"last_trainer"' in src
    assert "ui_buffer_lines" in src


def test_quick_model_chat_is_not_blocked_by_learning_download():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    # An already-downloaded GGUF is usable even if the Transformers learning
    # checkpoint is still incomplete.
    assert "if(quick.chat_ready&&quick.chat_path)" in js
    assert "quick.chat_ready?'Load & chat'" in js
    # The model activation path must open/start chat before it kicks off the
    # potentially multi-GB Brain auto-setup download.
    fn = js[js.index("async function activateModelAndChat"):js.index("function modelCard", js.index("async function activateModelAndChat"))]
    assert fn.index("await startOptimized()") < fn.index("/api/brain/autosetup")


def test_memory_mode_ui_uses_stable_apply_buttons_and_preserves_pending_choice():
    root = Path(__file__).resolve().parents[1]
    js = (root / "llamaforge" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="memoryModePicker"' in js
    assert "card('ram_only','Full RAM'" in js
    assert "card('ssd_test','SSD / mmap'" in js
    assert "card('hybrid','Smart RAM'" in js
    assert 'data-memory-mode="${mode}"' in js
    assert 'id="applyMemoryMode"' in js
    assert "App.settingsMemoryDirty=true" in js
    assert "App.route==='settings'&&(App.settingsMemoryDirty||App.settingsFormDirty)" in js
    assert "body:{model_memory_mode:requested}" in js
    assert 'id="modelMemoryMode"' not in js


def test_multimodal_and_settings_persistence_controls_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    assert "settingsFormDirty" in js
    assert "generationTemperature" in js
    assert "chatFileInput" in js
    assert "pendingAttachments" in js
    assert "vision_capable" in js


def test_cluster_dashboard_and_worker_controls_are_present():
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "['cluster','Cluster','cluster']" in js
    assert "renderCluster" in js
    assert "/api/cluster/pair" in js
    assert "Cluster RAM limit" in js
    assert "Selected Pool" in js and "Force Selected Nodes" in js
    assert "worker-autostart" in js
    assert ".cluster-node" in css
