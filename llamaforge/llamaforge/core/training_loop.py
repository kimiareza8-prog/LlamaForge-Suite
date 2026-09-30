"""Bounded candidate training with measurable learning/retention sanity checks.

No torch import occurs in the web process. Validation is teacher-forced loss on
the small curriculum, not a claim of held-out recall or general intelligence.
"""
from __future__ import annotations

import math

from .learning_data import training_schedule


def assess_quality(before: dict, after: dict) -> dict:
    reasons = []
    if "new" not in before or "new" not in after:
        reasons.append("missing new-supervision metric")
    for split, old in before.items():
        new = after.get(split)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or not math.isfinite(old) or not math.isfinite(new):
            reasons.append(f"{split}: non-finite or missing loss")
            continue
        tolerance = max(.02, .02 * old) if split == "new" else max(.05, .10 * old)
        if new > old + tolerance:
            reasons.append(f"{split}: loss regression")
    return {"accepted": not reasons, "reasons": reasons, "before": before, "after": after,
            "method": "bounded-curriculum-loss", "held_out": False}


def train_candidate(model, encoded: list[dict], rows: list[dict], *, steps: int,
                    learning_rate: float, seed: int = 0, emit=None) -> dict:
    import torch

    if not encoded or len(encoded) != len(rows):
        raise ValueError("Encoded examples must match the curriculum")
    params = [p for p in model.parameters() if p.requires_grad]
    if not params:
        raise RuntimeError("No trainable adapter parameters")
    schedule = training_schedule(rows, steps, seed)
    # At most four new and four replay examples, including the ones selected for
    # actual updates. No extra model, no all-dataset device allocation.
    validation_indices = []
    for split in ("new", "replay"):
        candidates = list(dict.fromkeys(schedule + list(range(len(rows)))))
        validation_indices.extend([i for i in candidates if rows[i].get("split", "new") == split][:4])
    target = next((p.device for p in model.parameters() if p.device.type != "meta"), torch.device("cpu"))

    def batch(index):
        return {k: v.to(target) for k, v in encoded[index].items()}

    def evaluate():
        model.eval()
        values = {}
        with torch.no_grad():
            for index in validation_indices:
                loss = float(model(**batch(index)).loss.detach().cpu())
                if not math.isfinite(loss):
                    raise RuntimeError("non-finite validation loss")
                values.setdefault(rows[index].get("split", "new"), []).append(loss)
        return {split: sum(v)/len(v) for split, v in values.items()}

    before = evaluate()
    if emit:
        emit(phase="validate-before", message="Recorded candidate baseline loss", progress=.29, validation_before=before)
    optimizer = torch.optim.AdamW(params, lr=learning_rate, weight_decay=0.0)
    model.train()
    losses = []
    for step, index in enumerate(schedule):
        optimizer.zero_grad(set_to_none=True)
        loss = model(**batch(index)).loss
        if not torch.isfinite(loss):
            raise RuntimeError(f"non-finite loss at step {step+1}")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0, error_if_nonfinite=True)
        optimizer.step()
        losses.append(float(loss.detach().cpu()))
        if emit:
            emit(phase="train", message=f"Learning step {step+1}/{len(schedule)}", step=step+1,
                 steps=len(schedule), split=rows[index].get("split", "new"), loss=losses[-1],
                 progress=.30 + .42 * (step+1)/len(schedule))
    after = evaluate()
    validation = assess_quality(before, after)
    validation["examples"] = len(validation_indices)
    validation["trained_new"] = sum(rows[i].get("split") != "replay" for i in schedule)
    validation["trained_replay"] = len(schedule) - validation["trained_new"]
    if emit:
        emit(phase="validate-after", message="Candidate learning and retention checked", progress=.78, validation=validation)
    if not validation["accepted"]:
        raise RuntimeError("Candidate quality check failed: " + "; ".join(validation["reasons"]))
    return {"loss": sum(losses)/len(losses), "validation": validation}
