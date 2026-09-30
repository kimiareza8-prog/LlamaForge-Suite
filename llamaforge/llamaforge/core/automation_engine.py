from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

CURRENT_AUTOMATION_ID: ContextVar[str] = ContextVar("llamaforge_current_automation_id", default="")


def _now() -> float:
    return time.time()


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _obj(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed
    except Exception:
        return default


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base or {})
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _field(payload: dict[str, Any], dotted: str) -> Any:
    value: Any = payload
    for part in str(dotted or "").split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _matches_filter(payload: dict[str, Any], spec: dict[str, Any]) -> bool:
    for key, expected in (spec or {}).items():
        actual = _field(payload, key)
        if isinstance(expected, dict):
            if "in" in expected:
                values = expected.get("in") if isinstance(expected.get("in"), list) else []
                if actual not in values:
                    return False
            elif "not_in" in expected:
                values = expected.get("not_in") if isinstance(expected.get("not_in"), list) else []
                if actual in values:
                    return False
            elif "equals" in expected:
                if actual != expected.get("equals"):
                    return False
            elif "contains" in expected:
                needle = expected.get("contains")
                if isinstance(actual, str):
                    if str(needle) not in actual:
                        return False
                elif isinstance(actual, list):
                    if needle not in actual:
                        return False
                else:
                    return False
            else:
                return False
        elif isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _cron_field(text: str, lo: int, hi: int) -> set[int]:
    text = str(text or "*").strip()
    out: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if token == "*":
            out.update(range(lo, hi + 1)); continue
        if token.startswith("*/"):
            step = max(1, int(token[2:]))
            out.update(range(lo, hi + 1, step)); continue
        if "/" in token:
            span, raw_step = token.split("/", 1)
            step = max(1, int(raw_step))
        else:
            span, step = token, 1
        if "-" in span:
            a, b = span.split("-", 1)
            a, b = int(a), int(b)
            if a > b: a, b = b, a
            out.update(range(max(lo, a), min(hi, b) + 1, step))
        else:
            value = int(span)
            if not lo <= value <= hi:
                raise ValueError(f"cron value {value} outside {lo}..{hi}")
            out.add(value)
    if not out:
        raise ValueError("empty cron field")
    return out


def _next_cron(expr: str, after: float) -> float:
    parts = str(expr or "").split()
    if len(parts) != 5:
        raise ValueError("cron must contain 5 fields: minute hour day month weekday")
    minutes = _cron_field(parts[0], 0, 59)
    hours = _cron_field(parts[1], 0, 23)
    days = _cron_field(parts[2], 1, 31)
    months = _cron_field(parts[3], 1, 12)
    weekdays = _cron_field(parts[4], 0, 6)  # Monday=0, Sunday=6
    dt = _dt.datetime.fromtimestamp(after).replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    deadline = dt + _dt.timedelta(days=370)
    while dt <= deadline:
        if dt.minute in minutes and dt.hour in hours and dt.day in days and dt.month in months and dt.weekday() in weekdays:
            return dt.timestamp()
        dt += _dt.timedelta(minutes=1)
    raise ValueError("cron expression has no match within 370 days")


