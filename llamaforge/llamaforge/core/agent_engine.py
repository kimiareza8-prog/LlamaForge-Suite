from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterator


@dataclass
class AgentDecision:
    action: str
    summary: str = ""
    skill: str = ""
    arguments: dict[str, Any] | None = None
    answer: str = ""
    actions: list[dict[str, Any]] | None = None


@dataclass
class AgentRouteDecision:
    route: str  # direct | skills
    summary: str = ""
    confidence: int = 0


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

    @classmethod
    def requires_agent(cls, task: str) -> bool:
        """Compatibility fallback used only when a routing-model response is invalid.

        Normal routing is model-driven in ``_route_decision``; this deterministic
        classifier no longer decides a valid user turn on its own.
        """
        return cls._needs_external_action(task)

    @staticmethod
    def _route_prompt(conversation: str) -> str:
        return f"""You are stage 0 of a local AI agent router.
Decide ONLY whether the user's request should be answered directly by the local model or should enter the external Skill system. Do not answer the user's task.

ROUTING RULES:
- route=direct for greetings, ordinary conversation, writing, summarizing text already provided, translation, coding/explanations, brainstorming, and questions that can be answered from the model's own knowledge without performing a real external action.
- route=skills whenever the user asks to actually open/check/read a URL or website, search/browse the web, get fresh/current external information, download something, call GET/POST/PUT/PATCH/DELETE or another API/HTTP endpoint, interact with a browser/site, or operate a configured connector/custom external skill.
- route=skills when the answer requires live personal state/tools: current local time/date, the user's calendar/schedule/reminders, or the LlamaForge File Manager/workspace.
- A staged attachment marker means a real file exists. Route to skills when the user wants to store/organize/find/read/inspect that file. Do NOT assume file content must be read; File Manager can organize it from metadata alone.
- Merely discussing what an API/browser/website is does NOT require skills. Asking to USE it does.
- If the user supplies a URL and asks you to look at/open/check/read it, route MUST be skills.
- If the user asks for current/live/latest information that requires retrieval, route MUST be skills.
- When uncertain between direct and skills because completing the request requires verifying something outside the conversation, choose skills.

RETURN EXACTLY ONE JSON OBJECT AND NOTHING ELSE:
{{"route":"direct","summary":"short reason","confidence":0}}
or
{{"route":"skills","summary":"short reason","confidence":0}}

CONVERSATION / LATEST USER TASK:
{conversation[-7000:]}"""

    def _route_decision(
        self,
        call_model: Callable[[list[dict], list[dict]], dict],
        conversation: str,
        latest_user: str,
    ) -> tuple[AgentRouteDecision, str]:
        prompt = self._route_prompt(conversation)
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
            return AgentRouteDecision(route=route, summary=str((obj or {}).get("summary") or "").strip()[:500], confidence=confidence), repaired_raw

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
        if action in {"use_tool", "tool", "skill", "execute"}:
            action = "tool"
        elif action in {"parallel", "parallel_tools", "batch"}:
            action = "parallel"
        elif action in {"answer", "done", "finish", "final"}:
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
        risk = str(row.get("risk") or "").lower()
        if risk == "read":
            return True
        if skill == "calendar":
            return str((arguments or {}).get("operation") or "").lower() in {"now", "convert", "month", "list"}
        if skill == "workspace_files":
            return str((arguments or {}).get("operation") or "").lower() in {"list", "search", "metadata", "probe", "read_content"}
        if skill == "http_request":
            return str((arguments or {}).get("method") or "GET").upper() in {"GET", "HEAD"}
        return False

    def _planner_prompt(self, conversation: str, manifest: str, observations: list[dict[str, str]], step: int, max_steps: int, *, observation_keep: int = 3, response_language: str = "the user's language") -> str:
        recent = observations[-max(1, int(observation_keep or 3)):]
        obs = "\n\n".join(
            f"OBSERVATION {i+1} from {o['skill']}:\n{o['result']}"
            for i, o in enumerate(recent)
        ) or "No tool has been used yet."
        return f"""You are the control brain of a local autonomous agent.
Your job is NOT to pretend you used the internet. Decide the next real action, one step at a time.

WORKFLOW YOU MUST FOLLOW:
1. Understand what the user is asking.
2. Look at AVAILABLE SKILLS and choose the single best skill if external data/action is needed. If 2-3 READ-ONLY skills are independent and do not depend on each other's results, you may request them as one parallel batch.
3. After skill results arrive, inspect the OBSERVATIONs and decide the next skill or finish.
4. Never invent a tool result. Never say a site was opened/read/changed unless an observation proves it.
5. For a supplied URL that only needs reading/checking, choose web_read FIRST. Browser automation is escalation only after web_read proves JavaScript/browser interaction is required or the user explicitly asks you to click/type/log in/interact.
6. For APIs or explicit GET/POST/endpoints, choose http_request instead of browser automation.
7. NEVER repeat the exact same failed skill call with the same arguments. Read the failure observation and choose a different skill or different arguments.
8. If an observation contains a next URL, API endpoint, form action, message id, or reply instructions needed to finish the task, use them in the next skill call.
9. Do not expose secrets/tokens in the final answer.
10. Prefer the lowest-cost skill that can complete the step: check/read before browser automation.
11. After a state-changing action (POST/PUT/PATCH/DELETE/click/type/select/calendar or file mutation), verify the resulting state with a safe read when practical before declaring success.
12. Calendar and File Manager are GENERAL DOMAIN CAPABILITIES, not one-skill-per-question systems. Solve novel requests by chaining their primitive operations. Example: for a free-time question, call calendar now/list and reason over busy intervals yourself; there is intentionally no find_free_time skill.
13. Local calendar/File Manager writes are separate from dangerous remote site writes. If calendar/files are listed as available, USE them instead of claiming you lack read/write access.
14. For an attached workspace file, use read_content with its attachment_id when the user needs its contents; use store_attachment when they only want it saved. Do not list the normal workspace and pretend the attachment is missing.
15. For workspace files, default to metadata/list/search when content is unnecessary. Storing/moving/renaming a file must not read it unnecessarily.
16. If a failure observation includes suggested_fallbacks, choose one of them unless you have a concrete reason not to.
17. Parallel batches are ONLY for independent read-only work. Never parallelize writes, browser mutations, POST/PUT/PATCH/DELETE, calendar mutations, file mutations, or a step that needs another step's output.
18. Keep control/planning concise and in English. The user-facing final answer must be in {response_language}.

RETURN EXACTLY ONE JSON OBJECT AND NOTHING ELSE.
To run one skill:
{{"action":"tool","summary":"short next-step description, no hidden chain-of-thought","skill":"SKILL_NAME","arguments":{{...}}}}
To run 2-3 independent READ-ONLY skills in parallel:
{{"action":"parallel","summary":"short batch description","actions":[{{"skill":"SKILL_A","arguments":{{...}}}},{{"skill":"SKILL_B","arguments":{{...}}}}]}}
To finish:
{{"action":"final","summary":"short completion note","answer":"final answer to the user"}}

AVAILABLE SKILLS:
{manifest}

CONVERSATION / USER TASK:
{conversation}

OBSERVATIONS SO FAR:
{obs}

STEP {step} OF {max_steps}. Choose exactly one next action."""

    @staticmethod
    def _repair_prompt(raw: str, manifest: str) -> str:
        return f"""Your previous agent-control output was not valid JSON for the required schema.
Return ONE JSON object only. Do not add markdown or explanations.
Valid forms:
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
    def _heuristic_first_action(task: str, available: set[str]) -> AgentDecision | None:
        """Last-resort router only when a small model twice fails to emit JSON."""
        urls = re.findall(r"https?://[^\s<>'\"]+", task or "")
        if urls and "web_read" in available:
            return AgentDecision("tool", "Read the URL supplied by the user.", "web_read", {"url": urls[0].rstrip(".,);]}")})
        low = str(task or "").lower()
        if "calendar" in available and any(x in low for x in ("ساعت چنده", "الان ساعت", "امروز چندمه", "فردا", "پس فردا", "وقت خالی", "تقویم", "جلسه", "قرار", "یادآوری", "meeting", "calendar", "current time", "free time", "tomorrow")):
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
    ) -> Iterator[dict[str, Any]]:
        from .skill_system import SkillRegistry
        policy = self._context_policy(context_limit)
        conversation, latest_user = self._conversation_text(messages, max_chars=policy["conversation_chars"])
        max_steps = max(1, min(16, int(max_steps or 8)))
        target_language = self._response_language(latest_user)

        registry = SkillRegistry(self.runtime, permissions)
        capability_hints = registry.hinted_families(latest_user)

        # Stage 0: the model decides Direct vs Skills, with domain-level guard rails.
        # Guard rails are intentionally broad (calendar/files/web/api/browser), not
        # one hard-coded skill per phrase. They prevent small models from claiming
        # "no access" for capabilities the runtime actually has.
        yield {"type": "agent", "event": "phase", "phase": "route", "label": "Deciding whether this request needs Skills"}
        route_started = time.monotonic()
        route_decision, route_raw = self._route_decision(call_model, conversation, latest_user)
        route_seconds = max(0.0, time.monotonic() - route_started)
        external_required = route_decision.route == "skills" or bool(capability_hints)
        if capability_hints and route_decision.route != "skills":
            route_decision = AgentRouteDecision(
                route="skills",
                summary="Runtime capability guard: " + ", ".join(capability_hints),
                confidence=max(90, route_decision.confidence),
            )
        yield {
            "type": "agent", "event": "route_decision", "route": route_decision.route,
            "summary": route_decision.summary, "confidence": route_decision.confidence,
            "model_seconds": round(route_seconds, 2),
            "label": "Local model selected Skill route" if external_required else "Local model selected direct-chat route",
        }

        if not external_required:
            yield {"type": "agent", "event": "route", "route": "direct", "label": "Direct model response — no external skill needed", "response_language": target_language}
            started = time.monotonic()
            if stream_final is not None:
                emitted = False
                for streamed in stream_final(messages):
                    if not isinstance(streamed, dict):
                        continue
                    if streamed.get("type") == "text" and streamed.get("delta"):
                        emitted = True
                    yield streamed
                elapsed = max(0.0, time.monotonic() - started)
                yield {
                    "type": "agent", "event": "direct_complete",
                    "model_seconds": round(elapsed, 2), "language_corrected": False,
                    "streamed": True, "label": "Answered directly with live token streaming",
                }
                if emitted:
                    return
            msg = call_model(messages, [])
            elapsed = max(0.0, time.monotonic() - started)
            answer = str(msg.get("content") or "").strip() if isinstance(msg, dict) else ""
            if not answer:
                answer = str(msg.get("reasoning_content") or "").strip() if isinstance(msg, dict) else ""
            if not answer:
                answer = "The local model returned an empty response."
            draft_answer = answer
            answer = self._finalize_language(
                call_model, latest_user=latest_user, observations=[], draft=answer,
                target_language=target_language, observation_keep=1,
            )
            yield {
                "type": "agent", "event": "direct_complete", "model_seconds": round(elapsed, 2),
                "language_corrected": answer != draft_answer, "streamed": False,
                "label": "Answered directly without tools",
            }
            yield from self._stream_text(answer)
            return

        tool_defs = self.runtime.tool_definitions(permissions)
        observations: list[dict[str, str]] = []
        yield {"type": "agent", "event": "route", "route": "agent", "label": "Local model requested external Skills", "response_language": target_language}
        yield {
            "type": "agent", "event": "context_policy", "context_limit": int(context_limit or 8192),
            "conversation_chars": policy["conversation_chars"], "observation_chars": policy["observation_chars"],
            "observation_keep": policy["observation_keep"], "skill_limit": policy["skill_limit"],
            "label": "Adaptive context budget enabled",
        }
        explicit_browser = self._explicit_browser_interaction(latest_user)
        check_only = self._is_check_only(latest_user)
        attempts: dict[str, dict[str, Any]] = {}

        yield {"type": "agent", "event": "phase", "phase": "understand", "label": "Understanding the request"}
        cap_started = time.monotonic()
        goal, categories, needs_write, cap_raw = self._discover_capabilities(call_model, conversation, latest_user, registry)
        cap_seconds = max(0.0, time.monotonic() - cap_started)
        families = registry.families_for_categories(categories)
        shortlist = registry.shortlist(latest_user, categories, limit=policy["skill_limit"])
        manifest, available = registry.manifest(shortlist, tool_defs)
        yield {
            "type": "agent", "event": "capabilities", "goal": goal or latest_user[:220],
            "families": families, "categories": categories, "needs_write": bool(needs_write),
            "skills": [x.get("name") for x in shortlist if x.get("name") in available],
            "model_seconds": round(cap_seconds, 2),
            "label": "Selected relevant skill set",
        }

        for step in range(1, max_steps + 1):
            yield {"type": "agent", "event": "thinking", "step": step, "max_steps": max_steps, "label": "Planning next action"}
            prompt = self._planner_prompt(
                conversation, manifest, observations, step, max_steps,
                observation_keep=policy["observation_keep"], response_language=target_language,
            )
            started = time.monotonic()
            recent_vision: list[str] = []
            for o in observations[-max(1, policy["observation_keep"]):]:
                recent_vision.extend([str(x) for x in (o.get("vision") or []) if str(x).startswith("data:image/")])
            decision, raw = self._model_decision(call_model, prompt, manifest, vision=recent_vision[-2:])
            model_seconds = max(0.0, time.monotonic() - started)

            if decision is None:
                decision = self._heuristic_first_action(latest_user, available) if not observations else None
                if decision is None:
                    # We still call the model for a final answer rather than silently stopping.
                    final_prompt = (
                        "Answer the user's request using only the real observations below. "
                        "If the requested external action could not be completed, state the concrete failure.\n\n"
                        + conversation + "\n\nOBSERVATIONS:\n"
                        + "\n".join(o["result"] for o in observations[-4:])
                    )
                    final_msg = call_model([{"role": "user", "content": final_prompt}], [])
                    answer = str(final_msg.get("content") or "").strip() if isinstance(final_msg, dict) else ""
                    if not answer:
                        answer = "The agent could not produce a valid next action from the local model."
                    yield {"type": "agent", "event": "decision_error", "raw_preview": raw[:240]}
                    yield from self._stream_text(answer)
                    return

            if decision.action == "parallel":
                filled_actions: list[dict[str, Any]] = []
                for row in decision.actions or []:
                    d = AgentDecision(action="tool", skill=str(row.get("skill") or ""), arguments=dict(row.get("arguments") or {}))
                    d = self._fill_common_args(d, latest_user)
                    filled_actions.append({"skill": d.skill, "arguments": d.arguments or {}})
                decision.actions = filled_actions
            else:
                decision = self._fill_common_args(decision, latest_user)

            if decision.action == "parallel":
                catalog_rows = registry.catalog(tool_defs)
                batch = [x for x in (decision.actions or []) if x.get("skill") in available][:3]
                invalid = []
                for row in batch:
                    valid, err = registry.validate_call(str(row.get("skill") or ""), dict(row.get("arguments") or {}))
                    if not valid:
                        invalid.append(err)
                    elif not self._parallel_read_safe(str(row.get("skill") or ""), dict(row.get("arguments") or {}), catalog_rows):
                        invalid.append(f"{row.get('skill')} is not read-only and cannot run in parallel")
                if len(batch) < 2 or invalid:
                    observations.append({
                        "skill": "parallel",
                        "result": _safe_json({"ok": False, "error": "; ".join(invalid) or "Parallel batch needs at least two valid read-only skills", "instruction": "Choose one skill at a time for dependent or mutating work."}),
                    })
                    yield {"type": "agent", "event": "parallel_rejected", "errors": invalid, "label": "Unsafe/dependent parallel batch rejected"}
                    continue
                yield {"type": "agent", "event": "decision", "action": "parallel", "summary": decision.summary or "Run independent reads in parallel", "actions": batch, "model_seconds": round(model_seconds, 2)}
                yield {"type": "agent", "event": "parallel_start", "count": len(batch), "skills": [x["skill"] for x in batch]}
                started_parallel = time.monotonic()
                results: list[tuple[int, dict[str, Any], Any, float]] = []
                with ThreadPoolExecutor(max_workers=min(3, len(batch)), thread_name_prefix="lf-agent-read") as pool:
                    futures = {}
                    for idx, row in enumerate(batch):
                        t0 = time.monotonic()
                        fut = pool.submit(self.runtime.execute, str(row["skill"]), dict(row.get("arguments") or {}), permissions)
                        futures[fut] = (idx, row, t0)
                    for fut in as_completed(futures):
                        idx, row, t0 = futures[fut]
                        try:
                            result = fut.result()
                        except Exception as exc:
                            result = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                        results.append((idx, row, result, max(0.0, time.monotonic() - t0)))
                results.sort(key=lambda x: x[0])
                for _idx, row, result, tool_seconds in results:
                    skill = str(row["skill"])
                    ok, error_text = self._parse_tool_result(result, skill)
                    compact = _safe_json(result, max_chars=policy["observation_chars"]) if ok else _safe_json({"tool_result": result, "error": error_text}, max_chars=policy["observation_chars"])
                    observations.append({"skill": skill, "result": compact})
                    yield {"type": "agent", "event": "tool_result", "tool": skill, "ok": ok, "step": step, "parallel": True, "tool_seconds": round(tool_seconds, 2), "error_preview": error_text[:300] if error_text else ""}
                yield {"type": "agent", "event": "parallel_done", "count": len(results), "wall_seconds": round(max(0.0, time.monotonic() - started_parallel), 2), "label": "Independent reads completed in parallel"}
                continue

            # Deterministic execution policy: the model remains the planner, but
            # the runtime prevents obviously wasteful browser use for simple URL
            # reads. This is especially important for 4B-class local models.
            if decision.action == "tool" and not observations and not explicit_browser:
                preferred = "web_check" if check_only and "web_check" in available else "web_read" if "web_read" in available else ""
                if decision.skill == "browser_open" and preferred:
                    original = decision.skill
                    decision.skill = preferred
                    decision.summary = (decision.summary or "Inspect the supplied URL") + " [runtime policy: lowest-cost web skill first]"
                    yield {
                        "type": "agent", "event": "policy", "policy": "lightweight_web_before_browser",
                        "from_skill": original, "to_skill": preferred,
                        "label": f"Using {preferred} before browser automation",
                    }
                elif decision.skill == "web_read" and check_only and "web_check" in available:
                    decision.skill = "web_check"
                    decision.summary = (decision.summary or "Check the supplied URL") + " [runtime policy: reachability check]"
                    yield {
                        "type": "agent", "event": "policy", "policy": "web_check_for_reachability",
                        "from_skill": "web_read", "to_skill": "web_check",
                        "label": "Using quick URL health check",
                    }

            # A web/API task cannot be declared complete before any real external observation.
            if decision.action == "final" and external_required and not observations:
                forced = self._heuristic_first_action(latest_user, available)
                if forced is not None:
                    decision = forced

            if decision.action == "final":
                yield {
                    "type": "agent", "event": "decision", "action": "final",
                    "summary": decision.summary or "Task complete", "model_seconds": round(model_seconds, 2),
                    "response_language": target_language, "streaming_final": bool(stream_final),
                }
                if stream_final is not None:
                    final_prompt = self._streaming_final_prompt(
                        latest_user=latest_user, observations=observations, draft=decision.answer,
                        target_language=target_language, observation_keep=policy["observation_keep"],
                    )
                    emitted = False
                    for streamed in stream_final([{"role": "user", "content": final_prompt}]):
                        if not isinstance(streamed, dict):
                            continue
                        if streamed.get("type") == "text" and streamed.get("delta"):
                            emitted = True
                        yield streamed
                    if emitted:
                        return
                final_answer = self._finalize_language(
                    call_model, latest_user=latest_user, observations=observations, draft=decision.answer,
                    target_language=target_language, observation_keep=policy["observation_keep"],
                )
                yield from self._stream_text(final_answer)
                return

            if decision.skill not in available:
                observations.append({
                    "skill": decision.skill or "unknown",
                    "result": _safe_json({"ok": False, "error": f"Unknown skill {decision.skill!r}. Choose one of: {sorted(available)}"}),
                })
                yield {
                    "type": "agent", "event": "decision", "action": "tool", "skill": decision.skill,
                    "summary": decision.summary or "Selected an unavailable skill", "model_seconds": round(model_seconds, 2),
                }
                yield {"type": "agent", "event": "tool_result", "tool": decision.skill, "ok": False, "error": "unknown_skill"}
                continue

            valid, validation_error = registry.validate_call(decision.skill, decision.arguments or {})
            if not valid:
                meta = next((x for x in registry.catalog(tool_defs) if x.get("name") == decision.skill), {})
                compact = _safe_json({
                    "ok": False, "error": validation_error, "failure": {"kind": "preflight", "retryable": False},
                    "suggested_fallbacks": meta.get("fallbacks") or [],
                    "instruction": "Fix arguments, permissions, requirements, or choose a fallback skill.",
                })
                observations.append({"skill": decision.skill, "result": compact})
                yield {"type": "agent", "event": "preflight_failed", "tool": decision.skill, "error": validation_error, "fallbacks": meta.get("fallbacks") or []}
                continue

            signature = self._call_signature(decision.skill, decision.arguments)
            previous = attempts.get(signature)
            if previous and previous.get("ok") is False:
                error_text = str(previous.get("error") or "previous call failed")
                compact = _safe_json({
                    "ok": False,
                    "error": "Repeated failed call blocked by Agent runtime",
                    "previous_error": error_text,
                    "instruction": "Choose a different skill or different arguments. Do not repeat this exact call.",
                })
                observations.append({"skill": decision.skill, "result": compact})
                yield {
                    "type": "agent", "event": "policy", "policy": "block_repeated_failed_call",
                    "skill": decision.skill, "label": "Blocked repeated failed tool call",
                    "error_preview": error_text[:240],
                }
                continue

            yield {
                "type": "agent", "event": "decision", "action": "tool", "skill": decision.skill,
                "summary": decision.summary or f"Use {decision.skill}", "arguments": decision.arguments or {},
                "model_seconds": round(model_seconds, 2),
            }
            yield {"type": "agent", "event": "tool_start", "tool": decision.skill, "arguments": decision.arguments or {}, "step": step}
            tool_started = time.monotonic()
            result = self.runtime.execute(decision.skill, decision.arguments or {}, permissions)
            tool_seconds = max(0.0, time.monotonic() - tool_started)
            ok, error_text = self._parse_tool_result(result, decision.skill)
            attempts[signature] = {"ok": ok, "error": error_text, "step": step}
            failure = None
            meta = next((x for x in registry.catalog(tool_defs) if x.get("name") == decision.skill), {})
            vision_items: list[str] = []
            if ok:
                raw_ok: Any = result
                try:
                    raw_ok = json.loads(result) if isinstance(result, str) else result
                except Exception:
                    raw_ok = result
                if isinstance(raw_ok, dict) and isinstance(raw_ok.get("result"), dict):
                    nested = dict(raw_ok.get("result") or {})
                    vision_obj = nested.pop("vision_attachment", None)
                    if isinstance(vision_obj, dict):
                        data_url = str(vision_obj.get("data_url") or "")
                        if data_url.startswith("data:image/"):
                            vision_items.append(data_url)
                            nested["vision_attachment"] = {"name": str(vision_obj.get("name") or "image"), "available_to_model": True}
                    raw_ok = dict(raw_ok)
                    raw_ok["result"] = nested
                compact = _safe_json(raw_ok, max_chars=policy["observation_chars"])
            else:
                failure = registry.classify_failure(error_text, result)
                try:
                    raw_result = json.loads(result) if isinstance(result, str) else result
                except Exception:
                    raw_result = {"raw": str(result)}
                compact = _safe_json({
                    "tool_result": raw_result,
                    "failure": failure,
                    "suggested_fallbacks": meta.get("fallbacks") or [],
                    "instruction": "Do not repeat the same failed call. Choose a fallback, change arguments, or explain the concrete blocker.",
                }, max_chars=policy["observation_chars"])
            observation_row: dict[str, Any] = {"skill": decision.skill, "result": compact}
            if vision_items:
                observation_row["vision"] = vision_items
            observations.append(observation_row)
            yield {
                "type": "agent", "event": "tool_result", "tool": decision.skill, "ok": ok,
                "step": step, "tool_seconds": round(tool_seconds, 2), "observation_chars": len(compact),
                "error_preview": error_text[:300] if error_text else "",
                "failure_kind": failure.get("kind") if failure else "",
                "retryable": bool(failure.get("retryable")) if failure else False,
                "fallbacks": meta.get("fallbacks") or [],
            }

        # Step budget exhausted: ask the local model to synthesize from what was actually observed.
        synthesis = (
            f"The agent has reached its tool-step budget. Give the best final answer in {target_language} using ONLY the observations below. "
            "Internal planning may be English, but the user-facing response must match the user's language. "
            "Do not claim an action succeeded unless its observation says ok=true.\n\n"
            + conversation + "\n\nOBSERVATIONS:\n"
            + "\n\n".join(f"{o['skill']}: {o['result']}" for o in observations[-policy["observation_keep"]:])
        )
        yield {"type": "agent", "event": "phase", "phase": "finalize", "label": "Synthesizing final answer"}
        msg = call_model([{"role": "user", "content": synthesis}], [])
        answer = str(msg.get("content") or "").strip() if isinstance(msg, dict) else ""
        if not answer:
            answer = "The agent reached its step limit before the local model produced a final answer."
        yield from self._stream_text(answer)
