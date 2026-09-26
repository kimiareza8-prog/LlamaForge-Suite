import base64
import io
import json
import threading
import zipfile
from datetime import datetime, timedelta

import pytest

from llamaforge.core import agent_tools
from llamaforge.core.agent_engine import AgentEngine
from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime
from llamaforge.core.skill_system import SkillRegistry
from llamaforge.core.workspace import CalendarStore, FileWorkspace


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name, path in {"AGENT_DIR":"agent", "CONNECTORS_PATH":"agent/connectors.json", "SKILLS_DIR":"skills",
                       "BROWSER_PROFILE_DIR":"browser", "DOWNLOADS_DIR":"downloads", "WORKSPACE_DIR":"workspace"}.items():
        monkeypatch.setattr(agent_tools, name, tmp_path / path)
    monkeypatch.setattr(agent_tools, "_keyring_get", lambda _: None)
    monkeypatch.setattr(agent_tools, "_keyring_set", lambda *_: False)
    return AgentRuntime()


@pytest.mark.parametrize("task,family", [
    ("امروز چندمه؟","calendar"), ("ساعت چنده؟","calendar"), ("برای فردا یک قرار بساز","calendar"),
    ("What time is it?","calendar"), ("What's the time?","calendar"), ("What is today's date?","calendar"),
    ("Create a meeting tomorrow at 10","calendar"), ("فردا یک meeting بساز","calendar"),
    ("این ZIP چیه؟","files"), ("کدهای داخل ZIP را بررسی کن","files"), ("این فایل را ذخیره کن","files"),
    ("PDF را خلاصه کن","files"), ("Summarize this PDF","files"), ("Save this attachment","files"),
    ("این PDF را summarize کن","files"), ("https://example.com","web"),
    ("این https://example.com را بخوان","web"), ("برو داخل سایت، کلیک کن و فرم را پر کن","browser"),
    ("Go to the website and fill the form","browser"), ("داخل website کلیک کن","browser"),
])
def test_intent_guard(task, family):
    assert family in SkillRegistry.hinted_families(task)


@pytest.mark.parametrize("task", ["سلام", "Hello!", "What is a calendar?", "Explain ZIP archives",
    'Translate "meeting tomorrow" into Persian', "تقویم چیست؟", "عبارت ساعت چنده را به انگلیسی ترجمه کن",
    "Write a poem about tomorrow", "Explain how a browser works", "What is HTTP GET?"])
def test_guard_does_not_override_conversation(task):
    assert SkillRegistry.hinted_families(task) == []


@pytest.mark.parametrize("task", ["I missed a meeting yesterday", "My browser crashed yesterday",
    "Tomorrow will be a beautiful day", "دیروز جلسه خوبی داشتم", "قرار نیست کاری انجام بدی",
    "I like ZIP compression", "Don't create a calendar event"])
def test_guard_does_not_turn_mentions_into_actions(task):
    assert SkillRegistry.hinted_families(task) == []


@pytest.mark.parametrize("skill,args,missing", [
    ("calendar", {"operation":"create"}, "title"),
    ("calendar", {"operation":"create", "title":"Meeting", "start":"10:00"}, "date"),
    ("calendar", {"operation":"delete"}, "id"),
    ("workspace_files", {"operation":"store_attachment"}, "attachment_id"),
    ("workspace_files", {"operation":"read_content"}, "id"),
    ("workspace_files", {"operation":"replace_text", "id":"file_a"}, "old_text"),
])
def test_operation_preconditions_are_checked_before_execution(runtime, skill, args, missing):
    ok, error = SkillRegistry(runtime, AgentPermissions()).validate_call(skill, args)
    assert not ok and missing in error


def test_manifest_contract_and_family_budget(runtime):
    reg = SkillRegistry(runtime, AgentPermissions())
    rows = reg.shortlist("برای فردا قرار بساز", ["calendar"], limit=2)
    assert [r["name"] for r in rows] == ["calendar"]
    manifest, _ = reg.manifest(rows, runtime.tool_definitions(AgentPermissions()))
    for word in ("create", "now", "ISO", "include_cancelled", "local_workspace", "input_schema"):
        assert word in manifest


