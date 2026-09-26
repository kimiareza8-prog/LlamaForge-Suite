from __future__ import annotations

import base64
import hashlib
from functools import wraps
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any

from .config import APP_DIR
from .archive_reader import archive_name, inspect_archive, zip_read, MAX_ENTRIES, MAX_EXPANDED

WORKSPACE_DIR = Path(__file__).resolve().parents[2] / "workspace"
CALENDAR_PATH = WORKSPACE_DIR / "calendar.json"
FILES_ROOT = WORKSPACE_DIR / "files"
TRASH_ROOT = WORKSPACE_DIR / ".trash"
INBOX_ROOT = WORKSPACE_DIR / ".inbox"
FILES_INDEX = WORKSPACE_DIR / "files-index.json"

PERSIAN_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
PERSIAN_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

# Fixed solar-calendar public holidays. Moving lunar holidays can be added through
# custom holidays without coupling the runtime to an online calendar service.
FIXED_IRAN_HOLIDAYS = {
    (1, 1): "نوروز",
    (1, 2): "تعطیلات نوروز",
    (1, 3): "تعطیلات نوروز",
    (1, 4): "تعطیلات نوروز",
    (1, 12): "روز جمهوری اسلامی ایران",
    (1, 13): "روز طبیعت",
    (3, 14): "رحلت امام خمینی",
    (3, 15): "قیام ۱۵ خرداد",
    (11, 22): "پیروزی انقلاب اسلامی",
    (12, 29): "ملی شدن صنعت نفت",
}


def _div(a: int, b: int) -> int:
    return a // b


