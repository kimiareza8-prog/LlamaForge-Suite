import json
import importlib.machinery
import sys
import time
from types import ModuleType
from pathlib import Path
import zipfile

import pytest

from llamaforge.core.agent_tools import AgentPermissions
from llamaforge.core.audio_transcription import LocalVoiceTranscriber
from llamaforge.core.skill_system import SkillRegistry
from llamaforge.core.workspace import CalendarStore, FileWorkspace, _parse_iso
from test_skill_audit import runtime


def test_tool_creation_is_separately_gated_and_declarative(runtime, tmp_path):
    denied = AgentPermissions()
    result = json.loads(runtime.execute("create_tool", {"name":"weather_now","description":"Read current weather","url":"https://api.example.test/weather"}, denied))
    assert result["ok"] is False and "disabled" in result["error"]
    enabled = AgentPermissions(allow_tool_creation=True)
    created = runtime.create_tool({"name":"weather_now","description":"Read current weather","url":"https://api.example.test/weather",
                                   "parameters":{"type":"object","properties":{}},"headers":{"Authorization":"Bearer {env:WEATHER_TOKEN}"}}, enabled)
    assert created["created"] and (tmp_path / "skills" / "weather_now.json").is_file()
    assert any(row["name"] == "weather_now" for row in runtime._load_skills())
    with pytest.raises(ValueError, match="Credential headers"):
        runtime.create_tool({"name":"bad_auth","description":"Bad","url":"https://example.test","headers":{"Authorization":"Bearer literal-secret"}}, enabled)
    with pytest.raises(ValueError, match="Credential headers"):
        runtime.create_tool({"name":"bad_key","description":"Bad","url":"https://example.test","headers":{"X-API-Key":"literal-secret"}}, enabled)


def test_local_command_tools_require_both_toggles_and_run_without_shell(runtime):
    args={"name":"local_echo","kind":"local_command","description":"Print one supplied value",
          "command":[sys.executable,"-c","import sys; print(sys.argv[1])","{arg:value}"],
          "parameters":{"type":"object","properties":{"value":{"type":"string"}}},"required":["value"]}
    with pytest.raises(PermissionError, match="System commands"):
        runtime.create_tool(args, AgentPermissions(allow_tool_creation=True))
    permissions=AgentPermissions(allow_tool_creation=True,allow_system_commands=True)
    created=runtime.create_tool(args,permissions)
    assert created["kind"]=="local_command"
    names={x["function"]["name"] for x in runtime.tool_definitions(permissions)}
    assert "skill_local_echo" in names
    assert "skill_local_echo" not in {x["function"]["name"] for x in runtime.tool_definitions(AgentPermissions())}
    registry=SkillRegistry(runtime,permissions)
    assert registry.validate_call("skill_local_echo",{})[0] is False
    assert registry.validate_call("skill_local_echo",{"value":"ok"})[0] is True
    result=json.loads(runtime.execute("skill_local_echo",{"value":"hello world"},permissions))
    assert result["ok"] and "hello world" in result["result"]["output"]
    assert "shell" in result["result"]["execution"]


def test_tool_creation_requires_local_scope_and_https(runtime):
    permissions = AgentPermissions(allow_tool_creation=True)
    runtime.set_workspace_scope("remote/connected-site")
    with pytest.raises(PermissionError, match="local chat"):
        runtime.create_tool({"name":"remote_tool","description":"No","url":"https://example.test"}, permissions)
    runtime.set_workspace_scope("local")
    with pytest.raises(ValueError, match="HTTPS"):
        runtime.create_tool({"name":"plain_http","description":"No","url":"http://example.test"}, permissions)


def test_system_commands_are_hidden_by_default_and_refuse_remote_tasks(runtime, tmp_path):
    assert "run_command" not in {x["function"]["name"] for x in runtime.tool_definitions(AgentPermissions())}
    assert "run_command" in {x["function"]["name"] for x in runtime.tool_definitions(AgentPermissions(allow_system_commands=True))}
    permissions = AgentPermissions(allow_system_commands=True)
    runtime.set_workspace_scope("remote/site-task")
    blocked = json.loads(runtime.execute("run_command", {"command":[sys.executable,"-c","print('should not run')"]}, permissions))
    assert blocked["ok"] is False and "local chat" in blocked["error"]
    runtime.set_workspace_scope("local")
    done = json.loads(runtime.execute("run_command", {"command":[sys.executable,"-c","print('command works')"]}, permissions))
    assert done["ok"] is True and done["result"]["return_code"] == 0 and "command works" in done["result"]["output"]
    assert "custom" in SkillRegistry.hinted_families("Run this command in CMD")
    assert "custom" in SkillRegistry.hinted_families("ساخت ابزار برای کار تکراری")


