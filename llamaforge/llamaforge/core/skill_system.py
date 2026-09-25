from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import re
from typing import Any, Iterable
from .skill_contracts import contract, operation_policy, validate_schema, validate_operation, OPERATION_INPUTS


@dataclass(frozen=True)
class SkillMeta:
    name: str
    category: str
    title: str
    description: str
    risk: str = "read"          # read | write | session | mixed
    cost: str = "low"           # low | medium | high
    requires_browser: bool = False
    requires_write: bool = False
    when_to_use: tuple[str, ...] = ()
    when_not_to_use: tuple[str, ...] = ()
    fallbacks: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()

    def public(self, *, available: bool = True, reason: str = "") -> dict[str, Any]:
        d = asdict(self)
        d["available"] = bool(available)
        d["unavailable_reason"] = str(reason or "")
        return d


BUILTIN_SKILLS: dict[str, SkillMeta] = {
    "web_check": SkillMeta(
        "web_check", "web.read", "Check URL", "Quickly check whether a URL is reachable, its HTTP status, redirect target, content type and response time without reading the full page.",
        when_to_use=("User asks whether a site/URL opens or is reachable.", "Need a cheap HTTP health check before a full read."),
        when_not_to_use=("Need page contents; use web_read.", "Need clicks/login/forms; use browser skills."),
        fallbacks=("web_read", "browser_open"),
        keywords=("open", "reachable", "status", "works", "up", "down", "باز", "باز میشه", "وضعیت", "در دسترس"),
    ),
    "web_read": SkillMeta(
        "web_read", "web.read", "Read web page", "Fetch a URL and extract readable text, links and forms. Preferred first tool for ordinary pages.",
        when_to_use=("User supplies a URL and wants its content.", "Need links/forms/text from a normal HTTP page."),
        when_not_to_use=("Only checking reachability; web_check is cheaper.", "Page requires browser-only JavaScript interaction."),
        fallbacks=("browser_open",),
        keywords=("read", "page", "site", "url", "content", "لینک", "سایت", "بخون", "محتوا", "داخل"),
    ),
    "web_find": SkillMeta(
        "web_find", "web.read", "Find in web page", "Find a phrase inside a known web page and return compact matching snippets and matching links.",
        when_to_use=("Need one fact/section from a long known page.", "User asks to find a term inside a supplied URL."),
        when_not_to_use=("Need the whole page; use web_read."),
        fallbacks=("web_read",),
        keywords=("find in", "contains", "where", "عبارت", "داخل صفحه", "پیدا کن", "کجاست"),
    ),
    "web_search": SkillMeta(
        "web_search", "web.search", "Search web", "Search the public web to discover current pages and URLs, then open promising results with web_read.",
        when_to_use=("No exact URL is known.", "User asks to search/find/research online."),
        when_not_to_use=("Exact URL already supplied and only needs reading."),
        fallbacks=("web_read",),
        keywords=("search", "find", "research", "google", "جستجو", "بگرد", "پیدا", "تحقیق"),
    ),
    "http_request": SkillMeta(
        "http_request", "api", "HTTP / API request", "Call explicit HTTP/API endpoints with GET/HEAD and, when permission is enabled, POST/PUT/PATCH/DELETE.",
        risk="write", requires_write=False,
        when_to_use=("Task names an API/endpoint/method.", "Observation provides a reply/action endpoint."),
        when_not_to_use=("Ordinary human-facing webpage reading; use web_read."),
        keywords=("api", "endpoint", "get", "post", "put", "patch", "delete", "json", "وبهوک", "ای پی آی", "پست", "گت"),
    ),
    "download_file": SkillMeta(
        "download_file", "web.download", "Download file", "Download a file from a public HTTP(S) URL into the Agent downloads folder and report its local path, size and content type.",
        cost="medium",
        when_to_use=("User explicitly asks to download/save a file.", "A page/API provides a file URL needed for the task."),
        fallbacks=("web_read",),
        keywords=("download", "save file", "pdf", "zip", "دانلود", "فایل", "ذخیره"),
    ),
    "browser_open": SkillMeta(
        "browser_open", "browser.read", "Open in browser", "Open a JavaScript-heavy page in the persistent Agent Chrome session and return a structured snapshot.",
        cost="high", requires_browser=True,
        when_to_use=("web_read is insufficient because the page requires JavaScript.", "User explicitly requests browser interaction."),
        when_not_to_use=("Simple URL reading or health checks."),
        fallbacks=("web_read",),
        keywords=("browser", "javascript", "dynamic", "مرورگر", "جاوااسکریپت"),
    ),
    "browser_snapshot": SkillMeta(
        "browser_snapshot", "browser.read", "Inspect browser page", "Inspect the currently open browser page and return current text and interactable element references.",
        cost="medium", requires_browser=True,
        when_to_use=("A browser page is already open and needs re-inspection."),
        fallbacks=("browser_open",),
        keywords=("snapshot", "inspect", "elements", "صفحه", "المان"),
    ),
    "browser_wait": SkillMeta(
        "browser_wait", "browser.read", "Wait for page state", "Wait for text or a CSS selector to appear in the current browser page, useful after navigation or asynchronous updates.",
        cost="medium", requires_browser=True,
        when_to_use=("Page is still loading or content appears asynchronously."),
        fallbacks=("browser_snapshot",),
        keywords=("wait", "loading", "appear", "صبر", "لود", "ظاهر"),
    ),
    "browser_scroll": SkillMeta(
        "browser_scroll", "browser.read", "Scroll browser", "Scroll the current browser page up/down or to an element, then return a fresh snapshot.",
        cost="medium", requires_browser=True,
        when_to_use=("Needed content is below the current viewport or lazy-loaded."),
        fallbacks=("browser_snapshot",),
        keywords=("scroll", "down", "up", "اسکرول", "پایین", "بالا"),
    ),
    "browser_hover": SkillMeta(
        "browser_hover", "browser.read", "Hover browser element", "Hover an element to reveal menus, tooltips or hover-only content, then inspect the page.",
        cost="medium", requires_browser=True,
        when_to_use=("A menu/tooltip appears only on hover.",),
        fallbacks=("browser_snapshot",),
        keywords=("hover", "menu", "tooltip", "هاور", "منو"),
    ),
    "browser_refresh": SkillMeta(
        "browser_refresh", "browser.session", "Refresh browser page", "Refresh the current browser page and return a new snapshot.",
        risk="session", cost="medium", requires_browser=True,
        when_to_use=("Page state is stale or user asks to refresh."),
        fallbacks=("browser_snapshot",),
        keywords=("refresh", "reload", "رفرش", "دوباره لود"),
    ),
    "browser_tabs": SkillMeta(
        "browser_tabs", "browser.session", "List browser tabs", "List current browser tabs and their index/title/URL.",
        risk="session", cost="low", requires_browser=True,
        when_to_use=("Need to inspect or switch among multiple open tabs."),
        keywords=("tabs", "tab", "تب"),
    ),
    "browser_new_tab": SkillMeta(
        "browser_new_tab", "browser.session", "Open new browser tab", "Open and switch to a new browser tab, optionally at a URL.",
        risk="session", cost="medium", requires_browser=True,
        when_to_use=("Task requires keeping the current page while opening another page."),
        keywords=("new tab", "another tab", "تب جدید"),
    ),
    "browser_switch_tab": SkillMeta(
        "browser_switch_tab", "browser.session", "Switch browser tab", "Switch to an existing browser tab by index.",
        risk="session", cost="low", requires_browser=True,
        when_to_use=("Need to return to another already-open browser tab."),
        fallbacks=("browser_tabs",),
        keywords=("switch tab", "tab index", "تعویض تب", "تب قبلی"),
    ),
    "browser_close_tab": SkillMeta(
        "browser_close_tab", "browser.session", "Close current tab", "Close the current browser tab and switch to a remaining tab.",
        risk="session", cost="low", requires_browser=True,
        when_to_use=("A temporary tab is no longer needed."),
        keywords=("close tab", "بستن تب"),
    ),
    "browser_click": SkillMeta(
        "browser_click", "browser.interact", "Click browser element", "Click a current browser element reference or CSS selector, then inspect the resulting page.",
        risk="write", cost="high", requires_browser=True, requires_write=True,
        when_to_use=("User explicitly asks to interact/click/navigate through UI."),
        fallbacks=("browser_snapshot",),
        keywords=("click", "button", "کلیک", "دکمه"),
    ),
    "browser_type": SkillMeta(
        "browser_type", "browser.interact", "Type in browser", "Type text into a browser field and optionally submit it.",
        risk="write", cost="high", requires_browser=True, requires_write=True,
        when_to_use=("User asks to fill/search/login/submit in a website."),
        fallbacks=("browser_snapshot",),
        keywords=("type", "fill", "submit", "login", "تایپ", "پر کن", "ورود", "ثبت"),
    ),
    "browser_select": SkillMeta(
        "browser_select", "browser.interact", "Select dropdown option", "Choose an option from a <select> dropdown using value, visible text or index.",
        risk="write", cost="high", requires_browser=True, requires_write=True,
        when_to_use=("A form requires choosing a dropdown value."),
        fallbacks=("browser_snapshot",),
        keywords=("select", "dropdown", "option", "انتخاب", "لیست"),
    ),
    "browser_back": SkillMeta(
        "browser_back", "browser.session", "Browser back", "Navigate back one page in the existing browser session.",
        risk="session", cost="low", requires_browser=True,
        when_to_use=("Need to return to the previous page in an active browser workflow."),
        keywords=("back", "previous", "برگرد", "قبلی"),
    ),
    "browser_close": SkillMeta(
        "browser_close", "browser.session", "Close browser", "Close the Agent browser session.",
        risk="session", cost="low", requires_browser=True,
        when_to_use=("Task is finished and closing the browser is useful."),
        keywords=("close", "ببند"),
    ),
    "calendar": SkillMeta(
        "calendar", "calendar", "Calendar agent", "One general calendar capability with composable primitives: real local time/Jalali date, date conversion, month/list reads, and event create/update/cancel/delete. The model should derive novel calendar answers from these primitives instead of expecting one skill per question.",
        risk="mixed", cost="low", requires_write=False,
        when_to_use=("Need the real current local time/date.", "Need to inspect, reason about, or change the user's calendar/schedule/reminders."),
        when_not_to_use=("Pure general knowledge about calendars that does not require the user's live calendar state.",),
        keywords=("calendar", "schedule", "meeting", "appointment", "reminder", "free time", "time now", "date today", "تقویم", "جلسه", "قرار", "وقت", "یادآوری", "ساعت چنده", "امروز چندمه", "زمان خالی"),
    ),
    "workspace_files": SkillMeta(
        "workspace_files", "files", "File Manager agent", "One universal workspace file capability for any attachment type. Attachments are metadata-first: probe unknown files or list archive members without reading content, and use read_content only when the user's actual instruction requires semantic inspection.",
        risk="mixed", cost="low", requires_write=False,
        when_to_use=("Any chat attachment exists, regardless of extension or MIME type.", "Need to store, find, move, rename, delete, download/locate, inspect, create, or edit a workspace file.", "Probe first when file type/content requirements are unclear; do not open content merely because a file was attached."),
        when_not_to_use=("The user only wants to discuss a hypothetical file system without operating the workspace.",),
        keywords=("file", "folder", "document", "attachment", "move", "rename", "delete", "read this file", "فایل", "پوشه", "مدرک", "سند", "پیوست", "ببر", "پاک کن", "بخون"),
    ),
    "connector_call": SkillMeta(
        "connector_call", "connector", "OpenAPI connector", "Call an operation from a configured OpenAPI connector using its connector and operation IDs.",
        risk="write", cost="medium",
        when_to_use=("A configured connector directly represents the target app/service."),
        keywords=("connector", "openapi", "bridge", "اتصال", "کانکتور", "بریج"),
    ),
}

