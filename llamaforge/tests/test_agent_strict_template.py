from llamaforge.core.net import strict_alternating_messages


def test_strict_alternating_messages_flattens_agent_roles():
    src = [
        {"role": "system", "content": "Use tools safely."},
        {"role": "user", "content": "Open the site"},
        {"role": "user", "content": "prefetched evidence"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "web_read", "arguments": '{"url":"https://example.com"}'}}
        ]},
        {"role": "tool", "tool_call_id": "abc", "content": '{"ok":true}'},
        {"role": "tool", "tool_call_id": "def", "content": '{"ok":true,"two":2}'},
    ]
    out = strict_alternating_messages(src)
    assert out[0]["role"] == "user"
    assert "SYSTEM INSTRUCTIONS" in out[0]["content"]
    assert "prefetched evidence" in out[0]["content"]
    assert all(m["role"] in {"user", "assistant"} for m in out)
    assert all(out[i]["role"] != out[i - 1]["role"] for i in range(1, len(out)))
    assert "TOOL_RESULT" in out[-1]["content"]