def test_file_append_copy_and_calendar_free_time(tmp_path):
    files=FileWorkspace(tmp_path / "files")
    written=files.tool({"operation":"write_text","name":"notes.txt","text":"first"},allow_write=True)
    appended=files.tool({"operation":"append_text","id":written["id"],"text":"\nsecond"},allow_write=True)
    copied=files.tool({"operation":"copy","id":appended["id"]},allow_write=True)
    assert files.tool({"operation":"read_content","id":copied["id"]},allow_write=True)["text"] == "first\nsecond"

    archive=files.files_root/"package.zip"
    with zipfile.ZipFile(archive,"w") as bundle:
        bundle.writestr("docs/readme.txt","safe content")
        bundle.writestr("../outside.txt","blocked")
    archive_row=files._register(archive)
    extracted=files.tool({"operation":"archive_extract","id":archive_row["id"]},allow_write=True)
    assert len(extracted["files"])==1 and extracted["skipped"][0]["reason"]=="unsafe path"
    assert files.tool({"operation":"read_content","id":extracted["files"][0]["id"]},allow_write=True)["text"]=="safe content"

    calendar=CalendarStore(tmp_path / "calendar")
    calendar.write("create",{"title":"Busy","start":"2026-10-01T09:00:00+03:30","end":"2026-10-01T10:00:00+03:30"})
    slots=calendar.tool({"operation":"find_free_time","start":"2026-10-01T08:00:00+03:30","end":"2026-10-01T12:00:00+03:30","duration_minutes":60},allow_write=False)["slots"]
    busy_start,busy_end=_parse_iso("2026-10-01T09:00:00+03:30"),_parse_iso("2026-10-01T10:00:00+03:30")
    assert slots and any(_parse_iso(slot["start"])>=busy_end for slot in slots)
    assert all(not (_parse_iso(slot["start"])<busy_end and _parse_iso(slot["end"])>busy_start) for slot in slots)


def test_free_time_and_month_include_events_after_the_first_five_hundred(tmp_path):
    calendar=CalendarStore(tmp_path / "calendar")
    early={"title":"Early", "start":"2026-10-01T09:00:00", "end":"2026-10-01T09:05:00", "status":"active"}
    late={"title":"Late", "start":"2026-10-01T10:00:00", "end":"2026-10-01T11:00:00", "status":"active"}
    calendar.import_snapshot({"events":[dict(early,id=f"early-{i}") for i in range(500)]+[dict(late,id="late")],"custom_holidays":[]})
    available=calendar.tool({"operation":"find_free_time","start":"2026-10-01T09:00:00",
                             "end":"2026-10-01T12:00:00","duration_minutes":60},allow_write=False)
    assert available["checked_events"]==501
    assert available["slots"] and all(not (_parse_iso(slot["start"])<_parse_iso(late["end"]) and
                                           _parse_iso(slot["end"])>_parse_iso(late["start"])) for slot in available["slots"])
    converted=calendar.convert(gregorian="2026-10-01")
    month=calendar.month(int(converted["jalali"][:4]),int(converted["jalali"][5:7]))
    assert len(month["events"])==501
    agent_month=calendar.tool({"operation":"month","year":month["year"],"month":month["month"]},allow_write=False)
    assert agent_month["total_events"]==501 and agent_month["events_truncated"]
    assert len(agent_month["events"])==500


def test_free_time_accepts_midnight_close_and_quarter_hour_start(tmp_path):
    calendar=CalendarStore(tmp_path / "calendar")
    slots=calendar.tool({"operation":"find_free_time","start":"2026-10-01T09:30:00",
                         "end":"2026-10-01T11:00:00","duration_minutes":30},allow_write=False)["slots"]
    assert _parse_iso(slots[0]["start"])==_parse_iso("2026-10-01T09:30:00")
    late=calendar.tool({"operation":"find_free_time","start":"2026-10-01T23:00:00",
                        "end":"2026-10-02T00:00:00","duration_minutes":30,
                        "workday_start_hour":22,"workday_end_hour":24,"include_weekends":True},allow_write=False)["slots"]
    assert late and _parse_iso(late[0]["start"])==_parse_iso("2026-10-01T23:00:00")


def test_local_voice_status_requires_configured_model_path(tmp_path):
    transcriber=LocalVoiceTranscriber(lambda:"/missing/ffmpeg",lambda:"",tmp_path)
    status=transcriber.status()
    assert not status["ready"] and not status["model_available"]
    with pytest.raises(RuntimeError,match="FFmpeg"):
        transcriber.start(b"audio","voice.webm","audio/webm")


