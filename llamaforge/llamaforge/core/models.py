from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .gguf import read_metadata
from .net import open_url, request_json, UA

HF_API = "https://huggingface.co/api"


@dataclass
class LocalModel:
    path: str
    name: str
    size_gb: float
    quantization: str
    architecture: str = ""
    context_length: int | None = None
    size_label: str = ""
    shard_count: int = 1
    shards_present: int = 1
    chat_template: str = ""
    sampling_temperature: float | None = None
    sampling_top_p: float | None = None
    sampling_top_k: int | None = None
    sampling_min_p: float | None = None
    vision_capable: bool = False
    vision_projector: str = ""
    vision_hint: bool = False
    block_count: int | None = None


def _is_mmproj_name(name: str) -> bool:
    low = str(name or "").lower()
    return "mmproj" in low and low.endswith(".gguf")


def _vision_family_hint(name: str, architecture: str = "") -> bool:
    text = f"{name} {architecture}".lower().replace("_", "-")
    hints = (
        "llava", "vision", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen-vl",
        "minicpm-v", "minicpmv", "internvl", "pixtral", "mllama", "llama-4",
        "smolvlm", "idefics", "gemma-3", "gemma3", "vl-instruct",
    )
    return any(token in text for token in hints)


def _find_vision_projector(model_path: Path) -> str:
    candidates = [x for x in model_path.parent.glob("*.gguf") if _is_mmproj_name(x.name)]
    if not candidates:
        return ""
    model_tokens = {x for x in re.split(r"[^a-z0-9]+", model_path.stem.lower()) if len(x) >= 3}
    def score(path: Path):
        tokens = {x for x in re.split(r"[^a-z0-9]+", path.stem.lower()) if len(x) >= 3}
        overlap = len(model_tokens & tokens)
        # Prefer F16/F32 projectors over heavily quantized variants when several exist.
        low = path.name.lower()
        quality = 2 if any(x in low for x in ("f16", "fp16", "f32", "bf16")) else (1 if "q8" in low else 0)
        return (overlap, quality, -len(path.name))
    return str(max(candidates, key=score).resolve())


