from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import platform
import random
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR
from .hardware import HardwareInfo, detect_hardware
from .processes import ManagedProcess, ProcessPolicy
from .runtime import RuntimeManager
from .system_metrics import cpu_percent, memory_gb, process_cpu_percent, process_memory_mb

DISCOVERY_PORT = 39391
CONTROL_PORT = 39392
DEFAULT_RPC_PORT = 50052
MASTER_RPC_PORT = 50051
MASTER_NODE_ID = "__master__"
DISCOVERY_MAGIC = "LLAMAFORGE_CLUSTER_V1"
CLUSTER_PATH = APP_DIR / "cluster.json"
PROFILE_PATH = APP_DIR / "cluster-profiles.json"
GB = 1024 ** 3


def _now() -> float:
    return time.time()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _json_load(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _json_save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _host_id() -> str:
    seed = f"{socket.gethostname()}|{platform.system()}|{platform.machine()}|{Path.home()}"
    return "node_" + hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:18]


def _private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(value).split("%", 1)[0])
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except Exception:
        return False


def _local_ipv4s() -> list[str]:
    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            ip = str(info[4][0])
            if ip and ip not in out and not ip.startswith("127."):
                out.append(ip)
    except Exception:
        pass
    # UDP connect does not send data but reveals the preferred outbound LAN IP.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and ip not in out and not ip.startswith("127."):
                out.insert(0, ip)
        finally:
            s.close()
    except Exception:
        pass
    return out or ["127.0.0.1"]


def _cpu_capabilities() -> list[str]:
    """Best-effort SIMD/features used to compare heterogeneous CPU Workers."""
    flags: set[str] = set()
    try:
        if platform.system().lower() == "linux":
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").lower()
            for line in text.splitlines():
                if line.startswith("flags") or line.startswith("features"):
                    flags.update(line.split(":", 1)[-1].split())
                    break
        elif platform.system().lower() == "darwin":
            out = subprocess.check_output(["sysctl", "-a"], text=True, encoding="utf-8", errors="ignore", timeout=2).lower()
            for name in ("avx512f", "avx2", "avx", "fma", "sse4.2", "neon", "dotprod"):
                if name in out: flags.add(name)
        elif platform.system().lower() == "windows":
            # Windows does not expose CPUID flags through a stable stdlib API.
            # The architecture plus llama.cpp runtime capabilities are still
            # reported; common flags can be learned from WMIC/PowerShell text.
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name"], text=True, encoding="utf-8", errors="ignore", timeout=3).lower()
            if out: flags.add(platform.machine().lower())
    except Exception:
        pass
    preferred = ["avx512f", "avx512_vnni", "avx2", "avx", "fma", "sse4_2", "sse4.2", "neon", "asimd", "dotprod", "i8mm", "amx_int8"]
    result = []
    for name in preferred:
        if name in flags and name not in result: result.append(name)
    for name in sorted(flags):
        if name.startswith(("avx", "amx")) and name not in result:
            result.append(name)
    return result[:24]

def _link_speed_mbps() -> float:
    """Best-effort active-link speed without third-party packages."""
    try:
        if platform.system() == "Windows":
            cmd = [
                "powershell", "-NoProfile", "-Command",
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Sort-Object LinkSpeed -Descending | Select-Object -First 1 -ExpandProperty LinkSpeed",
            ]
            text = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace", timeout=4).strip().lower()
            m = __import__("re").search(r"([0-9.]+)\s*(gbps|mbps)", text)
            if m:
                value = float(m.group(1))
                return value * 1000.0 if m.group(2) == "gbps" else value
        elif platform.system() == "Linux":
            for p in Path("/sys/class/net").glob("*/speed"):
                try:
                    value = float(p.read_text().strip())
                    if value > 0:
                        return value
                except Exception:
                    pass
        elif platform.system() == "Darwin":
            text = subprocess.check_output(["networkQuality", "-c"], text=True, errors="replace", timeout=8)
            data = json.loads(text)
            # networkQuality reports bps.
            return round(max(float(data.get("dl_throughput", 0)), float(data.get("ul_throughput", 0))) / 1_000_000, 1)
    except Exception:
        pass
    return 0.0


def recommended_ram_gb(total_gb: float, available_gb: float) -> float:
    """Return a conservative cluster budget while preserving OS headroom."""
    total = max(0.0, float(total_gb or 0.0))
    avail = max(0.0, float(available_gb or 0.0))
    reserve = max(1.5, total * 0.15)
    usable = min(total * 0.82, avail - reserve)
    return round(max(0.0, usable), 2)


@dataclass
class NodeLimits:
    enabled: bool = True
    ram_mode: str = "auto"  # auto | manual
    ram_limit_gb: float = 0.0
    cpu_mode: str = "auto"  # auto | manual
    cpu_threads: int = 0
    cpu_usage_percent: int = 100

    def normalize(self, hw: dict[str, Any]) -> "NodeLimits":
        total = _safe_float(hw.get("ram_total_gb"))
        avail = _safe_float(hw.get("ram_available_gb"))
        logical = max(1, _safe_int(hw.get("logical_cores"), 1))
        self.ram_mode = self.ram_mode if self.ram_mode in {"auto", "manual"} else "auto"
        self.cpu_mode = self.cpu_mode if self.cpu_mode in {"auto", "manual"} else "auto"
        safe = recommended_ram_gb(total, avail)
        if self.ram_mode == "manual":
            self.ram_limit_gb = round(max(0.25, min(float(self.ram_limit_gb or safe or 0.25), max(0.25, avail - 0.5))), 2)
        else:
            self.ram_limit_gb = safe
        pct = max(10, min(100, int(self.cpu_usage_percent or 100)))
        self.cpu_usage_percent = pct
        max_threads = max(1, math.floor(logical * pct / 100.0))
        if self.cpu_mode == "manual" and int(self.cpu_threads or 0) > 0:
            self.cpu_threads = max(1, min(logical, int(self.cpu_threads)))
        else:
            self.cpu_threads = max_threads
        return self


@dataclass
class NodeBenchmark:
    cpu_score: float = 0.0
    memory_bandwidth_gbps: float = 0.0
    disk_mbps: float = 0.0
    network_mbps: float = 0.0
    latency_ms: float = 0.0
    measured_at: float = 0.0

    @property
    def valid(self) -> bool:
        return self.measured_at > 0 and self.cpu_score > 0


@dataclass
class ClusterNode:
    node_id: str
    hostname: str
    ip: str
    control_port: int = CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    version: str = ""
    online: bool = False
    paired: bool = False
    last_seen: float = 0.0
    state: str = "offline"
    hardware: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    limits: NodeLimits = field(default_factory=NodeLimits)
    benchmark: NodeBenchmark = field(default_factory=NodeBenchmark)
    token: str = ""
    master_id: str = ""
    current_task: str = ""
    rpc_running: bool = False
    rpc_pid: int | None = None
    ram_used_gb: float = 0.0
    cpu_percent: float = 0.0
    ping_ms: float = 0.0
    network_mbps: float = 0.0
    model_ram_gb: float = 0.0
    kv_ram_gb: float = 0.0
    compute_share: float = 0.0
    allocated_ram_gb: float = 0.0

    def to_public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("token", None)
        data["limits"] = asdict(self.limits)
        data["benchmark"] = asdict(self.benchmark)
        return data


