from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

UA = "LlamaForge/0.33.0-adaptive-engine"


def _opener():
    # Honor normal OS proxy configuration without requiring requests/pip.
    return urllib.request.build_opener()


def request_json(url: str, *, method: str = "GET", data=None, headers=None, timeout: float = 30.0):
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdr.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdr.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with _opener().open(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, json.loads(payload.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def get_status(url: str, timeout: float = 2.0) -> int | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with _opener().open(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200))
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def open_url(req: urllib.request.Request, timeout: float = 120.0):
    return _opener().open(req, timeout=timeout)


def stream_chat_completion(
    host: str,
    port: int,
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    top_p: float = 0.90,
    top_k: int = 40,
    min_p: float = 0.05,
    repeat_penalty: float = 1.08,
    seed: int = -1,
    timeout: int = 900,
) -> Iterator[str]:
    url = f"http://{host}:{port}/v1/chat/completions"
    payload = {
        "model": "local-model",
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "repeat_penalty": repeat_penalty,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": True,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": UA},
    )
    try:
        with _opener().open(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type:
                raw = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                text = data["choices"][0]["message"]["content"]
                if text:
                    yield text
                return
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    obj = json.loads(data)
                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield str(text)
                except json.JSONDecodeError:
                    continue
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1600]
        raise RuntimeError(f"Local server returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError(f"Could not talk to local llama-server: {exc}") from exc



def stream_chat_events(
    host: str,
    port: int,
    messages: list[dict],
    *,
    temperature: float = 0.8,
    max_tokens: int = 2048,
    top_p: float = 0.95,
    top_k: int = 40,
    min_p: float = 0.05,
    repeat_penalty: float = 1.03,
    seed: int = -1,
    reasoning: str = "auto",
    reasoning_budget: int = -1,
    timeout: int = 900,
) -> Iterator[dict]:
    """Structured streaming for the product UI.

    Native llama.cpp reasoning_content is preferred. For models/templates that emit
    <think>...</think> (or <analysis>...</analysis>) in normal content, a small
    streaming parser separates it so hidden reasoning never pollutes the final answer.
    Unknown reasoning fields are retried once without them for older runtimes.
    """
    url = f"http://{host}:{port}/v1/chat/completions"
    base = {
        "model": "local-model",
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "repeat_penalty": repeat_penalty,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": True,
    }
    advanced = dict(base)
    advanced["reasoning_format"] = "auto"
    if reasoning == "off":
        advanced["reasoning_effort"] = "none"
        advanced["chat_template_kwargs"] = {"enable_thinking": False}
    elif reasoning == "on":
        advanced["chat_template_kwargs"] = {"enable_thinking": True}
    if reasoning_budget >= 0:
        advanced["reasoning_budget"] = int(reasoning_budget)

    def open_payload(payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": UA},
        )
        return _opener().open(req, timeout=timeout)

    # Fallback parser state for models that put chain-of-thought tags in content.
    explicit_reasoning = False
    tag_buffer = ""
    in_reasoning_tag = False
    open_tags = ("<think>", "<analysis>")
    close_tags = ("</think>", "</analysis>")
    max_tag = max(map(len, open_tags + close_tags))

    def split_tagged_content(chunk: str, final: bool = False):
        nonlocal tag_buffer, in_reasoning_tag
        tag_buffer += chunk
        emitted = []
        while tag_buffer:
            tags = close_tags if in_reasoning_tag else open_tags
            hits = [(tag_buffer.lower().find(t), t) for t in tags]
            hits = [(i, t) for i, t in hits if i >= 0]
            if hits:
                idx, tag = min(hits, key=lambda x: x[0])
                before = tag_buffer[:idx]
                if before:
                    emitted.append(("reasoning" if in_reasoning_tag else "text", before))
                tag_buffer = tag_buffer[idx + len(tag):]
                in_reasoning_tag = not in_reasoning_tag
                continue
            if final:
                emitted.append(("reasoning" if in_reasoning_tag else "text", tag_buffer))
                tag_buffer = ""
                break
            # Keep a short suffix so a control tag split across SSE chunks can still
            # be recognized. This only delays a handful of characters.
            keep = min(max_tag - 1, len(tag_buffer))
            safe_len = len(tag_buffer) - keep
            if safe_len > 0:
                emitted.append(("reasoning" if in_reasoning_tag else "text", tag_buffer[:safe_len]))
                tag_buffer = tag_buffer[safe_len:]
            break
        return emitted

    try:
        try:
            response = open_payload(advanced)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            if exc.code in (400, 404, 422) and any(x in detail.lower() for x in ("reasoning", "chat_template_kwargs", "unknown field", "invalid")):
                response = open_payload(base)
                yield {"type": "meta", "reasoning_supported": False}
            else:
                raise RuntimeError(f"Local server returned HTTP {exc.code}: {detail or exc.reason}") from exc

        with response:
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type:
                raw = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                reasoning_text = msg.get("reasoning_content") or ""
                content = msg.get("content") or ""
                if reasoning_text:
                    explicit_reasoning = True
                    yield {"type": "reasoning", "delta": str(reasoning_text)}
                if content:
                    if explicit_reasoning:
                        yield {"type": "text", "delta": str(content)}
                    else:
                        for typ, txt in split_tagged_content(str(content), final=True):
                            if txt:
                                yield {"type": typ, "delta": txt}
                usage = data.get("usage")
                if usage:
                    yield {"type": "meta", "usage": usage}
                return

            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                reasoning_text = delta.get("reasoning_content")
                text = delta.get("content")
                if reasoning_text:
                    # If the server starts sending native parsed reasoning, flush any
                    # held normal-content suffix and trust the native channel.
                    if not explicit_reasoning and tag_buffer:
                        for typ, txt in split_tagged_content("", final=True):
                            if txt and typ == "text":
                                yield {"type": "text", "delta": txt}
                    explicit_reasoning = True
                    yield {"type": "reasoning", "delta": str(reasoning_text)}
                if text:
                    if explicit_reasoning:
                        yield {"type": "text", "delta": str(text)}
                    else:
                        for typ, txt in split_tagged_content(str(text), final=False):
                            if txt:
                                yield {"type": typ, "delta": txt}
                if obj.get("usage"):
                    yield {"type": "meta", "usage": obj.get("usage")}
            if not explicit_reasoning and tag_buffer:
                for typ, txt in split_tagged_content("", final=True):
                    if txt:
                        yield {"type": typ, "delta": txt}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1600]
        raise RuntimeError(f"Local server returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError(f"Could not talk to local llama-server: {exc}") from exc

def count_chat_tokens(host: str, port: int, messages: list[dict], timeout: float = 15.0) -> int | None:
    """Ask a recent llama-server to count chat input tokens. Returns None on older builds."""
    url = f"http://{host}:{port}/v1/chat/completions/input_tokens"
    try:
        _status, data = request_json(
            url, method="POST", data={"model": "local-model", "messages": messages}, timeout=timeout
        )
        n = data.get("input_tokens") if isinstance(data, dict) else None
        return int(n) if n is not None else None
    except Exception:
        return None


def apply_chat_template(host: str, port: int, messages: list[dict], timeout: float = 15.0) -> str | None:
    url = f"http://{host}:{port}/apply-template"
    try:
        _status, data = request_json(url, method="POST", data={"messages": messages}, timeout=timeout)
        prompt = data.get("prompt") if isinstance(data, dict) else None
        return str(prompt) if prompt is not None else None
    except Exception:
        return None


def get_server_props(host: str, port: int, timeout: float = 10.0) -> dict:
    for endpoint in ("/props", "/v1/models"):
        try:
            _status, data = request_json(f"http://{host}:{port}{endpoint}", timeout=timeout)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def strict_alternating_messages(messages: list[dict]) -> list[dict]:
    """Return a conservative user/assistant-only transcript for strict Jinja templates.

    Agent/tool histories can contain system, tool, and adjacent same-role messages.
    A number of llama.cpp model templates reject those histories with a Jinja
    "roles must alternate"/"system message must be at the beginning" error.
    For the textual Agent fallback we preserve the information while folding it
    into a plain alternating transcript that virtually every instruct template
    can render.
    """
    system_parts: list[str] = []
    seq: list[dict] = []

    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user").strip().lower()
        content = str(raw.get("content") or "")
        if role == "system":
            if content.strip():
                system_parts.append(content.strip())
            continue
        if role == "tool":
            tool_name = str(raw.get("name") or raw.get("tool_call_id") or "tool")
            content = f"LlamaForge TOOL_RESULT [{tool_name}]:\n{content}"
            role = "user"
        elif role == "assistant":
            calls = raw.get("tool_calls") if isinstance(raw.get("tool_calls"), list) else []
            if calls and not content.strip():
                rendered = []
                for tc in calls[:4]:
                    fn = tc.get("function") if isinstance(tc, dict) and isinstance(tc.get("function"), dict) else {}
                    name = str(fn.get("name") or "tool")
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args or {}, ensure_ascii=False)
                        except Exception:
                            args = "{}"
                    rendered.append(f"Requested tool {name} with arguments {args}")
                content = "\n".join(rendered)
        elif role != "user":
            role = "user"

        if not content.strip():
            continue
        if seq and seq[-1]["role"] == role:
            seq[-1]["content"] = str(seq[-1].get("content") or "") + "\n\n" + content
        else:
            seq.append({"role": role, "content": content})

    system_text = "\n\n".join(system_parts).strip()
    if not seq:
        seq = [{"role": "user", "content": system_text or "Continue."}]
        system_text = ""
    elif seq[0]["role"] != "user":
        seq.insert(0, {"role": "user", "content": system_text or "Continue the conversation using the context below."})
        system_text = ""

    if system_text:
        seq[0]["content"] = f"SYSTEM INSTRUCTIONS:\n{system_text}\n\nUSER REQUEST / CONTEXT:\n{seq[0]['content']}"

    # A final pass guarantees alternation even after the synthetic first user.
    out: list[dict] = []
    for item in seq:
        if out and out[-1]["role"] == item["role"]:
            out[-1]["content"] += "\n\n" + item["content"]
        else:
            out.append(item)
    return out