def local_model_from_path(path: str | Path) -> LocalModel:
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Model file not found: {p}")
    if p.suffix.lower() != ".gguf":
        raise ValueError("Select a .gguf model file")
    if _is_mmproj_name(p.name):
        raise ValueError("Select the main model GGUF, not its multimodal projector (mmproj)")

    shard = re.match(r"^(.*?)-(\d{5})-of-(\d{5})\.gguf$", p.name, re.IGNORECASE)
    shard_count = 1; shards_present = 1; total_bytes = p.stat().st_size
    launch_path = p
    if shard:
        prefix, idx_s, total_s = shard.groups()
        shard_count = int(total_s)
        first = p.parent / f"{prefix}-00001-of-{shard_count:05d}.gguf"
        if first.exists():
            launch_path = first
        pat = re.compile(rf"^{re.escape(prefix)}-(\d{{5}})-of-{shard_count:05d}\.gguf$", re.IGNORECASE)
        parts = [x for x in p.parent.glob("*.gguf") if pat.match(x.name)]
        shards_present = len(parts)
        total_bytes = sum(x.stat().st_size for x in parts)

    m = read_metadata(launch_path)
    label = str(m.get("size_label", ""))
    if shard_count > 1 and shards_present != shard_count:
        label = (label + " | " if label else "") + f"INCOMPLETE {shards_present}/{shard_count} shards"
    projector = _find_vision_projector(launch_path)
    architecture = str(m.get("architecture", ""))
    vision_hint = _vision_family_hint(str(m.get("name") or launch_path.stem), architecture)
    return LocalModel(
        path=str(launch_path),
        name=str(m.get("name") or launch_path.stem),
        size_gb=round(total_bytes / (1024**3), 2),
        quantization=str(m.get("quantization", "Unknown")),
        architecture=architecture,
        context_length=m.get("context_length") if isinstance(m.get("context_length"), int) else None,
        size_label=label,
        shard_count=shard_count,
        shards_present=shards_present,
        chat_template=str((m.get("metadata") or {}).get("tokenizer.chat_template", "") or ""),
        sampling_temperature=_as_float((m.get("metadata") or {}).get("general.sampling.temp")),
        sampling_top_p=_as_float((m.get("metadata") or {}).get("general.sampling.top_p")),
        sampling_top_k=_as_int((m.get("metadata") or {}).get("general.sampling.top_k")),
        sampling_min_p=_as_float((m.get("metadata") or {}).get("general.sampling.min_p")),
        vision_capable=bool(projector),
        vision_projector=projector,
        vision_hint=vision_hint,
        block_count=_as_int((m.get("metadata") or {}).get(f"{architecture}.block_count")) if architecture else None,
    )


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class ModelManager:
    def __init__(self, token: str = ""):
        self.token = token

    @property
    def headers(self):
        h = {"User-Agent": UA}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def scan(self, dirs: list[str], on_progress: Callable[[str], None] | None = None) -> list[LocalModel]:
        found: list[LocalModel] = []
        seen = set()
        for d in dirs:
            root = Path(os.path.expanduser(d))
            if not root.exists():
                continue
            if on_progress:
                on_progress(f"Scanning {root}")
            for p in root.rglob("*.gguf"):
                if _is_mmproj_name(p.name):
                    continue
                shard = re.match(r"^(.*?)-(\d{5})-of-(\d{5})\.gguf$", p.name, re.IGNORECASE)
                if shard and int(shard.group(2)) != 1:
                    continue
                try:
                    model = local_model_from_path(p)
                except Exception:
                    continue
                rp = str(Path(model.path).resolve())
                if rp in seen:
                    continue
                seen.add(rp); found.append(model)
        return sorted(found, key=lambda x: x.name.lower())

    def search_hf(self, query: str, limit: int = 20) -> list[dict]:
        params = urllib.parse.urlencode({
            "search": query, "filter": "gguf", "sort": "downloads", "direction": -1,
            "limit": limit, "full": "true"
        })
        _, data = request_json(f"{HF_API}/models?{params}", headers=self.headers, timeout=25)
        return [{
            "id": item.get("id", ""), "downloads": item.get("downloads", 0),
            "likes": item.get("likes", 0), "updated": item.get("lastModified", "")
        } for item in data]

    def list_gguf_files(self, repo_id: str) -> list[dict]:
        params = urllib.parse.urlencode({"files_metadata": "true"})
        _, data = request_json(
            f"{HF_API}/models/{urllib.parse.quote(repo_id, safe='/')}?{params}",
            headers=self.headers, timeout=30
        )
        rows = []
        for s in data.get("siblings", []):
            name = s.get("rfilename", "")
            if not name.lower().endswith(".gguf"):
                continue
            size = s.get("size") or (s.get("lfs") or {}).get("size") or 0
            rows.append({"name": name, "size": size})
        return sorted(rows, key=lambda x: x["name"])

    @staticmethod
    def associated_mmproj(rows: list[dict], filename: str) -> dict | None:
        projectors = [r for r in rows if _is_mmproj_name(str(r.get("name") or ""))]
        if not projectors:
            return None
        model_tokens = {x for x in re.split(r"[^a-z0-9]+", Path(filename).stem.lower()) if len(x) >= 3}
        def rank(row: dict):
            name = str(row.get("name") or "")
            tokens = {x for x in re.split(r"[^a-z0-9]+", Path(name).stem.lower()) if len(x) >= 3}
            low = name.lower()
            quality = 3 if any(x in low for x in ("f16", "fp16", "bf16")) else (2 if "f32" in low else (1 if "q8" in low else 0))
            return (len(model_tokens & tokens), quality, int(row.get("size") or 0))
        return max(projectors, key=rank)

    @staticmethod
    def shard_group(rows: list[dict], filename: str) -> list[dict]:
        m = re.match(r"^(.*?)-(\d{5})-of-(\d{5})\.gguf$", Path(filename).name, re.IGNORECASE)
        if not m:
            return [r for r in rows if r.get("name") == filename] or [{"name": filename, "size": 0}]
        prefix, _idx, total_s = m.groups(); total = int(total_s)
        pat = re.compile(rf"^{re.escape(prefix)}-(\d{{5}})-of-{total:05d}\.gguf$", re.IGNORECASE)
        group = [r for r in rows if pat.match(Path(r.get("name", "")).name)]
        group.sort(key=lambda r: Path(r["name"]).name.lower())
        if len(group) != total:
            raise RuntimeError(f"This model is split into {total} GGUF shards, but only {len(group)} were found.")
        return group

    def download_many(self, repo_id: str, files: list[dict], dest_dir: str, progress_cb=None, stop_event: threading.Event | None = None) -> list[Path]:
        sizes = [int(f.get("size") or 0) for f in files]
        expected_total = sum(sizes) if sizes and all(sizes) else 0
        completed_expected = 0; out: list[Path] = []
        for row in files:
            expected = int(row.get("size") or 0)
            existing_path = Path(os.path.expanduser(dest_dir)) / Path(str(row["name"])).name
            if existing_path.is_file() and expected and existing_path.stat().st_size == expected:
                out.append(existing_path)
                completed_expected += expected
                if progress_cb:
                    progress_cb(min(completed_expected, expected_total) if expected_total else completed_expected, expected_total)
                continue
            def one_progress(done, _total, base=completed_expected):
                if progress_cb:
                    progress_cb(base + done, expected_total if expected_total else 0)
            path = self.download(repo_id, row["name"], dest_dir, one_progress, stop_event)
            out.append(path)
            completed_expected += expected or path.stat().st_size
            if progress_cb and expected_total:
                progress_cb(min(completed_expected, expected_total), expected_total)
        return out

    def download(self, repo_id: str, filename: str, dest_dir: str, progress_cb=None, stop_event: threading.Event | None = None) -> Path:
        dest_root = Path(os.path.expanduser(dest_dir)); dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / Path(filename).name
        tmp = dest.with_suffix(dest.suffix + ".part")
        existing = tmp.stat().st_size if tmp.exists() else 0
        headers = dict(self.headers)
        if existing:
            headers["Range"] = f"bytes={existing}-"
        url = (
            f"https://huggingface.co/{urllib.parse.quote(repo_id, safe='/')}/resolve/main/"
            f"{urllib.parse.quote(filename, safe='/')}?download=true"
        )
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            response = open_url(req, timeout=180)
        except Exception as exc:
            raise RuntimeError(f"Model download failed: {exc}") from exc
        with response as r:
            status = int(getattr(r, "status", 200))
            append = existing > 0 and status == 206
            if not append:
                existing = 0
            total = existing + int(r.headers.get("Content-Length", "0") or 0)
            if total:
                free = shutil.disk_usage(dest_root).free
                needed = max(0, total - existing); reserve = 512 * 1024 * 1024
                if free < needed + reserve:
                    raise RuntimeError(
                        f"Not enough free disk space. Need about {needed/(1024**3):.2f} GB plus reserve; "
                        f"only {free/(1024**3):.2f} GB is free."
                    )
            mode = "ab" if append else "wb"; done = existing
            with tmp.open(mode) as f:
                while True:
                    if stop_event and stop_event.is_set():
                        raise RuntimeError("Download cancelled")
                    chunk = r.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk); done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
        tmp.replace(dest)
        return dest
