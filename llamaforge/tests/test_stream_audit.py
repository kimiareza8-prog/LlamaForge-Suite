import io
import json
import threading
from types import SimpleNamespace

import pytest

from llamaforge.core.net import stream_chat_events
from llamaforge.web import server
from test_reasoning_stream import FakeOpener, FakeResponse


def stream(lines, monkeypatch, **kwargs):
    monkeypatch.setattr("llamaforge.core.net._opener", lambda: FakeOpener(FakeResponse(lines)))
    return stream_chat_events("127.0.0.1", 8080, [{"role":"user", "content":"hello"}], **kwargs)


def test_first_delta_is_not_held_for_ten_more_characters(monkeypatch):
    requested = []
    class Response(FakeResponse):
        def __iter__(self):
            requested.append(1)
            yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n'
            requested.append(2)
            yield b'data: [DONE]\n'
    monkeypatch.setattr("llamaforge.core.net._opener", lambda: FakeOpener(Response([])))
    it = stream_chat_events("localhost", 8080, [])
    assert next(it) == {"type":"text", "delta":"Hi"}
    assert requested == [1]
    it.close()


@pytest.mark.parametrize("tail", ['data: {"error":{"message":"backend failed"}}\n',
                                  'data: broken json\n', ''])
def test_broken_stream_cannot_finish_as_success(monkeypatch, tail):
    lines = ['data: {"choices":[{"delta":{"content":"partial answer"}}]}\n', tail]
    with pytest.raises(RuntimeError):
        list(stream(lines, monkeypatch))


def test_duplicate_text_is_real_output_and_cancel_stops_next_delta(monkeypatch):
    lines = ['data: {"choices":[{"delta":{"content":"ha"}}]}\n'] * 3 + ['data: [DONE]\n']
    cancel = threading.Event()
    it = stream(lines, monkeypatch, cancel=cancel)
    assert next(it)["delta"] == "ha"
    assert next(it)["delta"] == "ha"
    cancel.set()
    with pytest.raises(RuntimeError, match="cancel"):
        next(it)


def test_saving_image_does_not_load_projector():
    state = object.__new__(server.LlamaForgeState)
    state._ensure_vision_runtime = lambda _: pytest.fail("projector loaded just for attachment")
    state.agent_chat_stream = lambda _: iter([{"type":"text", "delta":"saved"}])
    events = list(state.chat_stream({"agent":True, "messages":[{"role":"user", "content":"save image", "attachments":[{"kind":"image"}]}]}))
    assert events[0]["delta"] == "saved"


def test_error_sse_has_one_header_and_final_marker():
    state = SimpleNamespace(log_exception=lambda *_: None)
    def broken(_):
        yield {"type":"text", "delta":"partial"}
        raise RuntimeError("failed")
    state.chat_stream = broken
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(state=state)
    handler.wfile = io.BytesIO()
    handler._read_json = lambda: {}
    headers = []
    handler.send_response = lambda code: headers.append(code)
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    handler._chat_stream()
    assert headers == [200]
    assert handler.wfile.getvalue().endswith(b'data: [DONE]\n\n')
    assert b'"type": "error"' in handler.wfile.getvalue()


@pytest.mark.parametrize("kind", ["vulkan", "memory"])
def test_safe_launch_retry_is_bounded(kind):
    state = object.__new__(server.LlamaForgeState)
    state.shutting_down = False
    state._server_generation = 1
    state.server_proc = SimpleNamespace(running=False, tail_text=lambda _: "Vulkan device lost" if kind == "vulkan" else "out of memory")
    state.active_plan = SimpleNamespace(load_mode="none", cpu_only=kind != "vulkan")
    state.last_launch_payload = {"model_path":"model.gguf"}
    state.events = SimpleNamespace(publish=lambda *_: None)
    state.log = lambda *_: None
    state._diagnose_failure = lambda text: text
    attempts = []
    state.start_server = lambda payload: attempts.append(payload)
    state._watch_server_ready(1)
    assert len(attempts) == 1

    if kind == "vulkan":
        assert attempts[0]["accelerator_mode"] == "cpu"
        state.last_launch_payload["safe_gpu_retry"] = True
    else:
        assert attempts[0]["memory_mode"] == "ssd_test"
        state.last_launch_payload["safe_memory_retry"] = True
    state._watch_server_ready(1)
    assert len(attempts) == 1


def test_event_reconnect_replays_only_missing_revisions():
    broker = server.EventBroker()
    broker.publish("state", {"reason":"one"})
    broker.publish("state", {"reason":"two"})
    q = broker.subscribe(after=1)
    assert q.get_nowait()["revision"] == 2
    assert q.empty()
    broker.publish("state", {"reason":"three"})
    assert q.get_nowait()["revision"] == 3
    assert [x["revision"] for x in broker.poll(1, timeout=0)] == [2, 3]
    broker.unsubscribe(q)


def test_event_history_gap_requests_snapshot_instead_of_silent_loss():
    broker = server.EventBroker()
    for _ in range(300): broker.publish("metrics")
    events = broker.poll(1, timeout=0)
    assert events[0]["type"] == "resync"
