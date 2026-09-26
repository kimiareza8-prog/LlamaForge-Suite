from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from llamaforge.core import agent_tools
from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime
from llamaforge.core.agent_engine import AgentEngine
from llamaforge.core.skill_system import SkillRegistry
from llamaforge.core.workspace import FileWorkspace


def isolated_runtime(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    monkeypatch.setattr(agent_tools, "AGENT_DIR", root)
    monkeypatch.setattr(agent_tools, "CONNECTORS_PATH", root / "connectors.json")
    monkeypatch.setattr(agent_tools, "SKILLS_DIR", root / "skills")
    monkeypatch.setattr(agent_tools, "BROWSER_PROFILE_DIR", root / "browser-profile")
    monkeypatch.setattr(agent_tools, "DOWNLOADS_DIR", root / "downloads")
    monkeypatch.setattr(agent_tools, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(agent_tools, "_keyring_get", lambda _id: None)
    monkeypatch.setattr(agent_tools, "_keyring_set", lambda _id, _token: False)
    return AgentRuntime()


def test_calendar_and_files_remain_visible_when_external_write_is_off(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    perms = AgentPermissions(allow_write=False, allow_workspace_write=True)
    reg = SkillRegistry(rt, perms)
    cal = reg.shortlist("برای فردا یک قرار در تقویم بگذار", ["calendar"], limit=5)
    files = reg.shortlist("این فایل را بخوان", ["files"], limit=5)
    assert "calendar" in {x["name"] for x in cal}
    assert "workspace_files" in {x["name"] for x in files}
    assert reg.validate_call("calendar", {"operation": "create", "title": "Meeting", "relative_date": "tomorrow", "time": "10:00"})[0] is True
    assert reg.validate_call("workspace_files", {"operation": "write_text", "name": "note.txt", "text": ""})[0] is True


def test_local_workspace_writes_are_separate_from_remote_site_writes(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    perms = AgentPermissions(allow_write=False, allow_workspace_write=True)
    created = json.loads(rt.execute("workspace_files", {"operation": "write_text", "name": "note.txt", "text": "hello"}, perms))
    assert created["ok"] is True
    event = json.loads(rt.execute("calendar", {"operation": "create", "title": "test", "start": "2026-09-24T09:00:00+03:30"}, perms))
    assert event["ok"] is True
    methods = next(x["function"] for x in rt.tool_definitions(perms) if x["function"]["name"] == "http_request")["parameters"]["properties"]["method"]["enum"]
    assert methods == ["GET", "HEAD"]


def test_current_date_guard_forces_skill_route_even_if_small_model_says_direct(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    executed = []

    def execute(name, args, permissions):
        executed.append((name, dict(args)))
        if name == "calendar":
            return json.dumps({"ok": True, "result": {"gregorian": "2026-09-23", "jalali": "1405-07-01", "time": "13:00:00"}}, ensure_ascii=False)
        raise AssertionError(name)

    monkeypatch.setattr(rt, "execute", execute)

    def model(messages, tools):
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"direct","summary":"I can answer","confidence":80}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"get today date","families":["calendar"],"needs_write":false}'}
        if "STEP 1 OF" in prompt:
            return {"content": '{"action":"final","summary":"done","answer":"امروز را می‌دانم"}'}
        if "STEP 2 OF" in prompt:
            return {"content": '{"action":"final","summary":"done","answer":"امروز ۱ مهر ۱۴۰۵ است."}'}
        if "Write the final answer" in prompt:
            return {"content": "امروز ۱ مهر ۱۴۰۵ است."}
        return {"content": '{"action":"final","summary":"done","answer":"امروز ۱ مهر ۱۴۰۵ است."}'}

    events = list(rt.run([{"role": "user", "content": "امروز چندمه؟"}], model, AgentPermissions(), max_steps=3))
    assert executed and executed[0] == ("calendar", {"operation": "now"})
    assert any(e.get("event") == "route_decision" and e.get("route") == "skills" for e in events)


def test_attachment_fallback_reads_attachment_directly(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    staged = rt.prepare_messages_for_agent([{
        "role": "user",
        "content": "این فایل رو بخون",
        "attachments": [{"kind": "text", "name": "a.txt", "text": "hello from attachment", "type": "text/plain"}],
    }])
    marker = staged[0]["content"]
    assert "attachment_id=" in marker

    # Exercise the deterministic fallback directly: it should not list the normal
    # workspace first because staged attachments live in the inbox.
    decision = AgentEngine._heuristic_first_action(marker, {"workspace_files"})
    assert decision is not None
    assert decision.skill == "workspace_files"
    assert decision.arguments["operation"] == "read_content"
    assert decision.arguments.get("attachment_id")


def test_workspace_can_read_zip_projects_and_edit_text(tmp_path):
    ws = FileWorkspace(tmp_path / "ws")
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("src/app.py", "print('hello')\n")
        zf.writestr("README.md", "# Demo project\n")
        zf.writestr("assets/blob.bin", b"\x00\x01\x02")
    row = ws.upload_data(name="project.zip", data_url="data:application/zip;base64," + __import__("base64").b64encode(buff.getvalue()).decode())
    read = ws.tool({"operation": "read_content", "id": row["id"], "max_chars": 10000}, allow_write=False)
    assert read["content_type"] == "archive"
    assert read["entry_count"] == 3
    assert "src/app.py" in read["text_preview"]
    assert "print('hello')" in read["text_preview"]

    text_row = ws.tool({"operation": "write_text", "name": "code.py", "text": "value = 1\n"}, allow_write=True)
    replaced = ws.tool({"operation": "replace_text", "id": text_row["id"], "old_text": "1", "new_text": "2"}, allow_write=True)
    assert replaced["replacements"] == 1
    out = ws.tool({"operation": "read_content", "id": text_row["id"]}, allow_write=False)
    assert "value = 2" in out["text"]


def test_generic_attachment_fallback_probes_before_reading(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    staged = rt.prepare_messages_for_agent([{
        "role": "user",
        "content": "این فایل مستندات پروژه است، ببین برای درخواست من چه کاری لازم است",
        "attachments": [{"kind": "file", "name": "docs.bin", "data_url": "data:application/octet-stream;base64,AAEC", "type": "application/octet-stream"}],
    }])
    marker = staged[0]["content"]
    decision = AgentEngine._heuristic_first_action(marker, {"workspace_files"})
    assert decision is not None
    assert decision.skill == "workspace_files"
    assert decision.arguments["operation"] == "probe"
    assert decision.arguments.get("attachment_id")


def test_skill_hints_detect_staged_and_archive_attachments(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    reg = SkillRegistry(rt, AgentPermissions())
    assert "files" in reg.hinted_families("attachment_id=abc123 project.zip")
    names = {x["name"] for x in reg.shortlist("attachment_id=abc123 project.zip", ["files"], limit=4)}
    assert "workspace_files" in names


def test_zip_probe_lists_tree_without_reading_file_contents(tmp_path):
    ws = FileWorkspace(tmp_path / "ws-probe")
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("src/secret.py", "TOP_SECRET_CONTENT = 123\n")
        zf.writestr("README.md", "# project docs\n")
    import base64
    row = ws.upload_data(name="project.zip", data_url="data:application/zip;base64," + base64.b64encode(buff.getvalue()).decode())
    probe = ws.tool({"operation": "probe", "id": row["id"]}, allow_write=False)
    assert probe["read_capability"] == "archive"
    assert probe["content_read"] is False
    assert any(x["name"] == "src/secret.py" for x in probe["archive_entries"])
    assert "text_preview" not in probe
    assert "TOP_SECRET_CONTENT" not in str(probe)
