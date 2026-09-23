from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _browser_candidates():
    if os.name == "nt":
        env = os.environ
        roots = [env.get("PROGRAMFILES(X86)", ""), env.get("PROGRAMFILES", ""), env.get("LOCALAPPDATA", "")]
        rels = [
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Chromium", "Application", "chrome.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for rel in rels:
                p = Path(root).joinpath(*rel)
                if p.is_file():
                    yield str(p)
        for name in ("msedge", "chrome", "chromium"):
            p = shutil.which(name)
            if p:
                yield p
    elif sys.platform == "darwin":
        for p in (
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            if Path(p).is_file(): yield p
    else:
        for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge", "microsoft-edge-stable"):
            p = shutil.which(name)
            if p: yield p


def open_app_window(url: str):
    profile = Path.home() / ".llamaforge" / "ui-profile"
    profile.mkdir(parents=True, exist_ok=True)
    for browser in _browser_candidates():
        try:
            args = [
                browser, f"--app={url}", f"--user-data-dir={profile}",
                "--disable-features=TranslateUI", "--disable-background-mode",
                "--no-first-run", "--no-default-browser-check",
            ]
            if os.name == "nt":
                args += ["--start-maximized"]
            return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            continue
    webbrowser.open(url, new=1, autoraise=True)
    return None


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args, _ = parser.parse_known_args()

    from llamaforge.web.server import create_server
    server, state, url = create_server(port=args.port or None)
    if args.worker:
        state.cluster.set_role("worker")
        args.no_browser = True
    print(f"[LlamaForge] Local UI: {url}")
    print("[LlamaForge] No pip packages are required. Press Ctrl+C to stop.")

    browser_proc = None
    if not args.no_browser:
        browser_proc = open_app_window(url)

    # When launched without a console (the normal Windows path), stop the local
    # control plane after the UI has been closed. The frontend receives state changes over /api/events,
    # so loss of all local API traffic is a reliable lightweight heartbeat.
    def idle_watchdog():
        while not state.shutting_down:
            time.sleep(2)
            timeout = max(6, int(getattr(state.cfg, "ui_disconnect_shutdown_seconds", 12) or 12))
            if state.client_seen and time.monotonic() - state.last_client_at > timeout:
                if not state.cfg.exit_unloads_model:
                    # Explicit background mode: keep the control plane alive so
                    # the resident model is still owned and can be reattached.
                    time.sleep(timeout)
                    continue
                try:
                    state.unload_model(reason="UI closed/disconnected")
                    state.shutting_down = True
                    server.shutdown()
                except Exception:
                    pass
                return
    if not args.worker:
        threading.Thread(target=idle_watchdog, name="ui-heartbeat", daemon=True).start()

    if browser_proc is not None:
        def watch_browser():
            try:
                browser_proc.wait()
                # Dedicated app-mode profile means this process belongs to this
                # LlamaForge window. Closing the window should release the model.
                time.sleep(0.25)
                if state.cfg.exit_unloads_model and not state.shutting_down:
                    state.unload_model(reason="app window closed")
                    state.shutting_down = True
                    server.shutdown()
            except Exception:
                pass
        threading.Thread(target=watch_browser, daemon=True).start()

    try:
        server.serve_forever(poll_interval=.4)
    except KeyboardInterrupt:
        pass
    finally:
        state.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
