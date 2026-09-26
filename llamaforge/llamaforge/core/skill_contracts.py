"""Machine-readable operation contracts shared by discovery and execution.

HTTP operation methods come from structured definitions, never description text.
Unknown custom tools are conservative: no parallel execution or automatic retry.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any, Literal
import re

TELEGRAM_READS = {"status", "recent_chats", "resolve_person", "messages", "my_messages", "search"}
TELEGRAM_WRITES = {"send", "reply"}

LOCAL_WRITES = {
    "calendar": {"create", "update", "cancel", "delete"},
    "workspace_files": {"store_attachment", "write_text", "replace_text", "mkdir", "move", "rename", "trash", "restore", "delete"},
}
LOCAL_READS = {
    "calendar": {"now", "convert", "month", "list"},
    "workspace_files": {"list", "search", "metadata", "probe", "read_content"},
}

# Required alternatives are explicit metadata, also surfaced in the compact
# planner manifest. Domain stores remain the final authority for valid dates/IDs.
OPERATION_INPUTS = {
    "telegram": {"resolve_person":{"required":["query"]},
        **{op:{"required":["chat_ref"]} for op in ("messages", "my_messages")},
        "search":{"required":["chat_ref", "query"]},
        "send":{"required":["chat_ref","text","request_key"]},
        "reply":{"required":["chat_ref","text","message_id","request_key"]}},
    "calendar": {
        "create": {"required": ["title"], "one_of": [["start"], ["gregorian", "time"], ["jalali", "time"], ["relative_date", "time"]]},
        "update": {"required": ["id"]}, "cancel": {"required": ["id"]}, "delete": {"required": ["id"]},
        "convert": {"one_of": [["gregorian"], ["jalali"]]},
    },
    "workspace_files": {
        **{op: {"one_of": [["id"], ["attachment_id"]]} for op in ("metadata", "probe", "read_content")},
        "store_attachment": {"required": ["attachment_id"]},
        "write_text": {"required": ["text"], "one_of": [["id"], ["name"]]},
        "replace_text": {"required": ["id", "old_text", "new_text"]},
        "mkdir": {"one_of": [["folder"], ["name"]]},
        **{op: {"required": ["id"]} for op in ("move", "rename", "trash", "restore", "delete")},
    },
}


def validate_operation(name: str, args: dict) -> str:
    spec = OPERATION_INPUTS.get(name, {}).get(args.get("operation"), {})
    # Empty new_text/text is intentional for deleting text or creating empty files.
    present = lambda k: k in args and args[k] is not None and (k in {"text", "new_text"} or str(args[k]).strip())
    for key in spec.get("required", []):
        if not present(key): return f"{key} is required for {args.get('operation')}"
    if spec.get("one_of") and not any(all(present(k) for k in keys) for keys in spec["one_of"]):
        return "Required input: " + " or ".join(" + ".join(keys) for keys in spec["one_of"])
    if name == "calendar" and args.get("operation") == "create":
        start = str(args.get("start") or "")
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", start) and not any(args.get(k) for k in ("gregorian", "jalali", "relative_date")):
            return "A clock needs a date: supply gregorian, jalali or relative_date; never guess the year"
    return ""


@dataclass(frozen=True)
class OperationPolicy:
    effect: Literal["read", "local_write", "external_write", "session", "unknown"] = "unknown"
    permission: Literal["none", "local_workspace", "external_website", "telegram_read", "telegram_write"] = "external_website"
    parallel_safe: bool = False
    idempotent: bool = False
    network: bool = False
    timeout_seconds: float = 120
    max_retries: int = 0
    verification: str = "receipt; do not infer successful mutation from prose"

    def public(self) -> dict:
        row = asdict(self)
        row["read_only"] = self.effect == "read"
        row["side_effects"] = [] if self.effect == "read" else [self.effect]
        row["retry_policy"] = {"max_retries":row.pop("max_retries"), "only_transient":True}
        return row


def operation_policy(name: str, args: dict | None = None, metadata: dict | None = None) -> OperationPolicy:
    args = args or {}; metadata = metadata or {}
    op = str(args.get("operation") or "").lower()
    if name == "telegram":
        if op in TELEGRAM_READS:
            return OperationPolicy("read", "telegram_read", False, True, op!="status", 35, 0, "bounded structured result")
        return OperationPolicy("external_write", "telegram_write", False, False, True, 35, 0, "message ID and readback; never retry unknown delivery")
    if name in LOCAL_READS:
        if op in LOCAL_READS[name]:
            return OperationPolicy("read", "none", True, True, False, 30, 0, "structured result")
        if op in LOCAL_WRITES[name]:
            return OperationPolicy("local_write", "local_workspace", False, op in {"store_attachment", "mkdir", "update", "cancel", "move", "rename"}, False, 30, 0, "read back persisted ID/state")
    if name == "download_file":
        return OperationPolicy("local_write", "local_workspace", False, False, True, 120, 0, "saved file size/hash")
    if name.startswith("browser_"):
        write = name in {"browser_click", "browser_type", "browser_select"}
        return OperationPolicy("external_write" if write else "session", "external_website" if write else "none", False, False, True, 45, 0, "fresh browser snapshot")
    method = str(args.get("method") or "GET").upper() if name == "http_request" else str(metadata.get("http_method") or "").upper()
    if name in {"web_read", "web_find", "web_check", "web_search"} or method in {"GET", "HEAD"}:
        return OperationPolicy("read", "none", True, True, True, 120, 1, "HTTP status and structured response")
    if method:
        return OperationPolicy("external_write", "external_website", False, method in {"PUT", "DELETE"}, True)
    return OperationPolicy()


def contract(name: str, category: str, schema: dict, metadata: dict | None = None) -> dict:
    operations = {op:operation_policy(name, {"operation":op}).public()
                  for op in sorted(LOCAL_READS.get(name, set()) | LOCAL_WRITES.get(name, set()))}
    if name == "telegram": operations = {op:operation_policy(name, {"operation":op}).public() for op in sorted(TELEGRAM_READS|TELEGRAM_WRITES)}
    for op, spec in operations.items():
        spec["input_requirements"] = OPERATION_INPUTS.get(name, {}).get(op, {})
    policy = operation_policy(name, metadata=metadata).public()
    if name == "http_request":
        operations = {m:operation_policy(name, {"method":m}).public() for m in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")}
    return {"version":1, "capability_family":category.split(".")[0], "input_schema":schema,
            "output_schema":{"type":"object", "required":["ok"], "properties":{
                "ok":{"type":"boolean"}, "result":{"type":"object"}, "error":{"type":"string"}}},
            "preconditions":["valid input schema", "operation permission", "references belong to current workspace"],
            "policy":policy, "operations":operations,
            "latency_hint":"network" if policy["network"] else "local", "cost_hint":"no additional LLM instance"}


def validate_schema(value: Any, schema: dict, path: str = "arguments") -> str:
    """Validate the JSON Schema subset emitted by built-ins, recursively."""
    typ = schema.get("type")
    checks = {"object":lambda v:isinstance(v, dict), "array":lambda v:isinstance(v, list),
              "string":lambda v:isinstance(v, str), "integer":lambda v:isinstance(v, int) and not isinstance(v, bool),
              "number":lambda v:isinstance(v, (int,float)) and not isinstance(v, bool), "boolean":lambda v:isinstance(v, bool)}
    if typ in checks and not checks[typ](value): return f"{path} must be {typ}"
    if "enum" in schema and value not in schema["enum"]: return f"{path} must be one of {schema['enum']}"
    if isinstance(value, (float,int)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]: return f"{path} below minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]: return f"{path} above maximum {schema['maximum']}"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value or value[key] is None: return f"{key} is required"
        props = schema.get("properties", {})
        for key, item in value.items():
            if key not in props and schema.get("additionalProperties") is False: return f"Unknown argument {key}"
            error = validate_schema(item, props.get(key, {}), f"{path}.{key}")
            if error: return error
    if isinstance(value, list):
        for i, item in enumerate(value):
            error = validate_schema(item, schema.get("items", {}), f"{path}[{i}]")
            if error: return error
    return ""