def test_local_voice_transcription_uses_vosk_and_persists_text(tmp_path,monkeypatch):
    import llamaforge.core.audio_transcription as audio
    vosk=ModuleType("vosk");vosk.__spec__=importlib.machinery.ModuleSpec("vosk",loader=None)
    vosk.Model=lambda path:object()
    class Recognizer:
        def __init__(self,*args):pass
        def SetWords(self,value):pass
        def AcceptWaveform(self,chunk):return True
        def Result(self):return json.dumps({"text":"سلام دنیا"})
        def FinalResult(self):return json.dumps({"text":""})
    vosk.KaldiRecognizer=Recognizer
    monkeypatch.setitem(sys.modules,"vosk",vosk)
    model=tmp_path/"model";model.mkdir();(model/"am").write_text("ready")
    ffmpeg=tmp_path/"ffmpeg";ffmpeg.write_text("test executable")
    class Stream:
        def __init__(self):self.done=False
        def read(self,size):
            if self.done:return b""
            self.done=True;return b"pcm"
    class Process:
        def __init__(self):self.stdout=Stream();self.code=0
        def poll(self):return self.code
        def wait(self,timeout=None):return self.code
        def terminate(self):self.code=-15
        def kill(self):self.code=-9
    monkeypatch.setattr(audio.subprocess,"Popen",lambda *a,**k:Process())
    transcriber=LocalVoiceTranscriber(lambda:str(ffmpeg),lambda:str(model),tmp_path/"recordings")
    started=transcriber.start(b"source audio","note.webm","audio/webm")
    deadline=time.monotonic()+3
    job=transcriber.get(started["job"])
    while job["state"] not in {"done","error"} and time.monotonic()<deadline:
        time.sleep(0.02);job=transcriber.get(started["job"])
    assert job["state"]=="done" and job["text"]=="سلام دنیا"
    assert Path(job["transcript_path"]).read_text(encoding="utf-8").strip()=="سلام دنیا"


def test_local_voice_cancel_terminates_ffmpeg_without_waiting_for_more_audio(tmp_path,monkeypatch):
    import llamaforge.core.audio_transcription as audio
    vosk=ModuleType("vosk");vosk.__spec__=importlib.machinery.ModuleSpec("vosk",loader=None)
    vosk.Model=lambda path:object()
    class Recognizer:
        def __init__(self,*args):pass
        def SetWords(self,value):pass
        def AcceptWaveform(self,chunk):return False
        def Result(self):return json.dumps({"text":""})
        def FinalResult(self):return json.dumps({"text":""})
    vosk.KaldiRecognizer=Recognizer;monkeypatch.setitem(sys.modules,"vosk",vosk)
    model=tmp_path/"model";model.mkdir();(model/"am").write_text("ready")
    ffmpeg=tmp_path/"ffmpeg";ffmpeg.write_text("test executable")
    import threading
    done=threading.Event()
    class Stream:
        def read(self,size):done.wait(2);return b""
    class Process:
        def __init__(self):self.stdout=Stream();self.code=None
        def poll(self):return self.code
        def wait(self,timeout=None):
            if not done.wait(timeout):raise TimeoutError()
            return self.code
        def terminate(self):self.code=-15;done.set()
        def kill(self):self.code=-9;done.set()
    monkeypatch.setattr(audio.subprocess,"Popen",lambda *a,**k:Process())
    transcriber=LocalVoiceTranscriber(lambda:str(ffmpeg),lambda:str(model),tmp_path/"recordings")
    started=transcriber.start(b"source audio","note.webm","audio/webm")
    transcriber.cancel(started["job"])
    deadline=time.monotonic()+3;job=transcriber.get(started["job"])
    while job["state"] not in {"cancelled","error","done"} and time.monotonic()<deadline:
        time.sleep(0.02);job=transcriber.get(started["job"])
    assert job["state"]=="cancelled" and done.is_set()


def test_agent_skill_catalog_exposes_expanded_calendar_file_and_telegram_ops(runtime):
    definitions={x["function"]["name"]:x["function"]["parameters"] for x in runtime.tool_definitions(AgentPermissions())}
    assert "find_free_time" in definitions["calendar"]["properties"]["operation"]["enum"]
    assert {"copy","append_text","archive_extract"}.issubset(definitions["workspace_files"]["properties"]["operation"]["enum"])
    ops=set(definitions["telegram"]["properties"]["operation"]["enum"])
    assert {"global_search","list_channels","list_bots","chat_info","participants","forward","edit","pin","download_media","send_file","unpin","react","mark_read"}.issubset(ops)
    registry=SkillRegistry(runtime,AgentPermissions())
    assert registry.validate_call("calendar",{"operation":"find_free_time"})[0] is False
