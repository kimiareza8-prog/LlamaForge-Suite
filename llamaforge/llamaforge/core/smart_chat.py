from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from .models import LocalModel


@dataclass
class SmartChatProfile:
    mode: str
    task: str
    language: str
    family: str
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    repeat_penalty: float
    max_tokens: int
    reasoning: str = "auto"  # requested: auto | on | off
    effective_reasoning: str = "off"  # actual decision for this turn
    reasoning_budget: int = -1
    source: str = "smart"
    confidence: int = 70
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["notes"] = list(self.notes)
        return row


_CODE_RE = re.compile(
    r"(?:\b(?:code|coding|program|function|class|bug|error|exception|traceback|api|sql|php|python|javascript|typescript|react|css|html|git|docker|regex|json|refactor|repository|repo)\b|"
    r"کد|برنامه\s*نویس|تابع|کلاس|باگ|خطا|ارور|پی\s*اچ\s*پی|پایتون|جاوا\s*اسکریپت|ری\s*اکت|دیتابیس|کوئری|ریپازیتوری|رفکتور)", re.I,
)
_CREATIVE_RE = re.compile(r"(?:story|poem|creative|brainstorm|name ideas|داستان|شعر|خلاق|ایده|سناریو|اسم پیشنهاد)", re.I)
_REASON_RE = re.compile(r"(?:reason|analy[sz]e|prove|derive|step by step|why|compare|trade.?off|diagnose|root cause|تحلیل|استدلال|اثبات|چرا|مقایسه|مرحله به مرحله|ریشه|علت)", re.I)
_TRANSLATE_RE = re.compile(r"(?:translate|translation|ترجمه|ترجمه کن)", re.I)
_PRECISE_RE = re.compile(r"(?:only|just the answer|one line|brief|concise|فقط|کوتاه|مختصر|یک خط)", re.I)
_RTL_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
_TEMPLATE_LEAK_RE = re.compile(r"(?:<\/?start_of_turn>|<\/?end_of_turn>|<\|im_(?:start|end)\|>|\[/?INST\]|<\|assistant\|>|<\|user\|>)", re.I)


def _family(model: LocalModel | None) -> str:
    if not model:
        return "generic"
    hay = f"{model.architecture} {model.name}".lower()
    if "gemma4" in hay or "gemma 4" in hay:
        return "gemma4"
    if "gemma3" in hay or "gemma 3" in hay:
        return "gemma3"
    if "qwen3" in hay or "qwen 3" in hay:
        return "qwen3"
    if "qwen2" in hay or "qwen 2" in hay:
        return "qwen2"
    if "gpt-oss" in hay or "gpt_oss" in hay:
        return "gpt-oss"
    if "deepseek" in hay:
        return "deepseek"
    if "mistral" in hay or "mixtral" in hay:
        return "mistral"
    if "llama" in hay:
        return "llama"
    return (model.architecture or "generic").lower()


def classify_prompt(text: str) -> tuple[str, str]:
    text = text or ""
    language = "fa" if _RTL_RE.search(text) else "other"
    # Translation is checked first because translation prompts often contain code-ish
    # words or the source language name.
    if _TRANSLATE_RE.search(text):
        task = "translation"
    elif _CODE_RE.search(text):
        task = "coding"
    elif _CREATIVE_RE.search(text):
        task = "creative"
    elif _REASON_RE.search(text):
        task = "reasoning"
    elif _PRECISE_RE.search(text):
        task = "precise"
    else:
        task = "general"
    return task, language


def _meta(model: LocalModel | None, key: str, default):
    if model is None:
        return default
    value = getattr(model, key, None)
    return default if value is None else value


def _auto_reasoning(family: str, task: str, requested: str) -> str:
    if requested in {"on", "off"}:
        return requested
    # Thinking is useful when the task benefits from planning; for greetings,
    # translation and short factual replies it often only adds latency/noise.
    capable = family in {"qwen3", "gpt-oss", "deepseek", "gemma4"}
    if capable and task in {"reasoning", "coding"}:
        return "on"
    return "off"


