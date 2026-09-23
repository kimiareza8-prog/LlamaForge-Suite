from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from llamaforge.core import agent_tools
from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime, AgentToolError
from llamaforge.core.skill_system import SkillRegistry


def isolated_runtime(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    monkeypatch.setattr(agent_tools, "AGENT_DIR", root)
    monkeypatch.setattr(agent_tools, "CONNECTORS_PATH", root / "connectors.json")
    monkeypatch.setattr(agent_tools, "SKILLS_DIR", root / "skills")
    monkeypatch.setattr(agent_tools, "BROWSER_PROFILE_DIR", root / "browser-profile")
    monkeypatch.setattr(agent_tools, "DOWNLOADS_DIR", root / "downloads")
    monkeypatch.setattr(agent_tools, "_keyring_get", lambda _id: None)
    monkeypatch.setattr(agent_tools, "_keyring_set", lambda _id, _token: False)
    return AgentRuntime()


def test_registry_shortlists_relevant_skills_and_hides_write_without_permission(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "browser_available", lambda: True)
    perms = AgentPermissions()
    reg = SkillRegistry(rt, perms)
    rows = reg.shortlist("Read https://example.com and summarize the page", ["web.read"], limit=8)
    names = [x["name"] for x in rows]
    assert "web_read" in names
    assert "web_check" in names
    assert "browser_click" not in names
    catalog = reg.catalog()
    click = next(x for x in catalog if x["name"] == "browser_click")
    assert click["available"] is False
    assert "permission" in click["unavailable_reason"].lower()


def test_web_check_and_download_file(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "5")
            self.end_headers()
        def do_GET(self):
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        perms = AgentPermissions(allow_private_network=True)
        url = f"http://127.0.0.1:{srv.server_port}/file.txt"
        checked = rt.web_check({"url": url}, perms)
        assert checked["status"] == 200
        assert checked["ok_status"] is True
        downloaded = rt.download_file({"url": url, "filename": "hello.txt", "max_mb": 1}, perms)
        assert downloaded["saved"] is True
        assert (agent_tools.DOWNLOADS_DIR / "hello.txt").read_bytes() == b"hello"
    finally:
        srv.shutdown(); srv.server_close()


def test_custom_skill_v2_supports_json_body_required_args_and_response_select(tmp_path, monkeypatch):
    rt = isolated_runtime(tmp_path, monkeypatch)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n) or b"{}")
            body = json.dumps({"data": {"ticket": {"id": 7, "title": payload.get("title")}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        skill = {
            "version": 2,
            "name": "create_ticket",
            "category": "support",
            "description": "Create a support ticket",
            "parameters": {"title": {"type": "string"}},
            "required": ["title"],
            "request": {
                "method": "POST",
                "url": f"http://127.0.0.1:{srv.server_port}/tickets",
                "json": {"title": "{title}"},
                "timeout": 5,
            },
            "response": {"format": "json", "select": "data.ticket"},
        }
        (agent_tools.SKILLS_DIR / "create_ticket.json").write_text(json.dumps(skill), encoding="utf-8")
        perms = AgentPermissions(allow_private_network=True, allow_write=True)
        with pytest.raises(AgentToolError):
            rt.execute_skill("create_ticket", {}, perms)
        out = rt.execute_skill("create_ticket", {"title": "Broken pipe"}, perms)
        assert out["status"] == 200
        assert out["data"] == {"id": 7, "title": "Broken pipe"}
    finally:
        srv.shutdown(); srv.server_close()
