from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ProcessPolicy:
    """Best-effort OS policy for the llama.cpp process.

    affinity_count=0 leaves affinity unchanged. A positive value allows CPUs
    0..N-1. priority is -1/0/1/2 matching low/normal/medium/high semantics.
    """
    affinity_count: int = 0
    priority: int = 0
    memory_limit_mb: int = 0
    memory_limit_required: bool = False


class ManagedProcess:
    """Own a long-running child process and retain diagnostics.

    The process policy is applied *after* spawn so LlamaForge does not rely only
    on version-dependent llama.cpp affinity flags.
    """

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._tail: deque[str] = deque(maxlen=320)
        self.last_exit_code: int | None = None
        self.last_command: list[str] = []
        self.last_policy = ProcessPolicy()
        self._stop_requested = False
        self._job_handle = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self.proc.pid if self.proc is not None and self.proc.poll() is None else None

    def tail_text(self, lines: int = 60) -> str:
        with self._lock:
            data = list(self._tail)[-max(1, lines):]
        return "\n".join(data)

    @staticmethod
    def hard_memory_limit_supported() -> bool:
        if os.name == "nt":
            return True
        try:
            import resource
            return hasattr(resource, "prlimit") and hasattr(resource, "RLIMIT_AS")
        except Exception:
            return False

    def _apply_hard_memory_limit(self, proc: subprocess.Popen, limit_mb: int) -> bool:
        if int(limit_mb or 0) <= 0:
            return True
        limit_bytes = int(limit_mb) * 1024 * 1024
        if os.name == "nt":
            try:
                wintypes = ctypes.wintypes
            except AttributeError:
                from ctypes import wintypes
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),("WriteOperationCount", ctypes.c_ulonglong),("OtherOperationCount", ctypes.c_ulonglong),("ReadTransferCount", ctypes.c_ulonglong),("WriteTransferCount", ctypes.c_ulonglong),("OtherTransferCount", ctypes.c_ulonglong)]
            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),("PerJobUserTimeLimit", ctypes.c_longlong),("LimitFlags", wintypes.DWORD),("MinimumWorkingSetSize", ctypes.c_size_t),("MaximumWorkingSetSize", ctypes.c_size_t),("ActiveProcessLimit", wintypes.DWORD),("Affinity", ctypes.c_size_t),("PriorityClass", wintypes.DWORD),("SchedulingClass", wintypes.DWORD)]
            class EXT_LIMIT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC_LIMIT),("IoInfo", IO_COUNTERS),("ProcessMemoryLimit", ctypes.c_size_t),("JobMemoryLimit", ctypes.c_size_t),("PeakProcessMemoryUsed", ctypes.c_size_t),("PeakJobMemoryUsed", ctypes.c_size_t)]
            k32 = ctypes.windll.kernel32
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return False
            info = EXT_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x00000100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = limit_bytes
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(job); return False
            PROCESS_SET_QUOTA = 0x0100
            PROCESS_TERMINATE = 0x0001
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            ph = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
            if not ph:
                k32.CloseHandle(job); return False
            try:
                if not k32.AssignProcessToJobObject(job, ph):
                    k32.CloseHandle(job); return False
            finally:
                k32.CloseHandle(ph)
            self._job_handle = job
            return True
        try:
            import resource
            if hasattr(resource, "prlimit"):
                resource.prlimit(proc.pid, resource.RLIMIT_AS, (limit_bytes, limit_bytes))
                return True
        except Exception:
            pass
        return False

    def _apply_policy(self, proc: subprocess.Popen, policy: ProcessPolicy) -> None:
        memory_ok = self._apply_hard_memory_limit(proc, int(policy.memory_limit_mb or 0))
        if policy.memory_limit_required and int(policy.memory_limit_mb or 0) > 0 and not memory_ok:
            raise RuntimeError("The operating system could not enforce the requested hard process memory limit")
        if os.name == "nt":
            PROCESS_SET_INFORMATION = 0x0200
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
            if not handle:
                return
            try:
                if policy.affinity_count > 0:
                    bits = min(policy.affinity_count, ctypes.sizeof(ctypes.c_size_t) * 8)
                    mask = (1 << bits) - 1
                    ctypes.windll.kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(mask))
                classes = {
                    -1: 0x00004000,  # BELOW_NORMAL_PRIORITY_CLASS
                    0: 0x00000020,   # NORMAL_PRIORITY_CLASS
                    1: 0x00008000,   # ABOVE_NORMAL_PRIORITY_CLASS
                    2: 0x00000080,   # HIGH_PRIORITY_CLASS
                }
                ctypes.windll.kernel32.SetPriorityClass(handle, classes.get(policy.priority, 0x20))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            if policy.affinity_count > 0 and hasattr(os, "sched_setaffinity"):
                try:
                    os.sched_setaffinity(proc.pid, set(range(policy.affinity_count)))
                except Exception:
                    pass
            # Raising Unix priority generally needs privileges. Lowering is safe;
            # leave positive priority requests to llama.cpp's own --prio flag.
            if policy.priority < 0:
                try:
                    os.setpriority(os.PRIO_PROCESS, proc.pid, 5)
                except Exception:
                    pass

    def start(
        self,
        command: list[str],
        log_cb: Callable[[str], None] | None = None,
        policy: ProcessPolicy | None = None,
    ) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("Process already running")
            self._tail.clear()
            self.last_exit_code = None
            self._stop_requested = False
            self.last_command = list(command)
            self.last_policy = policy or ProcessPolicy()
            kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = startupinfo
            else:
                kwargs["start_new_session"] = True
            self.proc = subprocess.Popen(command, **kwargs)
            proc = self.proc
            try:
                self._apply_policy(proc, self.last_policy)
            except Exception:
                if self.last_policy.memory_limit_required:
                    try: proc.terminate(); proc.wait(timeout=3.0)
                    except Exception:
                        try: proc.kill()
                        except Exception: pass
                    self.proc = None
                    raise
                # Affinity/priority are optimizations when no hard resource cap is requested.
                pass

        def emit(line: str) -> None:
            with self._lock:
                self._tail.append(line)
            if log_cb:
                log_cb(line)

        def read() -> None:
            try:
                assert proc.stdout
                for line in proc.stdout:
                    emit(line.rstrip())
                code = proc.wait()
                with self._lock:
                    self.last_exit_code = code
                    expected_stop = bool(self._stop_requested)
                if expected_stop:
                    emit(f"[LlamaForge] Process stopped (exit code {code})")
                else:
                    emit(f"[LlamaForge] Process exited with code {code}")
            except Exception as exc:
                emit(f"[LlamaForge] Output reader failed: {exc}")

        self.reader = threading.Thread(target=read, name="llama-output", daemon=True)
        self.reader.start()

    def stop(self, timeout: float = 8.0) -> None:
        """Terminate the entire child tree and wait for the OS to release it.

        Waiting matters for large mmap'd GGUFs: returning immediately after
        taskkill can make the UI claim the model is unloaded while Windows is
        still tearing down llama-server and its mapped pages.
        """
        with self._lock:
            proc = self.proc
            if proc is not None:
                self._stop_requested = True
        if not proc:
            return
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout
                    )
                    try:
                        proc.wait(timeout=max(1.0, timeout))
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        finally:
                            proc.wait(timeout=2.0)
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=2.0)
                        except Exception:
                            pass
        except Exception:
            try:
                proc.terminate(); proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill(); proc.wait(timeout=2.0)
                except Exception:
                    pass
        # Never discard a live Popen handle. A false "stopped" state is worse
        # than surfacing the termination failure because a second llama-server
        # can otherwise be spawned on top of the first model.
        if proc.poll() is None:
            with self._lock:
                if self.proc is proc:
                    self.proc = proc
            raise RuntimeError(f"Could not terminate llama-server PID {proc.pid}")
        with self._lock:
            if self.proc is proc:
                self.proc = None
            job_handle = self._job_handle
            self._job_handle = None
        if job_handle and os.name == "nt":
            try: ctypes.windll.kernel32.CloseHandle(job_handle)
            except Exception: pass
        if self.reader and self.reader.is_alive():
            self.reader.join(timeout=1.0)