@dataclass
class ClusterPlan:
    plan_id: str
    created_at: float
    optimization_mode: str
    selection_mode: str
    backend: str
    strategy: str
    nodes: list[dict[str, Any]]
    rpc_servers: list[str]
    tensor_split: list[float]
    model_size_gb: float
    runtime_overhead_gb: float
    required_ram_gb: float
    allowed_ram_gb: float
    predicted_score: float
    split_mode: str = "layer"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClusterScheduler:
    """Hardware/network aware planner independent of any one distributed backend.

    Current llama.cpp execution uses its RPC device backend. The planner keeps
    memory allocation separate from compute share so future pipeline/tensor
    backends can consume the same plan without changing the UI/config model.
    """

    MODES = {"smart", "maximum_compute", "maximum_model_size", "lowest_latency", "manual"}
    SELECTION = {"auto", "selected_pool", "force_selected"}

    @staticmethod
    def _compute_score(node: ClusterNode) -> float:
        b = node.benchmark
        logical = max(1, _safe_int(node.hardware.get("logical_cores"), 1))
        cpu = b.cpu_score if b.cpu_score > 0 else logical * 100.0
        mem = b.memory_bandwidth_gbps if b.memory_bandwidth_gbps > 0 else max(5.0, logical * 2.0)
        net = node.network_mbps or b.network_mbps or _safe_float(node.hardware.get("link_speed_mbps")) or 100.0
        latency = node.ping_ms or b.latency_ms or 2.0
        gpu_bonus = 1.0
        gpus = node.hardware.get("gpus") or []
        if gpus:
            gpu_mem = sum(_safe_float(x.get("memory_gb")) for x in gpus if isinstance(x, dict))
            gpu_bonus += min(4.0, gpu_mem / 12.0)
        network_factor = min(1.8, max(0.15, math.log2(max(net, 20.0)) / 10.0))
        latency_factor = 1.0 / (1.0 + max(0.0, latency - 0.4) / 18.0)
        return max(1.0, (cpu * 0.60 + mem * 12.0 * 0.40) * gpu_bonus * network_factor * latency_factor)

    @staticmethod
    def _capacity(node: ClusterNode) -> float:
        limits = NodeLimits(**asdict(node.limits)).normalize(node.hardware)
        # Keep explicit scheduler margin even though worker RPC also receives a hard cap.
        return max(0.0, round(limits.ram_limit_gb - max(0.25, limits.ram_limit_gb * 0.04), 2))

    @staticmethod
    def _waterfill(required: float, nodes: list[ClusterNode], weights: list[float]) -> list[float]:
        caps = [ClusterScheduler._capacity(n) for n in nodes]
        alloc = [0.0] * len(nodes)
        remaining = max(0.0, required)
        active = set(range(len(nodes)))
        for _ in range(len(nodes) + 2):
            if remaining <= 1e-6 or not active:
                break
            wsum = sum(max(0.01, weights[i]) for i in active)
            progressed = False
            for i in list(active):
                room = caps[i] - alloc[i]
                if room <= 1e-6:
                    active.discard(i)
                    continue
                share = remaining * max(0.01, weights[i]) / wsum
                add = min(room, share)
                if add > 0:
                    alloc[i] += add
                    progressed = True
            remaining = max(0.0, required - sum(alloc))
            for i in list(active):
                if caps[i] - alloc[i] <= 1e-6:
                    active.discard(i)
            if not progressed:
                break
        if remaining > 0.02:
            for i in sorted(range(len(nodes)), key=lambda j: caps[j] - alloc[j], reverse=True):
                add = min(caps[i] - alloc[i], remaining)
                if add > 0:
                    alloc[i] += add
                    remaining -= add
                if remaining <= 0.02:
                    break
        return [round(x, 3) for x in alloc]

    def plan(
        self,
        nodes: list[ClusterNode],
        model_size_gb: float,
        runtime_overhead_gb: float,
        optimization_mode: str = "smart",
        selection_mode: str = "auto",
    ) -> ClusterPlan:
        mode = optimization_mode if optimization_mode in self.MODES else "smart"
        selection = selection_mode if selection_mode in self.SELECTION else "auto"
        candidates = [n for n in nodes if n.online and n.paired and n.limits.enabled]
        if not candidates:
            raise RuntimeError("No paired online Worker is enabled for the cluster")
        for n in candidates:
            n.limits.normalize(n.hardware)
        required = round(max(0.25, float(model_size_gb)) + max(0.55, float(runtime_overhead_gb)), 3)
        scores = {n.node_id: self._compute_score(n) for n in candidates}

        # User-selected pool is represented by limits.enabled. Force mode keeps
        # every enabled node; other modes may deliberately use fewer nodes.
        if selection == "force_selected":
            chosen = list(candidates)
        else:
            ranked = sorted(candidates, key=lambda n: scores[n.node_id], reverse=True)
            chosen = []
            if mode == "maximum_model_size":
                chosen = ranked
            else:
                capacity = 0.0
                best: list[ClusterNode] | None = None
                best_value = -1e99
                for n in ranked:
                    chosen.append(n)
                    capacity += self._capacity(n)
                    if capacity + 1e-6 < required:
                        continue
                    compute = sum(scores[x.node_id] for x in chosen)
                    max_latency = max((x.ping_ms or x.benchmark.latency_ms or 2.0) for x in chosen)
                    min_net = min((x.network_mbps or x.benchmark.network_mbps or _safe_float(x.hardware.get("link_speed_mbps")) or 100.0) for x in chosen)
                    comm_penalty = (len(chosen) - 1) * (max_latency * 2.2 + 850.0 / max(25.0, min_net))
                    if mode == "lowest_latency":
                        value = compute - comm_penalty * 7.0 - len(chosen) * 90.0
                    elif mode == "maximum_compute":
                        value = compute - comm_penalty * 1.8
                    elif mode == "manual":
                        value = compute - comm_penalty * 2.0
                    else:
                        # Balanced strongly prefers enough memory with as few slow
                        # links as possible, but still rewards fast extra workers.
                        value = compute - comm_penalty * 3.2 - len(chosen) * 20.0
                    if value > best_value:
                        best_value, best = value, list(chosen)
                    # For smart/latency modes, once memory fits, evaluate only a
                    # couple extra nodes. Slow workers should not be forced in.
                    if mode in {"smart", "lowest_latency"} and len(chosen) >= 3 and best is not None:
                        break
                chosen = best or chosen

        allowed = round(sum(self._capacity(n) for n in chosen), 3)
        if allowed + 1e-6 < required:
            raise RuntimeError(
                f"Cluster RAM is insufficient: {allowed:.2f} GB allowed, about {required:.2f} GB required including runtime/KV safety overhead"
            )
        compute_scores = [scores[n.node_id] for n in chosen]
        if mode == "maximum_model_size":
            weights = [max(0.1, self._capacity(n)) for n in chosen]
        elif mode == "lowest_latency":
            weights = [s / (1.0 + (n.ping_ms or n.benchmark.latency_ms or 1.0) / 8.0) for n, s in zip(chosen, compute_scores)]
        else:
            weights = [math.sqrt(max(0.01, self._capacity(n)) * max(1.0, s)) for n, s in zip(chosen, compute_scores)]
        alloc = self._waterfill(required, chosen, weights)
        score_sum = sum(compute_scores) or 1.0
        compute_share = [round(s / score_sum * 100.0, 2) for s in compute_scores]
        tensor_total = sum(max(0.001, x) for x in alloc) or 1.0
        tensor_split = [round(max(0.001, x) / tensor_total, 6) for x in alloc]
        plan_nodes: list[dict[str, Any]] = []
        for n, ram, share, split in zip(chosen, alloc, compute_share, tensor_split):
            n.allocated_ram_gb = ram
            n.compute_share = share
            plan_nodes.append({
                "node_id": n.node_id,
                "hostname": n.hostname,
                "ip": n.ip,
                "rpc_port": n.rpc_port,
                "ram_limit_gb": n.limits.ram_limit_gb,
                "ram_allocation_gb": ram,
                "compute_share": share,
                "tensor_split": split,
                "threads": n.limits.cpu_threads,
                "score": round(scores[n.node_id], 2),
                "ping_ms": round(n.ping_ms or n.benchmark.latency_ms, 2),
                "network_mbps": round(n.network_mbps or n.benchmark.network_mbps, 1),
            })
        warnings: list[str] = []
        if any((n.network_mbps or n.benchmark.network_mbps or 0.0) and (n.network_mbps or n.benchmark.network_mbps) < 300 for n in chosen):
            warnings.append("One or more selected links benchmark below 300 Mbps; distributed inference may be slower than a smaller cluster.")
        if any((n.ping_ms or n.benchmark.latency_ms or 0.0) > 8 for n in chosen):
            warnings.append("One or more selected Workers have >8 ms latency; token-by-token latency can suffer.")
        predicted = sum(compute_scores) / (1.0 + max(0, len(chosen) - 1) * 0.08)
        return ClusterPlan(
            plan_id="plan_" + secrets.token_hex(8), created_at=_now(),
            optimization_mode=mode, selection_mode=selection,
            backend="llama.cpp-rpc", strategy="rpc_layer_pipeline" if len(chosen) > 1 else "rpc_single_device",
            nodes=plan_nodes, rpc_servers=[f"{n.ip}:{n.rpc_port}" for n in chosen], tensor_split=tensor_split,
            model_size_gb=round(float(model_size_gb), 3), runtime_overhead_gb=round(float(runtime_overhead_gb), 3),
            required_ram_gb=required, allowed_ram_gb=allowed, predicted_score=round(predicted, 2), split_mode="layer", warnings=warnings,
        )


