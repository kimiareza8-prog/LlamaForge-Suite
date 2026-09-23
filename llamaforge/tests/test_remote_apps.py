from llamaforge.core.remote_apps import _clean_connect_url, _public_text, RemoteAppManager


def test_connection_url_extracts_token_without_exposing_it():
    clean, token = _clean_connect_url("https://example.com/ai/connect.php?token=abc123&x=1")
    assert token == "abc123"
    assert "token=" not in clean
    assert "x=1" in clean


def test_public_text_redacts_tokens():
    text = _public_text("https://x.test/connect.php?token=supersecret123456 and Bearer abcdefghijklmnopqrstuvwxyz")
    assert "supersecret" not in text
    assert "abcdefghijklmnopqrstuvwxyz" not in text
    assert "[redacted]" in text


def test_agent_event_maps_to_user_visible_tool_activity():
    row = RemoteAppManager._activity_from_event({"type":"agent","event":"tool_start","tool":"web_read"})
    assert row["skill"] == "web_read"
    assert row["phase"] == "execute"
    assert row["status"] == "active"


def test_bridge_update_package_contains_manifest_and_checksums(tmp_path, monkeypatch):
    import json
    import zipfile
    import io
    import llamaforge.core.remote_apps as ra

    payload = tmp_path / "web-bridge"
    (payload / "assets").mkdir(parents=True)
    (payload / "VERSION").write_text("9.9.9-test\n", encoding="utf-8")
    (payload / "index.php").write_text("<?php echo 'ok';", encoding="utf-8")
    (payload / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setattr(ra, "BRIDGE_PAYLOAD_DIR", payload)

    blob, manifest = RemoteAppManager._bridge_update_package()
    assert manifest["format"] == "llamaforge-web-bridge-update-v1"
    assert manifest["version"] == "9.9.9-test"
    assert "index.php" in manifest["files"]
    with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
        stored = json.loads(zf.read("bridge-manifest.json"))
        assert stored["version"] == "9.9.9-test"
        assert zf.read("index.php").startswith(b"<?php")


def test_remote_app_public_state_exposes_bridge_update_support(monkeypatch):
    mgr = object.__new__(RemoteAppManager)
    mgr.live = {}
    monkeypatch.setattr(mgr, "_token", lambda row: "secret")
    row = {
        "id": "x1", "name": "Site", "remote_version": "3.4.0",
        "endpoints": {"bridge_update": "https://example.com/bridge-update.php"},
        "enabled": True,
    }
    public = mgr._public(row)
    assert public["remote_version"] == "3.4.0"
    assert public["bridge_update_supported"] is True
    assert public["token_configured"] is True


def test_update_bridge_uploads_package_and_refreshes_descriptor(monkeypatch):
    mgr = object.__new__(RemoteAppManager)
    row = {
        "id": "x1", "name": "Site",
        "endpoints": {"bridge_update": "https://example.com/bridge-update.php"},
    }
    monkeypatch.setattr(mgr, "_load", lambda: [row])
    monkeypatch.setattr(mgr, "_token", lambda row: "secret")
    monkeypatch.setattr(mgr, "_bridge_update_package", lambda: (b"ZIPDATA", {"version": "3.4.0"}))
    sent = {}
    def fake_upload(url, token, data, **kwargs):
        sent.update(url=url, token=token, data=data, kwargs=kwargs)
        return {"ok": True, "installed_version": "3.4.0", "backup": {"id": "b1"}}
    monkeypatch.setattr(mgr, "_request_bytes_json", fake_upload)
    monkeypatch.setattr(mgr, "test", lambda app_id: {"ok": True, "app": {"id": app_id, "remote_version": "3.4.0"}})
    result = mgr.update_bridge("x1")
    assert result["ok"] is True
    assert result["package_version"] == "3.4.0"
    assert sent["data"] == b"ZIPDATA"
    assert "action=install" in sent["url"]
    assert sent["kwargs"]["headers"]["X-AIB-Target-Version"] == "3.4.0"
