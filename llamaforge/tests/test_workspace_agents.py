from __future__ import annotations

import base64
from pathlib import Path

from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime
from llamaforge.core.workspace import CalendarStore, FileWorkspace


def test_calendar_is_general_capability_and_can_compose_free_time(tmp_path: Path):
    cal = CalendarStore(tmp_path / "calendar")
    created = cal.tool(
        {
            "operation": "create",
            "title": "جلسه",
            "start": "2026-09-26T10:00:00+03:30",
            "end": "2026-09-26T11:00:00+03:30",
            "reminders": [30],
        },
        allow_write=True,
    )
    assert created["title"] == "جلسه"
    rows = cal.tool(
        {"operation": "list", "start": "2026-09-26T00:00:00+03:30", "end": "2026-09-26T23:59:00+03:30"},
        allow_write=False,
    )["events"]
    assert len(rows) == 1
    # Availability is intentionally derived by the model from now/list; there is
    # no brittle one-question skill to maintain.
    try:
        cal.tool({"operation": "find_free_time"}, allow_write=False)
    except ValueError as exc:
        assert "calendar operation" in str(exc)
    else:
        raise AssertionError("find_free_time must not become a dedicated operation")


def test_file_attachment_is_staged_without_reading_content(tmp_path: Path):
    ws = FileWorkspace(tmp_path / "workspace")
    secret = "content should stay out of planner context"
    messages = [{"role": "user", "content": "این مدرک را بگذار داخل مدارک شرکت", "attachments": [{"kind": "text", "name": "company.txt", "text": secret}]}]
    staged = ws.stage_messages(messages)
    text = str(staged[0]["content"])
    assert "Workspace attachment" in text
    assert secret not in text
    assert "attachment_id=" in text


def test_file_read_content_is_on_demand_and_snapshot_roundtrips(tmp_path: Path):
    source = FileWorkspace(tmp_path / "source")
    row = source.upload_data(name="note.txt", folder="مدارک شرکت", text="hello document")
    meta = source.tool({"operation": "metadata", "id": row["id"]}, allow_write=False)
    assert "hello document" not in str(meta)
    content = source.tool({"operation": "read_content", "id": row["id"]}, allow_write=False)
    assert content["text"] == "hello document"

    snapshot = source.snapshot()
    target = FileWorkspace(tmp_path / "target")
    target.import_snapshot(snapshot)
    matches = target.search("note")
    assert matches and matches[0]["name"] == "note.txt"
    copied = target.tool({"operation": "read_content", "id": row["id"]}, allow_write=False)
    assert copied["text"] == "hello document"


def test_image_content_requires_vision_and_is_only_injected_on_read(tmp_path: Path):
    ws = FileWorkspace(tmp_path / "workspace")
    png = b"\x89PNG\r\n\x1a\nnot-a-real-image-but-enough-for-storage-test"
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    row = ws.upload_data(name="photo.png", data_url=data_url)
    try:
        ws.tool({"operation": "read_content", "id": row["id"]}, allow_write=False, vision_available=False)
    except ValueError as exc:
        assert "vision-capable" in str(exc)
    else:
        raise AssertionError("text-only model must not receive image content")
    result = ws.tool({"operation": "read_content", "id": row["id"]}, allow_write=False, vision_available=True)
    assert result["content_type"] == "image"
    assert result["vision_attachment"]["data_url"].startswith("data:image/png;base64,")


def test_agent_exposes_only_general_calendar_and_workspace_tools(monkeypatch, tmp_path: Path):
    import llamaforge.core.agent_tools as agent_tools

    monkeypatch.setattr(agent_tools, "WORKSPACE_DIR", tmp_path / "portable-workspace")
    runtime = AgentRuntime()
    names = [x["function"]["name"] for x in runtime.tool_definitions(AgentPermissions(allow_write=True), include_connectors=False)]
    assert "calendar" in names
    assert "workspace_files" in names
    assert "find_free_time" not in names
    assert "save_company_document" not in names

    runtime.set_workspace_scope("remote_site_ownerA")
    runtime.calendar.write("create", {"title": "Remote", "start": "2026-09-26T12:00:00+03:30"})
    assert runtime.calendar.list_events()
    runtime.set_workspace_scope("local")
    assert runtime.calendar.list_events() == []


def test_hosted_bridge_has_calendar_files_and_owner_scoped_sync():
    root = Path(__file__).resolve().parents[2]
    index = (root / "web-bridge" / "index.php").read_text(encoding="utf-8")
    connect = (root / "web-bridge" / "connect.php").read_text(encoding="utf-8")
    agent = (root / "web-bridge" / "agent.php").read_text(encoding="utf-8")
    sync = root / "web-bridge" / "workspace-sync.php"
    assert 'id="navCalendar"' in index
    assert 'id="navFiles"' in index
    assert "workspace_sync" in connect
    assert "workspace_owner" in agent
    assert sync.is_file()