def choose_profile(
    model: LocalModel | None,
    messages: list[dict],
    *,
    mode: str = "auto",
    max_tokens: int = 2048,
    reasoning: str = "auto",
    reasoning_budget: int = -1,
) -> SmartChatProfile:
    last_user = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
    task, language = classify_prompt(last_user)
    family = _family(model)

    # Start from metadata authored for the model where available, then constrain it
    # by task instead of applying one global preset to every architecture.
    temp = float(_meta(model, "sampling_temperature", 0.8))
    top_p = float(_meta(model, "sampling_top_p", 0.95))
    top_k = int(_meta(model, "sampling_top_k", 40))
    min_p = float(_meta(model, "sampling_min_p", 0.05))
    repeat = 1.03
    notes: list[str] = []
    confidence = 68
    has_meta = bool(model and any(getattr(model, k, None) is not None for k in (
        "sampling_temperature", "sampling_top_p", "sampling_top_k", "sampling_min_p"
    )))
    if has_meta:
        notes.append("Model-authored sampling defaults detected in GGUF")
        confidence += 8

    if family == "gemma4":
        temp = float(_meta(model, "sampling_temperature", 1.0))
        top_p = float(_meta(model, "sampling_top_p", .95))
        top_k = int(_meta(model, "sampling_top_k", 64))
        min_p = float(_meta(model, "sampling_min_p", 0.0))
    elif family == "gemma3":
        temp = float(_meta(model, "sampling_temperature", .8)); top_p = float(_meta(model, "sampling_top_p", .95)); top_k = int(_meta(model, "sampling_top_k", 64))
    elif family == "qwen3":
        temp, top_p, top_k, min_p = .6, .95, 20, 0.0
    elif family == "gpt-oss":
        temp, top_p, top_k, min_p = 1.0, 1.0, 0, 0.0
    elif family == "deepseek":
        temp, top_p, top_k, min_p = .6, .95, 40, 0.0

    requested = (mode or "auto").lower()
    effective = task if requested == "auto" else requested
    if requested == "auto":
        confidence += 6

    if effective == "coding":
        if family in {"qwen3", "gpt-oss", "deepseek"}:
            temp = max(temp, .55)
        elif family == "gemma4":
            temp = min(max(temp, .30), .35)
        else:
            temp = min(temp, .35)
        top_p = min(top_p, .95)
        repeat = 1.02
        notes.append("Coding: consistency favored without forcing greedy decoding")
    elif effective == "creative":
        temp = min(1.2, max(.85, temp + .10)); top_p = max(.95, top_p); repeat = 1.02
        notes.append("Creative: diversity increased")
    elif effective == "precise":
        temp = max(.45 if family in {"qwen3", "gpt-oss", "deepseek", "gemma4"} else .2, min(temp, .68))
        top_p = min(top_p, .90); repeat = 1.04
        notes.append("Precise: variance reduced")
    elif effective == "reasoning":
        if family not in {"qwen3", "gpt-oss", "deepseek", "gemma4"}:
            temp = min(temp, .65)
        elif family == "gemma4":
            temp = min(max(temp, .7), .95)
        repeat = 1.02
        notes.append("Reasoning: enough entropy retained for multi-step work")
    elif effective == "translation":
        temp = min(temp, .42); top_p = min(top_p, .90); repeat = 1.02
        notes.append("Translation: fidelity prioritized")
    else:
        # General conversation: prevent high-temperature metadata from making small
        # local models unnecessarily erratic while keeping them conversational.
        if family == "gemma4":
            temp = min(max(temp, .68), .88)
        elif family == "gemma3":
            temp = min(max(temp, .55), .80)
        repeat = 1.03

    if language == "fa":
        notes.append("Persian input detected; response language is quality-checked")
        confidence += 4

    effective_reasoning = _auto_reasoning(family, effective, reasoning)
    if reasoning == "auto":
        notes.append(f"Thinking auto-selected: {effective_reasoning}")

    # Smart token budget: keep the user's explicit cap, but avoid absurdly large
    # generations for simple turns. Long coding/reasoning tasks retain the cap.
    explicit_cap = max(16, min(32768, int(max_tokens)))
    if effective in {"general", "translation", "precise"}:
        smart_cap = min(explicit_cap, 2048)
    elif effective == "creative":
        smart_cap = min(explicit_cap, 3072)
    else:
        smart_cap = explicit_cap

    temp = round(max(0.0, min(2.0, temp)), 3)
    top_p = round(max(0.0, min(1.0, top_p)), 3)
    min_p = round(max(0.0, min(1.0, min_p)), 3)
    top_k = max(0, min(500, top_k))
    repeat = round(max(0.8, min(1.3, repeat)), 3)

    return SmartChatProfile(
        mode=requested,
        task=effective,
        language=language,
        family=family,
        temperature=temp,
        top_p=top_p,
        top_k=top_k,
        min_p=min_p,
        repeat_penalty=repeat,
        max_tokens=smart_cap,
        reasoning=reasoning if reasoning in {"auto", "on", "off"} else "auto",
        effective_reasoning=effective_reasoning,
        reasoning_budget=max(-1, min(32768, int(reasoning_budget))),
        confidence=max(0, min(100, confidence)),
        notes=tuple(notes),
    )


