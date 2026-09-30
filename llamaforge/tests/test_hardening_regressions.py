from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

from llamaforge.core.audio_transcription import LocalVoiceTranscriber
from llamaforge.core.config import AppConfig
from llamaforge.core.remote_apps import _clean_connect_url, _same_origin_endpoint
from llamaforge.core.skill_contracts import operation_policy
from llamaforge.core.workspace import FileWorkspace, _parse_iso


def test_calendar_preserves_explicit_offset():
    value = _parse_iso("2026-10-01T08:00:00+03:30")
    assert value.utcoffset() == timedelta(hours=3, minutes=30)
    assert value.hour == 8


def test_calendar_read_contract_does_not_get_overwritten():
    for operation in ("now", "convert", "month", "list", "find_free_time"):
        assert operation_policy("calendar", {"operation": operation}).effect == "read"


def test_remote_app_requires_https_off_loopback_and_keeps_descriptor_same_origin():
    with pytest.raises(ValueError):
        _clean_connect_url("http://example.com/connect?token=secret")
    clean, token = _clean_connect_url("http://127.0.0.1:8080/connect?token=secret")
    assert clean == "http://127.0.0.1:8080/connect" and token == "secret"
    assert _same_origin_endpoint("https://example.com/connect", "/api/poll") == "https://example.com/api/poll"
    with pytest.raises(RuntimeError):
        _same_origin_endpoint("https://example.com/connect", "https://evil.example/api/poll")


def test_full_request_traces_are_opt_in_by_default():
    assert AppConfig().diagnostic_full_traces is False


def test_voice_cleanup_removes_stale_recordings(tmp_path):
    root = tmp_path / "recordings"; root.mkdir()
    stale = root / "old.webm"; stale.write_bytes(b"old")
    old = time.time() - (8 * 24 * 3600)
    os.utime(stale, (old, old))
    LocalVoiceTranscriber(lambda: "", lambda: "", root)
    assert not stale.exists()


def test_workspace_snapshot_promotion_rolls_back_on_swap_failure(tmp_path, monkeypatch):
    ws = FileWorkspace(tmp_path / "ws")
    original = ws.files_root / "keep.txt"; original.write_text("keep", encoding="utf-8")
    ws._save_index({"items": {"old": {"id":"old", "name":"keep.txt", "path":"keep.txt", "status":"active"}}})
    payload = {
        "index": {"items": {"new": {"id":"new", "name":"new.txt", "path":"new.txt", "status":"active"}}},
        "folders": [],
        "files": [{"id":"new", "status":"active", "path":"new.txt", "data_base64":"bmV3"}],
    }
    real_replace = os.replace
    failed = {"once": False}
    def flaky(src, dst):
        src_s, dst_s = str(src), str(dst)
        if ".snapshot-stage-" in src_s and dst_s.endswith(os.sep + "files") and not failed["once"]:
            failed["once"] = True
            raise OSError("simulated promotion failure")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        ws.import_snapshot(payload)
    assert (ws.files_root / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert ws._index()["items"]["old"]["path"] == "keep.txt"
