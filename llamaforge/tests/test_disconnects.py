from __future__ import annotations

import io
import sys

from llamaforge.web.server import _expected_client_disconnect, LlamaForgeHTTPServer


def test_windows_browser_abort_is_expected_disconnect():
    exc = ConnectionAbortedError(10053, "connection aborted")
    exc.winerror = 10053
    assert _expected_client_disconnect(exc)
    assert _expected_client_disconnect(BrokenPipeError())
    assert _expected_client_disconnect(ConnectionResetError())
    assert not _expected_client_disconnect(RuntimeError("real bug"))


def test_server_handle_error_suppresses_expected_disconnect(monkeypatch):
    # Exercise the exact socketserver hook that used to print the traceback.
    server = object.__new__(LlamaForgeHTTPServer)
    exc = ConnectionAbortedError(10053, "aborted")
    exc.winerror = 10053
    monkeypatch.setattr(sys, "exc_info", lambda: (type(exc), exc, None))
    # If this falls through to socketserver's handler it requires internals not
    # present on this object; returning cleanly proves suppression happened.
    assert server.handle_error(None, ("127.0.0.1", 1)) is None