def chat_completion_with_tools(
    host: str,
    port: int,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    top_p: float = 0.90,
    top_k: int = 40,
    min_p: float = 0.05,
    repeat_penalty: float = 1.08,
    seed: int = -1,
    reasoning: str = "auto",
    reasoning_budget: int = -1,
    timeout: int = 900,
    json_mode: bool = False,
) -> dict:
    """Non-streaming OpenAI-compatible chat call with native function tools.

    Recent llama.cpp builds can return ``message.tool_calls`` for templates that
    support tools. AgentRuntime also has a textual fallback for older or
    incompatible templates, so this helper remains useful when ``tools`` is None.
    """
    url = f"http://{host}:{port}/v1/chat/completions"
    payload = {
        "model": "local-model",
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "repeat_penalty": repeat_penalty,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if json_mode:
        # Supported by current llama.cpp OpenAI-compatible servers. If an older
        # runtime rejects this field we retry without it below.
        payload["response_format"] = {"type": "json_object"}
    payload["reasoning_format"] = "auto"
    if reasoning == "off":
        payload["reasoning_effort"] = "none"
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    elif reasoning == "on":
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    if reasoning_budget >= 0:
        payload["reasoning_budget"] = int(reasoning_budget)

    def perform(data: dict) -> dict:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA},
        )
        with _opener().open(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            obj = json.loads(raw)
        choices = obj.get("choices") or []
        if not choices:
            raise RuntimeError("Local model returned no chat choices")
        msg = choices[0].get("message") or {}
        return {
            "content": msg.get("content") or "",
            "reasoning_content": msg.get("reasoning_content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "finish_reason": choices[0].get("finish_reason"),
            "usage": obj.get("usage") or {},
        }

    try:
        return perform(payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2400]
        # Older runtimes reject reasoning-specific fields. Retry once with the
        # minimal OpenAI-compatible payload while preserving native tools.
        low = detail.lower()
        if any(k in low for k in ("reasoning", "chat_template_kwargs", "response_format", "json_object", "unknown field", "unknown argument")):
            minimal = {k: v for k, v in payload.items() if k not in {"reasoning_format", "reasoning_effort", "reasoning_budget", "chat_template_kwargs", "response_format"}}
            try:
                return perform(minimal)
            except Exception:
                pass

        # Some model Jinja templates are intentionally strict: only alternating
        # user/assistant roles are accepted, and some llama.cpp builds cannot
        # auto-generate a native tool parser for those templates. Native-tool
        # failure is handled by AgentRuntime's textual protocol. Once tools are
        # absent, retry with a loss-minimizing plain alternating transcript.
        template_error = any(k in low for k in (
            "unable to generate parser", "automatic parser generation failed",
            "jinja exception", "roles must alternate", "conversation roles must alternate",
            "system message must be", "chat template", "tool call id",
        ))
        if template_error and not tools:
            strict = strict_alternating_messages(messages)
            strict_payload = dict(payload)
            strict_payload["messages"] = strict
            strict_payload.pop("tools", None)
            strict_payload.pop("tool_choice", None)
            strict_payload = {k: v for k, v in strict_payload.items() if k not in {"reasoning_format", "reasoning_effort", "reasoning_budget", "chat_template_kwargs"}}
            if strict != messages:
                try:
                    return perform(strict_payload)
                except Exception as strict_exc:
                    raise RuntimeError(
                        f"Local server rejected both the model chat template and LlamaForge's strict alternating fallback: {strict_exc}"
                    ) from strict_exc
        raise RuntimeError(f"Local server returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError(f"Could not talk to local llama-server: {exc}") from exc
