from __future__ import annotations

from dataclasses import dataclass
from .planner import make_plan, LaunchPlan, PROFILES
from .hardware import HardwareInfo
from .models import LocalModel

@dataclass
class SmartAssessment:
    fit: str
    score: int
    recommended_profile: str
    recommended_ctx: int
    memory_status: str
    summary: str
    reasons: list[str]
    plan: LaunchPlan


def assess_model(model: LocalModel, hw: HardwareInfo, max_ram_percent: int = 88, cpu_only: bool = True, memory_mode: str = "hybrid") -> SmartAssessment:
    avail = max(0.5, hw.ram_available_gb or hw.ram_total_gb * 0.65)
    ratio = model.size_gb / avail

    if ratio <= 0.45:
        profile, ctx, fit, score = "Max Speed", 8192, "Excellent", 96
    elif ratio <= 0.72:
        profile, ctx, fit, score = "Balanced", 8192, "Good", 88
    elif ratio <= 0.95:
        profile, ctx, fit, score = "Low RAM", 4096, "Tight", 74
    elif ratio <= 1.8:
        profile, ctx, fit, score = "Low RAM", 4096, "Paging", 55
    else:
        profile, ctx, fit, score = "Giant Model (Experimental)", 4096, "Extreme", 35

    if model.context_length:
        ctx = min(ctx, int(model.context_length))
    if hw.physical_cores <= 2:
        score -= 8
    elif hw.physical_cores >= 8:
        score += 3
    score = max(1, min(100, score))

    plan = make_plan(model, hw, profile, cpu_only, ctx, max_ram_percent, memory_mode=memory_mode)
    reasons = [
        f"{model.size_gb:.2f} GB model vs {avail:.2f} GB currently available RAM",
        f"{hw.physical_cores} physical / {hw.logical_cores} logical CPU cores",
        f"{model.quantization or 'Unknown quantization'} • {model.architecture or 'unknown architecture'}",
    ]
    if memory_mode == "ram_only" and plan.oversized:
        reasons.append("RAM Only is selected, but this model exceeds the safe RAM budget and will be blocked at launch")
    elif memory_mode == "ssd_test":
        reasons.append("SSD Test is selected; mmap/on-demand disk reads are intentionally favored for testing")
    elif plan.oversized:
        reasons.append("Hybrid mode will use mmap/SSD paging for pages that do not stay hot in RAM")
    else:
        reasons.append("Model fits the current conservative memory budget")
    if model.chat_template:
        reasons.append("Embedded chat template detected in GGUF metadata")

    memory_status = (
        "RAM Only: too large" if memory_mode == "ram_only" and plan.oversized else
        "RAM Only" if memory_mode == "ram_only" else
        "SSD Test" if memory_mode == "ssd_test" else
        "Hybrid paging" if plan.oversized else "Hybrid / fits in memory"
    )
    summary = f"{fit} fit • {profile} • {plan.ctx_size:,} context • {plan.threads} CPU threads"
    return SmartAssessment(fit, score, profile, plan.ctx_size, memory_status, summary, reasons, plan)


def candidate_plans(model: LocalModel, hw: HardwareInfo, max_ram_percent: int = 88, cpu_only: bool = True, memory_mode: str = "hybrid") -> list[tuple[int, LaunchPlan, str]]:
    rows: list[tuple[int, LaunchPlan, str]] = []
    seen = set()
    for profile in PROFILES:
        for ctx in (2048, 4096, 8192):
            if model.context_length and ctx > model.context_length:
                continue
            plan = make_plan(model, hw, profile, cpu_only, ctx, max_ram_percent, memory_mode=memory_mode)
            key = (profile, plan.ctx_size, plan.batch_size, plan.ubatch_size, plan.threads)
            if key in seen:
                continue
            seen.add(key)
            score = 100
            if plan.oversized:
                score -= 35
            score -= max(0, int((plan.estimated_total_gb - plan.available_ram_gb * 0.78) * 6))
            score += 8 if profile == "Balanced" else 0
            score += 6 if profile == "Max Speed" and not plan.oversized else 0
            score += 4 if profile == "Low RAM" and plan.oversized else 0
            score += 3 if plan.ctx_size >= 8192 and not plan.oversized else 0
            score = max(1, min(100, score))
            note = "Best balance" if score >= 90 else ("Memory-safe" if not plan.oversized else "SSD paging")
            rows.append((score, plan, note))
    rows.sort(key=lambda x: x[0], reverse=True)
    return rows[:8]