def _benchmark_local() -> NodeBenchmark:
    """Fast repeatable micro-benchmark suitable for first-pair setup."""
    # CPU: deliberately dependency-free integer/hash workload. Absolute units are
    # not meaningful; relative values across LlamaForge nodes are.
    start = time.perf_counter()
    payload = b"LlamaForgeClusterBenchmark" * 128
    count = 0
    digest = payload
    while time.perf_counter() - start < 0.40:
        digest = hashlib.sha256(digest + payload).digest()
        count += 1
    elapsed = max(0.001, time.perf_counter() - start)
    cpu_score = round(count / elapsed, 1)

    size = 32 * 1024 * 1024
    try:
        src = bytearray(os.urandom(min(size, 1024 * 1024)))
        src *= size // len(src)
        start = time.perf_counter()
        for _ in range(3):
            dst = bytearray(src)
            if not dst:
                break
        mem_elapsed = max(0.001, time.perf_counter() - start)
        mem_bw = round((size * 3 / mem_elapsed) / 1_000_000_000, 2)
    except Exception:
        mem_bw = 0.0

    disk_mbps = 0.0
    try:
        blob = b"\0" * (8 * 1024 * 1024)
        with tempfile.NamedTemporaryFile(prefix="lf-cluster-bench-", delete=True) as f:
            start = time.perf_counter(); f.write(blob); f.flush(); os.fsync(f.fileno())
            write_elapsed = max(0.001, time.perf_counter() - start)
            f.seek(0); start = time.perf_counter(); _ = f.read(); read_elapsed = max(0.001, time.perf_counter() - start)
        disk_mbps = round(min(len(blob) / write_elapsed, len(blob) / read_elapsed) / 1_000_000, 1)
    except Exception:
        pass
    return NodeBenchmark(cpu_score=cpu_score, memory_bandwidth_gbps=mem_bw, disk_mbps=disk_mbps, measured_at=_now())


