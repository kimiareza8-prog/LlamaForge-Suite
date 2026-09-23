from __future__ import annotations

import ctypes
import os
import platform
import re
import subprocess
import time
from pathlib import Path

_GB = 1024 ** 3
_prev_cpu = None


def _windows_memory() -> tuple[int, int]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise OSError("GlobalMemoryStatusEx failed")
    return int(stat.ullTotalPhys), int(stat.ullAvailPhys)


def _linux_memory() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(errors="ignore").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        m = re.search(r"(\d+)", v)
        if m:
            values[k] = int(m.group(1)) * 1024
    total = values.get("MemTotal", 0)
    avail = values.get("MemAvailable") or (
        values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    )
    return total, avail


def _mac_memory() -> tuple[int, int]:
    total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
    page_size = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"], text=True).strip())
    out = subprocess.check_output(["vm_stat"], text=True, errors="replace")
    vals: dict[str, int] = {}
    for line in out.splitlines():
        m = re.match(r"([^:]+):\s+(\d+)", line)
        if m:
            vals[m.group(1).strip()] = int(m.group(2))
    available_pages = (
        vals.get("Pages free", 0)
        + vals.get("Pages inactive", 0)
        + vals.get("Pages speculative", 0)
        + vals.get("Pages purgeable", 0)
    )
    return total, available_pages * page_size


def memory_bytes() -> tuple[int, int]:
    try:
        system = platform.system()
        if system == "Windows":
            return _windows_memory()
        if system == "Linux":
            return _linux_memory()
        if system == "Darwin":
            return _mac_memory()
    except Exception:
        pass
    # Conservative fallback when the platform-specific query is unavailable.
    return 0, 0


def memory_gb() -> tuple[float, float]:
    total, avail = memory_bytes()
    return round(total / _GB, 2), round(avail / _GB, 2)


def _windows_cpu_times() -> tuple[int, int, int]:
    idle = ctypes.c_ulonglong()
    kernel = ctypes.c_ulonglong()
    user = ctypes.c_ulonglong()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        raise OSError("GetSystemTimes failed")
    return idle.value, kernel.value, user.value


def _linux_cpu_times() -> tuple[int, int]:
    line = Path("/proc/stat").read_text(errors="ignore").splitlines()[0]
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    return idle, total


def cpu_percent() -> float:
    """Return a non-blocking system CPU utilization estimate without third-party packages."""
    global _prev_cpu
    try:
        system = platform.system()
        now = time.monotonic()
        if system == "Windows":
            idle, kernel, user = _windows_cpu_times()
            total = kernel + user
        elif system == "Linux":
            idle, total = _linux_cpu_times()
        else:
            # macOS fallback: use load average as a bounded utilization indicator.
            cpus = max(1, os.cpu_count() or 1)
            return max(0.0, min(100.0, (os.getloadavg()[0] / cpus) * 100.0))
        current = (idle, total, now)
        if _prev_cpu is None:
            _prev_cpu = current
            return 0.0
        p_idle, p_total, _ = _prev_cpu
        _prev_cpu = current
        d_total = total - p_total
        d_idle = idle - p_idle
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))
    except Exception:
        return 0.0

# Per-process CPU accounting. Values are normalized to whole-machine capacity,
# so a single fully busy logical core on a 4-thread CPU reports ~25%.
_process_cpu_prev: dict[int, tuple[float, float]] = {}


def _windows_process_cpu_seconds(pid: int) -> float:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        raise OSError("OpenProcess failed")
    try:
        creation = ctypes.c_ulonglong(); exit_time = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong(); user = ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise OSError("GetProcessTimes failed")
        # FILETIME units are 100 ns.
        return (kernel.value + user.value) / 10_000_000.0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _linux_process_cpu_seconds(pid: int) -> float:
    stat = Path(f"/proc/{int(pid)}/stat").read_text(errors="ignore").split()
    ticks = int(stat[13]) + int(stat[14])
    hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    return ticks / float(hz)


def process_cpu_percent(pid: int | None, logical_cores: int | None = None) -> float:
    """Return process CPU as a percentage of total machine capacity.

    100% means the process consumed the equivalent of all logical CPUs during
    the sample interval. This matches the user's mental model in Task Manager.
    """
    if not pid:
        return 0.0
    logical = max(1, int(logical_cores or os.cpu_count() or 1))
    try:
        if platform.system() == "Windows":
            cpu_s = _windows_process_cpu_seconds(int(pid))
        elif platform.system() == "Linux":
            cpu_s = _linux_process_cpu_seconds(int(pid))
        else:
            return 0.0
        now = time.monotonic()
        prev = _process_cpu_prev.get(int(pid))
        _process_cpu_prev[int(pid)] = (cpu_s, now)
        if not prev:
            return 0.0
        d_cpu = cpu_s - prev[0]
        d_wall = now - prev[1]
        if d_wall <= 0:
            return 0.0
        return max(0.0, min(100.0, (d_cpu / d_wall / logical) * 100.0))
    except Exception:
        return 0.0


def process_memory_mb(pid: int | None) -> float:
    """Return resident working-set memory for *pid* without third-party packages."""
    if not pid:
        return 0.0
    try:
        system = platform.system()
        if system == "Windows":
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            PROCESS_VM_READ = 0x0010
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, int(pid)
            )
            if not handle:
                # PROCESS_VM_READ is not always granted; query-only is enough on
                # modern Windows for GetProcessMemoryInfo.
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
                )
            if not handle:
                return 0.0
            try:
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(counters)
                if not ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb
                ):
                    return 0.0
                return round(counters.WorkingSetSize / (1024 ** 2), 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        if system == "Linux":
            for line in Path(f"/proc/{int(pid)}/status").read_text(errors="ignore").splitlines():
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        return 0.0
    return 0.0
