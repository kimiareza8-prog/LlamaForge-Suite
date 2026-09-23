from __future__ import annotations

import struct
from pathlib import Path

TYPE_SIZES = {0:1, 1:1, 2:2, 3:2, 4:4, 5:4, 6:4, 7:1, 10:8, 11:8, 12:8}


def _read_exact(f, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise EOFError("Unexpected end of GGUF")
    return b


def _u32(f): return struct.unpack("<I", _read_exact(f, 4))[0]
def _u64(f): return struct.unpack("<Q", _read_exact(f, 8))[0]


def _string(f) -> str:
    n = _u64(f)
    if n > 16_000_000:
        raise ValueError("Unreasonable GGUF string length")
    return _read_exact(f, n).decode("utf-8", errors="replace")


def _scalar(f, t: int):
    fmts = {0:"<B",1:"<b",2:"<H",3:"<h",4:"<I",5:"<i",6:"<f",7:"<?",10:"<Q",11:"<q",12:"<d"}
    if t == 8:
        return _string(f)
    if t not in fmts:
        raise ValueError(f"Unsupported GGUF metadata type {t}")
    return struct.unpack(fmts[t], _read_exact(f, TYPE_SIZES[t]))[0]


def _skip_array(f, elem_type: int, n: int):
    if elem_type in TYPE_SIZES:
        f.seek(TYPE_SIZES[elem_type] * n, 1)
        return
    if elem_type == 8:
        for _ in range(n):
            ln = _u64(f)
            f.seek(ln, 1)
        return
    raise ValueError(f"Unsupported GGUF array element type {elem_type}")


def read_metadata(path: str | Path, max_array_items: int = 64) -> dict:
    p = Path(path)
    out: dict = {"path": str(p), "file_size": p.stat().st_size}
    try:
        with p.open("rb") as f:
            if _read_exact(f, 4) != b"GGUF":
                return out
            version = _u32(f)
            tensor_count = _u64(f)
            kv_count = _u64(f)
            out.update({"gguf_version": version, "tensor_count": tensor_count, "kv_count": kv_count})
            meta = {}
            for _ in range(kv_count):
                key = _string(f)
                t = _u32(f)
                if t == 9:
                    elem_type = _u32(f)
                    n = _u64(f)
                    if n <= max_array_items:
                        meta[key] = [_scalar(f, elem_type) for _ in range(n)]
                    else:
                        _skip_array(f, elem_type, n)
                        meta[key] = f"<array:{n}>"
                else:
                    meta[key] = _scalar(f, t)
            out["metadata"] = meta
            arch = meta.get("general.architecture", "")
            out["architecture"] = arch
            out["name"] = meta.get("general.name") or p.stem
            out["quantization"] = guess_quantization(p.name)
            out["context_length"] = meta.get(f"{arch}.context_length") if arch else None
            out["size_label"] = meta.get("general.size_label", "")
    except Exception as e:
        out["error"] = str(e)
        out.setdefault("name", p.stem)
        out.setdefault("quantization", guess_quantization(p.name))
    return out


def guess_quantization(filename: str) -> str:
    up = filename.upper()
    known = [
        "IQ1_S","IQ1_M","IQ2_XXS","IQ2_XS","IQ2_S","IQ2_M","IQ3_XXS","IQ3_XS","IQ3_S","IQ3_M",
        "Q2_K","Q3_K_S","Q3_K_M","Q3_K_L","Q4_0","Q4_1","Q4_K_S","Q4_K_M","Q5_0","Q5_1","Q5_K_S","Q5_K_M",
        "Q6_K","Q8_0","F16","BF16","MXFP4"
    ]
    for q in known:
        if q in up:
            return q
    return "Unknown"
