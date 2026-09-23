from __future__ import annotations

import hashlib
import json
import math
import os
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
    speculative_mode: str = "auto"

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
            raw = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"
        except Exception:
            raw = f"{p}|{getattr(model, 'size_gb', 0)}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]

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

    def get(self, model: LocalModel) -> dict[str, Any] | None:
        key = self.model_key(model)
        with self._lock:
            row = self._load().get(key)
        return dict(row) if isinstance(row, dict) else None

    def put(self, model: LocalModel, result: TuneResult) -> dict[str, Any]:
        key = self.model_key(model)
        row = result.to_dict()
        row["model_key"] = key
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
    def _summarize(rows: list[dict[str, Any]], model: LocalModel) -> list[dict[str, Any]]:
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
            g = groups.setdefault(key, {"threads": key[0], "gpu_layers": key[1], "batch_size": key[2], "ubatch_size": key[3], "pp": 0.0, "tg": 0.0, "rows": 0})
            try:
                ts = float(row.get("avg_ts") or 0.0)
                n_prompt = int(row.get("n_prompt") or 0)
                n_gen = int(row.get("n_gen") or 0)
            except Exception:
                continue
            if n_prompt > 0 and n_gen <= 0:
                g["pp"] = max(g["pp"], ts)
            if n_gen > 0:
                g["tg"] = max(g["tg"], ts)
            g["rows"] += 1
        samples = [g for g in groups.values() if g["tg"] > 0 or g["pp"] > 0]
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
            ttft = (64.0 / pp + 1.0 / tg) * 1000.0
            g["ttft_ms"] = round(ttft, 2)
            # Decode speed dominates interactive chat; prompt speed and first-token
            # latency still matter enough to prevent pathological winners.
            tg_norm = tg / max_tg
            pp_norm = pp / max_pp
            latency_norm = 1.0 / (1.0 + ttft / 1000.0)
            g["score"] = round(100.0 * (0.70 * tg_norm + 0.22 * pp_norm + 0.08 * latency_norm), 3)
            ngl = int(g["gpu_layers"])
            if ngl < 0:
                pct = 100
            elif blocks > 0:
                pct = int(round(100.0 * ngl / blocks))
            else:
                pct = 0 if ngl == 0 else 35
            g["gpu_layer_percent"] = max(0, min(100, pct))
        return sorted(samples, key=lambda x: (float(x["score"]), float(x["tg"]), float(x["pp"])), reverse=True)

    def benchmark(
        self,
        model: LocalModel,
        hw: HardwareInfo,
        bench_path: str,
        backend: str = "",
        progress_cb: Callable[[str, float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not bench_path or not Path(bench_path).is_file():
            raise RuntimeError("llama-bench is not installed in the active runtime")
        threads = self._thread_candidates(hw)
        gpu_layers = self._gpu_candidates(model, hw)
        batch = 256 if (hw.ram_available_gb or hw.ram_total_gb) >= 6 else 128
        ubatch = 128 if batch >= 256 else 64
        combos = len(threads) * len(gpu_layers)
        if progress_cb:
            progress_cb(f"Benchmarking {combos} CPU/GPU combinations", 0.05)
        cmd = [
            bench_path,
            "-m", model.path,
            "-p", "64",
            "-n", "24",
            "-r", "1",
            "-o", "json",
            "-t", ",".join(str(x) for x in threads),
            "-b", str(batch),
            "-ub", str(ubatch),
            "-ngl", ",".join(str(x) for x in gpu_layers),
            "-ctk", "q8_0",
            "-ctv", "q8_0",
        ]
        if cancel and cancel.is_set():
            raise RuntimeError("AutoTune cancelled")
        started = time.time()
        # Use temporary files instead of PIPEs. llama-bench may emit enough JSON
        # for a Cartesian benchmark matrix to fill the Windows pipe buffer; if
        # nobody drains it while we poll for cancellation the benchmark can
        # deadlock. File-backed capture keeps cancellation responsive.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out_f, tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_f:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, text=True)
            while proc.poll() is None:
                if cancel and cancel.is_set():
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except Exception:
                        try: proc.kill()
                        except Exception: pass
                    raise RuntimeError("AutoTune cancelled")
                elapsed = time.time() - started
                if progress_cb:
                    # llama-bench does not emit machine-readable per-combination
                    # progress while JSON is reserved for stdout. Show an honest
                    # activity curve instead of a fake completion percentage.
                    frac = min(0.82, 0.08 + math.log1p(elapsed) / 14.0)
                    progress_cb("Testing prompt/decode throughput on the active runtime", frac)
                time.sleep(0.35)
            out_f.seek(0); err_f.seek(0)
            stdout, stderr = out_f.read(), err_f.read()
            rc = int(proc.returncode or 0)
        if rc != 0:
            tail = (stderr or stdout or "")[-5000:]
            raise RuntimeError(f"llama-bench failed with exit code {rc}: {tail}")
        if progress_cb:
            progress_cb("Scoring benchmark results", 0.88)
        rows = self._parse_rows(stdout)
        samples = self._summarize(rows, model)
        if not samples:
            raise RuntimeError("llama-bench completed but no comparable throughput rows were returned")
        best = samples[0]

        # Small second-stage batch refinement around the winning CPU/GPU split.
        refine_rows: list[dict[str, Any]] = []
        for b, ub in ((128, 64), (512, 256)):
            if cancel and cancel.is_set():
                raise RuntimeError("AutoTune cancelled")
            if b == batch and ub == ubatch:
                continue
            if b == 512 and (hw.ram_available_gb or 0.0) < 5.0:
                continue
            if progress_cb:
                progress_cb(f"Refining batch size {b}/{ub}", 0.90)
            cmd2 = [
                bench_path, "-m", model.path, "-p", "64", "-n", "24", "-r", "1", "-o", "json",
                "-t", str(best["threads"]), "-b", str(b), "-ub", str(ub), "-ngl", str(best["gpu_layers"]),
                "-ctk", "q8_0", "-ctv", "q8_0",
            ]
            cp = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            if cp.returncode == 0:
                refine_rows.extend(self._parse_rows(cp.stdout))
        if refine_rows:
            merged = self._summarize(rows + refine_rows, model)
            if merged:
                samples, best = merged, merged[0]

        result = TuneResult(
            model_key=self.model_key(model),
            model_path=model.path,
            measured_at=time.time(),
            backend=str(backend or "unknown"),
            threads=max(1, int(best["threads"] or hw.physical_cores or 1)),
            threads_batch=max(1, int(best["threads"] or hw.logical_cores or 1)),
            gpu_layers=int(best["gpu_layers"]),
            gpu_layer_percent=int(best["gpu_layer_percent"]),
            batch_size=max(32, int(best["batch_size"] or batch)),
            ubatch_size=max(16, int(best["ubatch_size"] or ubatch)),
            prompt_tps=round(float(best["pp"]), 3),
            generation_tps=round(float(best["tg"]), 3),
            estimated_ttft_ms=round(float(best["ttft_ms"]), 2),
            score=round(float(best["score"]), 3),
            samples=len(samples),
            speculative_mode="auto",
        )
        saved = self.put(model, result)
        saved["top_candidates"] = [
            {
                "threads": int(x["threads"]), "gpu_layers": int(x["gpu_layers"]),
                "gpu_layer_percent": int(x["gpu_layer_percent"]), "batch_size": int(x["batch_size"]),
                "ubatch_size": int(x["ubatch_size"]), "prompt_tps": round(float(x["pp"]), 2),
                "generation_tps": round(float(x["tg"]), 2), "estimated_ttft_ms": round(float(x["ttft_ms"]), 1),
                "score": round(float(x["score"]), 2),
            }
            for x in samples[:5]
        ]
        if progress_cb:
            progress_cb("AutoTune complete", 1.0)
        return saved
