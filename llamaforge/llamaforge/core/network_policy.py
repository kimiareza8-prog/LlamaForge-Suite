"""Network routing policy for LlamaForge.

Telegram is the only component allowed to opt into the Windows/Psiphon proxy.
Every other LlamaForge HTTP client is intentionally direct so a system proxy
cannot intercept loopback inference traffic or unrelated web/API traffic.
"""
from __future__ import annotations

import os
import urllib.request

_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
)


def direct_opener() -> urllib.request.OpenerDirector:
    """Return an urllib opener that never consults OS/environment proxies."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def direct_urlopen(req, *, timeout: float | None = None):
    opener = direct_opener()
    if timeout is None:
        return opener.open(req)
    return opener.open(req, timeout=timeout)


def direct_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for non-Telegram child processes with proxy variables removed."""
    env = dict(base or os.environ)
    for key in _PROXY_ENV_KEYS:
        env.pop(key, None)
    # Libraries that still consult no_proxy get an explicit all-direct policy.
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env
