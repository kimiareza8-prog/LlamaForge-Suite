from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from llamaforge.core import agent_tools
from llamaforge.core.agent_tools import AgentPermissions, AgentRuntime, AgentToolError, parse_html_document


def _isolated_runtime(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    monkeypatch.setattr(agent_tools, "AGENT_DIR", root)
    monkeypatch.setattr(agent_tools, "CONNECTORS_PATH", root / "connectors.json")
    monkeypatch.setattr(agent_tools, "SKILLS_DIR", root / "skills")
    monkeypatch.setattr(agent_tools, "BROWSER_PROFILE_DIR", root / "browser-profile")
    monkeypatch.setattr(agent_tools, "DOWNLOADS_DIR", root / "downloads")
    monkeypatch.setattr(agent_tools, "_keyring_get", lambda _id: None)
    monkeypatch.setattr(agent_tools, "_keyring_set", lambda _id, _token: False)
    return AgentRuntime()


def test_parse_html_extracts_text_links_and_forms():
    raw = """<html><head><title>Demo</title><script>bad()</script></head><body>
    <h1>Hello world</h1><a href='/next'>Next page</a>
    <form action='/submit' method='post'><input name='q' placeholder='Search'></form></body></html>"""
    out = parse_html_document(raw, "https://example.com/start")
    assert out["title"] == "Demo"
    assert "Hello world" in out["text"]
    assert "bad()" not in out["text"]
    assert out["links"][0]["url"] == "https://example.com/next"
    assert out["forms"][0]["action"] == "https://example.com/submit"
    assert out["forms"][0]["inputs"][0]["name"] == "q"


def test_private_network_is_blocked_by_default(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    with pytest.raises(AgentToolError):
        rt._validate_url("http://127.0.0.1:1234/", AgentPermissions())
    assert rt._validate_url("http://127.0.0.1:1234/", AgentPermissions(allow_private_network=True)).startswith("http://")


def test_openapi_connector_get_and_write_permission(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path == "/openapi.json":
                schema = {
                    "openapi": "3.1.0",
                    "info": {"title": "Demo Bridge"},
                    "servers": [{"url": f"http://127.0.0.1:{self.server.server_port}"}],
                    "paths": {
                        "/ping": {"get": {"operationId": "ping", "summary": "Ping"}},
                        "/reply": {"post": {"operationId": "reply", "summary": "Reply"}},
                    },
                }
                body = json.dumps(schema).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            if self.path.startswith("/ping"):
                body = json.dumps({"pong": True}).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body); return
            self.send_response(404); self.end_headers()
        def do_POST(self):
            if self.path == "/reply":
                n = int(self.headers.get("Content-Length", "0")); data = json.loads(self.rfile.read(n) or b"{}")
                body = json.dumps({"saved": True, "data": data}).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body); return
            self.send_response(404); self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        perms = AgentPermissions(allow_private_network=True)
        connector = rt.add_connector("Demo", f"http://127.0.0.1:{srv.server_port}/openapi.json", "secret", perms)
        assert {o["operation"] for o in connector["operations"]} == {"ping", "reply"}
        read_defs = {x["function"]["name"]: x["function"] for x in rt.tool_definitions(perms)}
        ping_skill = rt._connector_tool_name(connector["id"], "ping")
        reply_skill = rt._connector_tool_name(connector["id"], "reply")
        assert ping_skill in read_defs
        assert reply_skill not in read_defs  # write operation stays hidden until permission is enabled
        got = rt.connector_call({"connector": connector["id"], "operation": "ping"}, perms)
        assert got["body"]["pong"] is True
        virtual = json.loads(rt.execute(ping_skill, {}, perms))
        assert virtual["ok"] is True and virtual["result"]["body"]["pong"] is True
        with pytest.raises(AgentToolError):
            rt.connector_call({"connector": connector["id"], "operation": "reply", "body": {"x": 1}}, perms)
        write = AgentPermissions(allow_private_network=True, allow_write=True)
        write_names = {x["function"]["name"] for x in rt.tool_definitions(write)}
        assert reply_skill in write_names
        saved = rt.connector_call({"connector": connector["id"], "operation": "reply", "body": {"x": 1}}, write)
        assert saved["body"]["saved"] is True
    finally:
        srv.shutdown(); srv.server_close()


def test_agent_loop_model_plans_skill_then_observes_then_finishes(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    order = []

    def fake_execute(name, args, permissions):
        order.append(("tool", name, dict(args)))
        return json.dumps({"ok": True, "result": {"url": args.get("url"), "text": "demo page"}})

    monkeypatch.setattr(rt, "execute", fake_execute)
    calls = []

    def model(messages, tools):
        # Agent v3 performs one model-based capability discovery pass before planning.
        assert tools == []
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            order.append(("model", "route"))
            return {"content": '{"route":"skills","summary":"external operation","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            order.append(("model", "capability"))
            return {"content": '{"goal":"read the site","categories":["web.read"],"needs_write":false}'}
        calls.append(prompt)
        order.append(("model", len(calls)))
        if len(calls) == 1:
            assert "No tool has been used yet" in calls[-1]
            return {"content": json.dumps({
                "action": "tool",
                "summary": "Read the site first",
                "skill": "web_read",
                "arguments": {"url": "https://example.com"},
            })}
        assert "OBSERVATION 1 from web_read" in calls[-1]
        assert "demo page" in calls[-1]
        return {"content": json.dumps({
            "action": "final",
            "summary": "I have the page data",
            "answer": "Finished after reading the website.",
        })}

    events = list(rt.run([{"role": "user", "content": "Open the site"}], model, AgentPermissions(), max_steps=4))
    assert order[0] == ("model", "route")  # model decides direct vs Skills first
    assert order[1] == ("model", "capability")  # then model narrows the Skill family
    assert order[2][0] == "model"  # then local-model planning chooses the concrete Skill
    assert order[3][0] == "tool"
    assert any(e.get("event") == "decision" and e.get("skill") == "web_read" for e in events)
    assert any(e.get("event") == "tool_result" and e.get("tool") == "web_read" and e.get("ok") for e in events)
    assert "Finished after reading the website." == "".join(e.get("delta", "") for e in events if e.get("type") == "text")


def test_agent_url_is_not_prefetched_before_local_model_decides(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    order = []

    def fake_execute(name, args, permissions):
        order.append("tool")
        return json.dumps({"ok": True, "result": {"url": args.get("url"), "text": "real observation"}})

    monkeypatch.setattr(rt, "execute", fake_execute)
    calls = []

    def model(messages, tools):
        assert tools == []
        order.append("model")
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            assert "https://example.com" in prompt
            return {"content": '{"route":"skills","summary":"supplied URL must be fetched","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            assert "https://example.com" in prompt
            return {"content": '{"goal":"read and summarize URL","categories":["web.read"],"needs_write":false}'}
        calls.append(prompt)
        if len(calls) == 1:
            assert "https://example.com" in prompt
            assert "No tool has been used yet" in prompt
            return {"content": '{"action":"tool","summary":"Open the supplied URL","skill":"web_read","arguments":{"url":"https://example.com"}}'}
        assert "real observation" in prompt
        return {"content": '{"action":"final","summary":"Done","answer":"The page says: real observation"}'}

    events = list(rt.run(
        [{"role": "user", "content": "Read https://example.com and summarize it"}],
        model,
        AgentPermissions(),
        max_steps=3,
    ))
    assert order[:2] == ["model", "model"]
    assert "tool" in order
    assert "The page says: real observation" == "".join(e.get("delta", "") for e in events if e.get("type") == "text")


def test_agent_repairs_invalid_control_json_then_executes(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "execute", lambda name, args, permissions: json.dumps({"ok": True, "result": {"text": "ok"}}))
    calls = []

    def model(messages, tools):
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"skills","summary":"needs URL access","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"read URL","categories":["web.read"],"needs_write":false}'}
        calls.append(prompt)
        if len(calls) == 1:
            return {"content": "I should probably open the website first."}
        if len(calls) == 2:
            assert "not valid JSON" in calls[-1]
            return {"content": '{"action":"tool","summary":"Read URL","skill":"web_read","arguments":{"url":"https://example.com"}}'}
        return {"content": '{"action":"final","summary":"Done","answer":"ok"}'}

    events = list(rt.run([{"role": "user", "content": "Read https://example.com"}], model, AgentPermissions(), max_steps=3))
    assert len(calls) >= 3
    assert any(e.get("event") == "tool_start" and e.get("tool") == "web_read" for e in events)
    assert "ok" == "".join(e.get("delta", "") for e in events if e.get("type") == "text")


def test_agent_generic_read_then_post_flow(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    executed = []

    def fake_execute(name, args, permissions):
        executed.append((name, dict(args)))
        if name == "web_read":
            return json.dumps({"ok": True, "result": {"text": '{"pending":true,"reply_url":"https://example.com/reply","message_id":"m1"}'}})
        if name == "http_request":
            return json.dumps({"ok": True, "result": {"status": 200, "body": {"saved": True}}})
        return json.dumps({"ok": False, "error": "unexpected"})

    monkeypatch.setattr(rt, "execute", fake_execute)
    calls = 0

    def model(messages, tools):
        nonlocal calls
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"skills","summary":"external read and POST required","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"read inbox and post reply","categories":["web.read","api"],"needs_write":true}'}
        calls += 1
        if calls == 1:
            return {"content": '{"action":"tool","summary":"Read inbox","skill":"web_read","arguments":{"url":"https://example.com/inbox"}}'}
        if calls == 2:
            assert "reply_url" in prompt and "message_id" in prompt
            return {"content": '{"action":"tool","summary":"Post reply","skill":"http_request","arguments":{"method":"POST","url":"https://example.com/reply","json":{"message_id":"m1","answer":"hello"}}}'}
        assert '"saved":true' in prompt
        return {"content": '{"action":"final","summary":"Reply saved","answer":"Done"}'}

    events = list(rt.run(
        [{"role": "user", "content": "Open https://example.com/inbox, read it, and POST the needed reply."}],
        model,
        AgentPermissions(allow_write=True),
        max_steps=5,
    ))
    assert [x[0] for x in executed] == ["web_read", "http_request"]
    assert executed[1][1]["method"] == "POST"
    assert "Done" == "".join(e.get("delta", "") for e in events if e.get("type") == "text")


def test_agent_runtime_policy_uses_web_read_before_browser_for_simple_url(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    executed = []
    monkeypatch.setattr(rt, "browser_available", lambda: True)

    def fake_execute(name, args, permissions):
        executed.append((name, dict(args)))
        if name in {"web_read", "web_check"}:
            return json.dumps({"ok": True, "result": {"status": 200, "text": "page reachable"}})
        return json.dumps({"ok": False, "error": "unexpected browser call"})

    monkeypatch.setattr(rt, "execute", fake_execute)
    calls = 0

    def model(messages, tools):
        nonlocal calls
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"skills","summary":"needs site access","confidence":98}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"check site reachability","categories":["web.read"],"needs_write":false}'}
        calls += 1
        if calls == 1:
            # Simulate a small model making the same poor choice shown in the screenshot.
            return {"content": '{"action":"tool","summary":"Open in browser","skill":"browser_open","arguments":{"url":"https://example.com"}}'}
        return {"content": '{"action":"final","summary":"Checked","answer":"It is reachable."}'}

    events = list(rt.run(
        [{"role": "user", "content": "Does https://example.com open?"}],
        model,
        AgentPermissions(),
        max_steps=3,
    ))
    assert executed[0][0] == "web_check"
    assert not any(name == "browser_open" for name, _ in executed)
    assert any(e.get("event") == "policy" and e.get("policy") == "lightweight_web_before_browser" for e in events)


def test_agent_blocks_repeating_identical_failed_tool_call(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    executed = []

    def fake_execute(name, args, permissions):
        executed.append((name, dict(args)))
        return json.dumps({"ok": False, "error": "HTTP 403"})

    monkeypatch.setattr(rt, "execute", fake_execute)
    calls = 0

    def model(messages, tools):
        nonlocal calls
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"skills","summary":"needs URL access","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"read URL","categories":["web.read"],"needs_write":false}'}
        calls += 1
        if calls <= 2:
            return {"content": '{"action":"tool","summary":"Read it","skill":"web_read","arguments":{"url":"https://example.com"}}'}
        return {"content": '{"action":"final","summary":"Failed","answer":"The site returned HTTP 403."}'}

    events = list(rt.run(
        [{"role": "user", "content": "Read https://example.com"}],
        model,
        AgentPermissions(),
        max_steps=4,
    ))
    assert len(executed) == 1
    assert any(e.get("event") == "policy" and e.get("policy") == "block_repeated_failed_call" for e in events)


def test_browser_tools_are_hidden_when_browser_skill_is_unavailable(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "browser_available", lambda: False)
    names = {x["function"]["name"] for x in rt.tool_definitions(AgentPermissions(allow_write=True))}
    assert "web_read" in names
    assert "browser_open" not in names
    assert "browser_click" not in names


def test_agent_direct_route_skips_skills_for_simple_greeting(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "execute", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tool must not run")))
    calls = []

    def model(messages, tools):
        calls.append(messages)
        assert tools == []
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"direct","summary":"simple greeting","confidence":99}'}
        return {"content": "سلام، چطور می‌تونم کمک کنم؟"}

    events = list(rt.run([{"role":"user","content":"سلام"}], model, AgentPermissions(), max_steps=4, context_limit=8192))
    assert len(calls) == 2
    assert any(e.get("event") == "route_decision" and e.get("route") == "direct" for e in events)
    assert any(e.get("event") == "route" and e.get("route") == "direct" for e in events)
    assert not any(e.get("event") == "tool_start" for e in events)
    assert "سلام" in "".join(e.get("delta", "") for e in events if e.get("type") == "text")


def test_agent_direct_route_corrects_english_answer_back_to_persian(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "execute", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tool must not run")))
    calls = []

    def model(messages, tools):
        assert tools == []
        prompt = messages[0]["content"]
        calls.append(prompt)
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"direct","summary":"simple greeting","confidence":99}'}
        if "Write the final answer for the end user in Persian" in prompt:
            return {"content": "سلام، چطور می‌تونم کمک کنم؟"}
        return {"content": "Hello! How can I help?"}

    events = list(rt.run([{"role":"user","content":"سلام"}], model, AgentPermissions(), max_steps=4))
    answer = "".join(e.get("delta", "") for e in events if e.get("type") == "text")
    assert answer.startswith("سلام")
    assert len(calls) == 3
    assert any(e.get("event") == "direct_complete" and e.get("language_corrected") for e in events)


def test_agent_final_language_returns_to_persian_after_english_control(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "execute", lambda name, args, permissions: json.dumps({"ok": True, "result": {"status": 200, "text": "Example Domain"}}))
    calls = []

    def model(messages, tools):
        prompt = messages[0]["content"]
        calls.append(prompt)
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"skills","summary":"must read supplied URL","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"read site","categories":["web.read"],"needs_write":false}'}
        if "STEP 1 OF" in prompt:
            return {"content": '{"action":"tool","summary":"Read page","skill":"web_read","arguments":{"url":"https://example.com"}}'}
        if "STEP 2 OF" in prompt:
            return {"content": '{"action":"final","summary":"Done","answer":"The site is reachable and shows Example Domain."}'}
        if "Write the final answer for the end user in Persian" in prompt:
            return {"content": "سایت در دسترس است و عنوان صفحه Example Domain است."}
        raise AssertionError(prompt[:300])

    events = list(rt.run([{"role":"user","content":"این سایت را بخوان و بگو چیست https://example.com"}], model, AgentPermissions(), max_steps=4, context_limit=8192))
    answer = "".join(e.get("delta", "") for e in events if e.get("type") == "text")
    assert "سایت" in answer
    assert any(e.get("event") == "context_policy" and e.get("observation_keep") == 3 for e in events)


def test_agent_context_policy_scales_down_for_8k():
    from llamaforge.core.agent_engine import AgentEngine
    p = AgentEngine._context_policy(8192)
    assert p["skill_limit"] <= 6
    assert p["observation_keep"] <= 3
    assert p["observation_chars"] <= 3000


def test_agent_does_not_use_skills_for_explanatory_api_question(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    called = 0
    def model(messages, tools):
        nonlocal called
        called += 1
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"direct","summary":"explanatory question only","confidence":97}'}
        return {"content":"API یک رابط برای ارتباط نرم‌افزارهاست."}
    events = list(rt.run([{"role":"user","content":"API چیست؟"}], model, AgentPermissions(), max_steps=3))
    assert called == 2
    assert any(e.get("event") == "route" and e.get("route") == "direct" for e in events)
    assert not any(e.get("event") == "tool_start" for e in events)


def test_model_router_sends_operational_web_request_to_skills_even_when_old_regex_would_miss(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    executed = []
    monkeypatch.setattr(rt, "execute", lambda name, args, permissions: executed.append((name, dict(args))) or json.dumps({"ok": True, "result": {"status": 200, "text": "ok"}}))

    def model(messages, tools):
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"skills","summary":"user asked to inspect an external site","confidence":99}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"inspect the site","categories":["web.read"],"needs_write":false}'}
        if "STEP 1 OF" in prompt:
            return {"content": '{"action":"tool","summary":"Read site","skill":"web_read","arguments":{"url":"https://example.com"}}'}
        return {"content": '{"action":"final","summary":"Done","answer":"Done"}'}

    events = list(rt.run([{"role":"user","content":"این آدرس رو یه نگاه بنداز https://example.com"}], model, AgentPermissions(), max_steps=3))
    assert executed and executed[0][0] == "web_read"
    assert any(e.get("event") == "route_decision" and e.get("route") == "skills" for e in events)


def test_router_repair_keeps_decision_model_driven(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    calls = 0
    monkeypatch.setattr(rt, "execute", lambda name, args, permissions: json.dumps({"ok": True, "result": {"status": 200}}))

    def model(messages, tools):
        nonlocal calls
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            calls += 1
            return {"content": "I think tools are needed"}
        if "previous router output was invalid" in prompt:
            calls += 1
            return {"content": '{"route":"skills","summary":"needs external access","confidence":90}'}
        if "stage 1 of a local agent" in prompt:
            return {"content": '{"goal":"check URL","categories":["web.read"],"needs_write":false}'}
        if "STEP 1 OF" in prompt:
            return {"content": '{"action":"tool","summary":"Check","skill":"web_check","arguments":{"url":"https://example.com"}}'}
        return {"content": '{"action":"final","summary":"Done","answer":"ok"}'}

    events = list(rt.run([{"role":"user","content":"https://example.com رو چک کن"}], model, AgentPermissions(), max_steps=3))
    assert calls == 2
    assert any(e.get("event") == "route_decision" and e.get("route") == "skills" for e in events)


def test_agent_runtime_direct_route_forwards_true_streaming_final(tmp_path, monkeypatch):
    rt = _isolated_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(rt, "execute", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tool must not run")))
    model_calls = []
    stream_calls = []

    def model(messages, tools):
        model_calls.append(messages)
        prompt = messages[0]["content"]
        if "stage 0 of a local AI agent router" in prompt:
            return {"content": '{"route":"direct","summary":"simple","confidence":99}'}
        raise AssertionError("non-stream final model call should not happen")

    def stream_final(messages):
        stream_calls.append(messages)
        yield {"type": "text", "delta": "سلام"}
        yield {"type": "text", "delta": " دنیا"}

    events = list(rt.run(
        [{"role": "user", "content": "سلام"}], model, AgentPermissions(),
        max_steps=3, context_limit=8192, stream_final=stream_final,
    ))
    answer = "".join(e.get("delta", "") for e in events if e.get("type") == "text")
    assert answer == "سلام دنیا"
    assert len(stream_calls) == 1
    assert len(model_calls) == 1  # routing only; final answer came from the live stream
    assert any(e.get("event") == "direct_complete" and e.get("streamed") is True for e in events)