def response_quality(user_text: str, answer: str, *, task: str = "general", language: str = "other") -> dict[str, Any]:
    """Cheap, local-only degeneration guard.

    This is intentionally conservative: it only retries obvious failures rather
    than judging factual correctness, which would require another model.
    """
    raw_u = (user_text or "").strip()
    raw_a = (answer or "").strip()
    u = re.sub(r"\s+", " ", raw_u.lower())
    a = re.sub(r"\s+", " ", raw_a.lower())
    issues: list[str] = []
    score = 100

    if not a:
        issues.append("empty"); score -= 100
    else:
        if u and (a == u or (len(a) < max(48, len(u) * 2) and u in a)):
            issues.append("echo"); score -= 60
        if _TEMPLATE_LEAK_RE.search(raw_a):
            issues.append("template_leak"); score -= 65

        # Repeated n-grams and duplicate lines catch the common local-model loop.
        words = a.split()
        if len(words) >= 12:
            grams = [" ".join(words[i:i+4]) for i in range(len(words)-3)]
            if grams and (len(grams) - len(set(grams))) / len(grams) > .24:
                issues.append("repetition"); score -= 45
        lines = [re.sub(r"\s+", " ", x.strip().lower()) for x in raw_a.splitlines() if len(x.strip()) > 4]
        if len(lines) >= 4 and len(set(lines)) <= max(1, int(len(lines) * .55)):
            if "repetition" not in issues:
                issues.append("repetition")
            score -= 25

        # General Persian chat should normally answer in Persian. Coding and
        # translation are exempt because code/API names and target languages are valid.
        if language == "fa" and task in {"general", "reasoning", "precise", "creative"} and len(raw_a) >= 60:
            fa_chars = len(_RTL_RE.findall(raw_a))
            letters = len(re.findall(r"[^\W\d_]", raw_a, re.UNICODE))
            if letters > 20 and fa_chars / max(1, letters) < .16:
                issues.append("wrong_language"); score -= 35

        # Tiny answers to substantive prompts are often a failed generation, but do
        # not penalize greetings/brief requests.
        if len(raw_u) > 80 and task in {"coding", "reasoning"} and len(raw_a) < 55:
            issues.append("too_short"); score -= 30

    # preserve issue order while deduplicating
    issues = list(dict.fromkeys(issues))
    return {"ok": not issues, "issues": issues, "score": max(0, score)}


def recovery_hint(issues: list[str], language: str) -> str:
    """A tiny temporary hint appended only to the retry request, never history."""
    issues = list(issues or [])
    if language == "fa":
        bits = []
        if "wrong_language" in issues:
            bits.append("پاسخ نهایی را به فارسی روان بنویس")
        if any(x in issues for x in ("echo", "repetition", "too_short")):
            bits.append("متن سؤال را تکرار نکن و مستقیم پاسخ کامل بده")
        if "template_leak" in issues:
            bits.append("هیچ توکن یا نشانهٔ قالب چت را در پاسخ نمایش نده")
        return "؛ ".join(bits)
    bits = []
    if "wrong_language" in issues:
        bits.append("answer in the user's language")
    if any(x in issues for x in ("echo", "repetition", "too_short")):
        bits.append("do not echo the prompt; answer directly and completely")
    if "template_leak" in issues:
        bits.append("do not expose chat-template control tokens")
    return "; ".join(bits)
