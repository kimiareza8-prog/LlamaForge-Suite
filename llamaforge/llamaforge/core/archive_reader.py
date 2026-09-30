"""Bounded archive inspection. Never extracts paths onto the host filesystem."""
from __future__ import annotations

import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_ENTRIES = 2000
MAX_MEMBER = 4 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024
MAX_RATIO = 200


def archive_name(name: str) -> bool:
    return str(name).lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"))


def safe_name(name: str) -> bool:
    name = name.replace("\\", "/")
    return bool(name and not name.startswith("/") and ":" not in name and "\x00" not in name
                and ".." not in PurePosixPath(name).parts)


def zip_entry(info: zipfile.ZipInfo) -> dict:
    reason = ""
    if not safe_name(info.filename): reason = "unsafe path"
    elif stat.S_ISLNK(info.external_attr >> 16): reason = "symbolic link"
    elif info.flag_bits & 1: reason = "encrypted member"
    elif info.file_size > MAX_MEMBER: reason = "member size limit"
    elif info.file_size > max(1, info.compress_size) * MAX_RATIO: reason = "compression ratio limit"
    return {"name": info.filename, "size": info.file_size, "compressed": info.compress_size,
            "directory": info.is_dir(), "safe": not reason, "blocked_reason": reason}


def zip_read(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int = MAX_MEMBER) -> bytes:
    row = zip_entry(info)
    if not row["safe"]: raise ValueError(f"Unsafe archive member {info.filename}: {row['blocked_reason']}")
    with zf.open(info) as stream:
        return stream.read(min(MAX_MEMBER, max(0, limit)))


def inspect_archive(path: Path, *, members: list[str] | None = None,
                    text_extensions: set[str] | None = None, max_chars: int = 12000) -> dict:
    """members=None is metadata only; an explicit list selects content (empty: bounded preview)."""
    entries = []; previews = []; remaining = max(1000, min(int(max_chars), 50000))
    total = 0; truncated = False; found = set()
    requested = set(members or [])
    if len(requested) > 16: raise ValueError("Read at most 16 archive members at a time")
    for name in requested:
        if not safe_name(name): raise ValueError("Unsafe archive member path")

    def consume(row, reader):
        nonlocal remaining, total
        total += row["size"]
        if total > MAX_EXPANDED:
            row.update(safe=False, blocked_reason="archive expanded size limit")
        entries.append(row)
        if row["name"] in requested: found.add(row["name"])
        selected = members is not None and (not requested or row["name"] in requested)
        if selected and not row["safe"] and row["name"] in requested:
            raise ValueError(f"Unsafe archive member: {row['blocked_reason']}")
        if (selected and row["safe"] and not row["directory"] and remaining > 0
                and Path(row["name"]).suffix.lower() in (text_extensions or set())):
            limit = min(remaining, 5000)
            text = reader(min(MAX_MEMBER, limit * 4)).decode("utf-8", errors="replace")[:limit]
            previews.append(f"--- {row['name']} ---\n{text}")
            remaining -= len(text)

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES: raise ValueError("Archive entry count limit exceeded")
            for info in infos:
                consume(zip_entry(info), lambda limit, i=info: zip_read(zf, i, limit))
    else:
        # Stream mode bounds the metadata walk as well as selected payloads.
        with tarfile.open(path, "r|*") as tf:
            for i, info in enumerate(tf):
                if i >= MAX_ENTRIES: raise ValueError("Archive entry count limit exceeded")
                safe = safe_name(info.name) and (info.isfile() or info.isdir()) and info.size <= MAX_MEMBER
                row = {"name":info.name, "size":info.size, "directory":info.isdir(), "safe":safe,
                       "blocked_reason":"" if safe else "unsafe path, link, or member size"}
                def read(limit, item=info):
                    with tf.extractfile(item) as stream: return stream.read(limit)
                consume(row, read)
                if total > MAX_EXPANDED or info.size > MAX_MEMBER:
                    truncated = True
                    break  # do not decompress an enormous skipped member to seek the next header
    if requested - found: raise ValueError("Archive members missing or beyond safety limit: " + ", ".join(sorted(requested - found)))
    result = {"entries":entries[:500], "entry_count":len(entries), "truncated":truncated or len(entries)>500}
    if members is not None:
        result.update(text_preview="\n\n".join(previews), content_read=bool(previews), truncated=result["truncated"] or remaining<=0)
    return result
