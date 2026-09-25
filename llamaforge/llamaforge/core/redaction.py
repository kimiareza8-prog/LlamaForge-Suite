"""Redact credential fields at logging/progress boundaries, not execution inputs."""
import re

_KEY = re.compile(r"(?:^|[_-])(?:token|secret|password|authorization|cookie|api[_-]?key)(?:$|[_-])", re.I)
_PAIR = re.compile(r'''(?i)(\b[\w-]*(?:token|secret|password|api[_-]?key|authorization|cookie)[\w-]*["']?\s*[:=]\s*["']?)([^\s&"',;}]+)''')


def redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if _KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list): return [redact(x) for x in value]
    if not isinstance(value, str): return value
    value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_+/=.-]+", r"\1 [redacted]", value)
    value = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", value)
    return _PAIR.sub(r"\1[redacted]", value)