def _gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + _div(gy2 + 3, 4) - _div(gy2 + 99, 100) + _div(gy2 + 399, 400) + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * _div(days, 12053)
    days %= 12053
    jy += 4 * _div(days, 1461)
    days %= 1461
    if days > 365:
        jy += _div(days - 1, 365)
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + _div(days, 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + _div(days - 186, 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def _jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + 365 * jy + _div(jy, 33) * 8 + _div((jy % 33) + 3, 4) + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * _div(days, 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * _div(days - 1, 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * _div(days, 1461)
    days %= 1461
    if days > 365:
        gy += _div(days - 1, 365)
        days = (days - 1) % 365
    gd = days + 1
    leap = gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def jalali_month_length(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    g1 = date(*_jalali_to_gregorian(year, 12, 1))
    g2 = date(*_jalali_to_gregorian(year + 1, 1, 1))
    return (g2 - g1).days


def _parse_iso(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("date/time is required")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    # Normalize every timestamp to the machine local timezone so browser-sent UTC
    # values and local Python-created values compare/group on the same calendar day.
    if dt.tzinfo is None:
        dt = dt.astimezone()
    else:
        dt = dt.astimezone()
    return dt


def _safe_rel(value: str) -> Path:
    raw = str(value or "").replace("\\", "/").strip()
    if raw.startswith("/") or ":" in raw or "\x00" in raw or ".." in raw.split("/"):
        raise ValueError("invalid workspace path")
    parts = [p for p in raw.split("/") if p and p != "."]
    return Path(*parts) if parts else Path()


def _contained(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if root.resolve() != resolved and root.resolve() not in resolved.parents:
        raise ValueError("invalid workspace path: outside workspace")
    return resolved


def _locked(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return call


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class CalendarStore:
    def __init__(self, root: Path | None = None):
        self.lock = threading.RLock()
        self.root = Path(root or WORKSPACE_DIR)
        self.path = self.root / "calendar.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            _atomic_json(self.path, {"events": [], "custom_holidays": []})

    def _load(self) -> dict[str, Any]:
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {"events": [], "custom_holidays": []}
        except Exception:
            return {"events": [], "custom_holidays": []}

    def _save(self, obj: dict[str, Any]) -> None:
        _atomic_json(self.path, obj)

    @staticmethod
    def now() -> dict[str, Any]:
        current = datetime.now().astimezone()
        jy, jm, jd = _gregorian_to_jalali(current.year, current.month, current.day)
        return {
            "iso": current.isoformat(timespec="seconds"),
            "timezone": str(current.tzinfo),
            "utc_offset": current.strftime("%z"),
            "gregorian": current.strftime("%Y-%m-%d"),
            "jalali": f"{jy:04d}-{jm:02d}-{jd:02d}",
            "jalali_text": f"{jd} {PERSIAN_MONTHS[jm-1]} {jy}",
            "weekday": current.strftime("%A"),
            "weekday_fa": PERSIAN_WEEKDAYS[current.weekday()],
            "time": current.strftime("%H:%M:%S"),
        }

    def convert(self, *, jalali: str = "", gregorian: str = "") -> dict[str, Any]:
        if jalali:
            m = re.match(r"^\s*(\d{3,4})[-/]([01]?\d)[-/]([0-3]?\d)\s*$", jalali)
            if not m:
                raise ValueError("jalali must look like 1405-07-01")
            jy, jm, jd = map(int, m.groups())
            if not (1 <= jm <= 12 and 1 <= jd <= jalali_month_length(jy, jm)):
                raise ValueError("invalid Jalali date")
            gy, gm, gd = _jalali_to_gregorian(jy, jm, jd)
            d = date(gy, gm, gd)
            return {"jalali": f"{jy:04d}-{jm:02d}-{jd:02d}", "gregorian": d.isoformat(), "weekday": d.strftime("%A"), "weekday_fa": PERSIAN_WEEKDAYS[d.weekday()]}
        if gregorian:
            d = date.fromisoformat(str(gregorian)[:10])
            jy, jm, jd = _gregorian_to_jalali(d.year, d.month, d.day)
            return {"gregorian": d.isoformat(), "jalali": f"{jy:04d}-{jm:02d}-{jd:02d}", "weekday": d.strftime("%A"), "weekday_fa": PERSIAN_WEEKDAYS[d.weekday()]}
        raise ValueError("jalali or gregorian is required")

    def list_events(self, start: str = "", end: str = "", query: str = "", limit: int = 100, include_cancelled: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            events = list(self._load().get("events") or [])
        sdt = _parse_iso(start) if start else None
        edt = _parse_iso(end) if end else None
        q = str(query or "").strip().lower()
        out = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if not include_cancelled and str(ev.get("status") or "active") == "cancelled":
                continue
            try:
                ev_start = _parse_iso(str(ev.get("start") or ""))
                ev_end = _parse_iso(str(ev.get("end") or ev.get("start") or ""))
            except Exception:
                continue
            if sdt and ev_end < sdt:
                continue
            if edt and ev_start > edt:
                continue
            if q:
                hay = " ".join(str(ev.get(k) or "") for k in ("title", "notes", "location", "tags")).lower()
                if q not in hay:
                    continue
            out.append(ev)
        out.sort(key=lambda x: _parse_iso(str(x["start"])))
        return out[:max(1, min(int(limit or 100), 500))]

    def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        op = str(operation or "").lower()
        with self.lock:
            db = self._load()
            events = [x for x in (db.get("events") or []) if isinstance(x, dict)]
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            if op == "create":
                title = str(data.get("title") or "").strip()
                if not title:
                    raise ValueError("title is required")
                start = self._event_start(data)
                end_raw = str(data.get("end") or "").strip()
                end = _parse_iso(end_raw) if end_raw else start + timedelta(hours=24 if data.get("all_day") else 1)
                if end <= start:
                    raise ValueError("end must be after start")
                ev = {
                    "id": "evt_" + uuid.uuid4().hex[:16],
                    "title": title[:300],
                    "start": start.isoformat(timespec="minutes"),
                    "end": end.isoformat(timespec="minutes"),
                    "all_day": bool(data.get("all_day", False)),
                    "location": str(data.get("location") or "")[:500],
                    "notes": str(data.get("notes") or "")[:5000],
                    "tags": [str(x)[:80] for x in (data.get("tags") or []) if str(x).strip()][:20],
                    "reminders": [int(x) for x in (data.get("reminders") or []) if str(x).lstrip("-").isdigit()][:10],
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
                events.append(ev)
                db["events"] = events
                self._save(db)
                return ev
            event_id = str(data.get("id") or "").strip()
            idx = next((i for i, ev in enumerate(events) if str(ev.get("id")) == event_id), -1)
            if idx < 0:
                raise ValueError("event not found")
            ev = dict(events[idx])
            if op == "update":
                original_start = _parse_iso(ev["start"])
                original_end = _parse_iso(ev.get("end") or ev["start"])
                if "title" in data and not str(data.get("title") or "").strip():
                    raise ValueError("title is required")
                for key in ("title", "location", "notes"):
                    if key in data:
                        ev[key] = str(data.get(key) or "").strip()[:5000 if key == "notes" else 300 if key == "title" else 500]
                if any(key in data for key in ("start", "time", "gregorian", "jalali", "relative_date")):
                    start_data = dict(data)
                    for key in ("start", "time", "gregorian", "jalali", "relative_date"):
                        if key in start_data and not str(start_data[key] or "").strip():
                            raise ValueError(f"{key} must not be empty")
                    if not any(start_data.get(key) for key in ("gregorian", "jalali", "relative_date")):
                        start_data["gregorian"] = original_start.date().isoformat()
                    # Moving a date retains its clock; changing a clock retains
                    # its date. ISO start is still authoritative when supplied.
                    if not start_data.get("time") and not start_data.get("start"):
                        start_data["time"] = original_start.strftime("%H:%M")
                    start = self._event_start(start_data)
                    ev["start"] = start.isoformat(timespec="minutes")
                    if not data.get("end"):
                        ev["end"] = (start + (original_end - original_start)).isoformat(timespec="minutes")
                if "end" in data:
                    ev["end"] = _parse_iso(str(data.get("end") or "")).isoformat(timespec="minutes")
                if "all_day" in data:
                    ev["all_day"] = bool(data.get("all_day"))
                if "tags" in data:
                    ev["tags"] = [str(x)[:80] for x in (data.get("tags") or []) if str(x).strip()][:20]
                if "reminders" in data:
                    ev["reminders"] = [int(x) for x in (data.get("reminders") or []) if str(x).lstrip("-").isdigit()][:10]
                if _parse_iso(ev["end"]) <= _parse_iso(ev["start"]):
                    raise ValueError("end must be after start")
                ev["updated_at"] = now
            elif op == "cancel":
                ev["status"] = "cancelled"
                ev["updated_at"] = now
            elif op == "delete":
                events.pop(idx)
                db["events"] = events
                self._save(db)
                return {"id": event_id, "deleted": True}
            else:
                raise ValueError("unsupported calendar write operation")
            events[idx] = ev
            db["events"] = events
            self._save(db)
            return ev

    def _event_start(self, data: dict[str, Any]) -> datetime:
        start = str(data.get("start") or "").strip()
        if start and not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", start):
            return _parse_iso(start)
        day = str(data.get("gregorian") or "")
        if data.get("jalali"):
            day = self.convert(jalali=str(data["jalali"]))["gregorian"]
        relative = str(data.get("relative_date") or "")
        if relative:
            if relative not in {"today", "tomorrow"}: raise ValueError("relative_date must be today or tomorrow")
            day = (datetime.now().astimezone() + timedelta(days=relative == "tomorrow")).date().isoformat()
        clock = str(data.get("time") or start)
        if not day or not clock:
            raise ValueError("start needs an ISO date/time, or jalali/gregorian/relative_date plus time (HH:MM)")
        return _parse_iso(day + "T" + clock)

    def month(self, year: int, month: int) -> dict[str, Any]:
        jy, jm = int(year), int(month)
        if not (1200 <= jy <= 1700 and 1 <= jm <= 12):
            raise ValueError("invalid Jalali year/month")
        days = []
        first_g = date(*_jalali_to_gregorian(jy, jm, 1))
        last_g = date(*_jalali_to_gregorian(jy, jm, jalali_month_length(jy, jm)))
        start = datetime.combine(first_g, datetime.min.time()).astimezone().isoformat(timespec="minutes")
        end = datetime.combine(last_g, datetime.max.time()).astimezone().isoformat(timespec="minutes")
        events = [{**e, "start": _parse_iso(e["start"]).isoformat(timespec="minutes"),
                   "end": _parse_iso(e.get("end") or e["start"]).isoformat(timespec="minutes")}
                  for e in self.list_events(start, end, limit=500)]
        by_date: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            try:
                begins = _parse_iso(str(ev.get("start")))
                finishes = _parse_iso(str(ev.get("end") or ev.get("start")))
                # End is exclusive: a midnight finish does not occupy the next day.
                last = (finishes - timedelta(microseconds=1)).date() if finishes > begins else begins.date()
                d, stop = max(first_g, begins.date()), min(last_g, last)
            except Exception:
                continue
            while d <= stop:
                by_date.setdefault(d.isoformat(), []).append(ev)
                d += timedelta(days=1)
        custom = self._load().get("custom_holidays") or []
        custom_map = {(int(x.get("month", 0)), int(x.get("day", 0))): str(x.get("title") or "تعطیل") for x in custom if isinstance(x, dict)}
        for jd in range(1, jalali_month_length(jy, jm) + 1):
            gy, gm, gd = _jalali_to_gregorian(jy, jm, jd)
            g = date(gy, gm, gd)
            fixed = custom_map.get((jm, jd)) or FIXED_IRAN_HOLIDAYS.get((jm, jd), "")
            days.append({
                "jalali": f"{jy:04d}-{jm:02d}-{jd:02d}",
                "day": jd,
                "gregorian": g.isoformat(),
                "weekday": g.strftime("%A"),
                "weekday_fa": PERSIAN_WEEKDAYS[g.weekday()],
                "weekend": g.weekday() == 4,
                "holiday": fixed,
                "event_count": len(by_date.get(g.isoformat(), [])),
                "events": [{"id": e.get("id"), "title": e.get("title"), "start": e.get("start"), "status": e.get("status")} for e in by_date.get(g.isoformat(), [])[:8]],
            })
        return {"year": jy, "month": jm, "month_name": PERSIAN_MONTHS[jm - 1], "first_weekday": first_g.weekday(), "days": days, "events": events}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self._load(), ensure_ascii=False))

    def import_snapshot(self, payload: dict[str, Any]) -> None:
        clean = payload if isinstance(payload, dict) else {}
        events = [x for x in (clean.get("events") or []) if isinstance(x, dict)]
        holidays = [x for x in (clean.get("custom_holidays") or []) if isinstance(x, dict)]
        with self.lock:
            self._save({"events": events[:10000], "custom_holidays": holidays[:1000]})

    def tool(self, args: dict[str, Any], allow_write: bool) -> dict[str, Any]:
        op = str(args.get("operation") or "").strip().lower()
        if op == "now":
            return self.now()
        if op == "convert":
            return self.convert(jalali=str(args.get("jalali") or ""), gregorian=str(args.get("gregorian") or ""))
        if op == "month":
            now = self.now()
            return self.month(int(args.get("year") or now["jalali"][:4]), int(args.get("month") or now["jalali"][5:7]))
        if op == "list":
            return {"events": self.list_events(str(args.get("start") or ""), str(args.get("end") or ""), str(args.get("query") or ""), int(args.get("limit") or 100), bool(args.get("include_cancelled")))}
        if op in {"create", "update", "cancel", "delete"}:
            if not allow_write:
                raise PermissionError("Local calendar changes are disabled in Agent settings")
            return self.write(op, args)
        raise ValueError("calendar operation must be now, convert, month, list, create, update, cancel, or delete")


class FileWorkspace:
    TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".log", ".xml", ".html", ".css", ".js", ".ts", ".py", ".sql", ".yaml", ".yml", ".ini", ".toml"}
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    OFFICE_EXTS = {".docx", ".xlsx", ".pptx"}
    PDF_EXTS = {".pdf"}
    ARCHIVE_EXTS = {".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz"}
    TEXT_EXTS |= {".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".php", ".rb", ".sh", ".jsx", ".tsx", ".vue", ".svelte", ".rst", ".tsv"}

    def __init__(self, root: Path | None = None):
        self.lock = threading.RLock()
        self.root = Path(root or WORKSPACE_DIR)
        self.files_root = self.root / "files"
        self.trash_root = self.root / ".trash"
        self.inbox_root = self.root / ".inbox"
        self.index_path = self.root / "files-index.json"
        for p in (self.root, self.files_root, self.trash_root, self.inbox_root):
            p.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            _atomic_json(self.index_path, {"items": {}})

    def _index(self) -> dict[str, Any]:
        try:
            obj = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("items"), dict):
                return obj
        except Exception:
            pass
        return {"items": {}}

    def _save_index(self, obj: dict[str, Any]) -> None:
        _atomic_json(self.index_path, obj)

    @staticmethod
    def _decode_data_url(data_url: str) -> tuple[str, bytes]:
        m = re.match(r"^data:([^;,]+)?(?:;charset=[^;,]+)?;base64,(.*)$", str(data_url or ""), re.I | re.S)
        if not m:
            raise ValueError("invalid data_url")
        mime = m.group(1) or "application/octet-stream"
        if len(m.group(2)) > 70 * 1024 * 1024:
            raise ValueError("encoded attachment exceeds size limit")
        return mime, base64.b64decode(m.group(2), validate=True)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024 * 1024), b""): digest.update(chunk)
        return digest.hexdigest()

    def _register(self, path: Path, *, source: str = "local", description: str = "", tags: list[str] | None = None, item_id: str = "", status: str = "active") -> dict[str, Any]:
        idx = self._index()
        fid = item_id or ("file_" + uuid.uuid4().hex[:16])
        stat = path.stat()
        rel = path.relative_to(self.files_root).as_posix() if self.files_root in path.parents or path == self.files_root else path.name
        row = {
            "id": fid,
            "name": path.name,
            "path": rel,
            "size": stat.st_size,
            "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "extension": path.suffix.lower(),
            "sha256": self._digest(path),
            "description": str(description or "")[:1000],
            "tags": [str(x)[:80] for x in (tags or []) if str(x).strip()][:30],
            "source": source,
            "status": status,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        idx["items"][fid] = row
        self._save_index(idx)
        return row

    @_locked
    def stage_attachment(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("attachment_id") and not (raw.get("data_url") or "text" in raw):
            return self._attachment_meta(str(raw["attachment_id"]))[0]
        name = Path(str(raw.get("name") or "attachment")).name[:180]
        kind = str(raw.get("kind") or "file").lower()
        suffix = Path(name).suffix
        if kind == "text":
            data = str(raw.get("text") or "").encode("utf-8")
        else:
            data_url = str(raw.get("data_url") or "")
            if not data_url:
                raise ValueError("attachment data is missing")
            _, data = self._decode_data_url(data_url)
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("attachment exceeds 20 MB workspace staging limit")
        digest = hashlib.sha256(data).hexdigest()
        aid = "att_" + hashlib.sha256((name + "\0" + digest).encode()).hexdigest()[:24]
        try:
            return self._attachment_meta(aid)[0]
        except ValueError:
            pass
        temp = self.inbox_root / (aid + suffix)
        temp.write_bytes(data)
        meta = {
            "attachment_id": aid,
            "name": name,
            "kind": kind,
            "size": len(data),
            "sha256": digest,
            "extension": suffix.lower(),
            "mime": str(raw.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream"),
            "staged_path": str(temp),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        _atomic_json(self.inbox_root / (aid + ".meta.json"), meta)
        return meta

    def stage_messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for row in messages or []:
            if not isinstance(row, dict):
                continue
            copy = {"role": str(row.get("role") or "user"), "content": str(row.get("content") or "")}
            markers = []
            receipts = []
            for raw in (row.get("attachments") or [])[:8]:
                if not isinstance(raw, dict):
                    continue
                try:
                    meta = self.stage_attachment(raw)
                    receipts.append({"client_id":raw.get("id") or raw.get("client_id"), **{k:v for k,v in meta.items() if k in {"attachment_id", "name", "mime", "extension", "size", "sha256", "kind"}}})
                    markers.append(f"[Workspace attachment: attachment_id={meta['attachment_id']} name={meta['name']} kind={meta['kind']} size={meta['size']} bytes. The File Manager can store it without reading it, or read/inspect it only if the user's request requires content.]" )
                except Exception as exc:
                    markers.append(f"[Attachment staging failed: {exc}]")
            if receipts: copy["_attachment_refs"] = receipts
            if markers:
                copy["content"] = (copy["content"] + "\n\n" + "\n".join(markers)).strip()
            out.append(copy)
        return out

    def _attachment_meta(self, attachment_id: str) -> tuple[dict[str, Any], Path]:
        if not re.fullmatch(r"att_[a-f0-9]{16,64}", attachment_id):
            raise ValueError("invalid attachment_id")
        direct = self.inbox_root / (attachment_id + ".meta.json")
        # Read old receipts for compatibility; new receipts have a direct lookup.
        for side in ([direct] if direct.is_file() else self.inbox_root.glob("*.json")):
            try:
                meta = json.loads(side.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(meta, dict) and str(meta.get("attachment_id")) == attachment_id:
                if meta.get("stored_file_id"):
                    row, p = self._resolve_id(str(meta["stored_file_id"]))
                    return {**meta, **row, "attachment_id":attachment_id}, p
                p = _contained(self.inbox_root, Path(str(meta.get("staged_path") or "")))
                if p.is_file():
                    return meta, p
        raise ValueError("staged attachment not found")

    def _resolve_id(self, file_id: str) -> tuple[dict[str, Any], Path]:
        idx = self._index()
        row = idx.get("items", {}).get(str(file_id or ""))
        if not isinstance(row, dict):
            raise ValueError("file not found")
        root = self.trash_root if row.get("status") == "trash" else self.files_root
        p = (root / _safe_rel(str(row.get("path") or row.get("name") or ""))).resolve()
        if root.resolve() not in p.parents and p != root.resolve():
            raise ValueError("invalid workspace path")
        if not p.exists():
            raise ValueError("workspace file is missing on disk")
        return row, p

    @_locked
    def list(self, folder: str = "") -> dict[str, Any]:
        rel = _safe_rel(folder)
        root = (self.files_root / rel).resolve()
        if self.files_root.resolve() not in root.parents and root != self.files_root.resolve():
            raise ValueError("invalid folder")
        if not root.is_dir(): raise ValueError("workspace folder not found")
        idx = self._index().get("items", {})
        by_path = {str(v.get("path")): v for v in idx.values() if isinstance(v, dict) and v.get("status") != "trash"}
        items = []
        for p in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name.startswith(".") or p.is_symlink():
                continue
            relp = p.relative_to(self.files_root).as_posix()
            if p.is_dir():
                items.append({"kind": "folder", "name": p.name, "path": relp})
            else:
                row = by_path.get(relp) or self._register(p)
                items.append({"kind": "file", **row})
        return {"folder": rel.as_posix() if str(rel) != "." else "", "items": items}

    def search(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        idx = self._index().get("items", {})
        rows = []
        for row in idx.values():
            if not isinstance(row, dict) or row.get("status") == "trash":
                continue
            hay = " ".join([str(row.get("name") or ""), str(row.get("path") or ""), str(row.get("description") or ""), " ".join(row.get("tags") or [])]).lower()
            if q in hay:
                rows.append(row)
        return rows[:max(1, min(int(limit or 40), 200))]

    @_locked
    def upload_data(self, *, name: str, folder: str = "", data_url: str = "", text: str | None = None, description: str = "", tags: list[str] | None = None) -> dict[str, Any]:
        rel = _safe_rel(folder)
        target_dir = _contained(self.files_root, self.files_root / rel)
        target_dir.mkdir(parents=True, exist_ok=True)
        clean = Path(str(name or "file")).name[:180]
        if not clean:
            clean = "file"
        target = _contained(self.files_root, target_dir / clean)
        stem, suffix = target.stem, target.suffix
        n = 2
        while target.exists():
            target = target_dir / f"{stem} ({n}){suffix}"
            n += 1
        if text is not None:
            data = str(text).encode("utf-8")
        else:
            _, data = self._decode_data_url(data_url)
        if len(data) > 50 * 1024 * 1024:
            raise ValueError("file exceeds 50 MB workspace upload limit")
        target.write_bytes(data)
        return self._register(target, source="upload", description=description, tags=tags)

    @staticmethod
    def _read_office_text(path: Path, ext: str) -> str:
        prefixes = {
            ".docx": ("word/",),
            ".pptx": ("ppt/slides/", "ppt/notesSlides/"),
            ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/"),
        }.get(ext, ())
        parts: list[str] = []
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES or sum(x.file_size for x in infos) > MAX_EXPANDED:
                raise ValueError("Office archive exceeds safe expansion limits")
            shared = []
            if ext == ".xlsx" and "xl/sharedStrings.xml" in zf.namelist():
                tree = ET.fromstring(zip_read(zf, zf.getinfo("xl/sharedStrings.xml")))
                shared = ["".join(node.itertext()) for node in tree if str(node.tag).split("}")[-1] == "si"]
            names = [n for n in zf.namelist() if any(n == p or n.startswith(p) for p in prefixes) and n.lower().endswith(".xml")]
            for name in sorted(names)[:300]:
                if name == "xl/sharedStrings.xml": continue
                data = zip_read(zf, zf.getinfo(name))
                try:
                    root = ET.fromstring(data)
                except Exception:
                    continue
                if ext == ".xlsx":
                    parts.append("[" + name + "]")
                    for row in root.iter():
                        if str(row.tag).split("}")[-1] != "row": continue
                        cells = []
                        for cell in row:
                            values = [n.text or "" for n in cell.iter() if str(n.tag).split("}")[-1] in {"t", "v"}]
                            value = "".join(values)
                            if cell.get("t") == "s":
                                try: value = shared[int(value)]
                                except (ValueError, IndexError): value = "[invalid shared string]"
                            cells.append(value)
                        parts.append("\t".join(cells))
                    if sum(map(len, parts)) > 50000: break
                    continue
                vals = []
                for node in root.iter():
                    tag = str(node.tag).split("}")[-1].lower()
                    if tag in {"t", "v"} and node.text:
                        val = node.text.strip()
                        if val:
                            vals.append(val)
                if vals:
                    parts.append(" ".join(vals))
                if sum(map(len, parts)) > 50000: break
        return "\n".join(parts)

    @staticmethod
    def _read_pdf_text(path: Path) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise ValueError("PDF inspection needs the optional pypdf package (included in requirements-agent.txt)") from exc
        reader = PdfReader(str(path))
        chunks: list[str] = []
        for page in reader.pages[:200]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                chunks.append(text)
        return "\n\n".join(chunks)

    def snapshot(self, max_total_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
        """Portable owner-scoped snapshot for host/local Web Bridge sync."""
        with self.lock:
            idx = self._index()
            folders = [p.relative_to(self.files_root).as_posix() for p in self.files_root.rglob("*") if p.is_dir()]
            blobs = []
            total = 0
            for fid, row in (idx.get("items") or {}).items():
                if not isinstance(row, dict):
                    continue
                root = self.trash_root if row.get("status") == "trash" else self.files_root
                path = _contained(root, root / _safe_rel(str(row.get("path") or row.get("name") or "")))
                if not path.is_file():
                    continue
                data = path.read_bytes()
                total += len(data)
                if total > max_total_bytes:
                    raise ValueError("workspace snapshot exceeds 64 MB sync limit")
                blobs.append({"id": str(fid), "status": str(row.get("status") or "active"), "path": str(row.get("path") or path.name), "data_base64": base64.b64encode(data).decode("ascii")})
            return {"index": idx, "folders": folders, "files": blobs}

    def import_snapshot(self, payload: dict[str, Any]) -> None:
        # PHP encodes an empty associative array as [] unless JSON_FORCE_OBJECT
        # is used. Normalize only this empty map, never a non-empty list.
        if isinstance(payload, dict) and isinstance(payload.get("index"), dict) and payload["index"].get("items") == []:
            payload = {**payload, "index": {**payload["index"], "items": {}}}
        if not isinstance(payload, dict) or not isinstance(payload.get("index"), dict) or not isinstance(payload["index"].get("items"), dict):
            raise ValueError("Invalid workspace snapshot index")
        index = payload["index"]
        folders = [_safe_rel(x) for x in (payload.get("folders") or []) if isinstance(x, str)]
        decoded, total = [], 0
        for blob in payload.get("files") or []:
            if not isinstance(blob, dict): raise ValueError("Invalid snapshot file")
            rel = _safe_rel(str(blob.get("path") or ""))
            if str(rel) == ".": raise ValueError("Snapshot file path is required")
            raw = str(blob.get("data_base64") or "")
            if len(raw) > 90 * 1024 * 1024: raise ValueError("workspace snapshot exceeds 64 MB sync limit")
            data = base64.b64decode(raw, validate=True)
            total += len(data)
            if total > 64 * 1024 * 1024: raise ValueError("workspace snapshot exceeds 64 MB sync limit")
            decoded.append((blob.get("status") == "trash", rel, data))
        for row in index["items"].values():
            if not isinstance(row, dict): raise ValueError("Invalid snapshot index entry")
            _safe_rel(str(row.get("path") or row.get("name") or ""))
        # Validate the entire payload before replacing any existing data.
        with self.lock:
            shutil.rmtree(self.files_root)
            shutil.rmtree(self.trash_root)
            self.files_root.mkdir(parents=True, exist_ok=True)
            self.trash_root.mkdir(parents=True, exist_ok=True)
            for rel in folders:
                _contained(self.files_root, self.files_root / rel).mkdir(parents=True, exist_ok=True)
            for trash, rel, data in decoded:
                root = self.trash_root if trash else self.files_root
                target = _contained(root, root / rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            self._save_index(index)

    def tool(self, args: dict[str, Any], allow_write: bool, vision_available: bool = False) -> dict[str, Any]:
        if str(args.get("operation") or "").lower() in {"store_attachment", "mkdir", "move", "rename", "trash", "restore", "delete", "write_text", "replace_text"}:
            with self.lock:
                return self._tool(args, allow_write, vision_available)
        return self._tool(args, allow_write, vision_available)

    def _tool(self, args: dict[str, Any], allow_write: bool, vision_available: bool = False) -> dict[str, Any]:
        op = str(args.get("operation") or "").strip().lower()
        if op == "list":
            return self.list(str(args.get("folder") or ""))
        if op == "search":
            return {"matches": self.search(str(args.get("query") or ""), int(args.get("limit") or 40))}
        if op == "metadata":
            if args.get("attachment_id"):
                row, p = self._attachment_meta(str(args["attachment_id"]))
                return {k:v for k,v in row.items() if k != "staged_path"}
            row, _ = self._resolve_id(str(args.get("id") or ""))
            return row
        if op == "probe":
            file_id = str(args.get("id") or "")
            attachment_id = str(args.get("attachment_id") or "")
            if attachment_id:
                meta, p = self._attachment_meta(attachment_id)
                name = str(meta.get("name") or p.name)
                mime = str(meta.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream")
                row = {k:v for k,v in meta.items() if k != "staged_path"}
            else:
                row, p = self._resolve_id(file_id)
                name = str(row.get("name") or p.name)
                mime = str(row.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream")
            ext = Path(name).suffix.lower()
            capability = "metadata_only"
            if ext in self.TEXT_EXTS or mime.startswith("text/"):
                capability = "text"
            elif ext in self.OFFICE_EXTS:
                capability = "office_text"
            elif ext in self.PDF_EXTS:
                capability = "pdf_text"
            elif archive_name(name):
                capability = "archive"
            elif ext in self.IMAGE_EXTS or mime.startswith("image/"):
                capability = "vision" if vision_available else "image_requires_vision"
            result = {
                "file": row, "extension": ext, "mime": mime, "size": int(p.stat().st_size),
                "read_capability": capability, "content_read": False,
                "guidance": "Metadata/probe only. Call read_content only if the user's task requires understanding file contents.",
            }
            if archive_name(name):
                try:
                    info = inspect_archive(p)
                    result["archive_entries"] = info["entries"]
                    result["archive_entry_count"] = info["entry_count"]
                    result["archive_truncated"] = info["truncated"]
                except Exception as exc:
                    result["archive_error"] = str(exc)[:300]
            return result
        if op == "read_content":
            file_id = str(args.get("id") or "")
            attachment_id = str(args.get("attachment_id") or "")
            if attachment_id:
                meta, p = self._attachment_meta(attachment_id)
                name = str(meta.get("name") or p.name)
                mime = str(meta.get("mime") or mimetypes.guess_type(name)[0] or "")
                row = {"attachment_id": attachment_id, "name": name, "mime": mime, "size": p.stat().st_size}
            else:
                row, p = self._resolve_id(file_id)
                name = str(row.get("name") or p.name)
                mime = str(row.get("mime") or mimetypes.guess_type(name)[0] or "")
            ext = Path(name).suffix.lower()
            if ext in self.TEXT_EXTS or mime.startswith("text/"):
                limit = max(1000, min(int(args.get("max_chars") or 12000), 50000))
                with p.open(encoding="utf-8", errors="replace") as stream:
                    text = stream.read(limit + 1)
                return {"file": row, "content_type": "text", "text": text[:limit], "truncated": len(text) > limit}
            if ext in self.OFFICE_EXTS:
                text = self._read_office_text(p, ext)
                limit = max(1000, min(int(args.get("max_chars") or 12000), 50000))
                return {"file": row, "content_type": "document_text", "text": text[:limit], "truncated": len(text) > limit}
            if ext in self.PDF_EXTS:
                text = self._read_pdf_text(p)
                limit = max(1000, min(int(args.get("max_chars") or 12000), 50000))
                return {"file": row, "content_type": "pdf_text", "text": text[:limit], "truncated": len(text) > limit}
            if archive_name(name):
                archive = inspect_archive(p, members=args.get("members") or [], text_extensions=self.TEXT_EXTS, max_chars=int(args.get("max_chars") or 12000))
                return {"file": row, "content_type": "archive", **archive}
            if ext in self.IMAGE_EXTS or mime.startswith("image/"):
                if not vision_available:
                    raise ValueError("This file is an image, but the loaded model is not vision-capable")
                data = base64.b64encode(p.read_bytes()).decode("ascii")
                return {"file": row, "content_type": "image", "vision_attachment": {"name": name, "data_url": f"data:{mime or 'image/jpeg'};base64,{data}"}}
            raise ValueError("This file type is stored safely but direct content reading is not implemented; use metadata or download it")
        if op in {"store_attachment", "mkdir", "move", "rename", "trash", "restore", "delete", "write_text", "replace_text"} and not allow_write:
            raise PermissionError("Local File Manager changes are disabled in Agent settings")
        if op == "write_text":
            text = str(args.get("text") if args.get("text") is not None else "")
            if len(text.encode("utf-8")) > 5 * 1024 * 1024:
                raise ValueError("text write exceeds 5 MB limit")
            file_id = str(args.get("id") or "").strip()
            if file_id:
                row, p = self._resolve_id(file_id)
                ext = p.suffix.lower()
                mime = str(row.get("mime") or mimetypes.guess_type(p.name)[0] or "")
                if ext not in self.TEXT_EXTS and not mime.startswith("text/"):
                    raise ValueError("write_text only edits text/code files")
                p.write_text(text, encoding="utf-8")
                return self._register(p, source=str(row.get("source") or "local"), description=str(row.get("description") or ""), tags=row.get("tags") or [], item_id=file_id)
            folder = _safe_rel(str(args.get("folder") or ""))
            name = Path(str(args.get("name") or "note.txt")).name[:180] or "note.txt"
            if Path(name).suffix.lower() not in self.TEXT_EXTS:
                raise ValueError("write_text requires a text/code filename")
            target_dir = _contained(self.files_root, self.files_root / folder)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _contained(self.files_root, target_dir / name)
            overwrite = bool(args.get("overwrite"))
            if target.exists() and not overwrite:
                stem, suffix = target.stem, target.suffix
                n = 2
                while target.exists():
                    target = target_dir / f"{stem} ({n}){suffix}"
                    n += 1
            target.write_text(text, encoding="utf-8")
            return self._register(target, source="agent", description=str(args.get("description") or ""), tags=args.get("tags") or [])
        if op == "replace_text":
            row, p = self._resolve_id(str(args.get("id") or ""))
            ext = p.suffix.lower()
            mime = str(row.get("mime") or mimetypes.guess_type(p.name)[0] or "")
            if ext not in self.TEXT_EXTS and not mime.startswith("text/"):
                raise ValueError("replace_text only edits text/code files")
            old_text = str(args.get("old_text") or "")
            new_text = str(args.get("new_text") if args.get("new_text") is not None else "")
            if not old_text:
                raise ValueError("old_text is required")
            content = p.read_text(encoding="utf-8", errors="replace")
            count = content.count(old_text)
            if count == 0:
                raise ValueError("old_text was not found in the file")
            replace_all = bool(args.get("replace_all"))
            updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
            p.write_text(updated, encoding="utf-8")
            fresh = self._register(p, source=str(row.get("source") or "local"), description=str(row.get("description") or ""), tags=row.get("tags") or [], item_id=str(row.get("id") or ""))
            return {"file": fresh, "replacements": count if replace_all else 1}
        if op == "store_attachment":
            meta, p = self._attachment_meta(str(args.get("attachment_id") or ""))
            if meta.get("stored_file_id"):
                return self._resolve_id(str(meta["stored_file_id"]))[0]
            folder = _safe_rel(str(args.get("folder") or ""))
            target_dir = _contained(self.files_root, self.files_root / folder)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _contained(self.files_root, target_dir / Path(str(args.get("name") or meta.get("name") or p.name)).name)
            stem, suffix = target.stem, target.suffix
            n = 2
            while target.exists():
                target = target_dir / f"{stem} ({n}){suffix}"
                n += 1
            shutil.move(str(p), str(target))
            row = self._register(target, source="attachment", description=str(args.get("description") or ""), tags=args.get("tags") or [])
            meta["stored_file_id"] = row["id"]
            _atomic_json(self.inbox_root / (meta["attachment_id"] + ".meta.json"), meta)
            return row
        if op == "mkdir":
            folder = _safe_rel(str(args.get("folder") or ""))
            name = Path(str(args.get("name") or "")).name.strip()
            if not name:
                raise ValueError("folder name is required")
            p = _contained(self.files_root, self.files_root / folder / _safe_rel(name))
            p.mkdir(parents=True, exist_ok=True)
            return {"created": True, "kind": "folder", "path": p.relative_to(self.files_root).as_posix()}
        if op in {"move", "rename", "trash", "restore", "delete"}:
            row, p = self._resolve_id(str(args.get("id") or ""))
            idx = self._index()
            if op == "trash":
                target = self.trash_root / Path(str(row.get("path") or p.name)).name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target = self.trash_root / (uuid.uuid4().hex[:8] + "_" + target.name)
                shutil.move(str(p), str(target))
                row["original_path"] = row.get("path")
                row["path"] = target.name
                row["status"] = "trash"
            elif op == "restore":
                if row.get("status") != "trash":
                    raise ValueError("file is not in trash")
                dest = _contained(self.files_root, self.files_root / _safe_rel(str(row.get("original_path") or row.get("name") or p.name)))
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest = dest.with_name(dest.stem + " restored" + dest.suffix)
                shutil.move(str(p), str(dest))
                row["path"] = dest.relative_to(self.files_root).as_posix()
                row["name"] = dest.name
                row["status"] = "active"
            elif op == "delete":
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
                idx["items"].pop(str(row.get("id")), None)
                self._save_index(idx)
                return {"deleted": True, "id": row.get("id")}
            else:
                folder = _safe_rel(str(args.get("folder") or Path(str(row.get("path") or "")).parent.as_posix()))
                new_name = Path(str(args.get("name") or row.get("name") or p.name)).name
                dest = _contained(self.files_root, self.files_root / folder / _safe_rel(new_name))
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() and dest.resolve() != p.resolve():
                    raise ValueError("destination already exists")
                shutil.move(str(p), str(dest))
                row["path"] = dest.relative_to(self.files_root).as_posix()
                row["name"] = dest.name
            row["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            idx["items"][str(row.get("id"))] = row
            self._save_index(idx)
            return row
        raise ValueError("workspace_files operation must be list, search, metadata, probe, read_content, store_attachment, write_text, replace_text, mkdir, move, rename, trash, restore, or delete")
