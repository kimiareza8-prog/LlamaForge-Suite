from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR
from .hardware import HardwareInfo
from .models import LocalModel
from .system_metrics import memory_gb, process_memory_mb


@dataclass
class TuneResult:
    model_key: str
    model_path: str
    measured_at: float
    backend: str
    threads: int
    threads_batch: int
    gpu_layers: int
    gpu_layer_percent: int
    batch_size: int
    ubatch_size: int
    prompt_tps: float
    generation_tps: float
    estimated_ttft_ms: float
    score: float
    samples: int
    speculative_mode: str = "off"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdaptiveTuner:
    """Small persistent per-model tuner backed by llama-bench.

    The tuner deliberately benchmarks the *installed runtime* instead of trying
    to infer performance from utilization percentages. Results are keyed by the
    concrete GGUF file identity (path, size, mtime) so replacing/requantizing a
    model automatically invalidates stale tuning data.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or (APP_DIR / "autotune.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def model_key(model: LocalModel) -> str:
        p = Path(model.path).expanduser()
        try:
            st = p.stat()
            raw = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|{AdaptiveTuner._file_identity(p)}|{model.quantization}"
        except Exception:
            raw = f"{p}|{getattr(model, 'size_gb', 0)}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]

    @staticmethod
    def _file_identity(path: Path) -> dict[str, Any]:
        """Bounded header/tail digest plus stat; never read a whole GGUF."""
        try:
            st = path.stat()
            with path.open("rb") as f:
                first = f.read(65536)
                f.seek(max(0, st.st_size - 65536))
                digest = hashlib.sha256(first + f.read(65536)).hexdigest()
            return {"name": str(path.resolve()), "size": st.st_size, "mtime": st.st_mtime_ns, "sample_hash": digest}
        except OSError:
            return {"name": str(path), "missing": True}

    @staticmethod
    def environment(hw: HardwareInfo, bench_path: str, backend: str = "", *,
                    context: int = 8192, load_mode: str = "hybrid", profile: str = "interactive") -> dict:
        binary = Path(bench_path)
        siblings = sorted(p for p in binary.parent.glob("*")
                          if p.is_file() and (p.name.startswith("llama-server") or p.suffix in {".dll", ".so", ".dylib"})) if bench_path else []
        return {"schema": 2, "runtime": AdaptiveTuner._file_identity(binary),
                "libraries": [AdaptiveTuner._file_identity(p) for p in siblings],
                "backend": backend, "cpu": hw.cpu, "cores": [hw.physical_cores, hw.logical_cores],
                "os": [hw.os_name, hw.os_version, hw.machine], "ram": hw.ram_total_gb,
                "gpus": [asdict(g) for g in hw.gpus], "context": int(context),
                "load_mode": load_mode, "profile": profile}

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, model: LocalModel, *, environment: dict | None = None) -> dict[str, Any] | None:
        key = self.model_key(model)
        with self._lock:
            row = self._load().get(key)
        if not isinstance(row, dict) or row.get("environment") != environment:
            return None
        return dict(row)

    def put(self, model: LocalModel, result: TuneResult, *, environment: dict | None = None, details: dict | None = None) -> dict[str, Any]:
        key = self.model_key(model)
        row = result.to_dict()
        row["model_key"] = key
        row["environment"] = environment
        row.update(details or {})
        with self._lock:
            data = self._load()
            data[key] = row
            self._save(data)
        return row

    def clear(self, model: LocalModel | None = None) -> None:
        with self._lock:
            if model is None:
                self._save({})
                return
            data = self._load()
            data.pop(self.model_key(model), None)
            self._save(data)

    @staticmethod
    def _gpu_candidates(model: LocalModel, hw: HardwareInfo) -> list[int]:
        blocks = max(0, int(getattr(model, "block_count", 0) or 0))
        if not hw.gpus:
            return [0]
        if blocks <= 1:
            return [0, 2, 8, -1]
        # Dense enough around the low-offload region to catch iGPU sweet spots,
        # while still testing a high/full-offload path for stronger GPUs.
        pcts = [0, 5, 10, 20, 35, 60, 100]
        vals: list[int] = []
        for pct in pcts:
            if pct <= 0:
                n = 0
            elif pct >= 100:
                n = -1
            else:
                n = max(1, min(blocks - 1, int(round(blocks * pct / 100.0))))
            if n not in vals:
                vals.append(n)
        return vals

    @staticmethod
    def _thread_candidates(hw: HardwareInfo) -> list[int]:
        vals = [max(1, int(hw.physical_cores)), max(1, int(hw.logical_cores))]
        if hw.logical_cores >= 6:
            vals.insert(1, max(1, int(round(hw.logical_cores * 0.75))))
        out: list[int] = []
        for v in vals:
            if v not in out:
                out.append(v)
        return out

    @staticmethod
    def _parse_rows(stdout: str) -> list[dict[str, Any]]:
        text = str(stdout or "").strip()
        if not text:
            return []
        # Official llama-bench -o json prints a single array. Be tolerant of
        # backend banners before it by selecting the outermost JSON array.
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        try:
            raw = json.loads(text)
        except Exception as exc:
            raise RuntimeError(f"llama-bench returned unreadable JSON: {exc}") from exc
        return [dict(x) for x in raw] if isinstance(raw, list) else []

    @staticmethod
    def _summarize(rows: list[dict[str, Any]], model: LocalModel, profile: str = "interactive") -> list[dict[str, Any]]:
        groups: dict[tuple[int, int, int, int], dict[str, Any]] = {}
        for row in rows:
            try:
                key = (
                    int(row.get("n_threads") or 0),
                    int(row.get("n_gpu_layers") if row.get("n_gpu_layers") is not None else 0),
                    int(row.get("n_batch") or 0),
                    int(row.get("n_ubatch") or 0),
                )
            except Exception:
                continue
            g = groups.setdefault(key, {"threads": key[0], "gpu_layers": key[1], "batch_size": key[2], "ubatch_size": key[3], "pp_samples": [], "tg_samples": [], "prompt_tokens": 0, "peak_ram_mb": 0.0, "rows": 0})
            try:
                ts = float(row.get("avg_ts") or 0.0)
                n_prompt = int(row.get("n_prompt") or 0)
                n_gen = int(row.get("n_gen") or 0)
                values = [float(x) for x in (row.get("samples_ts") or [ts]) if math.isfinite(float(x)) and float(x) > 0]
            except Exception:
                continue
            if n_prompt > 0 and n_gen <= 0:
                g["pp_samples"].extend(values)
                g["prompt_tokens"] = max(g["prompt_tokens"], n_prompt)
            if n_gen > 0 and n_prompt == 0:
                g["tg_samples"].extend(values)
            g["peak_ram_mb"] = max(g["peak_ram_mb"], float(row.get("peak_ram_mb") or 0))
            g["rows"] += 1
        samples = [g for g in groups.values() if g["tg_samples"] and g["pp_samples"]]
        for g in samples:
            g["pp"], g["tg"] = statistics.median(g["pp_samples"]), statistics.median(g["tg_samples"])
            g["stability"] = min(min(g[k]) / statistics.median(g[k]) for k in ("pp_samples", "tg_samples"))
        if not samples:
            return []
        max_pp = max(max(float(x["pp"]), 0.001) for x in samples)
        max_tg = max(max(float(x["tg"]), 0.001) for x in samples)
        blocks = max(0, int(getattr(model, "block_count", 0) or 0))
        for g in samples:
            pp, tg = max(float(g["pp"]), 0.001), max(float(g["tg"]), 0.001)
            # Approximate time-to-first-token from a short 64-token prefill plus
            # one decode token. llama-bench excludes tokenization/sampling, which
            # is desirable here because we compare compute plans only.
            ttft = (g["prompt_tokens"] / pp + 1.0 / tg) * 1000.0
            g["ttft_ms"] = round(ttft, 2)
            # Decode speed dominates interactive chat; prompt speed and first-token
            # latency still matter enough to prevent pathological winners.
            tg_norm = tg / max_tg
            pp_norm = pp / max_pp
            latency_norm = 1.0 / (1.0 + ttft / 1000.0)
            weights = (0.35, 0.60, 0.05) if profile == "throughput" else (0.65, 0.20, 0.15)
            score = 100.0 * sum(w * x for w, x in zip(weights, (tg_norm, pp_norm, latency_norm)))
            if profile == "fit_largest" and g["peak_ram_mb"]:
                score /= 1.0 + g["peak_ram_mb"] / 4096.0
            g["score"] = round(score * g["stability"], 3)
            ngl = int(g["gpu_layers"])
            if ngl < 0:
                pct = 100
            elif blocks > 0:
                pct = int(round(100.0 * ngl / blocks))
            else:
                pct = 0 if ngl == 0 else 35
            g["gpu_layer_percent"] = max(0, min(100, pct))
        return sorted(samples, key=lambda x: (float(x["score"]), float(x["tg"]), float(x["pp"])), reverse=True)

    @staticmethod
    def _run_candidate(cmd: list[str], cancel: threading.Event | None = None,
                       timeout: float = 900) -> list[dict[str, Any]]:
        if cancel and cancel.is_set():
            raise RuntimeError("AutoTune cancelled")
        started = time.monotonic()
        peak_mb = 0.0
        minimum_free = float("inf")
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out_f, tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_f:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, text=True)
            try:
                while proc.poll() is None:
                    if cancel and cancel.is_set():
                        raise RuntimeError("AutoTune cancelled")
                    if time.monotonic() - started > timeout:
                        raise RuntimeError("AutoTune candidate timed out")
                    total, free = memory_gb()
                    if total > 0:
                        minimum_free = min(minimum_free, free)
                        if free < max(0.5, total * 0.04):
                            raise RuntimeError("AutoTune candidate stopped: memory pressure")
                    peak_mb = max(peak_mb, process_memory_mb(proc.pid))
                    if cancel:
                        cancel.wait(0.1)
                    else:
                        time.sleep(0.1)
                out_f.seek(0)
                err_f.seek(0)
                stdout, stderr = out_f.read(), err_f.read()
                if proc.returncode:
                    raise RuntimeError(f"llama-bench failed ({proc.returncode}): {stderr[-2000:]}")
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
        rows = AdaptiveTuner._parse_rows(stdout)
        for row in rows:
            row.update(peak_ram_mb=peak_mb or None,
                       min_free_ram_gb=minimum_free if math.isfinite(minimum_free) else None,
                       candidate_wall_seconds=round(time.monotonic() - started, 3))
        return rows

    def benchmark(
        self, model: LocalModel, hw: HardwareInfo, bench_path: str, backend: str = "",
        progress_cb: Callable[[str, float], None] | None = None,
        cancel: threading.Event | None = None, *, environment: dict | None = None,
        profile: str = "interactive",
    ) -> dict[str, Any]:
        if not bench_path or not Path(bench_path).is_file():
            raise RuntimeError("llama-bench is not installed in the active runtime")
        if profile not in {"interactive", "throughput", "fit_largest"}:
            raise ValueError("Unknown AutoTune scoring profile")
        environment = environment or self.environment(hw, bench_path, backend, profile=profile)
        rows: list[dict] = []
        failures: list[dict] = []
        attempted: set[tuple[int, int, int, int]] = set()
        threads = self._thread_candidates(hw)
        gpus = [0] if backend.lower() == "cpu" else self._gpu_candidates(model, hw)
        low_memory = model.size_gb > (hw.ram_available_gb or hw.ram_total_gb) * 0.7
        batches = [(64, 32), (128, 64)] if low_memory else [(128, 64), (256, 64), (256, 128), (512, 128), (512, 256)]
        initial_b, initial_ub = batches[0]
        # Coordinate search: threads, offload, batch/ubatch, then recheck threads
        # around the winning offload. No Cartesian matrix or unbounded grid.
        total_estimate = 2 * len(threads) + len(gpus) + len(batches)

        def attempt(t: int, ngl: int, b: int, ub: int):
            if cancel and cancel.is_set():
                raise RuntimeError("AutoTune cancelled")
            key = (t, ngl, b, ub)
            if key in attempted:
                return
            attempted.add(key)
            if progress_cb:
                progress_cb(f"Testing threads={t}, GPU layers={ngl}, batch={b}/{ub} (3 repeats)",
                            min(0.95, len(attempted) / total_estimate))
            # llama-bench warms up by default. 512 prompt tokens actually exercise
            # every candidate batch; -r 3 supplies samples for median scoring.
            cmd = [bench_path, "-m", model.path, "-p", "512", "-n", "32", "-r", "3", "-o", "json",
                   "-t", str(t), "-ngl", str(ngl), "-b", str(b), "-ub", str(ub),
                   "-ctk", "q8_0", "-ctv", "q8_0"]
            try:
                candidate = self._run_candidate(cmd, cancel=cancel)
                if not self._summarize(candidate, model, profile):
                    raise RuntimeError("Incomplete prompt/generation measurements")
                rows.extend(candidate)
            except RuntimeError as exc:
                if cancel and cancel.is_set():
                    raise
                failures.append({"threads": t, "gpu_layers": ngl, "batch_size": b,
                                 "ubatch_size": ub, "error": str(exc)[:2000]})

        def winner():
            choices = self._summarize(rows, model, profile)
            if not choices:
                raise RuntimeError("No stable, complete AutoTune candidates: " + str(failures[-1:] or "no measurements"))
            return choices[0]

        for t in threads:
            attempt(t, 0, initial_b, initial_ub)
        best = winner()
        for ngl in gpus:
            attempt(best["threads"], ngl, initial_b, initial_ub)
        best = winner()
        for b, ub in batches:
            attempt(best["threads"], best["gpu_layers"], b, ub)
        best = winner()
        for t in threads:
            attempt(t, best["gpu_layers"], best["batch_size"], best["ubatch_size"])
        samples = self._summarize(rows, model, profile)
        best = samples[0]
        result = TuneResult(
            model_key=self.model_key(model), model_path=model.path, measured_at=time.time(),
            backend=backend or "unknown", threads=best["threads"], threads_batch=best["threads"],
            gpu_layers=best["gpu_layers"], gpu_layer_percent=best["gpu_layer_percent"],
            batch_size=best["batch_size"], ubatch_size=best["ubatch_size"],
            prompt_tps=round(best["pp"], 3), generation_tps=round(best["tg"], 3),
            estimated_ttft_ms=best["ttft_ms"], score=best["score"], samples=len(samples), speculative_mode="off",
        )
        details = {"top_candidates": samples[:5], "failed_candidates": failures,
                   "repetitions": 3, "scoring_profile": profile,
                   "peak_ram_mb": best["peak_ram_mb"] or None,
                   "ttft_is_estimate": True, "prompt_tokens": 512,
                   "load_time_ms": None, "shared_gpu_memory_mb": None,
                   "benchmark_scope": "llama-bench compute only; not server TTFT or speculative decoding"}
        saved = self.put(model, result, environment=environment, details=details)
        if progress_cb:
            progress_cb("AutoTune complete", 1.0)
        return saved
