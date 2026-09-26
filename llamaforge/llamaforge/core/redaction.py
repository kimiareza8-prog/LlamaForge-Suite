"""Credential redaction at observability boundaries; execution inputs stay intact."""
import json
import re

_SAFE = re.compile(r'^(?:(?:max|min|n|num|prompt|completion|total|input|output|cached|reasoning|generation|predicted|evaluated)_tokens?|tokens?_(?:id|count|limit|per_second)|(?:pad|eos|bos|unk|sep)_token_id|token_step|token_cap|reasoning_budget|hf_token_configured)$',re.I)
_KEY = re.compile(r'(?:^|[_-])(?:tokens?|secret|password|passwd|authorization|cookie|api[_-]?key|owner[_-]?key|api[_-]?hash|phone[_-]?code[_-]?hash|session[_-]?string|client[_-]?secret)(?:$|[_-])',re.I)
_PAIR = re.compile(r'''(?i)(\b([\w-]*(?:tokens?|secret|password|passwd|authorization|cookie|api[_-]?key|owner[_-]?key|api[_-]?hash|phone[_-]?code[_-]?hash|session[_-]?string)[\w-]*)["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s&"',;}]+)''')


def secret_key(key):
    key=re.sub(r'([a-z])([A-Z])',r'\1_\2',str(key))
    return not _SAFE.fullmatch(key) and bool(_KEY.search(key))


def redact(value):
    if isinstance(value,dict):return {k:'[redacted]' if secret_key(k) else redact(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [redact(x) for x in value]
    if not isinstance(value,str):return value
    if value.lstrip().startswith(('{','[')):
        try:
            parsed=json.loads(value)
            if isinstance(parsed,(dict,list)):return json.dumps(redact(parsed),ensure_ascii=False)
        except (ValueError,RecursionError):pass
    value=re.sub(r'(?im)^(\s*(?:Cookie|Set-Cookie|Authorization)\s*:\s*).+$',r'\1[redacted]',value)
    value=re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_+/=.-]+',r'\1 [redacted]',value)
    value=re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@',r'\1[redacted]@',value)
    value=re.sub(r'\b(?:hf_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{20,})','[redacted]',value)
    return _PAIR.sub(lambda m:m.group(1)+'[redacted]' if secret_key(m.group(2)) else m.group(0),value)
