from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from dataclasses import asdict
from contextvars import copy_context
from typing import Any, Callable, Iterator
from .skill_contracts import OPERATION_INPUTS, operation_policy
from .request_tracing import record, logged_model, logged_stream, current_trace


@dataclass
class AgentDecision:
    action: str
    summary: str = ""
    skill: str = ""
    arguments: dict[str, Any] | None = None
    answer: str = ""
    actions: list[dict[str, Any]] | None = None
    families: list[str] | None = None


@dataclass
class AgentRouteDecision:
    route: str  # direct | skills
    summary: str = ""
    confidence: int = 0
    families: list[str] | None = None
    goal: str = ""
    needs_write: bool = False


def _json_object_from_text(text: str) -> dict[str, Any] | None:
    """Extract the first usable JSON object from noisy local-model output."""
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates: list[str] = [raw]
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.I | re.S):
        candidates.insert(0, m.group(1))
    # Balanced-object scan handles prose around JSON and nested argument objects.
    starts = [i for i, ch in enumerate(raw) if ch == "{"]
    for start in starts[:8]:
        depth = 0
        quoted = False
        escaped = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if quoted:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    quoted = False
                continue
            if ch == '"':
                quoted = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[start : i + 1])
                    break
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _safe_json(value: Any, max_chars: int = 5200) -> str:
    """Compact tool observations so small local context windows stay usable."""

    def prune(obj: Any, depth: int = 0) -> Any:
        if depth >= 5:
            if isinstance(obj, (dict, list)):
                return "…"
            return obj
        if isinstance(obj, str):
            return obj if len(obj) <= 3200 else obj[:3200] + "…[truncated]"
        if isinstance(obj, list):
            rows = [prune(x, depth + 1) for x in obj[:12]]
            if len(obj) > 12:
                rows.append(f"…[{len(obj)-12} more]")
            return rows
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            priority = [
                "ok", "error", "failure", "suggested_fallbacks", "status", "ok_status", "state",
                "reachable", "response_ms", "browser_recommended", "access_issue",
                "title", "url", "final_url", "content_type", "path", "filename", "bytes",
                "text", "body", "data", "match_count", "matches", "matching_links",
                "pending_count", "pending_messages", "next_wait_url",
                "after_reply_next_wait_url", "standard_api_reply", "web_get_action", "links", "forms",
            ]
            keys = list(obj.keys())
            ordered = [k for k in priority if k in obj] + [k for k in keys if k not in priority]
            for key in ordered[:28]:
                out[str(key)] = prune(obj[key], depth + 1)
            if len(keys) > 28:
                out["_more_keys"] = len(keys) - 28
            return out
        return obj

    try:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = {"raw": value}
        text = json.dumps(prune(value), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        text = text[:max_chars] + "…[observation truncated]"
    return text


class AgentEngine:
    """Model-first, skill-driven agent loop for local LLMs.

    The local model is always asked what to do *before* a tool runs. Tool output is
    then returned as an observation and the model chooses the next step. This avoids
    relying on llama.cpp native function parsers and works with strict chat templates.
    """

    def __init__(self, runtime: Any, log: Callable[[str], None] | None = None):
        self.runtime = runtime
        self.log = log or (lambda _line: None)

    @staticmethod
    def _conversation_text(messages: list[dict], max_chars: int = 9000) -> tuple[str, str]:
        """Build a compact, turn-aware context packet without cutting arbitrary text.

        The newest user turn is preserved with the highest priority; recent complete
        turns are then added backwards until the character budget is reached.
        """
        records: list[tuple[str, str]] = []
        latest_user = ""
        for row in messages or []:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "user").lower()
            if role not in {"user", "assistant", "system"}:
                continue
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                latest_user = content
            records.append((role, content))
        budget = max(1400, int(max_chars or 9000))
        chosen: list[str] = []
        used = 0
        for rev_index, (role, content) in enumerate(reversed(records)):
            label = "USER" if role == "user" else "ASSISTANT" if role == "assistant" else "SYSTEM"
            is_latest = rev_index == 0 or (role == "user" and content == latest_user and not any(x.startswith("USER:") for x in chosen))
            per_turn = max(900, budget // 2) if is_latest else min(1800, max(700, budget // 4))
            if len(content) > per_turn:
                content = content[:per_turn] + "…[turn compacted]"
            rendered = f"{label}: {content}"
            cost = len(rendered) + 2
            if chosen and used + cost > budget:
                continue
            if not chosen and cost > budget:
                rendered = rendered[:budget]
                cost = len(rendered)
            chosen.append(rendered)
            used += cost
            if used >= budget:
                break
        chosen.reverse()
        return "\n\n".join(chosen), latest_user

    @staticmethod
    def _manifest(tool_defs: list[dict]) -> tuple[str, set[str]]:
        lines: list[str] = []
        names: set[str] = set()
        for item in tool_defs:
            fn = item.get("function") if isinstance(item, dict) else None
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            names.add(name)
            desc = " ".join(str(fn.get("description") or "").split())[:420]
            params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
            props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
            required = params.get("required") if isinstance(params.get("required"), list) else []
            arg_parts = []
            for key, spec in list(props.items())[:14]:
                spec = spec if isinstance(spec, dict) else {}
                typ = str(spec.get("type") or "any")
                mark = "*" if key in required else ""
                arg_parts.append(f"{key}{mark}:{typ}")
            lines.append(f"- {name}({', '.join(arg_parts)}): {desc}")
        return "\n".join(lines), names

    @staticmethod
    def _needs_external_action(task: str) -> bool:
        low = " ".join(str(task or "").lower().split())
        if re.search(r"https?://", low):
            return True
        # Strong operational phrases. Mere discussion of "API", "HTTP" or a
        # "website" stays direct chat unless the user asks to operate on it.
        phrases = (
            "search online", "search the web", "look online", "browse the web", "open the site", "open the website",
            "go to the site", "go to the website", "check the site", "check this site", "visit the site", "download from",
            "جستجو کن", "سرچ کن", "تو اینترنت", "در اینترنت", "روی وب", "برو سایت", "برو تو سایت", "سایت را باز کن",
            "سایت رو باز کن", "این سایت رو", "این سایت را", "لینک رو باز کن", "لینک را باز کن", "از سایت دانلود", "دانلود کن",
            "بررسی آنلاین", "آنلاین بررسی",
            "every minute", "every hour", "every day", "repeat this", "keep checking", "monitor this", "when a message arrives",
            "هر دقیقه", "هر ساعت", "هر روز", "هر چند", "مدام چک", "پیوسته", "لوپ", "هر وقت", "وقتی پیام", "تایمر",
        )
        if any(x in low for x in phrases):
            return True
        # Fresh/current facts normally require an external source.
        freshness = ("latest", "current price", "today's news", "right now", "جدیدترین", "آخرین خبر", "قیمت روز", "امروز چند", "الان قیمت")
        if any(x in low for x in freshness):
            return True
        # Personal domain state is also external to the model weights. Calendar/time
        # and workspace-file questions must enter the Skill tree when they need
        # live state or an operation, even though no public internet is involved.
        personal_domain = (
            "what time is it", "what's the time", "current time", "today's date", "what date is it",
            "calendar", "my schedule", "meeting", "appointment", "remind me", "free time",
            "ساعت چنده", "الان ساعت", "امروز چندمه", "تقویم", "برنامه من", "جلسه", "قرار", "یادآوری", "وقت خالی",
            "my file", "my files", "file manager", "save this file", "move this file", "delete this file", "read this file",
            "فایل من", "فایل هام", "فایل‌های من", "فایل منیجر", "این فایل", "این مدرک", "این سند", "پوشه", "مدارک",
        )
        if any(x in low for x in personal_domain):
            return True
        # Staged attachment markers are produced by the File Manager before routing.
        if "[workspace attachment:" in low:
            return True

        # API terms only route to skills when paired with an execution verb/method.
        if re.search(r"\b(get|post|put|patch|delete)\b", low) and any(x in low for x in ("api", "endpoint", "http", "url", "request")):
            return True
        if any(x in low for x in ("call the api", "send a request", "make a request", "api را صدا", "api رو صدا", "درخواست http", "پست کن", "post کن", "get بزن")):
            return True
        # Browser interaction verbs imply a real action when paired with a web object.
        interaction = any(x in low for x in ("click", "type into", "fill the form", "submit", "log in", "sign in", "کلیک", "فرم را", "فرم رو", "لاگین", "وارد سایت"))
        web_object = any(x in low for x in ("site", "website", "page", "browser", "سایت", "صفحه", "مرورگر"))
        return interaction and web_object

    @staticmethod
    def _obvious_direct_task(task: str) -> bool:
        """Very narrow zero-tool fast path for requests that are self-contained.

        It intentionally does not try to classify general requests. Novel/personal
        requests still enter the continuous capability controller so the model can
        decide what it needs.
        """
        text = " ".join(str(task or "").strip().lower().split())
        if not text or re.search(r"https?://|\[workspace attachment:|attachment_id=", text):
            return False
        if any(x in text for x in ("تلگرام", "telegram", "تقویم", "calendar", "فایل من", "my file", "my schedule", "برنامه من", "automation", "loop", "every minute", "every hour", "هر دقیقه", "هر ساعت", "لوپ", "پیوسته")):
            # Pure explanations of the nouns themselves remain direct.
            if not (text.startswith("explain ") or " چیست" in text or "یعنی چه" in text):
                return False
        social = re.sub(r"[!?؟،,.\s]+$", "", text)
        if social in {"سلام","درود","سلام خوبی","سلام چطوری","hi","hello","hey","thanks","thank you","ممنون","متشکرم"}:
            return True
        if re.match(r"^(?:write (?:a |an )?(?:short )?(?:story|poem)|translate\b|explain\b|what is\b)", text):
            return True
        if any(text.startswith(x) for x in ("ترجمه کن", "ترجمه بکن", "یه داستان", "یک داستان", "یه شعر", "یک شعر")):
            return True
        if ("چیست" in text or "یعنی چه" in text) and not any(x in text for x in ("امروز", "الان", "قیمت روز", "آخرین")):
            return True
        return False

    @classmethod
    def requires_agent(cls, task: str) -> bool:
        """Compatibility fallback used only when a routing-model response is invalid.

        Normal routing is model-driven in ``_route_decision``; this deterministic
        classifier no longer decides a valid user turn on its own.
        """
        return cls._needs_external_action(task)

    @staticmethod
    def _route_prompt(conversation: str, families: list[str] | None = None) -> str:
        from .skill_system import FAMILY_LABELS
        titles = "\n".join(f"- {name}: {description}" for name,description in FAMILY_LABELS.items()
                           if families is None or name in families)
        return f"""You are stage 0 of a local AI agent router.
Does the latest request need a real tool? Do not answer or execute the task.
Use direct for greetings, writing, explanations, translation and text already in the conversation.
Use skills for current clock/date, personal state, reading URLs/files, search, actual calendar/file changes, Telegram messages, website actions, and any future/recurring/event-triggered work. Recurring work must use the automation family instead of keeping one model response alive.
Mentioning a tool is not an instruction to use it. Attachments are metadata first; do not read content unless needed.
Choose only needed families by title. Tool schemas are supplied AFTER this decision, never here.
RETURN EXACTLY ONE JSON OBJECT AND NOTHING ELSE:
{{"route":"direct"}} OR {{"route":"skills","families":["files"],"goal":"short goal"}}
Families enabled in this profile:
{titles}
If a needed family is disabled, do not invent access; explain the setting in the direct answer.
CONVERSATION / LATEST USER TASK:
{conversation[-7000:]}"""

    def _route_decision(
        self,
        call_model: Callable[[list[dict], list[dict]], dict],
        conversation: str,
        latest_user: str,
        families: list[str] | None = None,
    ) -> tuple[AgentRouteDecision, str]:
        # Exact social turns have no live-state dependency. Mixed turns still go
        # through the model ("hello, create a meeting" is not a greeting shortcut).
        social = re.sub(r"[!?؟،,.\s]+$", "", latest_user.strip().lower())
        if social in {"سلام", "درود", "سلام خوبی", "سلام چطوری", "hi", "hello", "hey", "thanks", "thank you", "ممنون", "متشکرم"}:
            return AgentRouteDecision("direct", "Simple conversational turn", 100), ""
        prompt = self._route_prompt(conversation, families)
        msg = call_model([{"role": "user", "content": prompt}], [])
        raw = ""
        if isinstance(msg, dict):
            raw = str(msg.get("content") or "").strip() or str(msg.get("reasoning_content") or "").strip()
        obj = _json_object_from_text(raw)
        route = str((obj or {}).get("route") or (obj or {}).get("mode") or "").strip().lower()
        if route in {"agent", "tool", "tools", "skill", "skills", "external"}:
            route = "skills"
        elif route in {"chat", "answer", "direct", "model"}:
            route = "direct"
        if route in {"direct", "skills"}:
            try:
                confidence = max(0, min(100, int((obj or {}).get("confidence") or 0)))
            except Exception:
                confidence = 0
            return AgentRouteDecision(
                route=route,
                summary=str((obj or {}).get("summary") or (obj or {}).get("reason") or "").strip()[:500],
                confidence=confidence,
                families=[str(x) for x in obj.get("families", [])] if isinstance(obj.get("families"), list) else None,
                goal=str(obj.get("goal") or "")[:500], needs_write=bool(obj.get("needs_write")),
            ), raw

        # One small repair inference keeps control with the model instead of
        # silently turning malformed JSON into a keyword-based routing decision.
        repair = (
            "Your previous router output was invalid. Return ONLY one JSON object with "
            "route equal to direct or skills, plus summary and confidence.\n"
            f"USER TASK: {latest_user[:3000]}\nINVALID OUTPUT: {raw[:1200]}"
        )
        repaired = call_model([{"role": "user", "content": repair}], [])
        repaired_raw = ""
        if isinstance(repaired, dict):
            repaired_raw = str(repaired.get("content") or "").strip() or str(repaired.get("reasoning_content") or "").strip()
        obj = _json_object_from_text(repaired_raw)
        route = str((obj or {}).get("route") or "").strip().lower()
        if route in {"agent", "tool", "tools", "skill", "skills", "external"}:
            route = "skills"
        elif route in {"chat", "answer", "direct", "model"}:
            route = "direct"
        if route in {"direct", "skills"}:
            try:
                confidence = max(0, min(100, int((obj or {}).get("confidence") or 0)))
            except Exception:
                confidence = 0
            return AgentRouteDecision(route=route, summary=str((obj or {}).get("summary") or "").strip()[:500], confidence=confidence,
                families=[str(x) for x in obj.get("families", [])] if isinstance(obj.get("families"), list) else None,
                goal=str(obj.get("goal") or "")[:500], needs_write=bool(obj.get("needs_write"))), repaired_raw

        # Last-resort compatibility fallback only if the model failed to provide
        # a usable route twice. This is no longer the normal decision path.
        fallback = "skills" if self._needs_external_action(latest_user) else "direct"
        return AgentRouteDecision(route=fallback, summary="Router JSON invalid; deterministic recovery used", confidence=0), repaired_raw or raw

    @staticmethod
    def _response_language(task: str) -> str:
        text = str(task or "")
        fa = len(re.findall(r"[\u0600-\u06ff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        if fa >= max(2, latin // 3):
            return "Persian (fa)"
        if latin >= 2:
            return "English (en)"
        return "the same language and register as the user's latest message"

    @staticmethod
    def _language_matches(answer: str, target_language: str) -> bool:
        text = str(answer or "")
        if target_language.startswith("Persian"):
            return len(re.findall(r"[\u0600-\u06ff]", text)) >= max(2, len(re.findall(r"[A-Za-z]", text)) // 4)
        if target_language.startswith("English"):
            return len(re.findall(r"[A-Za-z]", text)) >= 2
        return True

    @staticmethod
    def _context_policy(context_limit: int) -> dict[str, int]:
        ctx = max(2048, int(context_limit or 8192))
        if ctx <= 4096:
            return {"conversation_chars": 3200, "observation_chars": 1700, "observation_keep": 2, "skill_limit": 5}
        if ctx <= 8192:
            return {"conversation_chars": 5200, "observation_chars": 2800, "observation_keep": 3, "skill_limit": 6}
        if ctx <= 16384:
            return {"conversation_chars": 7600, "observation_chars": 3800, "observation_keep": 4, "skill_limit": 7}
        return {"conversation_chars": 10000, "observation_chars": 5200, "observation_keep": 4, "skill_limit": 8}

    @staticmethod
    def _decision(obj: dict[str, Any] | None) -> AgentDecision | None:
        if not isinstance(obj, dict):
            return None
        action = str(obj.get("action") or obj.get("type") or "").strip().lower()
        # Backward/weak-model compatibility: older controllers sometimes emit a
        # route+families object instead of an action. Treat that as a request to
        # reveal those capability branches, not as a separate routing stage.
        if not action:
            legacy_route = str(obj.get("route") or obj.get("mode") or "").strip().lower()
            legacy_families = obj.get("families") if isinstance(obj.get("families"), list) else []
            if legacy_route in {"skills", "agent", "tool", "tools"} and legacy_families:
                action = "discover"
            elif legacy_route in {"direct", "chat", "answer"} and (obj.get("answer") or obj.get("response") or obj.get("final")):
                action = "final"
        if action in {"use_tool", "tool", "skill", "execute"}:
            action = "tool"
        elif action in {"parallel", "parallel_tools", "batch"}:
            action = "parallel"
        elif action in {"discover", "expand", "open_branch", "capability", "capabilities"}:
            action = "discover"
        elif action in {"answer", "done", "finish", "final", "respond"}:
            action = "final"
        summary = str(obj.get("summary") or obj.get("plan") or obj.get("next_step") or "").strip()
        skill = str(obj.get("skill") or obj.get("tool") or obj.get("name") or "").strip()
        arguments = obj.get("arguments") if isinstance(obj.get("arguments"), dict) else obj.get("args") if isinstance(obj.get("args"), dict) else {}
        answer = str(obj.get("answer") or obj.get("final") or obj.get("response") or "").strip()
        if action == "tool" and skill:
            return AgentDecision(action="tool", summary=summary, skill=skill, arguments=arguments)
        if action == "parallel":
            raw_actions = obj.get("actions") if isinstance(obj.get("actions"), list) else []
            actions = []
            for row in raw_actions[:3]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("skill") or row.get("tool") or row.get("name") or "").strip()
                args = row.get("arguments") if isinstance(row.get("arguments"), dict) else row.get("args") if isinstance(row.get("args"), dict) else {}
                if name:
                    actions.append({"skill": name, "arguments": args})
            if len(actions) >= 2:
                return AgentDecision(action="parallel", summary=summary, actions=actions)
        if action == "discover":
            fam = obj.get("families") if isinstance(obj.get("families"), list) else obj.get("family")
            if isinstance(fam, str):
                fam = [fam]
            families = [str(x).strip() for x in (fam or []) if str(x).strip()]
            if families:
                return AgentDecision(action="discover", summary=summary, families=families[:4])
        if action == "final" and answer:
            return AgentDecision(action="final", summary=summary, answer=answer)
        return None

    def _discover_capabilities(
        self,
        call_model: Callable[[list[dict], list[dict]], dict],
        conversation: str,
        latest_user: str,
        registry: Any,
    ) -> tuple[str, list[str], bool, str]:
        prompt = (
            "You are stage 1 of a local agent. First understand the user's operational goal; do not execute anything yet.\n"
            + registry.capability_prompt()
            + "\n\nUSER TASK / CONTEXT:\n" + conversation[-7000:]
        )
        msg = call_model([{"role": "user", "content": prompt}], [])
        raw = ""
        if isinstance(msg, dict):
            raw = str(msg.get("content") or "").strip() or str(msg.get("reasoning_content") or "").strip()
        obj = _json_object_from_text(raw)
        goal, cats, needs_write = registry.parse_capabilities(obj)
        # Domain-level guard rails are merged with (not substituted for) the model's
        # capability choice. This keeps the tree creative while ensuring obvious
        # calendar/file/web requests never disappear because a small model missed a family.
        hinted = registry.categories_for_families(registry.hinted_families(latest_user))
        for category in hinted:
            if category not in cats:
                cats.append(category)
        if not cats:
            # Deterministic fallback only classifies which skills to SHOW. The
            # local model still selects the actual skill in the planning step.
            rows = registry.shortlist(latest_user, [], limit=7)
            for row in rows:
                cat = str(row.get("category") or "")
                if cat and cat not in cats:
                    cats.append(cat)
                if len(cats) >= 3:
                    break
        return goal, cats[:8], needs_write, raw

    @staticmethod
    def _is_check_only(task: str) -> bool:
        low = str(task or "").lower()
        if any(x in low for x in (
            "does this site open", "is this site up", "reachable", "is it online", "works?",
            "باز میشه", "باز می‌شود", "در دسترسه", "در دسترس است", "سایت بالا", "کار می‌کنه", "کار میکند",
        )):
            return True
        # URL can appear between the question words, e.g. "Does https://... open?"
        if re.search(r"\bdoes\b.*https?://.*\b(open|work)\b", low):
            return True
        if re.search(r"https?://.*(باز\s*می|در\s*دسترس|بالاست|کار\s*می)", low):
            return True
        return False

    @staticmethod
    def _parallel_read_safe(skill: str, arguments: dict[str, Any], catalog: list[dict[str, Any]]) -> bool:
        row = next((x for x in catalog if x.get("name") == skill), {})
        return operation_policy(skill, arguments, row).parallel_safe

    def _planner_prompt(self, conversation: str, manifest: str, observations: list[dict[str, str]], step: int, max_steps: int, *, observation_keep: int = 3, response_language: str = "the user's language", capability_map: str = "", task_ledger: list[dict[str, Any]] | None = None) -> str:
        recent = observations[-max(1, int(observation_keep or 3)):]
        obs = "\n\n".join(
            f"OBSERVATION {i+1} from {o['skill']}:\n{o['result']}"
            for i, o in enumerate(recent)
        ) or "No tool has been used yet."
        ledger_rows = []
        for row in (task_ledger or [])[-10:]:
            ledger_rows.append({
                "step": row.get("step"), "skill": row.get("skill"), "ok": row.get("ok"),
                "summary": str(row.get("summary") or "")[:520],
            })
        ledger = json.dumps(ledger_rows, ensure_ascii=False, separators=(",", ":")) if ledger_rows else "[]"
        active = manifest.strip() or "No detailed branch is expanded yet. Use action=discover for a family, unless a tool schema is already visible below."
        return f"""LLAMAFORGE_AGENT_CONTROL_V3
You are one continuous Agent brain. Keep the user's goal across all steps. Do not restart the task after each tool result.
The runtime is your deterministic helper: you decide what information/action is needed; the program executes it and injects the real result back into this SAME task loop.

CORE RULES:
1. Think about the whole user goal, then choose only the NEXT useful action.
2. You may need many read/think/action cycles. Continue until enough real data exists; do not stop just because one tool ran.
3. CAPABILITY MAP is always available so you do not forget what you can access. Detailed schemas are lazy-loaded.
4. If you need a family whose detailed schema is not currently visible, return action=discover with that family. The runtime will reveal it and you continue.
5. If a detailed tool is visible, call it directly. Independent READ-ONLY calls may be parallel; dependent calls must be sequential.
6. Tool observations are authoritative. Never invent a result, ID, message, file, calendar event, web page, or successful mutation.
7. Never repeat the exact same failed call. Change arguments/capability or explain the blocker.
8. After writes, verify state with a safe read when practical before claiming success.
9. Prefer low-cost deterministic reads before browser automation. Exact API endpoints use http_request.
10. For Telegram/person resolution, let the model choose among bounded candidates; if ambiguity remains, request more context or ask the user instead of guessing.
11. TASK LEDGER preserves older steps even when full observations are compacted. Use it to remember what was already done.
12. Keep internal control concise. The final user-facing answer must be in {response_language}.
13. Use the exact skill name shown below. For generic tools such as automation, telegram, calendar, and workspace_files, put the requested action in arguments.operation; never return an operation name as the skill.
14. If the user asks for future, recurring, continuous, timer/cron or event-triggered work, create/manage an automation. Never simulate a background loop by continuing one generation. Prefer event triggers (for example telegram.message.received) over polling when a live event source exists.
15. Generic tool calls require arguments.operation. If it is missing, add it when the summary and arguments make the operation unambiguous; otherwise re-plan with the complete schema.

RETURN EXACTLY ONE JSON OBJECT AND NOTHING ELSE.
Open a capability branch:
{{"action":"discover","summary":"why this capability is needed","families":["telegram"]}}
Run one tool:
{{"action":"tool","summary":"short next step","skill":"SKILL_NAME","arguments":{{...}}}}
Run 2-3 independent read-only tools:
{{"action":"parallel","summary":"short batch","actions":[{{"skill":"A","arguments":{{...}}}},{{"skill":"B","arguments":{{...}}}}]}}
Finish only when the user's request is actually answered/completed:
{{"action":"final","summary":"done","answer":"final answer to the user"}}

CAPABILITY MAP (always visible):
{capability_map or 'No capability is currently available.'}

ACTIVE DETAILED SKILLS (lazy-loaded schemas):
{active}

TASK LEDGER (compact persistent state):
{ledger}

CONVERSATION / USER TASK:
{conversation}

RECENT REAL OBSERVATIONS:
{obs}

STEP {step} OF {max_steps} · CONTROL CYCLE {step} OF {max_steps}. Return exactly one next action."""

    @staticmethod
    def _repair_prompt(raw: str, manifest: str) -> str:
        return f"""Your previous agent-control output was not valid JSON for the required schema.
Return ONE JSON object only. Do not add markdown or explanations.
Valid forms:
{{"action":"discover","summary":"why","families":["telegram"]}}
OR
{{"action":"tool","summary":"short action summary","skill":"one available skill","arguments":{{}}}}
OR
{{"action":"parallel","summary":"independent read batch","actions":[{{"skill":"read skill","arguments":{{}}}},{{"skill":"another read skill","arguments":{{}}}}]}}
OR
{{"action":"final","summary":"short completion note","answer":"user-facing answer"}}
Available skills:
{manifest}
Previous output:
{raw[:5000]}"""

    def _model_decision(
        self,
        call_model: Callable[[list[dict], list[dict]], dict],
        prompt: str,
        manifest: str,
        vision: list[str] | None = None,
    ) -> tuple[AgentDecision | None, str]:
        # Deliberately one user message and no native tools. When File Manager
        # explicitly returns an image for inspection, attach it only to this
        # planning turn so ordinary file operations remain metadata-only.
        content: Any = prompt
        images = [str(x) for x in (vision or []) if str(x).startswith("data:image/")][:2]
        if images:
            content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": x}} for x in images]
        msg = call_model([{"role": "user", "content": content}], [])
        if not isinstance(msg, dict):
            return None, ""
        raw = str(msg.get("content") or "").strip()
        reasoning_raw = str(msg.get("reasoning_content") or "").strip()
        decision = self._decision(_json_object_from_text(raw))
        if not decision and reasoning_raw:
            # Some reasoning-oriented local models place the structured answer in
            # reasoning_content and leave content empty.
            decision = self._decision(_json_object_from_text(reasoning_raw))
        if decision:
            return decision, raw or reasoning_raw
        # One cheap self-repair pass. Some small models wrap JSON in prose on the first try.
        repair = call_model([{"role": "user", "content": self._repair_prompt(raw or reasoning_raw, manifest)}], [])
        if isinstance(repair, dict):
            repair_raw = str(repair.get("content") or "").strip()
            repair_reasoning = str(repair.get("reasoning_content") or "").strip()
        else:
            repair_raw = ""
            repair_reasoning = ""
        repaired = self._decision(_json_object_from_text(repair_raw)) or self._decision(_json_object_from_text(repair_reasoning))
        return repaired, repair_raw or repair_reasoning or raw or reasoning_raw

    def _finalize_language(
        self,
        call_model: Callable[[list[dict], list[dict]], dict],
        *,
        latest_user: str,
        observations: list[dict[str, str]],
        draft: str,
        target_language: str,
        observation_keep: int,
    ) -> str:
        draft = str(draft or "").strip()
        if self._language_matches(draft, target_language):
            return draft
        evidence = "\n\n".join(
            f"{o['skill']}: {o['result']}" for o in observations[-max(1, observation_keep):]
        )
        prompt = f"""Write the final answer for the end user in {target_language}.
The agent's internal planning is English, but the final response must match the user's language and register.
Preserve factual content from the real observations. Do not invent actions or results. Do not mention internal prompts, routing, hidden reasoning, or translation.

LATEST USER MESSAGE:
{latest_user}

REAL TOOL OBSERVATIONS:
{evidence or 'No external observations.'}

DRAFT ANSWER:
{draft}

Return only the final user-facing answer."""
        msg = call_model([{"role": "user", "content": prompt}], [])
        if isinstance(msg, dict):
            fixed = str(msg.get("content") or "").strip()
            if fixed:
                return fixed
        return draft

    @staticmethod
    def _fill_common_args(decision: AgentDecision, task: str) -> AgentDecision:
        args = dict(decision.arguments or {})
        urls = re.findall(r"https?://[^\s<>'\"]+", task or "")
        url = urls[0].rstrip(".,);]}") if urls else ""
        if decision.skill in {"web_check", "web_read", "web_find", "download_file", "browser_open", "browser_new_tab"} and not args.get("url") and url:
            args["url"] = url
        if decision.skill == "http_request":
            args.setdefault("method", "GET")
            if not args.get("url") and url:
                args["url"] = url
        if decision.skill == "web_search" and not args.get("query"):
            args["query"] = task[:700]
        decision.arguments = args
        return decision

    @staticmethod
    def _normalize_tool_selection(
        skill: str,
        arguments: dict[str, Any] | None,
        summary: str,
        available: set[str],
        catalog: list[dict[str, Any]],
        *,
        infer_operation: bool = True,
    ) -> tuple[str, dict[str, Any], dict[str, str] | None]:
        """Repair unambiguous generic-tool aliases and missing operation fields.

        Small planners sometimes return an operation name (for example
        ``messages``) as the skill, or omit the required operation while the
        arguments and action summary make one operation explicit. Keep ambiguous
        calls untouched so normal schema validation can ask the model to replan.
        """
        name = str(skill or "").strip()
        args = dict(arguments or {})
        repair: dict[str, str] | None = None

        if name not in available:
            matches: list[str] = []
            for row in catalog:
                candidate = str(row.get("name") or "")
                if candidate not in available:
                    continue
                schema = (row.get("contract") or {}).get("input_schema") or {}
                properties = schema.get("properties") if isinstance(schema, dict) else {}
                operation = properties.get("operation") if isinstance(properties, dict) else {}
                enum = operation.get("enum") if isinstance(operation, dict) else []
                if name and name in (enum or []):
                    matches.append(candidate)
            if len(matches) == 1 and (not args.get("operation") or str(args.get("operation")) == name):
                old_name = name
                name = matches[0]
                args.setdefault("operation", old_name)
                repair = {"from": old_name, "to": name, "operation": old_name}

        if infer_operation and name in OPERATION_INPUTS and not str(args.get("operation") or "").strip():
            low = str(summary or "").casefold()
            operation = ""
            if name == "code_job":
                # A code write is structurally unambiguous once the existing job,
                # target path and content are all present. Repair it locally instead
                # of spending another model call on a schema-only correction.
                if all(key in args and args[key] is not None for key in ("job_id", "path", "content")):
                    operation = "write"
                elif all(key in args and args[key] is not None for key in ("job_id", "path", "old_text", "new_text")):
                    operation = "replace"
            elif name == "workspace_files":
                move_intent = bool(re.search(r"\b(move|moving|relocate|transfer)\b|انتقال|جابجا|جابه.?جا|منتقل|ببر", low))
                rename_intent = bool(re.search(r"\b(rename|renaming)\b|تغییر\s*نام|اسمش\s*را\s*عوض", low))
                if args.get("id") and args.get("folder") and move_intent:
                    operation = "move"
                elif args.get("id") and args.get("name") and rename_intent:
                    operation = "rename"
                elif all(args.get(key) is not None for key in ("id", "old_text", "new_text")):
                    operation = "replace_text"
            elif name == "automation":
                if args.get("task") and args.get("trigger_type"):
                    operation = "create"
                elif args.get("id") and args.get("state") is not None:
                    operation = "set_state"
                elif args.get("id") and re.search(r"\b(pause|stop)\b|توقف|مکث", low):
                    operation = "pause"
                elif args.get("id") and re.search(r"\b(resume|continue)\b|ادامه", low):
                    operation = "resume"
            elif name == "telegram":
                if args.get("chat_ref") and args.get("query"):
                    operation = "search"
                elif args.get("candidate_ref") and re.search(r"\b(select|choose|confirm)\b|انتخاب|گزین", low):
                    operation = "select_person"
                elif args.get("query") and not args.get("chat_ref") and re.search(r"\b(resolve|find|identify)\b.*\b(person|contact|recipient)\b|مخاطب|شخص|فرد", low):
                    operation = "resolve_person"
                elif args.get("chat_ref") and args.get("text") and args.get("message_id"):
                    operation = "reply"
                elif args.get("chat_ref") and args.get("text"):
                    operation = "send"

            if operation:
                args["operation"] = operation
                repair = {"from": name, "to": name, "operation": operation}
        return name, args, repair

    @staticmethod
    def _heuristic_first_action(task: str, available: set[str]) -> AgentDecision | None:
        """Last-resort router only when a small model twice fails to emit JSON."""
        urls = re.findall(r"https?://[^\s<>'\"]+", task or "")
        if urls and "web_read" in available:
            return AgentDecision("tool", "Read the URL supplied by the user.", "web_read", {"url": urls[0].rstrip(".,);]}")})
        low = str(task or "").lower()
        if "automation" in available:
            m = re.search(r"(?:every|هر)\s*(\d+)\s*(minute|minutes|دقیقه)", low)
            if m:
                seconds=max(30,int(m.group(1))*60)
                return AgentDecision("tool", "Create a persistent interval automation instead of an in-generation loop.", "automation", {"operation":"create","name":"Recurring task","task":str(task or "")[:4000],"trigger_type":"interval","interval_seconds":seconds,"schedule_mode":"fixed_delay","overlap_policy":"coalesce"})
        if "calendar" in available and any(x in low for x in ("ساعت چنده", "الان ساعت", "امروز چندمه", "فردا", "پس فردا", "وقت خالی", "تقویم", "جلسه", "قرار", "یادآوری", "meeting", "calendar", "current time", "what time is it", "what's the time", "today's date", "free time", "tomorrow")):
            # now is the safest first primitive: it grounds timezone/date and lets the
            # next planning step resolve relative phrases such as tomorrow correctly.
            return AgentDecision("tool", "Ground the request in the real local calendar clock.", "calendar", {"operation": "now"})
        if "workspace_files" in available:
            match = re.search(r"\[workspace attachment:\s*attachment_id=([^\s\]]+)", str(task or ""), re.I)
            if match:
                attachment_id = match.group(1)
                # Do not let our own generated attachment marker (which mentions
                # "store" and "read") contaminate intent detection.
                intent_low = re.sub(r"\[workspace attachment:.*?\]", " ", low, flags=re.I | re.S)
                # The fallback is deliberately conservative: generic requests such as
                # "check this file" / "این فایل را بررسی کن" should first probe metadata/type.
                # Open bytes only when the user explicitly asks for file contents. The
                # normal model-driven router can decide to read after seeing the probe.
                read_terms = (
                    "read the file", "read this file", "read its contents", "read contents",
                    "summarize", "summarise", "translate the file", "file contents", "inside the file",
                    "analyze the contents", "analyse the contents",
                    "متن فایل", "متنش", "بخون", "بخوان", "خلاصه", "ترجمه",
                    "محتوای فایل", "محتواش", "داخل فایل", "داخلش", "کد داخل", "کدش رو", "کدش را",
                )
                store_terms = ("save", "store", "keep", "ذخیره", "نگه", "بذار", "قرار بده", "ببر")
                if any(x in intent_low for x in read_terms):
                    return AgentDecision("tool", "Read the attached file because its contents are needed.", "workspace_files", {"operation": "read_content", "attachment_id": attachment_id})
                if any(x in intent_low for x in store_terms):
                    return AgentDecision("tool", "Store the attached file in the local workspace.", "workspace_files", {"operation": "store_attachment", "attachment_id": attachment_id})
                return AgentDecision("tool", "Probe the attached file metadata/type first without opening its contents.", "workspace_files", {"operation": "probe", "attachment_id": attachment_id})
            if any(x in low for x in ("فایل", "مدرک", "سند", "پوشه", "file", "document", "folder")):
                return AgentDecision("tool", "Inspect workspace metadata first.", "workspace_files", {"operation": "list", "folder": ""})
        if "web_search" in available and AgentEngine._needs_external_action(task):
            return AgentDecision("tool", "Search the web for the requested information.", "web_search", {"query": task[:700]})
        return None

    @staticmethod
    def _explicit_browser_interaction(task: str) -> bool:
        low = str(task or "").lower()
        terms = (
            "click", "type", "fill", "submit", "login", "log in", "sign in", "press", "button", "form",
            "کلیک", "تایپ", "پر کن", "فرم", "لاگین", "ورود کن", "دکمه", "ثبت کن", "ارسال کن",
        )
        return any(t in low for t in terms)

    @staticmethod
    def _call_signature(skill: str, arguments: dict[str, Any] | None) -> str:
        try:
            args = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            args = str(arguments or {})
        return f"{skill}|{args}"

    @staticmethod
    def _parse_tool_result(result: Any, skill: str = "") -> tuple[bool, str]:
        try:
            obj = json.loads(result) if isinstance(result, str) else result
        except Exception:
            return True, ""
        if isinstance(obj, dict):
            ok = bool(obj.get("ok", True))
            err = str(obj.get("error") or "")
            nested = obj.get("result") if isinstance(obj.get("result"), dict) else {}
            if not err:
                err = str(nested.get("error") or "")
            # Tool execution may succeed while the remote HTTP operation itself
            # returns a failure status. Treat that as a recoverable observation
            # for content/API skills; web_check intentionally reports status as data.
            if ok and skill != "web_check":
                # code_job uses a string process lifecycle status rather than an
                # HTTP status. A tool invocation can itself succeed while the
                # spawned program exits with an error. Treat terminal process
                # failures as real Agent failures so the model debugs instead
                # of incorrectly claiming that the program ran successfully.
                if skill == "code_job":
                    process_status = str(nested.get("status") or "").lower()
                    if process_status in {"failed", "timed_out", "interrupted"}:
                        exit_code = nested.get("exit_code")
                        tail = str(nested.get("output") or "").strip()
                        summary = f"Program {process_status.replace('_', ' ')}"
                        if exit_code is not None:
                            summary += f" (exit {exit_code})"
                        if tail:
                            summary += ": " + tail[-1200:]
                        return False, summary
                try:
                    status = int(nested.get("status")) if nested.get("status") is not None else None
                except Exception:
                    status = None
                if status is not None and status >= 400:
                    return False, f"HTTP {status}"
                if nested.get("ok_status") is False:
                    return False, f"HTTP {status}" if status is not None else "Remote operation returned a failure status"
            return ok, err
        return True, ""

    @staticmethod
    def _answer_messages(prompt: str, observations: list[dict]) -> list[dict]:
        images = [url for o in observations[-4:] for url in o.get("vision", []) if url.startswith("data:image/")][-2:]
        content = ([{"type":"text", "text":prompt}] + [{"type":"image_url", "image_url":{"url":url}} for url in images]) if images else prompt
        return [{"role":"user", "content":content}]

    @staticmethod
    def _streaming_final_prompt(*, latest_user: str, observations: list[dict[str, str]], draft: str, target_language: str, observation_keep: int) -> str:
        evidence = "\n\n".join(
            f"{o['skill']}: {o['result']}" for o in observations[-max(1, observation_keep):]
        )
        return f"""Write the final answer for the end user in {target_language}.
Use the REAL TOOL OBSERVATIONS as authoritative evidence. Do not invent actions or results.
Do not mention internal prompts, routing, hidden reasoning, JSON control, or skill-selection mechanics.
Be direct and answer the user's actual request.

LATEST USER MESSAGE:
{latest_user}

REAL TOOL OBSERVATIONS:
{evidence or 'No external observations.'}

PLANNER DRAFT / INTENT:
{str(draft or '').strip() or 'Answer the request from the evidence above.'}

Return only the final user-facing answer."""

    @staticmethod
    def _stream_text(text: str) -> Iterator[dict[str, Any]]:
        text = str(text or "")
        pos = 0
        while pos < len(text):
            end = min(len(text), pos + 180)
            if end < len(text):
                cut = max(text.rfind(" ", pos, end), text.rfind("\n", pos, end))
                if cut > pos + 70:
                    end = cut + 1
            yield {"type": "text", "delta": text[pos:end]}
            pos = end

    def run(
        self,
        messages: list[dict],
        call_model: Callable[[list[dict], list[dict]], dict],
        permissions: Any,
        max_steps: int = 8,
        context_limit: int = 8192,
        stream_final: Callable[[list[dict]], Iterator[dict[str, Any]]] | None = None,
        cancel: Any = None,
    ) -> Iterator[dict[str, Any]]:
        """Run one continuous Agent task.

        v3 intentionally removes the old route-model -> capability-model -> planner
        chain. One controller sees the permanent capability map, the active lazy
        schemas, a compact task ledger and fresh observations. It can answer,
        request another capability branch, or execute a tool. After a real tool
        result the same task loop resumes, so dependent workflows can take as many
        read/think/action cycles as the configured step budget permits.
        """
        from .skill_system import SkillRegistry, FAMILY_LABELS
        policy = self._context_policy(context_limit)
        conversation, latest_user = self._conversation_text(messages, max_chars=policy["conversation_chars"])
        max_steps = max(1, min(24, int(max_steps or 8)))
        target_language = self._response_language(latest_user)

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Agent cancelled")

        original_model = call_model
        def guarded_model(model_messages, tools):
            check_cancel()
            response = logged_model(lambda: original_model(model_messages, tools), model_messages, tools=tools)
            check_cancel()
            return response
        call_model = guarded_model

        if stream_final is not None:
            original_stream = stream_final
            def guarded_stream(model_messages):
                check_cancel()
                iterator = logged_stream(original_stream(model_messages), model_messages)
                try:
                    for event in iterator:
                        check_cancel()
                        yield event
                finally:
                    if hasattr(iterator, "close"):
                        iterator.close()
            stream_final = guarded_stream

        if self._obvious_direct_task(latest_user):
            yield {"type":"agent","event":"route_decision","route":"direct","summary":"Self-contained request","confidence":100,"model_seconds":0.0,"label":"Direct fast path — no Skill catalog needed"}
            yield {"type":"agent","event":"route","route":"direct","label":"Direct model response — no external skill needed","response_language":target_language}
            if stream_final is not None:
                emitted=False
                for streamed in stream_final(messages):
                    if isinstance(streamed,dict) and streamed.get("type")=="text" and streamed.get("delta"):
                        emitted=True
                    yield streamed
                if emitted:
                    yield {"type":"agent","event":"direct_complete","model_seconds":0.0,"language_corrected":False,
                           "streamed":True,"label":"Answered directly with live token streaming"}
                    return
            msg=call_model(messages,[])
            answer=str(msg.get("content") or "").strip() if isinstance(msg,dict) else ""
            if not answer: answer=str(msg.get("reasoning_content") or "").strip() if isinstance(msg,dict) else ""
            answer = answer or "The model returned an empty response."
            draft_answer=answer
            answer = self._finalize_language(call_model, latest_user=latest_user, observations=[], draft=answer,
                                             target_language=target_language, observation_keep=1)
            yield {"type":"agent","event":"direct_complete","model_seconds":0.0,"language_corrected":answer!=draft_answer,
                   "streamed":False,"label":"Answered directly without Skills"}
            yield from self._stream_text(answer)
            return

        registry = SkillRegistry(self.runtime, permissions)
        enabled_families = ["telegram"] if getattr(permissions, "skill_profile", "all") == "telegram_only" else None
        tool_defs = self.runtime.tool_definitions(permissions)
        catalog_rows = registry.catalog(tool_defs)
        if current_trace() is not None:
            record('skills.catalog', catalog=catalog_rows, definitions=tool_defs)

        capability_hints = [f for f in registry.hinted_families(latest_user) if enabled_families is None or f in enabled_families]
        capability_map = registry.capability_map(tool_defs, enabled_families)
        required_live = bool(capability_hints) or self._needs_external_action(latest_user)
        active_families: list[str] = list(dict.fromkeys(capability_hints))
        categories = registry.categories_for_families(active_families)
        shortlist = registry.shortlist(latest_user, categories, limit=policy["skill_limit"]) if categories else []
        manifest, available = registry.manifest(shortlist, tool_defs) if shortlist else ("", set())

        observations: list[dict[str, Any]] = []
        task_ledger: list[dict[str, Any]] = []
        attempts: dict[str, dict[str, Any]] = {}
        explicit_browser = self._explicit_browser_interaction(latest_user)
        check_only = self._is_check_only(latest_user)
        first_decision = True

        record('agent.context', messages=messages, conversation=conversation, latest_user=latest_user,
               policy=policy, permissions=vars(permissions), capability_hints=capability_hints,
               capability_map=capability_map, active_families=active_families)
        record('skills.shortlist', reason='initial_lazy_branch', families=active_families,
               categories=categories, rows=shortlist, available=sorted(available), manifest=manifest)

        yield {"type":"agent", "event":"phase", "phase":"continuous", "label":"Continuous Agent thinking"}
        yield {"type":"agent", "event":"context_policy", "context_limit":int(context_limit or 8192),
               "conversation_chars":policy["conversation_chars"], "observation_chars":policy["observation_chars"],
               "observation_keep":policy["observation_keep"], "skill_limit":policy["skill_limit"],
               "max_control_cycles":max_steps, "label":"Continuous task context enabled"}
        yield {"type":"agent", "event":"capability_map", "families":active_families,
               "available_families":[f for f in FAMILY_LABELS if enabled_families is None or f in enabled_families],
               "skills":sorted(available), "label":"Capabilities stay visible; schemas load on demand"}

        def expand_families(families: list[str], *, reason: str) -> bool:
            nonlocal categories, shortlist, manifest, available
            changed = False
            for family in families:
                family = str(family or "").strip()
                if family not in FAMILY_LABELS:
                    continue
                if enabled_families is not None and family not in enabled_families:
                    continue
                if family not in active_families:
                    active_families.append(family)
                    changed = True
            categories = registry.categories_for_families(active_families)
            if categories:
                # Keep a slightly wider schema window once multiple families are
                # active, while still avoiding a full catalog dump.
                limit = min(12, max(policy["skill_limit"], policy["skill_limit"] + len(active_families) - 1))
                shortlist = registry.shortlist(latest_user, categories, limit=limit)
                manifest, available = registry.manifest(shortlist, tool_defs)
            record('skills.shortlist', reason=reason, families=active_families, categories=categories,
                   rows=shortlist, available=sorted(available), manifest=manifest)
            return changed

        for step in range(1, max_steps + 1):
            check_cancel()
            yield {"type":"agent", "event":"thinking", "step":step, "max_steps":max_steps,
                   "label":"Thinking with current task state"}
            prompt = self._planner_prompt(
                conversation, manifest, observations, step, max_steps,
                observation_keep=policy["observation_keep"], response_language=target_language,
                capability_map=capability_map, task_ledger=task_ledger,
            )
            recent_vision: list[str] = []
            for o in observations[-max(1, policy["observation_keep"]):]:
                recent_vision.extend([str(x) for x in (o.get("vision") or []) if str(x).startswith("data:image/")])
            started = time.monotonic()
            decision, raw = self._model_decision(call_model, prompt, manifest or capability_map, vision=recent_vision[-2:])
            model_seconds = max(0.0, time.monotonic() - started)
            record('planner.decision', step=step, raw=raw, parsed=asdict(decision) if decision else None,
                   active_families=active_families, ledger=task_ledger[-10:])

            if decision is None:
                decision = self._heuristic_first_action(latest_user, available) if not observations else None
                if decision is None:
                    final_prompt = (
                        f"Answer the user's request in {target_language} using only the real observations below. "
                        "If a requested operation could not be completed, state the concrete blocker.\n\n"
                        + conversation + "\n\nOBSERVATIONS:\n"
                        + "\n".join(o["result"] for o in observations[-max(1, policy["observation_keep"]):])
                    )
                    yield {"type":"agent", "event":"decision_error", "raw_preview":raw[:240],
                           "label":"Controller output invalid; finalizing from real observations"}
                    if stream_final is not None:
                        emitted=False
                        for streamed in stream_final(self._answer_messages(final_prompt, observations)):
                            if isinstance(streamed,dict) and streamed.get("type")=="text" and streamed.get("delta"):
                                emitted=True
                            yield streamed
                        if emitted:
                            return
                    msg = call_model(self._answer_messages(final_prompt, observations), [])
                    answer = str(msg.get("content") or "").strip() if isinstance(msg, dict) else ""
                    if not answer:
                        answer = "The agent could not produce a valid next action."
                    yield from self._stream_text(answer)
                    return

            if first_decision:
                direct = decision.action == "final" and not required_live and not observations
                yield {"type":"agent", "event":"route_decision", "route":"direct" if direct else "skills",
                       "summary":decision.summary, "confidence":0, "model_seconds":round(model_seconds,2),
                       "label":"Unified Agent chose direct answer" if direct else "Unified Agent entered capability loop"}
                yield {"type":"agent", "event":"route", "route":"direct" if direct else "agent",
                       "label":"One-pass direct answer" if direct else "Continuous multi-step Agent", "response_language":target_language}
                first_decision = False

            if decision.action == "discover":
                requested = [f for f in (decision.families or []) if f in FAMILY_LABELS]
                changed = expand_families(requested, reason='model_discovery')
                summary = "Expanded: " + ", ".join(requested) if requested else "No valid family requested"
                task_ledger.append({"step":step, "skill":"discovery", "ok":bool(requested), "summary":summary})
                yield {"type":"agent", "event":"replan", "families":active_families, "skills":sorted(available),
                       "label":"Loaded requested capability branch" if changed else "Capability branch already loaded"}
                continue

            if decision.action == "parallel":
                filled_actions: list[dict[str, Any]] = []
                repairs: list[dict[str, str]] = []
                for row in decision.actions or []:
                    d = AgentDecision(action="tool", skill=str(row.get("skill") or ""), arguments=dict(row.get("arguments") or {}))
                    d = self._fill_common_args(d, latest_user)
                    name, args, repair = self._normalize_tool_selection(
                        d.skill, d.arguments, decision.summary, available, catalog_rows, infer_operation=False,
                    )
                    if repair: repairs.append(repair)
                    filled_actions.append({"skill":name, "arguments":args})
                decision.actions = filled_actions
            else:
                decision = self._fill_common_args(decision, latest_user)
                repairs = []
                if decision.action == "tool":
                    decision.skill, decision.arguments, repair = self._normalize_tool_selection(
                        decision.skill, decision.arguments, decision.summary, available, catalog_rows,
                    )
                    if repair: repairs.append(repair)
            record('planner.arguments', step=step, decision=asdict(decision))
            for repair in repairs:
                record('planner.argument_repair', step=step, repair=repair)
                yield {"type":"agent", "event":"decision_repair", "repair":repair,
                       "label":"Corrected an unambiguous generic-tool call"}

            # For a request that clearly needs live state, do not accept a first
            # ungrounded final answer from a small model. Make one safe primitive
            # available/execute it, then let the same task loop continue.
            if decision.action == "final" and required_live and not observations:
                forced = self._heuristic_first_action(latest_user, available)
                if forced is None and capability_hints:
                    expand_families(capability_hints, reason='live_state_guard')
                    forced = self._heuristic_first_action(latest_user, available)
                if forced is not None:
                    decision = forced

            if decision.action == "final":
                yield {"type":"agent", "event":"decision", "action":"final", "summary":decision.summary or "Task complete",
                       "model_seconds":round(model_seconds,2), "response_language":target_language,
                       "streaming_final":False, "label":"Answering from the same continuous Agent turn"}
                answer = str(decision.answer or "").strip()
                if not answer:
                    answer = "The task is complete."
                answer = self._finalize_language(call_model, latest_user=latest_user, observations=observations,
                                                 draft=answer, target_language=target_language,
                                                 observation_keep=policy["observation_keep"])
                yield from self._stream_text(answer)
                return

            if decision.action == "parallel":
                batch = [x for x in (decision.actions or []) if x.get("skill") in available][:3]
                invalid: list[str] = []
                for row in batch:
                    sig = self._call_signature(str(row.get("skill")), row.get("arguments"))
                    if sig in attempts and attempts[sig].get("ok") is False:
                        invalid.append("Repeated failed call blocked; change arguments or capability")
                    valid, err = registry.validate_call(str(row.get("skill") or ""), dict(row.get("arguments") or {}))
                    if not valid:
                        invalid.append(err)
                    elif not self._parallel_read_safe(str(row.get("skill") or ""), dict(row.get("arguments") or {}), catalog_rows):
                        invalid.append(f"{row.get('skill')} is not read-only and cannot run in parallel")
                if len(batch) < 2 or invalid:
                    compact = _safe_json({"ok":False,"error":"; ".join(invalid) or "Parallel batch needs at least two independent read-only skills",
                                          "instruction":"Use dependent/mutating operations sequentially."})
                    observations.append({"skill":"parallel","result":compact})
                    task_ledger.append({"step":step,"skill":"parallel","ok":False,"summary":compact[:520]})
                    yield {"type":"agent", "event":"parallel_rejected", "errors":invalid, "label":"Unsafe/dependent batch rejected"}
                    continue
                yield {"type":"agent", "event":"decision", "action":"parallel", "summary":decision.summary or "Run independent reads",
                       "actions":batch, "model_seconds":round(model_seconds,2)}
                yield {"type":"agent", "event":"parallel_start", "count":len(batch), "skills":[x["skill"] for x in batch]}
                started_parallel = time.monotonic()
                results: list[tuple[int, dict[str, Any], Any, float]] = []
                pool = ThreadPoolExecutor(max_workers=min(3,len(batch)), thread_name_prefix="lf-agent-read")
                try:
                    futures = {}
                    owner = self.runtime.workspace_scope() if hasattr(self.runtime, "workspace_scope") else None
                    def execute_scoped(name, args):
                        previous = self.runtime.workspace_scope() if owner is not None else None
                        try:
                            if owner is not None:
                                self.runtime.set_workspace_scope(owner)
                            check_cancel()
                            return self.runtime.execute(name, args, permissions)
                        finally:
                            if previous is not None:
                                self.runtime.set_workspace_scope(previous)
                    for idx,row in enumerate(batch):
                        t0=time.monotonic()
                        fut=pool.submit(copy_context().run, execute_scoped, str(row["skill"]), dict(row.get("arguments") or {}))
                        futures[fut]=(idx,row,t0)
                    pending=set(futures)
                    while pending:
                        check_cancel()
                        done,pending=wait(pending,timeout=.05,return_when=FIRST_COMPLETED)
                        for fut in list(pending):
                            idx,row,t0=futures[fut]
                            metadata=next((x for x in catalog_rows if x["name"]==row["skill"]),{})
                            deadline=operation_policy(row["skill"],row.get("arguments"),metadata).timeout_seconds
                            if time.monotonic()-t0>=deadline:
                                pending.remove(fut);fut.cancel()
                                results.append((idx,row,'{"ok":false,"error":"Read operation timed out"}',time.monotonic()-t0))
                        for fut in done:
                            idx,row,t0=futures[fut]
                            try: result=fut.result()
                            except Exception as exc: result=json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False)
                            results.append((idx,row,result,max(0.0,time.monotonic()-t0)))
                finally:
                    pool.shutdown(wait=False,cancel_futures=True)
                results.sort(key=lambda x:x[0])
                for _idx,row,result,tool_seconds in results:
                    skill=str(row["skill"]); ok,error_text=self._parse_tool_result(result,skill)
                    attempts[self._call_signature(skill,row.get("arguments"))]={"ok":ok,"error":error_text,"result":result}
                    compact=_safe_json(result,max_chars=policy["observation_chars"]) if ok else _safe_json({"tool_result":result,"error":error_text},max_chars=policy["observation_chars"])
                    observations.append({"skill":skill,"result":compact})
                    task_ledger.append({"step":step,"skill":skill,"ok":ok,"summary":compact[:520]})
                    record('observation',step=step,skill=skill,parallel=True,raw=result,presented_to_model=compact)
                    yield {"type":"agent", "event":"tool_result", "tool":skill, "ok":ok, "step":step, "parallel":True,
                           "tool_seconds":round(tool_seconds,2), "error_preview":error_text[:300] if error_text else ""}
                yield {"type":"agent", "event":"parallel_done", "count":len(results),
                       "wall_seconds":round(max(0.0,time.monotonic()-started_parallel),2),
                       "label":"Independent reads completed; thinking resumes with both results"}
                continue

            if decision.action != "tool":
                observations.append({"skill":"control", "result":_safe_json({"ok":False,"error":"Unknown control action","action":decision.action})})
                continue

            # If the model remembers a capability from the permanent map but its
            # schema is not currently expanded, load that branch and let it re-plan
            # against the real schema rather than guessing arguments.
            if decision.skill not in available:
                requested = next((x for x in catalog_rows if x["name"] == decision.skill and x.get("available")), None)
                if requested:
                    family = registry.families_for_categories([requested["category"]])
                    expand_families(family, reason='lazy_tool_expansion')
                    if decision.skill not in available:
                        shortlist = [requested] + [row for row in shortlist if row.get("name") != decision.skill]
                        shortlist = shortlist[:12]
                        manifest, available = registry.manifest(shortlist, tool_defs)
                        record('skills.shortlist', reason='force_requested_tool', families=active_families, categories=categories,
                               rows=shortlist, available=sorted(available), manifest=manifest)
                    # If the model already supplied arguments that pass the real
                    # newly-loaded schema, execute immediately. This avoids a
                    # redundant "now choose the same tool again" model round.
                    valid_after_expand, _err_after_expand = registry.validate_call(decision.skill, decision.arguments or {})
                    if decision.skill in available and valid_after_expand:
                        task_ledger.append({"step":step,"skill":"discovery","ok":True,
                                            "summary":f"Loaded schema for {decision.skill}; existing arguments validated, executing now."})
                        yield {"type":"agent", "event":"replan", "families":active_families, "skills":sorted(available),
                               "label":"Loaded schema and reused the model's valid tool call"}
                    else:
                        task_ledger.append({"step":step,"skill":"discovery","ok":True,
                                            "summary":f"Loaded schema for {decision.skill}; re-plan with validated arguments."})
                        observations.append({"skill":"discovery","result":f"Detailed schema for {decision.skill} is now loaded. Re-plan this same task; do not restart."})
                        yield {"type":"agent", "event":"replan", "families":active_families, "skills":sorted(available),
                               "label":"Loaded the tool schema without losing task state"}
                        continue
                if decision.skill not in available:
                    compact=_safe_json({"ok":False,"error":f"Unknown or disabled skill {decision.skill!r}","available_families":active_families})
                    observations.append({"skill":decision.skill or "unknown","result":compact})
                    task_ledger.append({"step":step,"skill":decision.skill or "unknown","ok":False,"summary":compact[:520]})
                    yield {"type":"agent", "event":"tool_result", "tool":decision.skill, "ok":False, "error":"unknown_skill"}
                    continue

            # Cheap deterministic web policy retained from v2.
            if not observations and not explicit_browser:
                preferred = "web_check" if check_only and "web_check" in available else "web_read" if "web_read" in available else ""
                if decision.skill == "browser_open" and preferred:
                    original=decision.skill;decision.skill=preferred
                    yield {"type":"agent","event":"policy","policy":"lightweight_web_before_browser","from_skill":original,"to_skill":preferred}
                elif decision.skill == "web_read" and check_only and "web_check" in available:
                    decision.skill="web_check"
                    yield {"type":"agent","event":"policy","policy":"web_check_for_reachability","from_skill":"web_read","to_skill":"web_check"}

            signature=self._call_signature(decision.skill,decision.arguments)
            previous=attempts.get(signature)
            meta=next((x for x in catalog_rows if x.get("name")==decision.skill),{})
            op_policy=operation_policy(decision.skill,decision.arguments,meta)
            if previous and previous.get("ok") and op_policy.effect in {"local_write","external_write"}:
                compact=_safe_json(previous.get("result"),max_chars=policy["observation_chars"])
                observations.append({"skill":decision.skill,"result":compact})
                task_ledger.append({"step":step,"skill":decision.skill,"ok":True,"summary":"Reused verified write receipt: "+compact[:420]})
                yield {"type":"agent","event":"policy","policy":"reuse_write_receipt","skill":decision.skill}
                continue
            if previous and previous.get("ok") is False:
                error_text=str(previous.get("error") or "previous call failed")
                compact=_safe_json({"ok":False,"error":"Repeated failed call blocked by Agent runtime","previous_error":error_text,
                                    "instruction":"Choose different arguments/capability; keep working on the same goal."})
                observations.append({"skill":decision.skill,"result":compact})
                task_ledger.append({"step":step,"skill":decision.skill,"ok":False,"summary":compact[:520]})
                yield {"type":"agent","event":"policy","policy":"block_repeated_failed_call","skill":decision.skill,
                       "error_preview":error_text[:240]}
                continue

            valid,validation_error=registry.validate_call(decision.skill,decision.arguments or {})
            record("preflight",step=step,skill=decision.skill,arguments=decision.arguments,valid=valid,error=validation_error)
            if not valid:
                attempts[signature]={"ok":False,"error":validation_error}
                compact=_safe_json({"ok":False,"error":validation_error,"failure":{"kind":"preflight","retryable":False},
                                    "suggested_fallbacks":meta.get("fallbacks") or [],
                                    "instruction":"Fix arguments or choose another capability; task state is preserved."})
                observations.append({"skill":decision.skill,"result":compact})
                task_ledger.append({"step":step,"skill":decision.skill,"ok":False,"summary":compact[:520]})
                yield {"type":"agent","event":"preflight_failed","tool":decision.skill,"error":validation_error,
                       "fallbacks":meta.get("fallbacks") or []}
                continue

            yield {"type":"agent","event":"decision","action":"tool","skill":decision.skill,
                   "summary":decision.summary or f"Use {decision.skill}","arguments":decision.arguments or {},
                   "model_seconds":round(model_seconds,2)}
            display_args = dict(decision.arguments or {})
            if decision.skill == "code_job" and isinstance(display_args.get("content"), str):
                content = display_args["content"]
                display_args["content"] = content[:12000]
                if len(content)>12000: display_args["content_truncated"] = True
            yield {"type":"agent","event":"tool_start","tool":decision.skill,"arguments":display_args,"step":step}
            tool_started=time.monotonic()
            result=self.runtime.execute(decision.skill,decision.arguments or {},permissions)
            tool_seconds=max(0.0,time.monotonic()-tool_started)
            ok,error_text=self._parse_tool_result(result,decision.skill)
            attempts[signature]={"ok":ok,"error":error_text,"step":step,"result":result}
            failure=None;vision_items:list[str]=[]
            if ok:
                raw_ok:Any=result
                try: raw_ok=json.loads(result) if isinstance(result,str) else result
                except Exception: raw_ok=result
                if isinstance(raw_ok,dict) and isinstance(raw_ok.get("result"),dict):
                    nested=dict(raw_ok.get("result") or {})
                    vision_obj=nested.pop("vision_attachment",None)
                    if isinstance(vision_obj,dict):
                        data_url=str(vision_obj.get("data_url") or "")
                        if data_url.startswith("data:image/"):
                            vision_items.append(data_url)
                            nested["vision_attachment"]={"name":str(vision_obj.get("name") or "image"),"available_to_model":True}
                    raw_ok=dict(raw_ok);raw_ok["result"]=nested
                compact=_safe_json(raw_ok,max_chars=policy["observation_chars"])
            else:
                failure=registry.classify_failure(error_text,result)
                try: raw_result=json.loads(result) if isinstance(result,str) else result
                except Exception: raw_result={"raw":str(result)}
                compact=_safe_json({"tool_result":raw_result,"failure":failure,"suggested_fallbacks":meta.get("fallbacks") or [],
                                    "instruction":"Do not repeat the same failed call. Continue the same task using a fallback/different arguments."},
                                   max_chars=policy["observation_chars"])
            row:dict[str,Any]={"skill":decision.skill,"result":compact}
            if vision_items: row["vision"]=vision_items
            observations.append(row)
            task_ledger.append({"step":step,"skill":decision.skill,"ok":ok,"summary":compact[:520]})
            record('observation',step=step,skill=decision.skill,raw=result,presented_to_model=compact,ok=ok,failure=failure)
            code_job = None
            if decision.skill == "code_job":
                try:
                    receipt = json.loads(result) if isinstance(result, str) else result
                    detail = receipt.get("result", {}) if isinstance(receipt, dict) else {}
                    code_job = {"operation":str((decision.arguments or {}).get("operation") or ""),
                                "job_id":str(detail.get("job_id") or (decision.arguments or {}).get("job_id") or ""),
                                "status":str(detail.get("status") or ""),
                                "output":str(detail.get("output") or "")[-4000:]}
                except (TypeError, ValueError, AttributeError):
                    code_job = None
            yield {"type":"agent","event":"tool_result","tool":decision.skill,"ok":ok,"step":step,
                   **({"code_job":code_job} if code_job is not None else {}),
                   "tool_seconds":round(tool_seconds,2),"observation_chars":len(compact),
                   "error_preview":error_text[:300] if error_text else "",
                   "failure_kind":failure.get("kind") if failure else "",
                   "retryable":bool(failure.get("retryable")) if failure else False,
                   "fallbacks":meta.get("fallbacks") or [],
                   "label":"Result injected; Agent continues thinking"}

        synthesis=(
            f"The continuous Agent reached its configured control-cycle budget. Give the best final answer in {target_language} "
            "using only the real task ledger and observations. Do not claim unverified success.\n\n"
            + conversation + "\n\nTASK LEDGER:\n" + json.dumps(task_ledger[-12:],ensure_ascii=False)
            + "\n\nOBSERVATIONS:\n" + "\n\n".join(f"{o['skill']}: {o['result']}" for o in observations[-max(1,policy['observation_keep']):])
        )
        yield {"type":"agent","event":"phase","phase":"finalize","label":"Control-cycle budget reached; synthesizing"}
        if stream_final is not None:
            yield from stream_final(self._answer_messages(synthesis,observations));return
        msg=call_model(self._answer_messages(synthesis,observations),[])
        answer=str(msg.get("content") or "").strip() if isinstance(msg,dict) else ""
        if not answer: answer="The agent reached its step limit before it could finish the task."
        yield from self._stream_text(answer)