class WorkerService:
    def __init__(self, manager: "ClusterManager"):
        self.manager = manager
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.announce_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.rpc_process = ManagedProcess()
        self.rpc_port = int(manager.config.get("worker_rpc_port", DEFAULT_RPC_PORT) or DEFAULT_RPC_PORT)
        self.pairing_code = f"{random.randint(0, 999999):06d}"
        self.current_limits = NodeLimits(**manager.config.get("worker_limits", {})) if isinstance(manager.config.get("worker_limits"), dict) else NodeLimits()
        self.current_task = ""
        self.last_master = ""

    def _trusted(self) -> dict[str, str]:
        data = self.manager.config.setdefault("trusted_masters", {})
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()

    def authorize(self, header: str) -> bool:
        if not header.lower().startswith("bearer "):
            return False
        token = header.split(" ", 1)[1].strip()
        if not token:
            return False
        wanted = self._token_hash(token)
        return any(hmac.compare_digest(wanted, str(v)) for v in self._trusted().values())

    def pair(self, master_id: str, master_name: str, code: str) -> dict[str, Any]:
        if str(code).strip() != self.pairing_code:
            raise PermissionError("Pairing code is incorrect")
        if not master_id:
            raise ValueError("master_id is required")
        token = secrets.token_urlsafe(36)
        self._trusted()[master_id] = self._token_hash(token)
        names = self.manager.config.setdefault("trusted_master_names", {})
        if isinstance(names, dict):
            names[master_id] = str(master_name or master_id)
        self.manager.save()
        # Rotate the visible code after every successful pairing.
        self.pairing_code = f"{random.randint(0, 999999):06d}"
        return {"ok": True, "token": token, "node_id": self.manager.node_id, "hostname": socket.gethostname()}

    def capabilities(self) -> dict[str, Any]:
        return self.manager.backend_capabilities()

    def status(self) -> dict[str, Any]:
        total, avail = memory_gb()
        hw = self.manager.hardware.to_dict()
        hw["ram_total_gb"] = total or hw.get("ram_total_gb", 0)
        hw["ram_available_gb"] = avail or hw.get("ram_available_gb", 0)
        hw["link_speed_mbps"] = _link_speed_mbps()
        hw["cpu_capabilities"] = _cpu_capabilities()
        self.current_limits.normalize(hw)
        return {
            "ok": True, "node_id": self.manager.node_id, "hostname": socket.gethostname(),
            "version": self.manager.version, "state": "computing" if self.current_task else "connected",
            "hardware": hw, "capabilities": self.capabilities(), "limits": asdict(self.current_limits),
            "rpc": {
                "running": self.rpc_process.running, "pid": self.rpc_process.pid, "port": self.rpc_port,
                "memory_mb": process_memory_mb(self.rpc_process.pid),
                "cpu_percent": process_cpu_percent(self.rpc_process.pid, self.manager.hardware.logical_cores),
            },
            "current_task": self.current_task,
            "pairing_required": not bool(self._trusted()),
            "trusted_masters": len(self._trusted()),
            "last_master": self.last_master,
            "trusted_master_names": list((self.manager.config.get("trusted_master_names") or {}).values()) if isinstance(self.manager.config.get("trusted_master_names"), dict) else [],
        }

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        hw = self.manager.hardware.to_dict()
        total, avail = memory_gb(); hw["ram_total_gb"] = total or hw.get("ram_total_gb", 0); hw["ram_available_gb"] = avail or hw.get("ram_available_gb", 0)
        raw = payload.get("limits") if isinstance(payload.get("limits"), dict) else payload
        limits = NodeLimits(
            enabled=bool(raw.get("enabled", True)),
            ram_mode=str(raw.get("ram_mode") or "auto"), ram_limit_gb=_safe_float(raw.get("ram_limit_gb")),
            cpu_mode=str(raw.get("cpu_mode") or "auto"), cpu_threads=_safe_int(raw.get("cpu_threads")),
            cpu_usage_percent=_safe_int(raw.get("cpu_usage_percent"), 100),
        ).normalize(hw)
        self.current_limits = limits
        limits_key = str(payload.get("_limits_key") or "worker_limits")
        self.manager.config[limits_key] = asdict(limits)
        self.manager.save()
        return {"ok": True, "limits": asdict(limits)}

    def _rpc_flags(self, binary: str) -> set[str]:
        return self.manager.runtime.binary_supported_flags(binary)

    def start_rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        master_ip = str(payload.get("master_ip") or "").strip()
        if not _private_ip(master_ip):
            raise PermissionError("RPC workers may only be activated for a private/LAN/VPN master address")
        binary = self.manager.runtime.find_rpc_binary()
        if not binary:
            raise RuntimeError("ggml-rpc-server/rpc-server was not found. Install or build a llama.cpp runtime with GGML_RPC=ON")
        flags = self._rpc_flags(binary)
        self.configure(payload)
        limits = self.current_limits
        rpc_mem_flag = "--mem" in flags or "-m" in flags
        os_mem_cap = ManagedProcess.hard_memory_limit_supported()
        if not rpc_mem_flag and not os_mem_cap:
            raise RuntimeError("This Worker cannot enforce a hard RAM limit: the RPC runtime has no --mem support and this OS has no supported process-memory cap.")
        if self.rpc_process.running:
            # Restart if settings changed; keeps hard limit authoritative.
            self.rpc_process.stop()
        port = _safe_int(payload.get("rpc_port"), self.rpc_port)
        port = max(1024, min(65535, port))
        self.rpc_port = port
        bind_ip = str(payload.get("bind_ip") or "0.0.0.0")
        args = [binary]
        if "--host" in flags:
            args += ["--host", bind_ip]
        elif "-H" in flags:
            args += ["-H", bind_ip]
        if "--port" in flags:
            args += ["--port", str(port)]
        elif "-p" in flags:
            args += ["-p", str(port)]
        mem_mib = max(256, int(limits.ram_limit_gb * 1024))
        if "--mem" in flags:
            args += ["--mem", str(mem_mib)]
        elif "-m" in flags:
            args += ["-m", str(mem_mib)]
        if "--threads" in flags:
            args += ["--threads", str(max(1, limits.cpu_threads))]
        elif "-t" in flags:
            args += ["-t", str(max(1, limits.cpu_threads))]
        cache_requested = bool(payload.get("cache", True))
        if cache_requested:
            if "--cache" in flags:
                args += ["--cache"]
            elif "-c" in flags:
                args += ["-c"]
        self.current_task = "RPC ready for Master"
        self.last_master = master_ip
        self.manager.log("[cluster:worker] " + subprocess.list2cmdline(args))
        self.rpc_process.start(
            args, log_cb=lambda line: self.manager.log("[rpc-worker] " + line),
            policy=ProcessPolicy(
                affinity_count=limits.cpu_threads, memory_limit_mb=mem_mib, memory_limit_required=True
            ),
        )
        port_key = str(payload.get("_port_key") or "worker_rpc_port")
        self.manager.config[port_key] = port
        self.manager.save()
        return {"ok": True, "rpc_port": port, "pid": self.rpc_process.pid, "ram_limit_gb": limits.ram_limit_gb, "threads": limits.cpu_threads}

    def stop_rpc(self) -> dict[str, Any]:
        pid = self.rpc_process.pid
        if self.rpc_process.running:
            self.rpc_process.stop()
        self.current_task = ""
        return {"ok": True, "stopped": True, "pid": pid}

    def benchmark(self) -> dict[str, Any]:
        self.current_task = "Benchmarking"
        try:
            b = _benchmark_local()
            self.manager.config["worker_benchmark"] = asdict(b)
            self.manager.save()
            return {"ok": True, "benchmark": asdict(b)}
        finally:
            self.current_task = ""

    def start(self) -> None:
        if self.server:
            return
        self.stop_event.clear()
        manager = self.manager
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LlamaForgeWorker/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _json(self, obj: Any, status: int = 200):
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close"); self.end_headers(); self.close_connection = True
                try: self.wfile.write(raw)
                except Exception: pass

            def _body(self) -> dict[str, Any]:
                n = min(2 * 1024 * 1024, max(0, int(self.headers.get("Content-Length", "0") or 0)))
                raw = self.rfile.read(n) if n else b"{}"
                data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                return data if isinstance(data, dict) else {}

            def _authorized(self) -> bool:
                return service.authorize(str(self.headers.get("Authorization") or ""))

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == "/v1/ping":
                    return self._json({"ok": True, "node_id": manager.node_id, "version": manager.version})
                if not self._authorized():
                    return self._json({"error": "unauthorized"}, 401)
                if parsed.path == "/v1/status":
                    return self._json(service.status())
                if parsed.path == "/v1/network-test":
                    q = urllib.parse.parse_qs(parsed.query); size = max(1024, min(8 * 1024 * 1024, _safe_int((q.get("size") or ["2097152"])[0], 2097152)))
                    blob = b"L" * size
                    self.send_response(200); self.send_header("Content-Type", "application/octet-stream"); self.send_header("Content-Length", str(len(blob))); self.send_header("Connection", "close"); self.end_headers(); self.close_connection=True
                    try: self.wfile.write(blob)
                    except Exception: pass
                    return
                return self._json({"error": "not found"}, 404)

            def do_POST(self):
                try: body = self._body()
                except Exception as exc: return self._json({"error": str(exc)}, 400)
                if self.path == "/v1/pair":
                    try: return self._json(service.pair(str(body.get("master_id") or ""), str(body.get("master_name") or ""), str(body.get("pairing_code") or "")))
                    except PermissionError as exc: return self._json({"error": str(exc)}, 403)
                    except Exception as exc: return self._json({"error": str(exc)}, 400)
                if not self._authorized():
                    return self._json({"error": "unauthorized"}, 401)
                try:
                    if self.path == "/v1/configure": return self._json(service.configure(body))
                    if self.path == "/v1/rpc/start": return self._json(service.start_rpc(body))
                    if self.path == "/v1/rpc/stop": return self._json(service.stop_rpc())
                    if self.path == "/v1/benchmark": return self._json(service.benchmark())
                except PermissionError as exc:
                    return self._json({"error": str(exc)}, 403)
                except Exception as exc:
                    manager.log(f"[cluster:worker:error] {exc}")
                    return self._json({"error": str(exc)}, 500)
                return self._json({"error": "not found"}, 404)

        # Listen only on LAN/private interfaces; binding all interfaces is needed
        # for multi-NIC discovery, while every mutating endpoint remains paired.
        self.server = ThreadingHTTPServer(("0.0.0.0", int(manager.config.get("control_port", CONTROL_PORT) or CONTROL_PORT)), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="cluster-worker-control", daemon=True); self.thread.start()
        self.announce_thread = threading.Thread(target=self._announce_loop, name="cluster-worker-discovery", daemon=True); self.announce_thread.start()
        manager.log(f"[cluster] Worker mode ready; pairing code {self.pairing_code}")

    def _announce_loop(self):
        port = int(self.manager.config.get("control_port", CONTROL_PORT) or CONTROL_PORT)
        while not self.stop_event.is_set():
            packet = {
                "magic": DISCOVERY_MAGIC, "type": "worker", "node_id": self.manager.node_id,
                "hostname": socket.gethostname(), "control_port": port, "rpc_port": self.rpc_port,
                "version": self.manager.version, "paired": bool(self._trusted()),
                "state": "computing" if self.current_task else "ready",
            }
            raw = json.dumps(packet).encode("utf-8")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.settimeout(0.5)
                try: s.sendto(raw, ("255.255.255.255", DISCOVERY_PORT))
                finally: s.close()
            except Exception:
                pass
            self.stop_event.wait(2.0)

    def stop(self) -> None:
        self.stop_event.set()
        try: self.stop_rpc()
        except Exception: pass
        if self.server:
            try: self.server.shutdown(); self.server.server_close()
            except Exception: pass
        self.server = None
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=1.0)
        if self.announce_thread and self.announce_thread.is_alive(): self.announce_thread.join(timeout=1.0)