CATEGORY_LABELS = {
    "web.read": "read/check a known URL",
    "web.search": "discover information or URLs on the public web",
    "web.download": "download a file",
    "api": "call an HTTP/API endpoint",
    "browser.read": "inspect a JavaScript-heavy page with a real browser",
    "browser.interact": "click/type/select in a browser",
    "browser.session": "manage browser navigation/session",
    "calendar": "use the personal calendar/time workspace",
    "files": "use the personal File Manager workspace",
    "connector": "use a configured OpenAPI connector",
    "custom": "use a custom installed skill",
}

SKILL_FAMILIES = {
    "web": ("web.read", "web.search", "web.download"),
    "api": ("api",),
    "browser": ("browser.read", "browser.interact", "browser.session"),
    "calendar": ("calendar",),
    "files": ("files",),
    "connector": ("connector",),
    "custom": ("custom",),
}

FAMILY_LABELS = {
    "web": "read/check/search/download public web content",
    "api": "call explicit HTTP/API endpoints",
    "browser": "use a real browser for JavaScript or UI interaction",
    "calendar": "reason over and operate the user's calendar using general primitives",
    "files": "reason over and operate the user's workspace files using general primitives",
    "connector": "operate configured OpenAPI connectors",
    "custom": "use installed custom skills",
}


