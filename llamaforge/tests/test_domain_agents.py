from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime
from llamaforge.core.skill_system import SkillRegistry
from llamaforge.core.workspace import CalendarStore, FileWorkspace


def test_calendar_is_one_general_composable_skill(tmp_path):
    store = CalendarStore(tmp_path / "cal")
    now = store.tool({"operation": "now"}, allow_write=False)
    assert now["jalali"] and now["time"]

    start = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(days=1)
    event = store.tool(
        {
            "operation": "create",
            "title": "جلسه نمونه",
            "start": start.isoformat(),
            "end": (start + timedelta(hours=1)).isoformat(),
        },
        allow_write=True,
    )
    rows = store.tool(
        {
            "operation": "list",
            "start": (start - timedelta(hours=1)).isoformat(),
            "end": (start + timedelta(hours=2)).isoformat(),
        },
        allow_write=False,
    )["events"]
    assert [x["id"] for x in rows] == [event["id"]]

    # Free-time is intentionally reasoning over primitives, not another skill/op.
    runtime = AgentRuntime()
    calendar_def = next(x["function"] for x in runtime.tool_definitions(AgentPermissions()) if x["function"]["name"] == "calendar")
    ops = calendar_def["parameters"]["properties"]["operation"]["enum"]
    assert "find_free_time" not in ops
    assert {"now", "list", "create", "update", "cancel", "delete"}.issubset(set(ops))


def test_skill_tree_can_expose_only_calendar_domain():
    runtime = AgentRuntime()
    permissions = AgentPermissions(allow_write=True)
    registry = SkillRegistry(runtime, permissions)
    rows = registry.shortlist("یک سؤال جدید درباره برنامه زمانی من", ["calendar"], limit=5)
    names = {x["name"] for x in rows}
    assert "calendar" in names
    # The calendar branch does not need one concrete skill for every calendar question.
    assert "find_free_time" not in names


def test_attachment_is_staged_without_putting_contents_in_model_context(tmp_path):
    workspace = FileWorkspace(tmp_path / "ws")
    secret = "THIS_CONTENT_SHOULD_NOT_ENTER_CONTEXT_UNLESS_READ"
    staged = workspace.stage_messages([
        {
            "role": "user",
            "content": "این مدرک را فقط ذخیره کن",
            "attachments": [{"kind": "text", "name": "company.txt", "text": secret, "type": "text/plain"}],
        }
    ])
    rendered = staged[0]["content"]
    assert "Workspace attachment:" in rendered
    assert "attachment_id=" in rendered
    assert secret not in rendered

    attachment_id = rendered.split("attachment_id=", 1)[1].split(" ", 1)[0]
    stored = workspace.tool(
        {"operation": "store_attachment", "attachment_id": attachment_id, "folder": "مدارک شرکت"},
        allow_write=True,
    )
    assert stored["path"].startswith("مدارک شرکت/")

    inspected = workspace.tool({"operation": "read_content", "id": stored["id"]}, allow_write=False)
    assert secret in inspected["text"]


def test_jalali_conversion_round_trip(tmp_path):
    store = CalendarStore(tmp_path / "cal")
    g = store.convert(jalali="1405-07-01")
    j = store.convert(gregorian=g["gregorian"])
    assert j["jalali"] == "1405-07-01"


def test_embedded_bridge_payload_contains_workspace_sync_and_matches_version():
    repo = Path(__file__).resolve().parents[2]
    outer = repo / "web-bridge"
    embedded = repo / "llamaforge" / "bridge_payload" / "web-bridge"
    assert (outer / "workspace-sync.php").is_file()
    assert (embedded / "workspace-sync.php").is_file()
    assert (outer / "VERSION").read_text(encoding="utf-8").strip() == "3.9.0-live-stream-files"
    assert (embedded / "VERSION").read_text(encoding="utf-8").strip() == "3.9.0-live-stream-files"
    for rel in ("workspace-sync.php", "api.php", "connect.php", "lib/bootstrap.php", "assets/app.js", "assets/app.css"):
        assert (outer / rel).read_bytes() == (embedded / rel).read_bytes()


def test_web_bridge_exposes_sse_stream_with_long_poll_fallback():
    repo = Path(__file__).resolve().parents[2]
    api = (repo / "web-bridge" / "api.php").read_text(encoding="utf-8")
    app = (repo / "web-bridge" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "action === 'stream'" in api or 'action === "stream"' in api
    assert "text/event-stream" in api
    assert "X-Accel-Buffering" in api
    assert "action=stream" in app
    assert "action=watch" in app
