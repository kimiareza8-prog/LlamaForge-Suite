import urllib.request


def test_core_direct_opener_ignores_system_proxy(monkeypatch):
    from llamaforge.core.network_policy import direct_opener
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'http':'http://127.0.0.1:9999','https':'http://127.0.0.1:9999'})
    opener=direct_opener()
    proxy_handlers=[h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    # An explicit empty ProxyHandler is optimized out by build_opener; either
    # way there must be no configured proxy handler in the final opener.
    assert not proxy_handlers or all(not getattr(h,'proxies',{}) for h in proxy_handlers)


def test_non_telegram_child_environment_removes_proxy_vars():
    from llamaforge.core.network_policy import direct_subprocess_env
    env=direct_subprocess_env({'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:2','ALL_PROXY':'socks5://127.0.0.1:3','KEEP':'yes'})
    assert env['KEEP']=='yes'
    assert 'HTTP_PROXY' not in env and 'HTTPS_PROXY' not in env and 'ALL_PROXY' not in env
    assert env['NO_PROXY']=='*' and env['no_proxy']=='*'


def test_local_inference_uses_direct_opener_source():
    from pathlib import Path
    source=Path(__file__).parents[1].joinpath('llamaforge/core/net.py').read_text(encoding='utf-8')
    assert 'return direct_opener()' in source


def test_ui_and_agent_browser_disable_system_proxy():
    from pathlib import Path
    root=Path(__file__).parents[1]
    assert '--no-proxy-server' in root.joinpath('run.py').read_text(encoding='utf-8')
    assert '--no-proxy-server' in root.joinpath('llamaforge/core/agent_tools.py').read_text(encoding='utf-8')


def test_local_health_stays_reachable_even_with_broken_proxy_env(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from llamaforge.core.net import get_status

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        def log_message(self, *args):
            pass

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    try:
        assert get_status(f'http://127.0.0.1:{server.server_port}/health',timeout=1.0)==200
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