def test_validation_rejects_operation_and_type(runtime):
    reg = SkillRegistry(runtime, AgentPermissions())
    assert not reg.validate_call("calendar", {"operation":"make_event"})[0]
    assert not reg.validate_call("workspace_files", {"operation":"read_content", "max_chars":"many"})[0]


def test_download_permission(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_http", lambda *_a, **_k: pytest.fail("network must not run"))
    out = json.loads(runtime.execute("download_file", {"url":"https://example.com/file"}, AgentPermissions(allow_workspace_write=False)))
    assert not out["ok"] and "disabled" in out["error"].lower()


@pytest.mark.parametrize("skill", ["browser_open", "browser_snapshot", "download_file"])
def test_session_and_writes_are_not_parallel_reads(runtime, skill):
    assert not AgentEngine._parallel_read_safe(skill, {}, SkillRegistry(runtime, AgentPermissions()).catalog())


def test_greeting_one_inference(runtime):
    calls = []
    def model(messages, tools):
        calls.append(messages)
        return {"content":"سلام!"}
    events = list(runtime.run([{"role":"user", "content":"سلام"}], model, AgentPermissions()))
    assert len(calls) == 1
    assert "".join(e.get("delta", "") for e in events) == "سلام!"


def test_cancel_does_not_wait_for_parallel_network_reads(runtime):
    import time
    cancel, entered, release = threading.Event(), threading.Event(), threading.Event()
    def execute(*_):
        entered.set()
        release.wait(2)
        return '{"ok":true,"result":{}}'
    runtime.execute = execute
    def model(messages, tools):
        p = messages[0]["content"]
        if "stage 0" in p: return {"content":'{"route":"skills","families":["web"]}'}
        if "STEP 1" in p: return {"content":json.dumps({"action":"parallel", "actions":[
            {"skill":"web_read","arguments":{"url":"https://example.com/a"}},
            {"skill":"web_read","arguments":{"url":"https://example.com/b"}}]})}
        return {"content":'{"action":"final","answer":"Done"}'}
    def stop():
        entered.wait(1)
        cancel.set()
    stopper = threading.Thread(target=stop); stopper.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="cancel"):
            list(runtime.run([{"role":"user", "content":"Read https://example.com/a and https://example.com/b"}], model, AgentPermissions(), cancel=cancel))
        assert time.monotonic() - started < 1
    finally:
        release.set(); stopper.join()


def test_parallel_owner_isolation_and_overlap(runtime):
    runtime.set_workspace_scope("owner-a")
    barrier = threading.Barrier(2, timeout=3)
    original = runtime.execute
    seen = []
    def execute(name, args, perms):
        seen.append(runtime.workspace_scope()); barrier.wait()
        return original(name, args, perms)
    runtime.execute = execute
    def model(messages, tools):
        p = messages[0]["content"]
        if "stage 0" in p: return {"content":'{"route":"skills"}'}
        if "stage 1" in p: return {"content":'{"families":["files","calendar"]}'}
        if "STEP 1 OF" in p: return {"content":json.dumps({"action":"parallel", "actions":[
            {"skill":"workspace_files", "arguments":{"operation":"list"}},
            {"skill":"calendar", "arguments":{"operation":"now"}}]})}
        return {"content":'{"action":"final","answer":"Done."}'}
    events = list(runtime.run([{"role":"user","content":"List my files and the current time"}], model, AgentPermissions()))
    assert seen == ["owner-a", "owner-a"]
    assert any(e.get("event") == "parallel_done" for e in events)


def test_budget_final_is_streamed(runtime):
    def model(messages, tools):
        p = messages[0]["content"]
        if "stage 0" in p: return {"content":'{"route":"skills"}'}
        if "stage 1" in p: return {"content":'{"families":["calendar"]}'}
        if "STEP 1 OF" in p: return {"content":'{"action":"tool","skill":"calendar","arguments":{"operation":"now"}}'}
        pytest.fail("Final must use streaming callback")
    def stream(messages):
        yield {"type":"text","delta":"Today "}
        yield {"type":"text","delta":"is…"}
    events = list(runtime.run([{"role":"user","content":"What time is it?"}], model,
                             AgentPermissions(), max_steps=1, stream_final=stream))
    assert [e["delta"] for e in events if e.get("type") == "text"] == ["Today ", "is…"]


