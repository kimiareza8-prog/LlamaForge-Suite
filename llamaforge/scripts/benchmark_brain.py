"""Reproducible control-plane probes, plus an optional tiny real torch update.

Run this script with PYTHONPATH pointing at each checkout. It never downloads
weights and does not claim GGUF recall quality or hardware training throughput.
"""
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import llamaforge.core.personal_brain as pb


def main():
    negatives = ["اسم من چیه؟", "اسمم چیست؟", "اسم من چی بود؟", "My name is what?",
                 "آیا اسم من رضا است؟", "ترجمه کن: my name is Reza", "اگر اسم من رضا باشد چه؟"]
    timings = []
    inputs = negatives + ["اسم من رضاست.", "My name is Sara.", "اسمم ژاله"]
    for _ in range(7):
        start = time.perf_counter()
        for _ in range(100):
            for text in inputs:
                pb.PersonalBrain.deterministic_examples(text)
        timings.append((time.perf_counter()-start)*1000/1000)
    result = {"repeats": 7, "calls_per_repeat": 1000, "deterministic_median_ms": statistics.median(timings),
              "questions_mislearned": sum(bool(pb.PersonalBrain.deterministic_examples(s)) for s in negatives),
              "question_cases": len(negatives), "gguf_quality_measured": False}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        constants = {"BRAIN_ROOT": root, "BRAIN_CONFIG": root/"config.json", "BRAIN_ARCHIVE": root/"archive.jsonl",
                     "BRAIN_ENV": root/"env", "BRAIN_TOOLCHAIN": root/"toolchain", "BRAIN_BATCH": root/"batch.json",
                     "BRAIN_READY_MARKER": root/"ready", "BRAIN_LOG_DIR": root/"logs", "BRAIN_LAST_TRAINER": root/"last.json"}
        with patch.multiple(pb, **constants):
            brain = pb.PersonalBrain(root)
            model = SimpleNamespace(name="Test", architecture="llama", size_label="1B")
            old = brain.append_packet(model, "اسم من رضاست.", "", brain.deterministic_examples("اسم من رضاست."))
            brain._set_learned_ids(brain.model_key(model), {old["id"]})
            new = {"id": "new", "examples": brain.deterministic_examples("My name is Sara.")}
            batch = brain.build_training_batch(model, new)
            result["obsolete_identity_answers_in_batch"] = sum(row["assistant"] == "رضا" for row in batch)
            brain.cfg.replay_samples = 0
            fresh = {"id": "new", "examples": [{"user": "q", "assistant": "a"}]}
            result["replay_rows_when_disabled"] = len(brain.build_training_batch(model, fresh))-1
    try:
        from llamaforge.core.learning_data import training_schedule
        rows = [{"split": "new"}]*4 + [{"split": "replay"}]*8
        result["replay_steps_in_three_updates"] = sum(rows[i]["split"] == "replay" for i in training_schedule(rows, 3))
    except ImportError:
        result["replay_steps_in_three_updates"] = 0  # baseline: encoded[step % len(encoded)]
    try:
        import torch
        from llamaforge.core.training_loop import train_candidate
        torch.set_num_threads(1)
        class TinyAdapter(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.base = torch.nn.Parameter(torch.zeros(2), requires_grad=False)
                self.adapter = torch.nn.Parameter(torch.zeros(2))
            def forward(self, input_ids, labels):
                return SimpleNamespace(loss=torch.nn.functional.cross_entropy((self.base+self.adapter).expand(labels.numel(), 2), labels.reshape(-1)))
        model = TinyAdapter()
        trained = train_candidate(model, [{"input_ids": torch.tensor([[0]]), "labels": torch.tensor([[1]])}],
                                  [{"split": "new"}], steps=3, learning_rate=.2)
        result["tiny_torch_sanity"] = {"parameters": 4, "optimizer_steps": 3,
                                      "validation": trained["validation"], "frozen_weights_unchanged": model.base.tolist() == [0, 0]}
    except ImportError:
        result["tiny_torch_sanity"] = None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