class AutomationEngine:
    """Persistent scheduler/event runner for short independent Agent invocations.

    The engine never keeps an LLM generation alive as a loop. Schedules and event
    subscriptions live in SQLite; each due item creates one bounded run, persists a
    compact state/result, then exits. This keeps context, local-model latency and
    transient provider failures isolated per iteration.
    """

    def __init__(self, root: Path, *, log: Callable[[str], None] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "automations.sqlite3"
        self.log = log or (lambda _line: None)
        self.runner: Callable[[dict[str, Any], dict[str, Any], threading.Event], dict[str, Any]] | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: dict[str, list[threading.Event]] = {}
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_db(self):
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS automations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                schedule_json TEXT NOT NULL DEFAULT '{}',
                event_name TEXT NOT NULL DEFAULT '',
                event_filter_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'scheduled',
                overlap_policy TEXT NOT NULL DEFAULT 'coalesce',
                misfire_policy TEXT NOT NULL DEFAULT 'run_once',
                max_runtime_seconds INTEGER NOT NULL DEFAULT 300,
                max_retries INTEGER NOT NULL DEFAULT 2,
                retry_backoff_json TEXT NOT NULL DEFAULT '[5,15,30]',
                permissions_json TEXT NOT NULL DEFAULT '{}',
                allowed_tools_json TEXT NOT NULL DEFAULT '[]',
                state_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                next_run_at REAL,
                last_run_at REAL,
                last_status TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                running_count INTEGER NOT NULL DEFAULT 0,
                pending_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_automations_due ON automations(enabled, next_run_at);
            CREATE INDEX IF NOT EXISTS idx_automations_event ON automations(enabled, event_name);
            CREATE TABLE IF NOT EXISTS automation_runs (
                run_id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL,
                trigger_kind TEXT NOT NULL,
                event_id TEXT NOT NULL DEFAULT '',
                event_json TEXT NOT NULL DEFAULT '{}',
                started_at REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                output TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                duration_seconds REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_runs_automation ON automation_runs(automation_id, started_at DESC);
            CREATE TABLE IF NOT EXISTS automation_event_dedupe (
                automation_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                processed_at REAL NOT NULL,
                PRIMARY KEY (automation_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS automation_pending_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                automation_id TEXT NOT NULL,
                trigger_kind TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pending_automation ON automation_pending_events(automation_id, seq);
            """)

    def start(self, runner: Callable[[dict[str, Any], dict[str, Any], threading.Event], dict[str, Any]]):
        self.runner = runner
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, name="automation-scheduler", daemon=True)
        self._thread.start()
        self.log("[automation] scheduler started")

    def close(self):
        self._stop.set(); self._wake.set()
        with self._lock:
            for events in self._running.values():
                for ev in events: ev.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.log("[automation] scheduler stopped")

    @staticmethod
    def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        r = dict(row)
        for src, dst, default in (
            ("schedule_json", "schedule", {}), ("event_filter_json", "event_filter", {}),
            ("permissions_json", "permissions", {}), ("allowed_tools_json", "allowed_tools", []),
            ("state_json", "state", {}), ("retry_backoff_json", "retry_backoff_seconds", [5,15,30]),
        ):
            r[dst] = _obj(r.pop(src, None), default)
        r["enabled"] = bool(r.get("enabled"))
        return r

    def _get(self, automation_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM automations WHERE id=?", (str(automation_id),)).fetchone()
        if not row:
            raise KeyError("Unknown automation")
        return self._public(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM automations ORDER BY created_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        return [self._public(row) for row in rows]

    def history(self, automation_id: str, limit: int = 30) -> list[dict[str, Any]]:
        self._get(automation_id)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM automation_runs WHERE automation_id=? ORDER BY started_at DESC LIMIT ?", (automation_id, max(1, min(200, int(limit))))).fetchall()
        out = []
        for row in rows:
            item = dict(row); item["event"] = _obj(item.pop("event_json", None), {})
            out.append(item)
        return out

    def has_event_subscribers(self, event_prefix: str = "") -> bool:
        prefix = str(event_prefix or "")
        with self._connect() as db:
            if prefix:
                row = db.execute("SELECT 1 FROM automations WHERE enabled=1 AND trigger_type='event' AND event_name LIKE ? LIMIT 1", (prefix + '%',)).fetchone()
            else:
                row = db.execute("SELECT 1 FROM automations WHERE enabled=1 AND trigger_type='event' LIMIT 1").fetchone()
        return bool(row)

    def status(self, automation_id: str | None = None) -> dict[str, Any]:
        if automation_id:
            row = self._get(automation_id)
            row["history"] = self.history(automation_id, 10)
            return row
        rows = self.list(500)
        return {
            "count": len(rows), "enabled": sum(1 for r in rows if r["enabled"]),
            "running": sum(int(r.get("running_count") or 0) for r in rows),
            "pending": sum(int(r.get("pending_count") or 0) for r in rows),
            "automations": rows,
        }

    def _initial_next_run(self, trigger_type: str, schedule: dict[str, Any], *, now: float | None = None) -> float | None:
        now = float(now or _now())
        if trigger_type == "event": return None
        if trigger_type == "delay":
            seconds = max(1, int(schedule.get("delay_seconds") or 0))
            return now + seconds
        if trigger_type == "interval":
            seconds = max(30, int(schedule.get("interval_seconds") or 60))
            return now + seconds
        if trigger_type == "cron":
            return _next_cron(str(schedule.get("cron") or ""), now)
        raise ValueError("trigger_type must be interval, delay, cron or event")

    def create(self, args: dict[str, Any], *, permissions: dict[str, Any], available_tools: list[str]) -> dict[str, Any]:
        name = str(args.get("name") or "Automation").strip()[:120] or "Automation"
        task = str(args.get("task") or "").strip()
        if not task: raise ValueError("task is required")
        if len(task) > 12000: raise ValueError("task is too long (maximum 12000 characters)")
        trigger_type = str(args.get("trigger_type") or "interval").strip().lower()
        schedule = {
            "interval_seconds": int(args.get("interval_seconds") or 60),
            "delay_seconds": int(args.get("delay_seconds") or 0),
            "cron": str(args.get("cron") or "").strip(),
            "schedule_mode": str(args.get("schedule_mode") or "fixed_delay").lower(),
        }
        if schedule["schedule_mode"] not in {"fixed_delay", "fixed_rate"}: raise ValueError("schedule_mode must be fixed_delay or fixed_rate")
        if trigger_type == "interval" and schedule["interval_seconds"] < 30: raise ValueError("interval_seconds must be at least 30")
        if trigger_type == "delay" and schedule["delay_seconds"] < 1: raise ValueError("delay_seconds must be positive")
        event_name = str(args.get("event_name") or "").strip()[:160]
        if trigger_type == "event" and not event_name: raise ValueError("event_name is required for event trigger")
        event_filter = args.get("event_filter") if isinstance(args.get("event_filter"), dict) else {}
        if len(_j(event_filter).encode("utf-8")) > 16384: raise ValueError("event_filter is too large")
        overlap = str(args.get("overlap_policy") or "coalesce").lower()
        if overlap not in {"skip", "coalesce", "queue", "parallel"}: raise ValueError("invalid overlap_policy")
        misfire = str(args.get("misfire_policy") or "run_once").lower()
        if misfire not in {"skip", "run_once"}: raise ValueError("invalid misfire_policy")
        max_runtime = max(15, min(int(args.get("max_runtime_seconds") or 300), 7200))
        max_retries = max(0, min(int(args.get("max_retries") or 2), 6))
        requested = args.get("allowed_tools") if isinstance(args.get("allowed_tools"), list) else []
        available = set(str(x) for x in available_tools)
        if requested:
            unknown = [str(x) for x in requested if str(x) not in available]
            if unknown: raise ValueError("Unavailable/disabled automation tools: " + ", ".join(unknown[:8]))
            allowed = [str(x) for x in requested if str(x) in available]
        else:
            # Snapshot currently-available tools, but omit self-recursive and broad
            # code/tool-creation capabilities unless the user explicitly includes them.
            dangerous = {"automation", "create_tool", "run_command", "code_job"}
            allowed = [x for x in available_tools if x not in dangerous]
        state = args.get("state") if isinstance(args.get("state"), dict) else {}
        if len(_j(state).encode("utf-8")) > 65536: raise ValueError("automation state is too large")
        enabled = bool(args.get("enabled", True))
        automation_id = "auto_" + uuid.uuid4().hex[:18]
        now = _now(); next_run = self._initial_next_run(trigger_type, schedule, now=now) if enabled else None
        with self._connect() as db:
            db.execute("""INSERT INTO automations
                (id,name,task,trigger_type,schedule_json,event_name,event_filter_json,enabled,status,overlap_policy,misfire_policy,
                 max_runtime_seconds,max_retries,retry_backoff_json,permissions_json,allowed_tools_json,state_json,created_at,updated_at,next_run_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (automation_id,name,task,trigger_type,_j(schedule),event_name,_j(event_filter),int(enabled),"scheduled" if enabled else "paused",
                 overlap,misfire,max_runtime,max_retries,_j([5,15,30,60,120,300]),_j(permissions),_j(allowed),_j(state),now,now,next_run))
        self._wake.set(); self.log(f"[automation:create] {automation_id} trigger={trigger_type} name={name}")
        return self._get(automation_id)

    def update(self, automation_id: str, args: dict[str, Any], *, available_tools: list[str]) -> dict[str, Any]:
        row = self._get(automation_id)
        fields: dict[str, Any] = {}
        if "name" in args: fields["name"] = str(args.get("name") or "Automation").strip()[:120] or "Automation"
        if "task" in args:
            task = str(args.get("task") or "").strip()
            if not task: raise ValueError("task cannot be empty")
            if len(task) > 12000: raise ValueError("task is too long (maximum 12000 characters)")
            fields["task"] = task
        trigger_type = str(args.get("trigger_type") or row["trigger_type"]).lower()
        schedule = dict(row.get("schedule") or {})
        for key in ("interval_seconds", "delay_seconds", "cron", "schedule_mode"):
            if key in args: schedule[key] = args[key]
        if str(schedule.get("schedule_mode") or "fixed_delay") not in {"fixed_delay","fixed_rate"}: raise ValueError("invalid schedule_mode")
        if trigger_type == "interval" and int(schedule.get("interval_seconds") or 0) < 30: raise ValueError("interval_seconds must be at least 30")
        event_name = str(args.get("event_name") if "event_name" in args else row.get("event_name") or "").strip()
        if trigger_type == "event" and not event_name: raise ValueError("event_name is required")
        fields.update(trigger_type=trigger_type, schedule_json=_j(schedule), event_name=event_name)
        if "event_filter" in args:
            event_filter = args.get("event_filter") if isinstance(args.get("event_filter"),dict) else {}
            if len(_j(event_filter).encode("utf-8")) > 16384: raise ValueError("event_filter is too large")
            fields["event_filter_json"] = _j(event_filter)
        if "overlap_policy" in args:
            value = str(args.get("overlap_policy") or "").lower()
            if value not in {"skip","coalesce","queue","parallel"}: raise ValueError("invalid overlap_policy")
            fields["overlap_policy"] = value
        if "misfire_policy" in args:
            value = str(args.get("misfire_policy") or "").lower()
            if value not in {"skip","run_once"}: raise ValueError("invalid misfire_policy")
            fields["misfire_policy"] = value
        if "max_runtime_seconds" in args: fields["max_runtime_seconds"] = max(15,min(int(args.get("max_runtime_seconds") or 300),7200))
        if "max_retries" in args: fields["max_retries"] = max(0,min(int(args.get("max_retries") or 0),6))
        if "allowed_tools" in args:
            requested = args.get("allowed_tools") if isinstance(args.get("allowed_tools"),list) else []
            available = set(available_tools)
            unknown=[str(x) for x in requested if str(x) not in available]
            if unknown: raise ValueError("Unavailable/disabled automation tools: " + ", ".join(unknown[:8]))
            fields["allowed_tools_json"] = _j([str(x) for x in requested])
        if "state" in args and isinstance(args.get("state"),dict):
            if len(_j(args["state"]).encode("utf-8")) > 65536: raise ValueError("automation state is too large")
            fields["state_json"] = _j(args["state"])
        enabled = bool(args.get("enabled", row["enabled"]))
        fields["enabled"] = int(enabled); fields["status"] = "scheduled" if enabled else "paused"
        fields["next_run_at"] = self._initial_next_run(trigger_type,schedule) if enabled and trigger_type!="event" else None
        fields["updated_at"] = _now()
        sql = "UPDATE automations SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?"
        with self._connect() as db: db.execute(sql, (*fields.values(), automation_id))
        self._wake.set(); return self._get(automation_id)

    def set_enabled(self, automation_id: str, enabled: bool) -> dict[str, Any]:
        row = self._get(automation_id); next_run = None
        if enabled and row["trigger_type"] != "event": next_run = self._initial_next_run(row["trigger_type"], row["schedule"])
        with self._connect() as db:
            db.execute("UPDATE automations SET enabled=?,status=?,updated_at=?,next_run_at=? WHERE id=?", (int(enabled),"scheduled" if enabled else "paused",_now(),next_run,automation_id))
        if not enabled:
            with self._connect() as db:
                db.execute("DELETE FROM automation_pending_events WHERE automation_id=?", (automation_id,))
                db.execute("UPDATE automations SET pending_count=0 WHERE id=?", (automation_id,))
            with self._lock:
                for ev in self._running.get(automation_id, []): ev.set()
        self._wake.set(); return self._get(automation_id)

    def delete(self, automation_id: str) -> dict[str, Any]:
        self._get(automation_id)
        with self._lock:
            for ev in self._running.get(automation_id, []): ev.set()
        with self._connect() as db:
            db.execute("DELETE FROM automation_event_dedupe WHERE automation_id=?",(automation_id,))
            db.execute("DELETE FROM automation_pending_events WHERE automation_id=?",(automation_id,))
            db.execute("DELETE FROM automation_runs WHERE automation_id=?",(automation_id,))
            db.execute("DELETE FROM automations WHERE id=?",(automation_id,))
        self._wake.set(); return {"deleted":True,"id":automation_id}

    def get_state(self, automation_id: str) -> dict[str, Any]:
        return dict(self._get(automation_id).get("state") or {})

    def set_state(self, automation_id: str, patch: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
        row = self._get(automation_id)
        value = dict(patch or {}) if replace else _deep_merge(dict(row.get("state") or {}), dict(patch or {}))
        if len(_j(value).encode("utf-8")) > 65536: raise ValueError("automation state is too large")
        with self._connect() as db:
            db.execute("UPDATE automations SET state_json=?,updated_at=? WHERE id=?",(_j(value),_now(),automation_id))
        return value

    def run_now(self, automation_id: str, event: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self._get(automation_id)
        self._dispatch(row, trigger_kind="manual", event=event or {"manual":True}, event_id="manual:"+uuid.uuid4().hex)
        return {"queued":True,"id":automation_id}

    def emit_event(self, name: str, payload: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name: raise ValueError("event name is required")
        payload = dict(payload or {})
        if len(_j(payload).encode("utf-8")) > 65536: raise ValueError("event payload is too large")
        event_id = str(event_id or payload.get("event_id") or (name+":"+uuid.uuid4().hex))[:300]
        with self._connect() as db:
            rows = db.execute("SELECT * FROM automations WHERE enabled=1 AND trigger_type='event' AND event_name=?",(name,)).fetchall()
        matched = 0
        for raw in rows:
            row = self._public(raw)
            if not _matches_filter(payload, row.get("event_filter") or {}): continue
            with self._connect() as db:
                try: db.execute("INSERT INTO automation_event_dedupe(automation_id,event_id,processed_at) VALUES(?,?,?)",(row["id"],event_id,_now()))
                except sqlite3.IntegrityError: continue
            self._dispatch(row, trigger_kind="event", event={"name":name, **payload}, event_id=event_id)
            matched += 1
        self._prune_dedupe()
        return {"event":name,"event_id":event_id,"matched":matched}

    def _prune_dedupe(self):
        cutoff = _now() - 7*86400
        try:
            with self._connect() as db: db.execute("DELETE FROM automation_event_dedupe WHERE processed_at < ?",(cutoff,))
        except Exception: pass

    def _advance_scheduled(self, row: dict[str, Any], scheduled_at: float | None = None, *, completion: bool = False):
        if row["trigger_type"] == "delay":
            with self._connect() as db:
                if completion:
                    db.execute("UPDATE automations SET enabled=0,status='completed',next_run_at=NULL,updated_at=? WHERE id=?",(_now(),row["id"]))
                else:
                    db.execute("UPDATE automations SET next_run_at=NULL,updated_at=? WHERE id=?",(_now(),row["id"]))
            return
        if row["trigger_type"] == "cron":
            nxt = _next_cron(str(row["schedule"].get("cron") or ""), max(_now(), float(scheduled_at or 0)))
        elif row["trigger_type"] == "interval":
            interval = max(30,int(row["schedule"].get("interval_seconds") or 60))
            mode = str(row["schedule"].get("schedule_mode") or "fixed_delay")
            if mode == "fixed_delay" and not completion:
                nxt = None
            elif mode == "fixed_delay": nxt = _now()+interval
            else:
                base=float(scheduled_at or row.get("next_run_at") or _now())
                nxt=base+interval
                while nxt <= _now(): nxt += interval
        else: nxt = None
        with self._connect() as db: db.execute("UPDATE automations SET next_run_at=?,updated_at=? WHERE id=?",(nxt,_now(),row["id"]))

    def _scheduler_loop(self):
        # Reconcile stale running counters left by an interrupted process.
        try:
            with self._connect() as db:
                db.execute("UPDATE automations SET running_count=0,pending_count=0,status=CASE WHEN enabled=1 THEN 'scheduled' ELSE 'paused' END WHERE running_count<>0 OR pending_count<>0")
                db.execute("DELETE FROM automation_pending_events")
        except Exception: pass
        while not self._stop.is_set():
            try:
                now=_now()
                with self._connect() as db:
                    rows=db.execute("SELECT * FROM automations WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at LIMIT 32",(now,)).fetchall()
                for raw in rows:
                    row=self._public(raw); scheduled=float(row.get("next_run_at") or now)
                    # On restart, a deeply stale fixed-rate task runs at most once.
                    if now-scheduled > 2 and row.get("misfire_policy") == "skip":
                        self._advance_scheduled(row, scheduled_at=now, completion=True); continue
                    self._advance_scheduled(row, scheduled_at=scheduled, completion=False)
                    self._dispatch(row, trigger_kind="schedule", event={"scheduled_at":scheduled}, event_id=f"schedule:{row['id']}:{scheduled:.3f}")
            except Exception as exc:
                self.log(f"[automation:scheduler:error] {type(exc).__name__}: {exc}")
            self._wake.wait(0.75); self._wake.clear()

    def _dispatch(self, row: dict[str, Any], *, trigger_kind: str, event: dict[str, Any], event_id: str):
        automation_id=row["id"]
        with self._connect() as db:
            fresh=db.execute("SELECT running_count,pending_count,enabled,overlap_policy FROM automations WHERE id=?",(automation_id,)).fetchone()
            if not fresh or not fresh["enabled"] and trigger_kind != "manual": return
            running=int(fresh["running_count"] or 0); pending=int(fresh["pending_count"] or 0); overlap=str(fresh["overlap_policy"] or "coalesce")
            if running>0 and overlap!="parallel":
                if overlap=="coalesce":
                    db.execute("DELETE FROM automation_pending_events WHERE automation_id=?", (automation_id,))
                    db.execute("INSERT INTO automation_pending_events(automation_id,trigger_kind,event_id,event_json,created_at) VALUES(?,?,?,?,?)",
                               (automation_id,trigger_kind,event_id,_j(event),_now()))
                    pending=1
                elif overlap=="queue":
                    if pending < 10:
                        db.execute("INSERT INTO automation_pending_events(automation_id,trigger_kind,event_id,event_json,created_at) VALUES(?,?,?,?,?)",
                                   (automation_id,trigger_kind,event_id,_j(event),_now()))
                        pending += 1
                # skip deliberately drops this trigger/event.
                db.execute("UPDATE automations SET pending_count=?,updated_at=? WHERE id=?",(pending,_now(),automation_id))
                self.log(f"[automation:overlap] {automation_id} policy={overlap} running={running} pending={pending}")
                return
            db.execute("UPDATE automations SET running_count=running_count+1,status='running',last_run_at=?,updated_at=? WHERE id=?",(_now(),_now(),automation_id))
        cancel=threading.Event()
        with self._lock: self._running.setdefault(automation_id,[]).append(cancel)
        threading.Thread(target=self._run_one,args=(automation_id,trigger_kind,event,event_id,cancel),name=f"automation-{automation_id[-8:]}",daemon=True).start()

    def _run_one(self, automation_id: str, trigger_kind: str, event: dict[str, Any], event_id: str, cancel: threading.Event):
        run_id="run_"+uuid.uuid4().hex[:20]; started=_now(); attempts=0; output=""; error=""; status="failed"
        with self._connect() as db:
            db.execute("INSERT INTO automation_runs(run_id,automation_id,trigger_kind,event_id,event_json,started_at,status) VALUES(?,?,?,?,?,?,?)",(run_id,automation_id,trigger_kind,event_id,_j(event),started,"running"))
        try:
            row=self._get(automation_id)
            timer=threading.Timer(int(row.get("max_runtime_seconds") or 300),cancel.set); timer.daemon=True; timer.start()
            backoffs=list(row.get("retry_backoff_seconds") or [5,15,30])
            while attempts <= int(row.get("max_retries") or 0):
                attempts += 1
                if cancel.is_set(): raise TimeoutError("automation run cancelled or exceeded max runtime")
                try:
                    if self.runner is None: raise RuntimeError("automation runner is not configured")
                    result=self.runner(row,event,cancel) or {}
                    output=str(result.get("output") or "")[:16000]
                    patch=result.get("state_patch") if isinstance(result.get("state_patch"),dict) else {}
                    auto_patch={"last_event_id":event_id,"last_run_status":"success","last_run_at":_now()}
                    if output: auto_patch["last_output"] = output[-4000:]
                    self.set_state(automation_id,_deep_merge(auto_patch,patch))
                    status="success"; error=""; break
                except Exception as exc:
                    error=f"{type(exc).__name__}: {exc}"[:4000]
                    if attempts > int(row.get("max_retries") or 0) or cancel.is_set(): raise
                    delay=float(backoffs[min(attempts-1,len(backoffs)-1)] if backoffs else 5)
                    self.log(f"[automation:retry] {automation_id} attempt={attempts} after={delay}s error={error}")
                    cancel.wait(delay)
            timer.cancel()
        except Exception as exc:
            error=f"{type(exc).__name__}: {exc}"[:4000]
            status="timeout" if cancel.is_set() else "failed"
            try: self.set_state(automation_id,{"last_event_id":event_id,"last_run_status":status,"last_error":error,"last_run_at":_now()})
            except Exception: pass
        finally:
            finished=_now(); duration=max(0.0,finished-started)
            with self._connect() as db:
                db.execute("UPDATE automation_runs SET finished_at=?,status=?,attempts=?,output=?,error=?,duration_seconds=? WHERE run_id=?",(finished,status,attempts,output,error,duration,run_id))
                db.execute("UPDATE automations SET running_count=MAX(0,running_count-1),last_status=?,last_error=?,updated_at=? WHERE id=?",(status,error,finished,automation_id))
                pending_row=db.execute("SELECT pending_count,enabled FROM automations WHERE id=?",(automation_id,)).fetchone()
            try:
                row=self._get(automation_id)
                if row["trigger_type"]=="interval" and str(row["schedule"].get("schedule_mode") or "fixed_delay")=="fixed_delay" and row["enabled"]:
                    self._advance_scheduled(row, completion=True)
                elif row["trigger_type"]=="delay":
                    self._advance_scheduled(row, completion=True)
            except Exception: pass
            with self._lock:
                current=self._running.get(automation_id,[])
                if cancel in current: current.remove(cancel)
                if not current: self._running.pop(automation_id,None)
            pending_event=None
            if pending_row and pending_row["enabled"] and int(pending_row["pending_count"] or 0)>0:
                with self._connect() as db:
                    pending_event=db.execute("SELECT * FROM automation_pending_events WHERE automation_id=? ORDER BY seq LIMIT 1",(automation_id,)).fetchone()
                    if pending_event:
                        db.execute("DELETE FROM automation_pending_events WHERE seq=?",(pending_event["seq"],))
                    count=db.execute("SELECT COUNT(*) AS n FROM automation_pending_events WHERE automation_id=?",(automation_id,)).fetchone()["n"]
                    db.execute("UPDATE automations SET pending_count=? WHERE id=?",(int(count),automation_id))
            if pending_event:
                try:
                    self._dispatch(self._get(automation_id),trigger_kind=str(pending_event["trigger_kind"]),
                                   event=_obj(pending_event["event_json"],{}),event_id=str(pending_event["event_id"]))
                except Exception as exc: self.log(f"[automation:pending:error] {exc}")
            self.log(f"[automation:run] {automation_id} status={status} attempts={attempts} duration={duration:.2f}s")

    def tool(self, args: dict[str, Any], *, permissions: dict[str, Any], available_tools: list[str]) -> dict[str, Any]:
        op=str(args.get("operation") or "").lower(); current=CURRENT_AUTOMATION_ID.get()
        automation_id=str(args.get("id") or current or "")
        # Background runs can inspect/update their own compact state, but cannot
        # recursively create or rewire automations without a fresh user turn.
        if current and op not in {"status","get_state","set_state","history"}:
            raise PermissionError("An automation run may only inspect/update its own automation state")
        if current and automation_id != current:
            raise PermissionError("An automation run may access only its own automation state")
        if op=="create": return self.create(args,permissions=permissions,available_tools=available_tools)
        if op=="list": return {"automations":self.list(int(args.get("limit") or 100))}
        if op=="status": return self.status(automation_id or None)
        if op=="history":
            if not automation_id: raise ValueError("id is required")
            return {"id":automation_id,"runs":self.history(automation_id,int(args.get("limit") or 30))}
        if op=="update":
            if not automation_id: raise ValueError("id is required")
            return self.update(automation_id,args,available_tools=available_tools)
        if op=="pause": return self.set_enabled(automation_id,False)
        if op=="resume": return self.set_enabled(automation_id,True)
        if op=="delete": return self.delete(automation_id)
        if op=="run_now": return self.run_now(automation_id,args.get("event") if isinstance(args.get("event"),dict) else None)
        if op=="get_state": return {"id":automation_id,"state":self.get_state(automation_id)}
        if op=="set_state":
            patch=args.get("state") if isinstance(args.get("state"),dict) else {}
            return {"id":automation_id,"state":self.set_state(automation_id,patch,replace=bool(args.get("replace_state")))}
        raise ValueError("Unknown automation operation")