def test_final_budget_still_receives_explicitly_read_image(runtime):
    runtime.vision_available = True
    staged = runtime.prepare_messages_for_agent([{"role":"user", "content":"Describe this image", "attachments":[
        {"kind":"image", "name":"photo.png", "type":"image/png", "data_url":"data:image/png;base64,AAEC"}]}])
    attachment_id = staged[0]["_attachment_refs"][0]["attachment_id"]
    def model(messages, tools):
        if "stage 0" in messages[0]["content"]:
            return {"content":'{"route":"skills","families":["files"]}'}
        return {"content":json.dumps({"action":"tool", "skill":"workspace_files", "arguments":{"operation":"read_content", "attachment_id":attachment_id}})}
    def final(messages):
        assert isinstance(messages[0]["content"], list)
        assert messages[0]["content"][1]["type"] == "image_url"
        yield {"type":"text", "delta":"Image inspected"}
    list(runtime.run(staged, model, AgentPermissions(), max_steps=1, stream_final=final))


def encoded(data):
    return "data:application/octet-stream;base64," + base64.b64encode(data).decode()


def test_attachment_receipt_and_dedup(tmp_path):
    ws = FileWorkspace(tmp_path)
    raw = {"name":"records.bin", "data_url":encoded(b"\x00\xff\x01")}
    meta = ws.stage_attachment(raw)
    assert ws.stage_attachment(raw)["attachment_id"] == meta["attachment_id"]
    args = {"operation":"store_attachment", "attachment_id":meta["attachment_id"], "folder":"Company documents"}
    saved = ws.tool(args, True)
    ws.tool({"operation":"rename", "id":saved["id"], "name":"renamed.bin"}, True)
    assert ws.tool(args, True)["id"] == saved["id"]
    result = ws.tool({"operation":"metadata", "attachment_id":meta["attachment_id"]}, False)
    assert result["name"] == "renamed.bin" and len(result["sha256"]) == 64
    assert len(list(ws.files_root.rglob("*.bin"))) == 1


def test_attachment_refs_survive_browser_history_reload(tmp_path):
    ws = FileWorkspace(tmp_path)
    staged = ws.stage_messages([{"role":"user", "content":"save", "attachments":[{"id":"browser-id", "name":"x.bin", "data_url":encoded(b"bytes")}]}])
    receipt = staged[0]["_attachment_refs"][0]
    assert receipt["client_id"] == "browser-id"
    restored = ws.stage_messages([{"role":"user", "content":"inspect", "attachments":[receipt]}])
    assert receipt["attachment_id"] in restored[0]["content"]
    assert "failed" not in restored[0]["content"]


def test_invalid_snapshot_does_not_destroy_workspace(tmp_path):
    ws = FileWorkspace(tmp_path)
    row = ws.tool({"operation":"write_text", "name":"keep.txt", "text":"keep"}, True)
    with pytest.raises(ValueError):
        ws.import_snapshot({"index":{"items":{}}, "folders":["../escape"], "files":[]})
    assert ws.tool({"operation":"read_content", "id":row["id"]}, False)["text"] == "keep"


@pytest.mark.parametrize("name", ["data.tar", "code.rs", "unknown.blob", "voice.mp3", "movie.mp4", "image.png", "notes.yaml", "doc.pdf", "report.xlsx", "slides.pptx"])
def test_any_attachment_metadata(tmp_path, name):
    ws = FileWorkspace(tmp_path)
    meta = ws.stage_attachment({"name":name, "data_url":encoded(b"opaque bytes")})
    result = ws.tool({"operation":"metadata", "attachment_id":meta["attachment_id"]}, False)
    assert result["name"] == name and result["size"] == 12
    assert result["mime"] and result["extension"] and len(result["sha256"]) == 64


