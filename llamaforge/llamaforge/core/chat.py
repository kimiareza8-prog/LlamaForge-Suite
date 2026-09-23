from __future__ import annotations

from .net import stream_chat_completion


def chat_completion(host: str, port: int, messages: list[dict], temperature: float = 0.2, timeout: int = 900) -> str:
    return "".join(stream_chat_completion(host, port, messages, temperature=temperature, timeout=timeout))