class ClusterManager:
    def __init__(
        self,
        runtime: RuntimeManager,
        hardware: HardwareInfo | None = None,
        version: str = "",
        log: Callable[[str], None] | None = None,
        on_node_lost: Callable[[str], None] | None = None,
    ):
        self.runtime = runtime
        self.hardware = hardware or detect_hardware()
        self.version = version
        self.log = log or (lambda _: None)
        self.on_node_lost = on_node_lost
        self.node_id = _host_id()
        self.config = _json_load(CLUSTER_PATH, {})
        if not isinstance(self.config, dict): self.config = {}
        self.config.setdefault("role", "standalone")
        self.config.setdefault("selection_mode", "auto")
        self.config.setdefault("optimization_mode", "smart")
        self.config.setdefault("enabled", False)
        self.config.setdefault("control_port", CONTROL_PORT)
        self.config.setdefault("worker_rpc_port", DEFAULT_RPC_PORT)
        self.config.setdefault("master_rpc_port", MASTER_RPC_PORT)
        self.config.setdefault("master_limits", {})
        self.config.setdefault("model_benchmarks", {})
        self.nodes: dict[str, ClusterNode] = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.discovery_thread: threading.Thread | None = None
        self.heartbeat_thread: threading.Thread | None = None
        self.worker: WorkerService | None = None
        self.local_rpc_service: WorkerService | None = None
        self.scheduler = ClusterScheduler()
        self.active_plan: ClusterPlan | None = None
        self._lost_reported: set[str] = set()
        self._load_known_nodes()

    def save(self) -> None:
        with self.lock:
            # Persist master tokens and user limits, never transient online/load data.
            known: dict[str, Any] = {}
            for node_id, n in self.nodes.items():
                known[node_id] = {
                    "node_id": n.node_id, "hostname": n.hostname, "ip": n.ip,
                    "control_port": n.control_port, "rpc_port": n.rpc_port, "version": n.version,
                    "paired": n.paired, "token": n.token, "limits": asdict(n.limits), "benchmark": asdict(n.benchmark),
                }
            self.config["known_nodes"] = known
            _json_save(CLUSTER_PATH, self.config)

    def _load_known_nodes(self) -> None:
        raw = self.config.get("known_nodes") if isinstance(self.config.get("known_nodes"), dict) else {}
        for node_id, item in raw.items():
            if not isinstance(item, dict): continue
            limits = NodeLimits(**item.get("limits", {})) if isinstance(item.get("limits"), dict) else NodeLimits()
            bench = NodeBenchmark(**item.get("benchmark", {})) if isinstance(item.get("benchmark"), dict) else NodeBenchmark()
            self.nodes[str(node_id)] = ClusterNode(
                node_id=str(node_id), hostname=str(item.get("hostname") or node_id), ip=str(item.get("ip") or ""),
                control_port=_safe_int(item.get("control_port"), CONTROL_PORT), rpc_port=_safe_int(item.get("rpc_port"), DEFAULT_RPC_PORT),
                version=str(item.get("version") or ""), paired=bool(item.get("paired")), token=str(item.get("token") or ""), limits=limits, benchmark=bench,
            )

    @property
    def role(self) -> str:
        role = str(self.config.get("role") or "standalone")
        return role if role in {"standalone", "master", "worker"} else "standalone"

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled")) and self.role == "master"

    def master_compute_node(self) -> ClusterNode:
        hw = self.hardware.to_dict()
        try:
            total, avail = memory_gb()
            hw["ram_total_gb"] = total or hw.get("ram_total_gb", 0)
            hw["ram_available_gb"] = avail or hw.get("ram_available_gb", 0)
        except Exception:
            pass
        hw["link_speed_mbps"] = 100000.0  # loopback path to local RPC backend
        hw["cpu_capabilities"] = _cpu_capabilities()
        raw_limits = self.config.get("master_limits") if isinstance(self.config.get("master_limits"), dict) else {}
        limits = NodeLimits(**{k:v for k,v in raw_limits.items() if k in NodeLimits.__dataclass_fields__}).normalize(hw)
        self.config["master_limits"] = asdict(limits)
        raw_bench = self.config.get("master_benchmark") if isinstance(self.config.get("master_benchmark"), dict) else {}
        bench = NodeBenchmark(**{k:v for k,v in raw_bench.items() if k in NodeBenchmark.__dataclass_fields__})
        return ClusterNode(
            node_id=MASTER_NODE_ID, hostname=socket.gethostname() + " (Master)", ip="127.0.0.1",
            control_port=0, rpc_port=_safe_int(self.config.get("master_rpc_port"), MASTER_RPC_PORT), version=self.version,
            online=True, paired=True, last_seen=_now(), state="ready", hardware=hw, capabilities=self.backend_capabilities(local_only=True),
            limits=limits, benchmark=bench, ping_ms=0.05, network_mbps=100000.0, current_task="Master local compute",
        )

    def backend_capabilities(self, local_only: bool = False) -> dict[str, Any]:
        server_flags = self.runtime.supported_server_flags()
        rpc_bin = self.runtime.find_rpc_binary()
        rpc_flags = self.runtime.binary_supported_flags(rpc_bin) if rpc_bin else set()
        return {
            "backend": "llama.cpp-rpc",
            "server_rpc": "--rpc" in server_flags,
            "tensor_split": "--tensor-split" in server_flags,
            "rpc_binary": rpc_bin or "",
            "rpc_hard_memory_limit": bool("--mem" in rpc_flags or "-m" in rpc_flags or ManagedProcess.hard_memory_limit_supported()),
            "rpc_memory_advertise_flag": bool("--mem" in rpc_flags or "-m" in rpc_flags),
            "os_process_memory_cap": ManagedProcess.hard_memory_limit_supported(),
            "rpc_cache": "--cache" in rpc_flags or "-c" in rpc_flags,
            "distributed_compute": bool("--rpc" in server_flags and rpc_bin),
            # Capability matrix is explicit so future backends can implement these
            # without pretending the current llama.cpp RPC backend provides them.
            "layer_parallelism": bool("--split-mode" in server_flags or "--tensor-split" in server_flags),
            "tensor_parallelism": bool("--split-mode" in server_flags and "--tensor-split" in server_flags),
            "pipeline_parallelism": bool("--split-mode" in server_flags),
            "hybrid_parallelism": False,
            "security": "paired-control + private-LAN/VPN RPC transport",
        }

    def start_background(self) -> None:
        self.set_role(self.role, enabled=bool(self.config.get("enabled")), persist=False)

    def set_role(self, role: str, enabled: bool | None = None, persist: bool = True) -> dict[str, Any]:
        role = str(role or "standalone").lower()
        if role not in {"standalone", "master", "worker"}:
            raise ValueError("role must be standalone, master or worker")
        try:
            self.stop_cluster_runtime()
        except Exception:
            pass
        self.stop_background()
        self.stop_event = threading.Event()
        self.config["role"] = role
        if enabled is not None: self.config["enabled"] = bool(enabled)
        if role == "master":
            self.discovery_thread = threading.Thread(target=self._discovery_loop, name="cluster-discovery", daemon=True); self.discovery_thread.start()
            self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="cluster-heartbeat", daemon=True); self.heartbeat_thread.start()
            self.log("[cluster] Master mode enabled")
        elif role == "worker":
            self.config["enabled"] = False
            self.worker = WorkerService(self); self.worker.start()
        else:
            self.config["enabled"] = False
            self.log("[cluster] Standalone mode enabled")
        if persist: self.save()
        return self.snapshot()

    def stop_background(self) -> None:
        self.stop_event.set()
        if self.worker:
            try: self.worker.stop()
            except Exception: pass
        self.worker = None
        if self.discovery_thread and self.discovery_thread.is_alive(): self.discovery_thread.join(timeout=0.7)
        if self.heartbeat_thread and self.heartbeat_thread.is_alive(): self.heartbeat_thread.join(timeout=0.7)
        self.discovery_thread = None; self.heartbeat_thread = None

    def shutdown(self) -> None:
        try: self.stop_cluster_runtime()
        except Exception: pass
        self.stop_background()

    def _discovery_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", DISCOVERY_PORT)); sock.settimeout(1.0)
            while not self.stop_event.is_set() and self.role == "master":
                try: raw, addr = sock.recvfrom(32 * 1024)
                except socket.timeout: continue
                except OSError: break
                try: data = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception: continue
                if not isinstance(data, dict) or data.get("magic") != DISCOVERY_MAGIC or data.get("type") != "worker": continue
                node_id = str(data.get("node_id") or "").strip()
                ip = str(addr[0] or "")
                if not node_id or not _private_ip(ip): continue
                with self.lock:
                    node = self.nodes.get(node_id)
                    if node is None:
                        node = ClusterNode(node_id=node_id, hostname=str(data.get("hostname") or node_id), ip=ip)
                        self.nodes[node_id] = node
                    node.hostname = str(data.get("hostname") or node.hostname); node.ip = ip
                    node.control_port = _safe_int(data.get("control_port"), CONTROL_PORT); node.rpc_port = _safe_int(data.get("rpc_port"), DEFAULT_RPC_PORT)
                    node.version = str(data.get("version") or node.version); node.online = True; node.last_seen = _now(); node.state = str(data.get("state") or "ready")
                    if node_id in self._lost_reported: self._lost_reported.discard(node_id)
        finally:
            try: sock.close()
            except Exception: pass

    def _worker_url(self, node: ClusterNode, path: str) -> str:
        return f"http://{node.ip}:{node.control_port}{path}"

    def _request(self, node: ClusterNode, path: str, method: str = "GET", body: dict[str, Any] | None = None, token: str | None = None, timeout: float = 4.0) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"User-Agent": "LlamaForge-Cluster/1", "Content-Type": "application/json"}
        auth = token if token is not None else node.token
        if auth: headers["Authorization"] = "Bearer " + auth
        req = urllib.request.Request(self._worker_url(node, path), data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace") or "{}")
                if isinstance(data, dict) and data.get("error"): raise RuntimeError(str(data.get("error")))
                return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as exc:
            try: detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("error")
            except Exception: detail = str(exc)
            raise RuntimeError(str(detail or exc)) from exc

    def pair_node(self, node_id: str, pairing_code: str) -> dict[str, Any]:
        with self.lock: node = self.nodes.get(node_id)
        if not node: raise KeyError("Worker not found")
        if not _private_ip(node.ip): raise PermissionError("Pairing is only allowed on a private LAN/VPN address")
        data = self._request(node, "/v1/pair", "POST", {"master_id": self.node_id, "master_name": socket.gethostname(), "pairing_code": pairing_code}, token="", timeout=5.0)
        token = str(data.get("token") or "")
        if not token: raise RuntimeError("Worker did not return a pairing token")
        node.token = token; node.paired = True; node.online = True; node.last_seen = _now(); self.save()
        self.refresh_node(node_id, include_network=True)
        return node.to_public()

    def forget_node(self, node_id: str) -> None:
        with self.lock: self.nodes.pop(node_id, None)
        self.save()

    def refresh_node(self, node_id: str, include_network: bool = False) -> ClusterNode:
        with self.lock: node = self.nodes.get(node_id)
        if not node: raise KeyError("Worker not found")
        if not node.paired or not node.token: return node
        start = time.perf_counter(); data = self._request(node, "/v1/status", timeout=3.0); latency = (time.perf_counter() - start) * 1000.0
        node.online = True; node.last_seen = _now(); node.ping_ms = round(latency, 2)
        node.version = str(data.get("version") or node.version); node.state = str(data.get("state") or "ready")
        if isinstance(data.get("hardware"), dict): node.hardware = data["hardware"]
        if isinstance(data.get("capabilities"), dict): node.capabilities = data["capabilities"]
        if isinstance(data.get("limits"), dict):
            remote_limits = NodeLimits(**{k:v for k,v in data["limits"].items() if k in NodeLimits.__dataclass_fields__})
            # Master-owned limits remain authoritative after pairing, but initialize
            # previously untouched nodes from Worker recommendations.
            if node.limits.ram_limit_gb <= 0: node.limits = remote_limits
        rpc = data.get("rpc") if isinstance(data.get("rpc"), dict) else {}
        node.rpc_running = bool(rpc.get("running")); node.rpc_pid = rpc.get("pid"); node.ram_used_gb = round(_safe_float(rpc.get("memory_mb")) / 1024.0, 3); node.cpu_percent = _safe_float(rpc.get("cpu_percent")); node.current_task = str(data.get("current_task") or "")
        if include_network:
            try:
                size = 2 * 1024 * 1024
                req = urllib.request.Request(self._worker_url(node, f"/v1/network-test?size={size}"), headers={"Authorization":"Bearer "+node.token, "User-Agent":"LlamaForge-Cluster/1"})
                t0 = time.perf_counter()
                with urllib.request.urlopen(req, timeout=6.0) as r: blob = r.read(size + 1024)
                dt = max(0.001, time.perf_counter() - t0)
                node.network_mbps = round(len(blob) * 8 / dt / 1_000_000, 1)
            except Exception: pass
        if node.benchmark.valid:
            node.benchmark.latency_ms = node.ping_ms; node.benchmark.network_mbps = node.network_mbps or node.benchmark.network_mbps
        return node

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set() and self.role == "master":
            with self.lock: paired = [n for n in self.nodes.values() if n.paired and n.token]
            for node in paired:
                try:
                    self.refresh_node(node.node_id, include_network=False)
                    self._lost_reported.discard(node.node_id)
                except Exception:
                    was_online = node.online
                    if _now() - node.last_seen > 5.5: node.online = False; node.state = "offline"
                    if was_online and not node.online and node.node_id not in self._lost_reported:
                        self._lost_reported.add(node.node_id); self.log(f"[cluster] Worker lost: {node.hostname} ({node.ip})")
                        if self.active_plan and any(x.get("node_id") == node.node_id for x in self.active_plan.nodes) and self.on_node_lost:
                            try: self.on_node_lost(node.node_id)
                            except Exception as exc: self.log(f"[cluster:recovery] callback failed: {exc}")
            self.stop_event.wait(2.0)

    def update_node(self, node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if node_id == MASTER_NODE_ID:
            node = self.master_compute_node()
            raw = payload.get("limits") if isinstance(payload.get("limits"), dict) else payload
            node.limits = NodeLimits(
                enabled=bool(raw.get("enabled", node.limits.enabled)),
                ram_mode=str(raw.get("ram_mode") or node.limits.ram_mode),
                ram_limit_gb=_safe_float(raw.get("ram_limit_gb"), node.limits.ram_limit_gb),
                cpu_mode=str(raw.get("cpu_mode") or node.limits.cpu_mode),
                cpu_threads=_safe_int(raw.get("cpu_threads"), node.limits.cpu_threads),
                cpu_usage_percent=_safe_int(raw.get("cpu_usage_percent"), node.limits.cpu_usage_percent),
            ).normalize(node.hardware)
            self.config["master_limits"] = asdict(node.limits); self.save(); return node.to_public()
        with self.lock: node = self.nodes.get(node_id)
        if not node: raise KeyError("Worker not found")
        raw = payload.get("limits") if isinstance(payload.get("limits"), dict) else payload
        node.limits = NodeLimits(
            enabled=bool(raw.get("enabled", node.limits.enabled)),
            ram_mode=str(raw.get("ram_mode") or node.limits.ram_mode),
            ram_limit_gb=_safe_float(raw.get("ram_limit_gb"), node.limits.ram_limit_gb),
            cpu_mode=str(raw.get("cpu_mode") or node.limits.cpu_mode),
            cpu_threads=_safe_int(raw.get("cpu_threads"), node.limits.cpu_threads),
            cpu_usage_percent=_safe_int(raw.get("cpu_usage_percent"), node.limits.cpu_usage_percent),
        ).normalize(node.hardware or self.hardware.to_dict())
        if node.paired and node.online:
            self._request(node, "/v1/configure", "POST", {"limits": asdict(node.limits)})
        self.save(); return node.to_public()

    def set_modes(self, selection_mode: str | None = None, optimization_mode: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        if selection_mode is not None:
            if selection_mode not in ClusterScheduler.SELECTION: raise ValueError("Invalid cluster selection mode")
            self.config["selection_mode"] = selection_mode
        if optimization_mode is not None:
            if optimization_mode not in ClusterScheduler.MODES: raise ValueError("Invalid cluster optimization mode")
            self.config["optimization_mode"] = optimization_mode
        if enabled is not None: self.config["enabled"] = bool(enabled)
        self.save(); return self.snapshot()

    def benchmark_node(self, node_id: str) -> dict[str, Any]:
        if node_id == MASTER_NODE_ID:
            b = _benchmark_local(); b.network_mbps = 100000.0; b.latency_ms = 0.05
            self.config["master_benchmark"] = asdict(b); self.save(); return asdict(b)
        with self.lock: node = self.nodes.get(node_id)
        if not node or not node.paired: raise RuntimeError("Pair the Worker first")
        t0 = time.perf_counter(); data = self._request(node, "/v1/benchmark", "POST", {}, timeout=30.0); latency = (time.perf_counter()-t0)*1000
        raw = data.get("benchmark") if isinstance(data.get("benchmark"), dict) else {}
        node.benchmark = NodeBenchmark(**{k:v for k,v in raw.items() if k in NodeBenchmark.__dataclass_fields__})
        node.benchmark.latency_ms = round(node.ping_ms or latency, 2)
        try: self.refresh_node(node_id, include_network=True)
        except Exception: pass
        node.benchmark.network_mbps = node.network_mbps or node.benchmark.network_mbps
        self.save(); return asdict(node.benchmark)

    def plan_for_model(self, model_size_gb: float, runtime_overhead_gb: float) -> ClusterPlan:
        with self.lock: nodes = [self.master_compute_node(), *list(self.nodes.values())]
        return self.scheduler.plan(nodes, model_size_gb, runtime_overhead_gb, str(self.config.get("optimization_mode") or "smart"), str(self.config.get("selection_mode") or "auto"))

    def prepare_launch(self, model_size_gb: float, runtime_overhead_gb: float, model_key: str = "") -> ClusterPlan | None:
        if not self.enabled:
            self.active_plan = None
            return None
        caps = self.backend_capabilities()
        if not caps.get("server_rpc"):
            raise RuntimeError("The active llama-server was not built with RPC support (--rpc missing). Build/activate a cluster-capable llama.cpp runtime first.")
        plan = self.plan_for_model(model_size_gb, runtime_overhead_gb)
        master_ip = _local_ipv4s()[0]
        started: list[ClusterNode] = []
        try:
            for row in plan.nodes:
                node_id = str(row["node_id"])
                if node_id == MASTER_NODE_ID:
                    node = self.master_compute_node()
                    if not node.capabilities.get("rpc_hard_memory_limit", True):
                        raise RuntimeError("Master cannot enforce its configured hard RAM limit with the current runtime/OS")
                    svc = WorkerService(self)
                    svc.current_limits = node.limits
                    svc.rpc_port = node.rpc_port
                    result = svc.start_rpc({
                        "master_ip": "127.0.0.1", "bind_ip": "127.0.0.1", "rpc_port": node.rpc_port, "cache": True,
                        "limits": asdict(node.limits), "_limits_key": "master_limits", "_port_key": "master_rpc_port",
                    })
                    self.local_rpc_service = svc
                    node.rpc_port = _safe_int(result.get("rpc_port"), node.rpc_port); node.rpc_running = True; node.rpc_pid = result.get("pid")
                    started.append(node)
                else:
                    node = self.nodes[node_id]
                    if not node.capabilities.get("rpc_hard_memory_limit", True):
                        raise RuntimeError(f"{node.hostname} cannot enforce a hard RPC memory limit with its current runtime")
                    payload = {
                        "master_ip": master_ip, "rpc_port": node.rpc_port, "cache": True,
                        "limits": asdict(node.limits),
                    }
                    result = self._request(node, "/v1/rpc/start", "POST", payload, timeout=12.0)
                    node.rpc_port = _safe_int(result.get("rpc_port"), node.rpc_port); node.rpc_running = True; node.rpc_pid = result.get("pid"); node.current_task = "Distributed inference"
                    started.append(node)
            # RPC endpoint order matches plan/tensor_split order exactly.
            endpoints = []
            for row in plan.nodes:
                nid = str(row["node_id"])
                if nid == MASTER_NODE_ID:
                    endpoints.append(f"127.0.0.1:{_safe_int(self.config.get('master_rpc_port'), MASTER_RPC_PORT)}")
                else:
                    wn = self.nodes[nid]; endpoints.append(f"{wn.ip}:{wn.rpc_port}")
            plan.rpc_servers = endpoints
            self.active_plan = plan
            setattr(plan, "model_key", str(model_key or ""))
            self.config["active_model_key"] = str(model_key or "")
            self.save()
            self.log(f"[cluster] plan={plan.plan_id} strategy={plan.strategy} nodes={len(plan.nodes)} rpc={','.join(plan.rpc_servers)}")
            return plan
        except Exception:
            if self.local_rpc_service:
                try: self.local_rpc_service.stop_rpc()
                except Exception: pass
                self.local_rpc_service = None
            for node in started:
                if node.node_id == MASTER_NODE_ID:
                    continue
                try: self._request(node, "/v1/rpc/stop", "POST", {}, timeout=3.0)
                except Exception: pass
            self.active_plan = None
            raise

    def record_runtime_result(self, model_key: str, generation_tps: float) -> None:
        """Cache measured decode throughput for the exact model + node set.

        This is intentionally observational: it never guesses token/s. The
        scheduler can later compare real configurations collected on this LAN.
        """
        plan = self.active_plan
        if not plan or generation_tps <= 0:
            return
        key = str(model_key or self.config.get("active_model_key") or "").strip()
        if not key:
            return
        node_ids = sorted(str(row.get("node_id") or "") for row in plan.nodes if row.get("node_id"))
        signature = "+".join(node_ids) or "master"
        benches = self.config.setdefault("model_benchmarks", {})
        if not isinstance(benches, dict):
            benches = {}; self.config["model_benchmarks"] = benches
        model_rows = benches.setdefault(key, {})
        if not isinstance(model_rows, dict):
            model_rows = {}; benches[key] = model_rows
        previous = model_rows.get(signature) if isinstance(model_rows.get(signature), dict) else {}
        count = max(0, _safe_int(previous.get("samples"), 0))
        old = max(0.0, _safe_float(previous.get("avg_tps"), 0.0))
        avg = ((old * count) + float(generation_tps)) / (count + 1)
        model_rows[signature] = {
            "avg_tps": round(avg, 3), "last_tps": round(float(generation_tps), 3),
            "samples": count + 1, "updated_at": _now(), "plan_id": plan.plan_id,
            "node_ids": node_ids, "strategy": plan.strategy,
        }
        self.save()

    def model_benchmarks(self, model_key: str = "") -> dict[str, Any]:
        benches = self.config.get("model_benchmarks") if isinstance(self.config.get("model_benchmarks"), dict) else {}
        if model_key:
            return dict(benches.get(model_key) or {}) if isinstance(benches.get(model_key), dict) else {}
        return dict(benches)

    def stop_cluster_runtime(self) -> None:
        if self.local_rpc_service:
            try: self.local_rpc_service.stop_rpc()
            except Exception: pass
            self.local_rpc_service = None
        with self.lock: nodes = list(self.nodes.values())
        for node in nodes:
            if node.paired and node.rpc_running and node.online:
                try: self._request(node, "/v1/rpc/stop", "POST", {}, timeout=3.0)
                except Exception: pass
                node.rpc_running = False; node.rpc_pid = None; node.current_task = ""; node.compute_share = 0; node.allocated_ram_gb = 0
        self.active_plan = None

    def save_profile(self, name: str) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name: raise ValueError("Profile name is required")
        profiles = _json_load(PROFILE_PATH, {})
        if not isinstance(profiles, dict): profiles = {}
        with self.lock:
            profiles[name] = {
                "selection_mode": self.config.get("selection_mode", "auto"), "optimization_mode": self.config.get("optimization_mode", "smart"),
                "nodes": {nid: asdict(n.limits) for nid, n in self.nodes.items()}, "master_limits": dict(self.config.get("master_limits") or {}), "updated_at": _now(),
            }
        _json_save(PROFILE_PATH, profiles); return {"ok": True, "name": name}

    def load_profile(self, name: str) -> dict[str, Any]:
        profiles = _json_load(PROFILE_PATH, {})
        profile = profiles.get(name) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict): raise KeyError("Cluster profile not found")
        self.config["selection_mode"] = profile.get("selection_mode", "auto"); self.config["optimization_mode"] = profile.get("optimization_mode", "smart")
        node_limits = profile.get("nodes") if isinstance(profile.get("nodes"), dict) else {}
        if isinstance(profile.get("master_limits"), dict): self.config["master_limits"] = dict(profile.get("master_limits") or {})
        with self.lock:
            for nid, raw in node_limits.items():
                if nid in self.nodes and isinstance(raw, dict):
                    self.nodes[nid].limits = NodeLimits(**{k:v for k,v in raw.items() if k in NodeLimits.__dataclass_fields__}).normalize(self.nodes[nid].hardware or self.hardware.to_dict())
        self.save(); return self.snapshot()

    def worker_autostart_status(self) -> bool:
        run_py = Path(__file__).resolve().parents[2] / "run.py"
        system = platform.system().lower()
        try:
            if system == "windows":
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                    value, _ = winreg.QueryValueEx(key, "LlamaForgeWorker")
                return str(run_py).lower() in str(value).lower()
            if system == "darwin":
                return (Path.home()/"Library"/"LaunchAgents"/"com.llamaforge.worker.plist").exists()
            return (Path.home()/".config"/"autostart"/"llamaforge-worker.desktop").exists()
        except Exception:
            return False

    def set_worker_autostart(self, enabled: bool) -> bool:
        run_py = Path(__file__).resolve().parents[2] / "run.py"
        if not run_py.is_file():
            raise RuntimeError("LlamaForge run.py was not found for Worker auto-start")
        command = f'"{sys.executable}" "{run_py}" --worker --no-browser'
        system = platform.system().lower()
        if system == "windows":
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                if enabled:
                    winreg.SetValueEx(key, "LlamaForgeWorker", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "LlamaForgeWorker")
                    except FileNotFoundError:
                        pass
        elif system == "darwin":
            path = Path.home()/"Library"/"LaunchAgents"/"com.llamaforge.worker.plist"
            path.parent.mkdir(parents=True, exist_ok=True)
            if enabled:
                plist = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                         '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                         '<plist version="1.0"><dict><key>Label</key><string>com.llamaforge.worker</string>'
                         '<key>ProgramArguments</key><array><string>'+str(sys.executable)+'</string><string>'+str(run_py)+'</string>'
                         '<string>--worker</string><string>--no-browser</string></array><key>RunAtLoad</key><true/></dict></plist>')
                path.write_text(plist, encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
        else:
            path = Path.home()/".config"/"autostart"/"llamaforge-worker.desktop"
            path.parent.mkdir(parents=True, exist_ok=True)
            if enabled:
                path.write_text(f"[Desktop Entry]\nType=Application\nName=LlamaForge Worker\nExec={command}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
        return self.worker_autostart_status()

    def profiles(self) -> list[dict[str, Any]]:
        profiles = _json_load(PROFILE_PATH, {})
        if not isinstance(profiles, dict): return []
        return [{"name": name, **(value if isinstance(value, dict) else {})} for name, value in profiles.items()]

    def snapshot(self) -> dict[str, Any]:
        with self.lock: nodes = [n.to_public() for n in self.nodes.values()]
        online = sum(1 for n in nodes if n.get("online")); paired = sum(1 for n in nodes if n.get("paired"))
        allowed = 0.0
        threads = 0
        for row in nodes:
            if row.get("online") and row.get("paired") and row.get("limits", {}).get("enabled", True):
                limits = NodeLimits(**row.get("limits", {})).normalize(row.get("hardware", {})); allowed += limits.ram_limit_gb; threads += limits.cpu_threads
        master_compute = self.master_compute_node().to_public() if self.role == "master" else None
        if master_compute and master_compute.get("limits", {}).get("enabled", True):
            ml = NodeLimits(**master_compute.get("limits", {})).normalize(master_compute.get("hardware", {})); allowed += ml.ram_limit_gb; threads += ml.cpu_threads
        worker_status = self.worker.status() if self.worker else None
        return {
            "role": self.role, "enabled": self.enabled, "node_id": self.node_id, "hostname": socket.gethostname(),
            "selection_mode": str(self.config.get("selection_mode") or "auto"), "optimization_mode": str(self.config.get("optimization_mode") or "smart"),
            "workers_online": online, "workers_registered": len(nodes), "workers_paired": paired,
            "allowed_cluster_ram_gb": round(allowed, 2), "active_cpu_threads": threads,
            "nodes": sorted(nodes, key=lambda x: (not bool(x.get("online")), str(x.get("hostname") or ""))),
            "backend": self.backend_capabilities(), "active_plan": self.active_plan.to_dict() if self.active_plan else None,
            "worker": worker_status,
            "master_compute": master_compute,
            "pairing_code": self.worker.pairing_code if self.worker else "",
            "profiles": self.profiles(),
            "worker_autostart": self.worker_autostart_status(),
            "model_benchmarks": self.model_benchmarks(str(self.config.get("active_model_key") or "")),
        }