@pytest.mark.parametrize("folder", ["../escape", "/absolute", "C:\\outside", "a/../../b"])
def test_traversal_rejected(tmp_path, folder):
    with pytest.raises(ValueError):
        FileWorkspace(tmp_path).upload_data(name="x.txt", folder=folder, text="x")


def test_symlink_escape_and_read_only_list(tmp_path):
    outside = tmp_path / "outside"; outside.mkdir()
    ws = FileWorkspace(tmp_path / "ws")
    (ws.files_root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ws.tool({"operation":"write_text", "folder":"link", "name":"x.txt", "text":"no"}, True)
    with pytest.raises(ValueError):
        ws.tool({"operation":"list", "folder":"absent"}, False)
    assert not (ws.files_root / "absent").exists() and not (outside / "x.txt").exists()


def test_archive_probe_selective_read_and_bomb(tmp_path):
    ws = FileWorkspace(tmp_path); b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("src/main.py", "print('safe')"); z.writestr("other.txt", "DO_NOT_READ")
        z.writestr("../escape.py", "UNSAFE"); z.writestr("bomb.txt", "x" * 2_000_000)
    row = ws.upload_data(name="code.zip", data_url=encoded(b.getvalue()))
    probe = ws.tool({"operation":"probe", "id":row["id"]}, False)
    assert "DO_NOT_READ" not in str(probe) and any(not e["safe"] for e in probe["archive_entries"])
    read = ws.tool({"operation":"read_content", "id":row["id"], "members":["src/main.py"]}, False)
    assert "safe" in read["text_preview"] and "DO_NOT_READ" not in read["text_preview"]
    for member in ("../escape.py", "bomb.txt"):
        with pytest.raises(ValueError): ws.tool({"operation":"read_content", "id":row["id"], "members":[member]}, False)


def test_calendar_date_clock_composition_and_validation(tmp_path):
    cal = CalendarStore(tmp_path)
    event = cal.tool({"operation":"create", "title":"جلسه", "jalali":"1405-07-02", "start":"10:00"}, True)
    assert event["start"].startswith("2026-09-24T10:00")
    event2 = cal.tool({"operation":"create", "title":"Tomorrow", "relative_date":"tomorrow", "time":"10:00"}, True)
    assert datetime.fromisoformat(event2["start"]).date() == (datetime.now().astimezone() + timedelta(days=1)).date()
    with pytest.raises(ValueError, match="date"): cal.tool({"operation":"create", "title":"Ambiguous", "start":"10:00"}, True)
    with pytest.raises(ValueError): cal.convert(jalali="1405-07-31")
    with pytest.raises(ValueError): cal.tool({"operation":"update", "id":event["id"], "end":"2026-09-23T10:00"}, True)


def test_large_vision_result_valid_json(runtime):
    runtime.vision_available = True
    meta = runtime.workspace.stage_attachment({"name":"image.png", "kind":"image", "data_url":encoded(b"x" * 20000)})
    result = json.loads(runtime.execute("workspace_files", {"operation":"read_content", "attachment_id":meta["attachment_id"]}, AgentPermissions()))
    assert len(result["result"]["vision_attachment"]["data_url"]) > 18000


def test_router_families_avoid_second_inference(runtime):
    calls = []
    def model(messages, tools):
        p = messages[0]["content"]; calls.append(p)
        if "stage 0" in p: return {"content":'{"route":"skills","families":["calendar"],"goal":"clock"}'}
        if "STEP 1 OF" in p: return {"content":'{"action":"tool","skill":"calendar","arguments":{"operation":"now"}}'}
        return {"content":'{"action":"final","answer":"Time checked."}'}
    list(runtime.run([{"role":"user","content":"What time is it?"}], model, AgentPermissions()))
    assert len(calls) == 3 and not any("stage 1" in p for p in calls)


def test_write_receipt_prevents_duplicate_events(runtime):
    def model(messages, tools):
        p = messages[0]["content"]
        if "stage 0" in p: return {"content":'{"route":"skills","families":["calendar"]}'}
        if "STEP 3 OF" in p: return {"content":'{"action":"final","answer":"Created."}'}
        return {"content":json.dumps({"action":"tool","skill":"calendar","arguments":{
            "operation":"create","title":"meeting","start":"2026-09-25T10:00"}})}
    events = list(runtime.run([{"role":"user","content":"Create a meeting tomorrow"}], model, AgentPermissions(allow_write=False)))
    assert len(runtime.calendar.snapshot()["events"]) == 1
    assert any(e.get("policy") == "reuse_write_receipt" for e in events)


def test_parallel_write_is_rejected(runtime):
    def model(messages, tools):
        if "stage 0" in messages[0]["content"]: return {"content":'{"route":"skills","families":["files"]}'}
        return {"content":json.dumps({"action":"parallel","actions":[
            {"skill":"workspace_files","arguments":{"operation":"write_text","name":"a.txt","text":"x"}},
            {"skill":"workspace_files","arguments":{"operation":"write_text","name":"b.txt","text":"y"}}]})}
    events = list(runtime.run([{"role":"user","content":"Create two files"}], model, AgentPermissions(), max_steps=1, stream_final=lambda _:iter([])))
    assert any(e.get("event") == "parallel_rejected" for e in events)
    assert runtime.workspace.list()["items"] == []


def test_cancel_stream_closes_source(runtime):
    cancel = threading.Event(); closed = []
    def stream(messages):
        try:
            yield {"type":"text","delta":"first"}
            cancel.set()
            yield {"type":"text","delta":"must not arrive"}
        finally: closed.append(True)
    iterator = runtime.run([{"role":"user","content":"Hello!"}], lambda *_:pytest.fail("no router needed"),
                           AgentPermissions(), stream_final=stream, cancel=cancel)
    deltas = []
    with pytest.raises(RuntimeError, match="cancel"):
        for ev in iterator:
            if ev.get("type") == "text": deltas.append(ev["delta"])
    assert deltas == ["first"] and closed == [True]


def test_invalid_planner_final_uses_stream(runtime):
    def model(messages, tools):
        p = messages[0]["content"]
        if "stage 0" in p: return {"content":'{"route":"skills","families":["calendar"]}'}
        if "STEP 1 OF" in p: return {"content":'{"action":"tool","skill":"calendar","arguments":{"operation":"now"}}'}
        if "Answer the user's request using only" in p: pytest.fail("must stream fallback")
        return {"content":"invalid"}
    events = list(runtime.run([{"role":"user","content":"What time is it?"}], model, AgentPermissions(),
        stream_final=lambda _:iter([{"type":"text","delta":"Evidence received."}])))
    assert any(e.get("delta") == "Evidence received." for e in events)


def test_office_archive_bomb_and_xlsx_shared_strings(tmp_path):
    ws = FileWorkspace(tmp_path); b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/sharedStrings.xml", '<sst><si><t>Revenue</t></si><si><t>Name</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row><c t="s"><v>1</v></c><c t="s"><v>0</v></c></row><row><c t="inlineStr"><is><t>Acme</t></is></c><c><v>42</v></c></row></sheetData></worksheet>')
    row = ws.upload_data(name="data.xlsx", data_url=encoded(b.getvalue()))
    text = ws.tool({"operation":"read_content", "id":row["id"]}, False)["text"]
    assert "Name\tRevenue" in text and "Acme\t42" in text
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z: z.writestr("word/document.xml", "x" * 6_000_000)
    row = ws.upload_data(name="bomb.docx", data_url=encoded(b.getvalue()))
    with pytest.raises(ValueError): ws.tool({"operation":"read_content", "id":row["id"]}, False)


def test_tar_probe_blocks_links(tmp_path):
    import tarfile
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        info = tarfile.TarInfo("safe.txt"); info.size = 5; t.addfile(info, io.BytesIO(b"hello"))
        info = tarfile.TarInfo("link"); info.type = tarfile.SYMTYPE; info.linkname = "../outside"; t.addfile(info)
    ws = FileWorkspace(tmp_path); row = ws.upload_data(name="data.tar.gz", data_url=encoded(b.getvalue()))
    probe = ws.tool({"operation":"probe", "id":row["id"]}, False)
    assert probe["read_capability"] == "archive"
    assert not next(x for x in probe["archive_entries"] if x["name"] == "link")["safe"]
