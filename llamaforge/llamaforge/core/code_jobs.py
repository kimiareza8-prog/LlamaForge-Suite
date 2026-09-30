"""Local, permission-gated programs owned by the Agent.

The virtual environment separates packages from LlamaForge, not OS privileges.
Each process is started without a shell and only its own process group is stopped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import venv

from .config import APP_DIR

ROOT = APP_DIR / "agent" / "code-jobs"
JOB_ID = re.compile(r"^job_[0-9a-f]{16}$")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:==|~=|>=|<=|!=|>|<)\s*[A-Za-z0-9.*+_-]+(?:\s*,\s*(?:==|~=|>=|<=|!=|>|<)\s*[A-Za-z0-9.*+_-]+)*)?$")
MAX_LOG = 2_000_000


class CodeJobs:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: dict[str, subprocess.Popen] = {}
        self._inputs: dict[str, queue.Queue] = {}
        # A previous server cannot own a process just because a PID is present on disk.
        for row in self.root.glob("job_*/state.json"):
            try:
                state = json.loads(row.read_text(encoding="utf-8"))
                if state.get("status") == "running":
                    state.update(status="interrupted", exit_code=None, ended_at=time.time())
                    self._save(row.parent, state)
            except (OSError, ValueError):
                continue

    @staticmethod
    def _save(folder: Path, state: dict) -> None:
        temp = folder / "state.json.tmp"
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, folder / "state.json")

    def _job(self, job_id: str) -> tuple[Path, dict]:
        if not JOB_ID.fullmatch(str(job_id or "")):
            raise ValueError("Invalid job_id")
        folder = self.root / job_id
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Unknown job_id") from exc
        return folder, state

    @staticmethod
    def _file(folder: Path, value: str) -> Path:
        name = str(value or "")
        if not name or "\x00" in name or "\\" in name or Path(name).is_absolute() or any(p in {".", "..", ""} for p in name.split("/")):
            raise ValueError("Path must be a relative file path inside this job")
        target = (folder / name).resolve()
        if not target.is_relative_to(folder.resolve()) or target == folder:
            raise ValueError("Path escapes this job")
        return target

    @staticmethod
    def _python(folder: Path) -> Path:
        return folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _environment(self, folder: Path) -> Path:
        python = self._python(folder)
        if not python.is_file():
            venv.EnvBuilder(with_pip=True).create(str(folder / ".venv"))
        return python

    def _refresh(self, folder: Path, state: dict) -> dict:
        proc = self._active.get(state["job_id"])
        if proc is None and state.get("status") == "running":
            return json.loads((folder / "state.json").read_text(encoding="utf-8"))
        if proc is not None and proc.poll() is not None:
            state.update(status="finished" if proc.returncode == 0 else "failed", exit_code=proc.returncode, ended_at=time.time())
            self._save(folder, state)
            self._active.pop(state["job_id"], None)
        return state

    @staticmethod
    def _tail(folder: Path, max_chars: int = 8000) -> str:
        path = folder / "output.log"
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - max_chars * 4))
            return handle.read().decode("utf-8", "replace")[-max_chars:]

    def _record_output(self, folder: Path, proc: subprocess.Popen) -> None:
        # Drain even after reaching the cap; otherwise a child with verbose output blocks.
        with (folder / "output.log").open("ab") as output:
            try:
                while True:
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    if output.tell() < MAX_LOG:
                        output.write(chunk[:MAX_LOG - output.tell()])
                        output.flush()
            finally:
                proc.stdout.close()
                proc.wait()
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                self._inputs.pop(folder.name, None)
                with self._lock:
                    try:
                        folder_now, state = self._job(folder.name)
                        self._refresh(folder_now, state)
                    except ValueError:
                        pass

    def _stop(self, job_id: str, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False)
            if proc.poll() is None:
                proc.kill()
        else:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                return
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def stop_all(self) -> None:
        with self._lock:
            for job_id, proc in list(self._active.items()):
                self._stop(job_id, proc)
                folder, state = self._job(job_id)
                state.update(status="stopped", exit_code=proc.poll(), ended_at=time.time())
                self._save(folder, state)
                self._active.pop(job_id, None)

    def tool(self, args: dict) -> dict:
        op = str(args.get("operation") or "")
        with self._lock:
            if op == "new":
                job_id = "job_" + uuid.uuid4().hex[:16]
                folder = self.root / job_id
                folder.mkdir()
                state = {"job_id":job_id, "name":str(args.get("name") or "Program")[:80],
                         "status":"created", "created_at":time.time(), "exit_code":None}
                self._save(folder, state)
                return {**state, "folder":str(folder)}
            if op == "list":
                rows = []
                for path in sorted(self.root.glob("job_*/state.json"), reverse=True)[:100]:
                    try:
                        folder, row = self._job(path.parent.name)
                        rows.append(dict(self._refresh(folder, row)))
                    except ValueError:
                        continue
                return {"jobs":rows}
            folder, state = self._job(str(args.get("job_id") or ""))
            state = self._refresh(folder, state)
            job_id = state["job_id"]
            if op in {"status", "logs", "wait"}:
                if op == "wait" and state["status"] == "running":
                    # Release lock so monitor/input/stop operations remain available.
                    self._lock.release()
                    try:
                        deadline = time.monotonic() + min(20, max(0, float(args.get("wait_seconds") or 2)))
                        while time.monotonic() < deadline:
                            active = self._active.get(job_id)
                            if active is None or active.poll() is not None:
                                break
                            time.sleep(.1)
                    finally:
                        self._lock.acquire()
                    state = self._refresh(folder, state)
                return {**state, "output":self._tail(folder, min(16000, max(100, int(args.get("max_chars") or 8000))))}
            if op == "files":
                files = []
                for base, dirs, names in os.walk(folder, followlinks=False):
                    dirs[:] = [d for d in dirs if d != ".venv" and not (Path(base) / d).is_symlink()]
                    for name in names:
                        p = Path(base) / name
                        if name not in {"state.json", "output.log", "state.json.tmp"} and not p.is_symlink():
                            files.append({"path":p.relative_to(folder).as_posix(), "bytes":p.stat().st_size})
                        if len(files)>200: break
                    if len(files)>200: break
                return {"files":files[:200], "truncated":len(files)>200}
            if op in {"write", "replace", "read"}:
                path = self._file(folder, args.get("path"))
                if path.is_dir(): raise ValueError("Expected a file")
                if op == "read":
                    if path.stat().st_size > 200_000: raise ValueError("File too large to read")
                    content = path.read_text(encoding="utf-8")
                    return {"path":str(path), "content":content[:16000], "truncated":len(content)>16000}
                if op == "replace":
                    before = path.read_text(encoding="utf-8")
                    old = str(args.get("old_text") or "")
                    if not old or before.count(old) != 1: raise ValueError("old_text must occur exactly once")
                    content = before.replace(old, str(args.get("new_text") or ""), 1)
                else:
                    content = args.get("content")
                    if not isinstance(content, str): raise ValueError("content must be text")
                if len(content.encode("utf-8")) > 200_000: raise ValueError("File exceeds 200 KB")
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_name(path.name + ".tmp")
                temp.write_text(content, encoding="utf-8")
                os.replace(temp, path)
                return {"path":str(path), "bytes":path.stat().st_size, "saved":True}
            if op == "check_packages":
                packages = args.get("packages") or []
                if not isinstance(packages, list) or len(packages)>20 or any(not isinstance(x,str) or not PACKAGE.fullmatch(x.strip()) for x in packages):
                    raise ValueError("packages must be a list of up to 20 distribution names/specifiers")
                python = self._environment(folder)
                names = [re.split(r"[\[<>=!~\s]", p)[0] for p in packages]
                script = ("import importlib.metadata as m,json,sys\n"
                          "def version(name):\n"
                          " try: return m.version(name)\n"
                          " except m.PackageNotFoundError: return None\n"
                          "print(json.dumps({name:version(name) for name in sys.argv[1:]}))")
                done = subprocess.run([str(python),"-c",script,*names], cwd=folder,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, text=True, check=True)
                rows = json.loads(done.stdout)
                return {"packages":rows, "python":str(python)}
            if op in {"run", "install"}:
                if state["status"] == "running": raise ValueError("Stop or finish this job before starting another process")
                python = self._environment(folder)
                if op == "install":
                    packages = args.get("packages") or []
                    if not isinstance(packages,list) or not 1<=len(packages)<=20 or any(not isinstance(x,str) or not PACKAGE.fullmatch(x.strip()) for x in packages):
                        raise ValueError("Use 1–20 package names/version specifiers, without pip flags or URLs")
                    argv = [str(python),"-m","pip","install","--disable-pip-version-check","--no-input",*packages]
                elif args.get("command") is not None:
                    command = args["command"]
                    if not isinstance(command,list) or not 1<=len(command)<=64 or any(not isinstance(x,str) or not x or len(x)>4000 or "\x00" in x for x in command):
                        raise ValueError("command must be an executable and argument array")
                    argv = command
                else:
                    entry = self._file(folder, str(args.get("path") or "main.py"))
                    if not entry.is_file() or entry.suffix.lower() != ".py": raise ValueError("Python entry file is missing")
                    argv = [str(python),"-u",str(entry)]
                timeout = min(3600, max(1, int(args.get("timeout_seconds") or 300)))
                (folder / "output.log").write_bytes(b"")
                kwargs = {"cwd":str(folder), "stdin":subprocess.PIPE, "stdout":subprocess.PIPE,
                          "stderr":subprocess.STDOUT, "shell":False, "bufsize":0}
                if os.name == "nt": kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else: kwargs["start_new_session"] = True
                # Run generated code with a minimal environment.  Inheriting the
                # parent environment can leak API tokens/cloud credentials to model-
                # generated code.  Keep only OS/runtime values needed to start Python.
                allowed_env = {
                    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
                    "TEMP", "TMP", "TMPDIR", "USERPROFILE", "HOME",
                    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
                    "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "TERM",
                }
                env = {k: v for k, v in os.environ.items() if k.upper() in allowed_env}
                env["PYTHONUNBUFFERED"] = "1"
                env["VIRTUAL_ENV"] = str(folder / ".venv")
                env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
                # Do not inherit HTTP(S)/ALL proxy variables into generated programs.
                for key in list(env):
                    if key.upper() in {"HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","FTP_PROXY"}:
                        env.pop(key, None)
                proc = subprocess.Popen(argv, env=env, **kwargs)
                state.update(status="running", action=op, command=argv, pid=proc.pid,
                             started_at=time.time(), ended_at=None, exit_code=None)
                self._active[job_id] = proc
                inputs = queue.Queue(maxsize=32)
                self._inputs[job_id] = inputs
                self._save(folder, state)
                threading.Thread(target=self._record_output, args=(folder,proc), daemon=True).start()
                def send_input():
                    while proc.poll() is None:
                        try: value = inputs.get(timeout=.2)
                        except queue.Empty: continue
                        try:
                            proc.stdin.write((value+"\n").encode("utf-8"))
                            proc.stdin.flush()
                        except (OSError, ValueError):
                            return
                threading.Thread(target=send_input, daemon=True).start()
                def enforce():
                    try: proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        with self._lock:
                            if self._active.get(job_id) is proc and proc.poll() is None:
                                self._stop(job_id, proc)
                                current = self._job(job_id)[1]
                                current.update(status="timed_out", ended_at=time.time(), exit_code=proc.poll())
                                self._save(folder, current)
                                self._active.pop(job_id, None)
                threading.Thread(target=enforce, daemon=True).start()
                # Fast scripts and already-installed packages report a result in
                # this same tool step; long-running programs remain interactive.
                initial_wait = min(2.0, max(0.0, float(args.get("wait_seconds", 1))))
                self._lock.release()
                try:
                    try: proc.wait(timeout=initial_wait)
                    except subprocess.TimeoutExpired: pass
                finally:
                    self._lock.acquire()
                state = self._refresh(folder, state)
                return {**state,"folder":str(folder),"timeout_seconds":timeout,
                        "output":self._tail(folder, 8000)}
            if op == "input":
                proc = self._active.get(job_id)
                if proc is None or proc.poll() is not None: raise ValueError("No running process")
                value = args.get("content")
                if not isinstance(value,str) or len(value)>8000: raise ValueError("Input must be text up to 8000 characters")
                try: self._inputs[job_id].put_nowait(value)
                except queue.Full: raise ValueError("Input queue is full")
                return {"queued":True,"job_id":job_id}
            if op == "stop":
                proc = self._active.get(job_id)
                if proc is None or proc.poll() is not None:
                    return {**state,"stopped":False}
                self._stop(job_id, proc)
                state.update(status="stopped",exit_code=proc.poll(),ended_at=time.time())
                self._save(folder,state)
                self._active.pop(job_id,None)
                return {**state,"stopped":True}
            raise ValueError("Unknown code_job operation")
