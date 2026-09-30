from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


APP_DIR = Path.home() / ".llamaforge"
INSTANCE_LOCK = APP_DIR / "instance.lock"


class SingleInstanceLock:
    """Cross-version process lock for the shared ~/.llamaforge state.

    Every portable LlamaForge build intentionally shares config/runtime/browser
    state. Running two control planes against that state can race config writes
    and can also start two llama-servers on the same inference port. Keep one
    owner at a time across all extracted versions.
    """

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b", buffering=0)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


def _find_running_control_plane() -> dict | None:
    """Detect legacy builds that predate the cross-version lock.

    LlamaForge control planes choose 8765..8795. Query only loopback and require
    the exact /api/ping JSON shape so unrelated local services are ignored.
    """
    for port in range(8765, 8796):
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.12)
            conn.request("GET", "/api/ping", headers={"Connection": "close"})
            response = conn.getresponse()
            raw = response.read(4096)
            if response.status != 200:
                continue
            data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            version = str(data.get("version") or "") if isinstance(data, dict) else ""
            if isinstance(data, dict) and data.get("ok") is True and version.startswith("0."):
                return {"port": port, "version": version}
        except Exception:
            pass
        finally:
            if conn is not None:
                try: conn.close()
                except Exception: pass
    return None


def _already_running_notice(existing: dict | None = None) -> None:
    detail = ""
    if existing:
        detail = f"Detected version {existing.get('version','?')} on local port {existing.get('port','?')}.\n\n"
    message = (
        "Another LlamaForge control plane is still running.\n\n"
        + detail
        + "Close the currently open LlamaForge window and use Exit & unload, "
        + "or allow the previous instance a few seconds to shut down, then start this version again.\n\n"
        + "This safety lock prevents two extracted versions from corrupting shared settings or competing for the model server port."
    )
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "LlamaForge is already running", 0x40)
            return
        except Exception:
            pass
    print(message)


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
    profile = APP_DIR / "ui-profile"
    profile.mkdir(parents=True, exist_ok=True)
    for browser in _browser_candidates():
        try:
            args = [
                browser, f"--app={url}", f"--user-data-dir={profile}",
                "--disable-features=TranslateUI", "--disable-background-mode",
                "--no-first-run", "--no-default-browser-check",
                # Keep the local LlamaForge UI outside Windows/Psiphon proxy.
                "--no-proxy-server",
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

    # Legacy 0.34.x builds do not own instance.lock, so detect their local API
    # before taking the new lock. This stops an old invisible pythonw process from
    # racing shared config/runtime state with this extracted build.
    existing = _find_running_control_plane()
    if existing:
        _already_running_notice(existing)
        return

    instance_lock = SingleInstanceLock(INSTANCE_LOCK)
    if not instance_lock.acquire():
        _already_running_notice()
        return

    server = state = None
    try:
        from llamaforge.web.server import create_server
        server, state, url = create_server(port=args.port or None)
        if args.worker:
            state.cluster.set_role("worker")
            args.no_browser = True
        print(f"[LlamaForge] Local UI: {url}")
        print("[LlamaForge] No pip packages are required for the core app. Press Ctrl+C to stop.")

        browser_proc = None
        if not args.no_browser:
            browser_proc = open_app_window(url)

        # Browser app-mode launchers are not reliable lifetime handles. Edge/Chrome
        # can hand a second --app URL to an already-running process using the same
        # ~/.llamaforge/ui-profile, then let the newly spawned launcher process exit
        # immediately. Older builds interpreted that handoff as "the UI was closed"
        # and killed the backend while the window was visibly open. API heartbeat is
        # the authoritative window-liveness signal instead.
        def idle_watchdog():
            while not state.shutting_down:
                time.sleep(2)
                timeout = max(6, int(getattr(state.cfg, "ui_disconnect_shutdown_seconds", 12) or 12))
                if state.client_seen and time.monotonic() - state.last_client_at > timeout:
                    if not state.cfg.exit_unloads_model:
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
            def watch_browser_launcher():
                try:
                    browser_proc.wait()
                    # Give a browser-process handoff time to open the app window and
                    # make its first API request. Once any client is seen, launcher
                    # process exit must never shut down the control plane.
                    deadline = time.monotonic() + 8.0
                    while not state.shutting_down and not state.client_seen and time.monotonic() < deadline:
                        time.sleep(0.2)
                    if state.client_seen:
                        state.log("[ui] browser launcher exited/handoff detected; API heartbeat owns UI lifetime")
                        return
                    if not state.shutting_down:
                        state.log("[ui] browser launcher exited and no UI connected; shutting down control plane")
                        state.shutting_down = True
                        server.shutdown()
                except Exception:
                    pass
            threading.Thread(target=watch_browser_launcher, name="browser-launcher-watch", daemon=True).start()

        try:
            server.serve_forever(poll_interval=.4)
        except KeyboardInterrupt:
            pass
    finally:
        try:
            if state is not None:
                state.shutdown()
        finally:
            try:
                if server is not None:
                    server.server_close()
            finally:
                instance_lock.release()


if __name__ == "__main__":
    main()