class SkillRegistry:
    def __init__(self, runtime: Any, permissions: Any):
        self.runtime = runtime
        self.permissions = permissions
        # One immutable discovery snapshot per request, not a global stale cache.
        self._catalog_cache = None
        self._definitions = None

    def _availability(self, meta: SkillMeta) -> tuple[bool, str]:
        if meta.requires_browser and not self.runtime.browser_available():
            return False, "Browser skill (Selenium) is not installed"
        if meta.requires_write and not bool(getattr(self.permissions, "allow_write", False)):
            return False, "Agent write/site-action permission is disabled"
        return True, ""

    def catalog(self, tool_defs: Iterable[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if self._catalog_cache is not None:
            return self._catalog_cache
        defs = list(tool_defs if tool_defs is not None else self.runtime.tool_definitions(self.permissions))
        self._definitions = {x["function"]["name"]:x["function"] for x in defs if isinstance(x.get("function"), dict)}
        defined = set()
        descriptions: dict[str, str] = {}
        for item in defs:
            fn = item.get("function") if isinstance(item, dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                name = str(fn["name"])
                defined.add(name)
                descriptions[name] = str(fn.get("description") or "")
        rows: list[dict[str, Any]] = []
        for name, meta in BUILTIN_SKILLS.items():
            available, reason = self._availability(meta)
            # Some tools are deliberately not advertised when unavailable.
            available = available and (name in defined or name in {"browser_click", "browser_type", "browser_select"})
            if not available and not reason and name not in defined:
                reason = "Not currently exposed by runtime"
            rows.append(meta.public(available=available, reason=reason))
        loaded_custom = {str(s.get("name")): s for s in self.runtime._load_skills()}
        for item in defs:
            fn = item.get("function") if isinstance(item, dict) else None
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "")
            if not name or name in BUILTIN_SKILLS:
                continue
            is_connector = name == "connector_call" or name.startswith("conn_")
            category = "connector" if is_connector else "custom"
            risk = "read"
            title = name.replace("_", " ").title()
            when_to_use: list[str] = []
            when_not_to_use: list[str] = []
            fallbacks: list[str] = []
            keywords: list[str] = []
            description = descriptions.get(name, "")
            requires_write = False
            if name.startswith("skill_"):
                skill_name = name[6:]
                skill = loaded_custom.get(skill_name)
                if skill:
                    request = skill.get("request") if isinstance(skill.get("request"), dict) else skill
                    method = str(request.get("method") or skill.get("method") or "GET").upper()
                    risk = "write" if method not in {"GET", "HEAD"} else "read"
                    requires_write = risk == "write"
                    title = str(skill.get("title") or skill_name.replace("_", " ").title())
                    description = str(skill.get("description") or description)
                    when_to_use = [str(x) for x in (skill.get("when_to_use") or []) if str(x).strip()]
                    when_not_to_use = [str(x) for x in (skill.get("when_not_to_use") or []) if str(x).strip()]
                    fallbacks = [str(x) for x in (skill.get("fallbacks") or []) if str(x).strip()]
                    keywords = [str(x) for x in (skill.get("keywords") or []) if str(x).strip()]
            elif name.startswith("conn_"):
                policy = operation_policy(name, metadata=fn.get("x-llamaforge", {}))
                risk = "read" if policy.effect == "read" else "write"
                requires_write = risk == "write"
                when_to_use = ["Use when this exact configured OpenAPI operation matches the user's target service/action."]
            rows.append({
                "name": name, "category": category, "title": title,
                "description": description, "risk": risk, "cost": "medium",
                "requires_browser": False, "requires_write": requires_write,
                "when_to_use": when_to_use, "when_not_to_use": when_not_to_use,
                "fallbacks": fallbacks, "keywords": keywords,
                "available": True, "unavailable_reason": "",
            })
        for row in rows:
            fn = self._definitions.get(row["name"], {})
            metadata = fn.get("x-llamaforge", {})
            row["contract"] = contract(row["name"], row["category"], fn.get("parameters", {}), metadata)
            row["http_method"] = metadata.get("http_method", "")
        self._catalog_cache = rows
        return rows

    @staticmethod
    def capability_prompt() -> str:
        families = "\n".join(f"- {k}: {v}" for k, v in FAMILY_LABELS.items())
        return (
            "You are the capability selector for a local AI agent. Skills are broad reusable capabilities, not one skill per user question. "
            "Infer what REAL capability is needed from the user's goal, even when they did not name a tool. "
            "Choose the minimum family set. RETURN EXACTLY ONE JSON OBJECT: {\"goal\":\"short goal\",\"families\":[\"web\"],\"needs_write\":false}.\n"
            "Rules: current/live public facts -> web unless a dedicated local capability is more authoritative; current clock/date or personal schedule -> calendar; "
            "ANY attached file (ZIP, code, PDF, Office, audio/video, unknown binary, image, etc.) -> files; explicit API endpoint -> api; browser only for JS UI/click/type/login. "
            "For attachments, route to files because the runtime can stage every file type. Do not assume content must be opened: preserve metadata-first behavior, probe when useful, and read content only if the user's goal needs it. "
            "A request may need multiple families (for example web+files or calendar+web). Do not claim lack of access when a matching family exists.\nFamilies:\n" + families
        )

    @staticmethod
    def parse_capabilities(obj: Any) -> tuple[str, list[str], bool]:
        if not isinstance(obj, dict):
            return "", [], False
        goal = str(obj.get("goal") or obj.get("summary") or "")[:500]
        cats: list[str] = []
        raw_families = obj.get("families") if isinstance(obj.get("families"), list) else []
        for family in raw_families:
            for category in SKILL_FAMILIES.get(str(family).strip(), ()):
                if category not in cats:
                    cats.append(category)
        # Backwards-compatible parser for older/smaller models that still return
        # category names. They are normalized into the same tree branch.
        raw_categories = obj.get("categories") if isinstance(obj.get("categories"), list) else []
        for category in raw_categories:
            category = str(category).strip()
            if category in CATEGORY_LABELS and category not in cats:
                cats.append(category)
        return goal, cats[:8], bool(obj.get("needs_write", False))

    @staticmethod
    def families_for_categories(categories: list[str] | None) -> list[str]:
        out: list[str] = []
        for category in categories or []:
            for family, members in SKILL_FAMILIES.items():
                if category in members and family not in out:
                    out.append(family)
        return out

    @staticmethod
    def categories_for_families(families: list[str] | None) -> list[str]:
        out: list[str] = []
        for family in families or []:
            for category in SKILL_FAMILIES.get(str(family), ()):
                if category not in out:
                    out.append(category)
        return out

    @staticmethod
    def hinted_families(task: str) -> list[str]:
        """High-confidence capability hints that protect small models from dead ends.

        This is intentionally domain-level (web/files/calendar/api/browser), not a
        brittle one-skill-per-phrase router. The model can still add other families.
        """
        text = " ".join(str(task or "").lower().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").split())
        out: list[str] = []
        def add(name: str) -> None:
            if name not in out:
                out.append(name)

        # Explanations/translations are NOT operational requests. The model can
        # still select tools for these; the guard must not override a valid direct route.
        if "attachment_id=" not in text and "[workspace attachment:" not in text:
            discussion = re.search(r"^(?:explain\b|what is (?:a |an )?(?:calendar|zip|http|browser|api)|translate\b|write (?:a |an )?(?:poem|story))", text)
            discussion = discussion or any(x in text for x in ("چیست", "یعنی چه", "عبارت", "معنی کلمه"))
            if discussion or re.search(r"^(?:don't|do not|never)\s+(?:create|open|browse|search|save)", text): return []

        if re.search(r"https?://", text) or any(x in text for x in (
            "search the web", "search online", "browse the web", "look online", "latest", "today's news", "current price",
            "جستجو کن", "سرچ کن", "تو اینترنت", "در اینترنت", "روی وب", "تحقیق کن", "آخرین خبر", "جدیدترین", "قیمت روز",
        )):
            add("web")

        time_phrases = (
            "what time is it", "what's the time", "current time", "what date is it", "today's date", "my schedule",
            "remind me", "free time", "ساعت چنده", "الان ساعت", "امروز چندمه", "برنامه من", "وقت خالی",
        )
        calendar_subject = any(x in text for x in ("calendar", "meeting", "appointment", "tomorrow", "تقویم", "جلسه", "قرار", "فردا", "یادآوری"))
        operation = bool(re.search(r"\b(create|schedule|set|add|show|list|cancel|delete|reschedule|move|update)\b", text)) or any(x in text for x in ("بساز", "بذار", "بگذار", "نشون", "نشان بده", "حذف کن", "لغو کن", "ثبت کن", "تنظیم کن", "تغییر بده"))
        if any(x in text for x in time_phrases) or (calendar_subject and operation):
            add("calendar")

        file_marker = "[workspace attachment:" in text or "attachment_id=" in text
        file_words = any(x in text for x in (
            "my file", "my files", "file manager", "this file", "this document", "attachment", "folder", "archive", "zip", "package", "spreadsheet", "binary",
            "فایل من", "فایل هام", "فایل‌های من", "فایل منیجر", "این فایل", "این مدرک", "این سند", "پیوست", "پوشه", "زیپ", "آرشیو", "فشرده",
        ))
        extension_hint = bool(re.search(r"\.(zip|7z|rar|tar|gz|pdf|docx?|xlsx?|pptx?|csv|json|ya?ml|txt|md|py|js|ts|html|css|mp3|wav|mp4|mov|mkv|bin|gguf)\b", text))
        format_task = bool(re.search(r"\b(pdf|docx|xlsx|pptx|zip|tar|json|yaml)\b", text)) and any(x in text for x in ("summari", "read", "inspect", "analy", "خلاصه", "بخوان", "بررسی", "ترجمه"))
        file_action = any(x in text for x in ("this ", "my files", "save ", "store ", "read ", "inspect ", "list ", "rename ", "move ", "delete ", "این ", "فایل من", "فایل هام", "ذخیره", "بساز", "بررسی", "حذف کن", "بخوان", "بخون"))
        if file_marker or ((file_words or extension_hint) and file_action) or format_task:
            add("files")

        api_action = bool(re.search(r"\b(get|post|put|patch|delete)\b", text)) and any(x in text for x in ("api", "endpoint", "http", "request", "url"))
        if api_action or any(x in text for x in ("call the api", "send a request", "درخواست http", "api رو صدا", "api را صدا")):
            add("api")

        interaction = any(x in text for x in (
            "click", "type into", "fill the form", "submit", "log in", "sign in",
            "کلیک", "تایپ", "فرم", "لاگین", "وارد سایت", "دکمه",
        ))
        if interaction and (re.search(r"https?://", text) or any(x in text for x in ("site", "website", "page", "browser", "سایت", "صفحه", "مرورگر"))):
            add("browser")
        return out

    @staticmethod
    def _keyword_score(text: str, row: dict[str, Any]) -> int:
        low = text.lower()
        score = 0
        for kw in row.get("keywords") or []:
            if str(kw).lower() in low:
                score += 5
        name = str(row.get("name") or "").lower()
        for part in name.split("_"):
            if len(part) > 3 and part in low:
                score += 2
        hay = (str(row.get("title") or "") + " " + str(row.get("description") or "")).lower()
        task_terms = [x for x in re.findall(r"[a-z0-9_\-]{4,}|[؀-ۿ]{3,}", low) if len(x) >= 3]
        for term in task_terms[:24]:
            if term in hay:
                score += 1
        return score

    def shortlist(self, task: str, categories: list[str] | None = None, *, limit: int = 8) -> list[dict[str, Any]]:
        categories = [c for c in (categories or []) if c in CATEGORY_LABELS]
        catalog = [x for x in self.catalog() if x.get("available")]
        # Family selection is semantic/model-driven; lexical scores only rank
        # siblings WITHIN the chosen branches. Never fill a local branch with web tools.
        if categories:
            catalog = [x for x in catalog if x.get("category") in categories]
        urls = bool(re.search(r"https?://", task or "", re.I))
        low = str(task or "").lower()
        explicit_interaction = any(x in low for x in ("click", "type", "fill", "submit", "login", "کلیک", "تایپ", "پر کن", "ورود", "فرم", "دکمه"))
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in catalog:
            cat = str(row.get("category") or "custom")
            score = self._keyword_score(task, row)
            if categories and (cat in categories or (cat.startswith("browser") and any(c.startswith("browser") for c in categories))):
                score += 12
            if urls and row.get("name") in {"web_check", "web_read", "http_request"}:
                score += 5
            if urls and row.get("name") == "web_read":
                score += 4
            if explicit_interaction and cat == "browser.interact":
                score += 10
            if not explicit_interaction and cat == "browser.interact":
                score -= 8
            if row.get("requires_write") and not bool(getattr(self.permissions, "allow_write", False)):
                score -= 20
            scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], x[1].get("cost") == "high", str(x[1].get("name"))))
        cap = max(1, min(12, limit))
        chosen = [row for score, row in scored if score > -10][:cap]
        # Core safe escape hatches prevent a bad category prediction from dead-ending.
        # Reserve room instead of appending them beyond the visible skill budget.
        by_name = {x.get("name"): x for x in catalog}
        # Family-critical primitives are mandatory. A weak/small model must not
        # route an attachment or date request correctly and then lose the one
        # tool that can actually execute it due to scoring noise.
        mandatory = []
        if "files" in categories: mandatory.append("workspace_files")
        if "calendar" in categories: mandatory.append("calendar")
        for core in mandatory:
            if core in by_name and all(x.get("name") != core for x in chosen):
                if len(chosen) >= cap:
                    chosen.pop()
                chosen.insert(0, by_name[core])
        core_hatches = [] if categories else ["web_read", "web_search", "http_request"]
        for core in core_hatches:
            if core in by_name and all(x.get("name") != core for x in chosen):
                if len(chosen) >= cap:
                    chosen.pop()
                chosen.append(by_name[core])
        return chosen[:cap]

    @staticmethod
    def manifest(rows: list[dict[str, Any]], tool_defs: list[dict[str, Any]]) -> tuple[str, set[str]]:
        defs = {}
        for item in tool_defs:
            fn = item.get("function") if isinstance(item, dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                defs[str(fn["name"])] = fn
        lines: list[str] = []
        names: set[str] = set()
        for row in rows:
            name = str(row.get("name") or "")
            fn = defs.get(name)
            if not name or not fn:
                continue
            names.add(name)
            c = row.get("contract") or contract(name, str(row.get("category") or "custom"), fn.get("parameters", {}))
            # Preserve enums, constraints and descriptions. Operation details are
            # compacted by grouping policies rather than dropping input fields.
            policies = {}
            for op, policy in c.get("operations", {}).items():
                key = policy["effect"] + ":" + policy["permission"]
                policies.setdefault(key, []).append(op)
            visible = {
                "name":name, "family":c["capability_family"],
                "description":str(row.get("description") or fn.get("description") or "")[:280],
                "input_schema":c["input_schema"],
                "operation_policy":policies or c["policy"],
                "operation_inputs":OPERATION_INPUTS.get(name, {}),
                "verification": "Read back persisted state after writes; never retry writes blindly.",
                "fallbacks":row.get("fallbacks") or [],
            }
            lines.append(json.dumps(visible, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines), names

    def validate_call(self, skill: str, args: dict[str, Any]) -> tuple[bool, str]:
        row = next((x for x in self.catalog() if x.get("name") == skill), None)
        if not row:
            return False, f"Unknown skill: {skill}"
        if not row.get("available"):
            return False, str(row.get("unavailable_reason") or "Skill is unavailable")
        error = validate_schema(args, (self._definitions or {}).get(skill, {}).get("parameters", {}))
        if error: return False, error
        error = validate_operation(skill, args)
        if error: return False, error
        policy = operation_policy(skill, args, row)
        if policy.permission == "local_workspace" and not getattr(self.permissions, "allow_workspace_write", True):
            return False, "Local workspace changes are disabled in Agent settings"
        if row.get("requires_write") and not bool(getattr(self.permissions, "allow_write", False)):
            return False, "Agent write/site-action permission is disabled"
        if skill in {"web_check", "web_read", "web_find", "browser_open", "download_file"} and not str((args or {}).get("url") or "").strip():
            return False, "url is required"
        if skill in {"web_search", "web_find"} and not str((args or {}).get("query") or "").strip():
            return False, "query is required"
        if skill in {"browser_click", "browser_type", "browser_select", "browser_hover"} and not str((args or {}).get("target") or "").strip():
            return False, "target is required"
        if skill == "browser_switch_tab" and (args or {}).get("index") is None:
            return False, "index is required"
        if skill == "http_request" and not str((args or {}).get("url") or "").strip():
            return False, "url is required"
        if skill == "calendar":
            op = str((args or {}).get("operation") or "").strip().lower()
            if not op:
                return False, "operation is required"
            if op in {"create", "update", "cancel", "delete"} and not bool(getattr(self.permissions, "allow_workspace_write", True)):
                return False, "Local calendar changes are disabled in Agent settings"
        if skill == "workspace_files":
            op = str((args or {}).get("operation") or "").strip().lower()
            if not op:
                return False, "operation is required"
            if op in {"store_attachment", "mkdir", "move", "rename", "trash", "restore", "delete", "write_text", "replace_text"} and not bool(getattr(self.permissions, "allow_workspace_write", True)):
                return False, "Local File Manager changes are disabled in Agent settings"
        return True, ""

    @staticmethod
    def classify_failure(error: str, result: Any = None) -> dict[str, Any]:
        text = str(error or "").lower()
        kind = "tool_error"
        retryable = False
        if "timeout" in text or "timed out" in text:
            kind, retryable = "timeout", True
        elif "429" in text or "too many requests" in text:
            kind, retryable = "rate_limit", True
        elif any(x in text for x in ("502", "503", "504", "temporarily unavailable")):
            kind, retryable = "server_transient", True
        elif "403" in text or "forbidden" in text:
            kind = "forbidden"
        elif "401" in text or "unauthorized" in text:
            kind = "authentication"
        elif "404" in text or "not found" in text:
            kind = "not_found"
        elif "selenium" in text or "chrome" in text or "driver" in text:
            kind = "browser_unavailable"
        elif "private/local network" in text:
            kind = "private_network_blocked"
        elif "write/site-action permission" in text:
            kind = "write_permission_blocked"
        return {"kind": kind, "retryable": retryable, "error": str(error or "")[:800]}
