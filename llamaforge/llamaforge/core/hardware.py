from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from .system_metrics import memory_gb


@dataclass
class GPUInfo:
    name: str
    kind: str
    memory_gb: float | None = None
    driver: str = ""


@dataclass
class HardwareInfo:
    os_name: str
    os_version: str
    machine: str
    cpu: str
    logical_cores: int
    physical_cores: int
    ram_total_gb: float
    ram_available_gb: float
    gpus: list[GPUInfo]
    disks: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def gpu_name(self) -> str:
        """Backward-compatible primary GPU name."""
        return self.gpus[0].name if self.gpus else ""

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpus)


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=timeout
        ).strip()
    except Exception:
        return ""


def _cpu_name() -> str:
    name = platform.processor().strip()
    if name:
        return name
    system = platform.system()
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
                if "model name" in line:
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    if system == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.machine()
    if system == "Windows":
        return os.environ.get("PROCESSOR_IDENTIFIER", platform.machine())
    return platform.machine()


def _physical_cores(logical: int) -> int:
    system = platform.system()
    try:
        if system == "Windows":
            # Keep startup instant and dependency-free. SMT systems are commonly
            # 2 logical threads/core; the fallback below remains conservative.
            pass
        elif system == "Linux":
            pairs = set()
            phys = core = None
            for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
                if line.startswith("physical id"):
                    phys = line.split(":", 1)[1].strip()
                elif line.startswith("core id"):
                    core = line.split(":", 1)[1].strip()
                elif not line.strip() and phys is not None and core is not None:
                    pairs.add((phys, core)); phys = core = None
            if pairs:
                return len(pairs)
        elif system == "Darwin":
            out = _run(["sysctl", "-n", "hw.physicalcpu"])
            if out.isdigit():
                return max(1, int(out))
    except Exception:
        pass
    return max(1, logical // 2 if logical > 2 else logical)


def _detect_gpus() -> list[GPUInfo]:
    gpus: list[GPUInfo] = []
    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = [x.strip() for x in line.split(",")]
            mem = None
            if len(parts) > 1:
                try:
                    mem = round(float(parts[1]) / 1024, 1)
                except Exception:
                    pass
            gpus.append(GPUInfo(parts[0], "NVIDIA/CUDA", mem, parts[2] if len(parts) > 2 else ""))
    if platform.system() == "Windows":
        # nvidia-smi does not see AMD/Intel integrated GPUs.  Query Windows' own
        # display inventory as well so Ryzen/Intel iGPUs can be offered to the
        # Vulkan runtime.  Win32_VideoController.AdapterRAM is best-effort only
        # (shared-memory iGPUs frequently report a small aperture instead of the
        # total memory they can borrow from system RAM), so it is informational.
        ps = shutil.which("powershell.exe") or shutil.which("powershell")
        if ps:
            script = (
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,AdapterRAM,PNPDeviceID,DriverVersion | ConvertTo-Json -Compress"
            )
            raw = _run([ps, "-NoProfile", "-NonInteractive", "-Command", script], timeout=8)
            if raw:
                try:
                    import json
                    rows = json.loads(raw)
                    if isinstance(rows, dict):
                        rows = [rows]
                    for row in rows if isinstance(rows, list) else []:
                        name = str((row or {}).get("Name") or "").strip()
                        low = name.lower()
                        if not name or "microsoft basic display" in low or "remote display" in low:
                            continue
                        if any(g.name.lower() == low for g in gpus):
                            continue
                        mem = None
                        try:
                            ram = int((row or {}).get("AdapterRAM") or 0)
                            if ram > 0:
                                mem = round(ram / (1024**3), 1)
                        except Exception:
                            pass
                        if "nvidia" in low:
                            kind = "NVIDIA/Vulkan"
                        elif any(x in low for x in ("amd", "radeon")):
                            kind = "AMD/Vulkan"
                        elif any(x in low for x in ("intel", "iris", "uhd", "arc")):
                            kind = "Intel/Vulkan"
                        else:
                            kind = "GPU/Vulkan"
                        gpus.append(GPUInfo(name, kind, mem, str(row.get("DriverVersion") or "")))
                except Exception:
                    pass
    if platform.system() == "Darwin":
        out = _run(["system_profiler", "SPDisplaysDataType"], timeout=8)
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Chipset Model:"):
                chipset = s.split(":", 1)[1].strip()
                if chipset and not any(g.name == chipset for g in gpus):
                    gpus.append(GPUInfo(chipset, "Apple/Metal", None))
    if not gpus and platform.system() == "Linux":
        out = _run(["bash", "-lc", "lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -4"])
        for line in out.splitlines():
            name = line.split(":", 2)[-1].strip()
            if name:
                gpus.append(GPUInfo(name, "GPU", None))
    return gpus


def _windows_roots() -> list[str]:
    roots: list[str] = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                roots.append(chr(65 + i) + ":\\")
    except Exception:
        roots = [Path.home().anchor or "C:\\"]
    return roots


def _disks() -> list[dict]:
    roots: list[str]
    if platform.system() == "Windows":
        roots = _windows_roots()
    else:
        roots = ["/", str(Path.home())]
    seen = set(); rows = []
    for root in roots:
        try:
            resolved = str(Path(root).resolve()) if platform.system() != "Windows" else root.upper()
            if resolved in seen:
                continue
            seen.add(resolved)
            usage = shutil.disk_usage(root)
            rows.append({
                "mount": root,
                "total_gb": round(usage.total / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "fstype": "",
            })
        except Exception:
            pass
    return rows


def detect_hardware() -> HardwareInfo:
    logical = max(1, os.cpu_count() or 1)
    total, avail = memory_gb()
    return HardwareInfo(
        os_name=platform.system(),
        os_version=platform.release(),
        machine=platform.machine(),
        cpu=_cpu_name(),
        logical_cores=logical,
        physical_cores=_physical_cores(logical),
        ram_total_gb=total,
        ram_available_gb=avail,
        gpus=_detect_gpus(),
        disks=_disks(),
    )
