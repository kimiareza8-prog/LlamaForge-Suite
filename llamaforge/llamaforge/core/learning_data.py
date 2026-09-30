"""Small, validated curricula from user supervision; never an inference memory store."""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

MAX_EXAMPLES = 12


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).translate(str.maketrans("يك", "یک")).replace("\u200c", " ").casefold().split())


def question_key(text: str) -> str:
    return re.sub(r"[^\w\s]", "", normalized(text)).strip()


@dataclass(frozen=True)
class LearningExample:
    user: str
    assistant: str
    kind: str = "fact"
    evidence: str = ""
    source: str = "user"

    def to_dict(self) -> dict:
        return {"user": self.user, "assistant": self.assistant, "kind": self.kind,
                "evidence": self.evidence, "source": self.source}


def validate_examples(rows, *, source_text: str | None = None, require_evidence: bool = False,
                      limit: int = MAX_EXAMPLES) -> list[dict]:
    """Teacher targets must be extractive and traceable to this user message.

    Explicit Q/A supplied by the user needs no teacher evidence. Refuse type
    coercion and duplicates so malformed model JSON cannot become supervision.
    """
    if not isinstance(rows, list):
        return []
    out, seen = [], set()
    source_norm = normalized(source_text or "")
    for row in rows[:256]:
        if not isinstance(row, dict):
            continue
        user, answer = row.get("user"), row.get("assistant")
        if not isinstance(user, str) or not isinstance(answer, str):
            continue
        user, answer = user.strip(), answer.strip()
        if not user or not answer or len(user) > 6000 or len(answer) > 12000:
            continue
        key = question_key(user)
        if not key or key in seen:
            continue
        evidence = row.get("evidence", "")
        evidence = evidence.strip() if isinstance(evidence, str) else ""
        if require_evidence and (not evidence or normalized(evidence) not in source_norm
                                 or normalized(answer).strip(" .،") not in normalized(evidence)):
            continue
        kind = row.get("kind", "fact")
        if not isinstance(kind, str):
            kind = "fact"
        # Identity ownership is assigned only by the deterministic compiler.
        if require_evidence and kind in ("identity", "self-identity"):
            kind = "fact"
        out.append(LearningExample(user, answer, kind[:40], evidence[:12000],
                                   "teacher-grounded" if require_evidence else "user").to_dict())
        seen.add(key)
        if len(out) >= max(1, limit):
            break
    return out


def deterministic_examples(user_text: str) -> list[dict]:
    """Recognize assertions, not questions, quoted examples or hypothetical names."""
    text = " ".join(str(user_text).strip().split())
    text = re.sub(r"^(?:نه|خیر|no)[،,:]\s*", "", text, flags=re.I)
    if "?" in text or "؟" in text:
        return []
    clause = re.split(r"[.!،,;؛]", text, maxsplit=1)[0].strip()
    patterns = (
        (r"^(?:اسم من|نام من|اسمم|نامم)\s+(.+)$", "identity", True),
        (r"^(?:اسم تو|نام تو|اسمت|نامت|اسم خودت|نام خودت)\s+(.+)$", "self-identity", True),
        (r"^my name is\s+(.+)$", "identity", False),
        (r"^(?:your name is|call yourself)\s+(.+)$", "self-identity", False),
    )
    for pattern, kind, persian in patterns:
        match = re.match(pattern, clause, re.I)
        if not match:
            continue
        name = match[1].strip()
        if persian:
            stripped = re.sub(r"\s+(?:می باشد|میباشد|هست|است)$", "", name)
            if stripped == name and len(name) > 3 and name.endswith("ست") and name[-3] in "اوی":
                stripped = name[:-2]
            name = stripped.strip()
        if not 1 <= len(name) <= 60 or len(name.split()) > 4:
            return []
        if not re.fullmatch(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*", name, re.UNICODE):
            return []
        if set(normalized(name).split()) & {"چی", "چیه", "چیست", "چه", "کی", "کیه", "نیست", "نیستم", "بود", "باشد", "what", "who", "not", "is", "and"}:
            return []
        if kind == "identity":
            questions = ("اسم من چیه؟", "نام من چیست؟", "من چه اسمی دارم؟", "اسم صاحب این مغز شخصی چیست؟") if persian else ("What is my name?", "What's my name?", "Who owns this personal model?")
        else:
            questions = ("اسمت چیه؟", "نام تو چیست؟", "خودت را چه صدا کنم؟", "What is your name?") if persian else ("What is your name?", "Who are you?", "What should I call you?")
        return [LearningExample(q, name, kind, clause).to_dict() for q in questions]
    return []


def obvious_non_teaching(text: str) -> bool:
    """A cheap no-op for clear greetings/questions; all ambiguous cases go to the teacher."""
    if text.rstrip().endswith(("?", "؟")):
        return True  # Mixed teaching + questions can use the explicit correction form.
    value = normalized(text).strip(" .!?؟")
    if value in {"سلام", "درود", "ممنون", "مرسی", "hi", "hello", "thanks", "thank you"}:
        return True
    return bool(re.match(r"^(?:(?:اسم من|نام من|اسمم|اسمت|نامت)\s+(?:چی|چه)|(?:what|who|when|where|why|how|آیا|چرا|چطور|چگونه)\b)", value))


def fact_key(row: dict) -> str:
    kind = row.get("kind")
    return str(kind) if kind in ("identity", "self-identity") else "q:" + question_key(str(row.get("user", "")))


def curriculum(new_rows: list[dict], packets: Iterable[dict], replay_limit: int) -> list[dict]:
    """Latest correction wins, including all paraphrases of an identity fact."""
    current = validate_examples(new_rows)
    out = [{**r, "split": "new"} for r in current]
    seen_questions = {question_key(r["user"]) for r in current}
    blocked_facts = {fact_key(r) for r in current}
    if replay_limit <= 0 or not current:
        return out
    for packet in packets:  # newest confirmed packet first
        rows = validate_examples(packet.get("examples", []), limit=MAX_EXAMPLES)
        packet_facts = set()
        for row in rows:
            key, qkey = fact_key(row), question_key(row["user"])
            if key in blocked_facts or qkey in seen_questions:
                continue
            out.append({**row, "split": "replay"})
            seen_questions.add(qkey)
            packet_facts.add(key)
            if len(out) - len(current) >= replay_limit:
                return out
        blocked_facts.update(packet_facts)
    return out


def training_schedule(rows: list[dict], steps: int, seed: int = 0) -> list[int]:
    """Keep the user's step budget; interleave replay before short runs exhaust it."""
    fresh = [i for i, row in enumerate(rows) if row.get("split") != "replay"]
    replay = [i for i, row in enumerate(rows) if row.get("split") == "replay"]
    if not fresh:
        raise ValueError("A training run requires new supervision")
    rng = random.Random(seed)
    rng.shuffle(fresh)
    rng.shuffle(replay)
    schedule, ni, ri = [], 0, 0
    for step in range(max(1, int(steps))):
        if replay and step % 3 == 1:
            schedule.append(replay[ri % len(replay)]); ri += 1
        else:
            schedule.append(fresh[ni % len(fresh)]); ni += 1
    return schedule


def lesson_fingerprint(rows: list[dict]) -> str:
    values = sorted((question_key(r["user"]), normalized(r["assistant"])) for r in rows)
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()
