"""Learning must use user supervision and preserve a confirmed adapter transaction."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from llamaforge.core.personal_brain import PersonalBrain
from llamaforge.web.server import LlamaForgeState
from test_personal_brain import _isolated_brain


MODEL = SimpleNamespace(name="Test", architecture="llama", size_label="1B", path="model.gguf")


@pytest.mark.parametrize("text", ["اسم من چیه؟", "اسمم چیست؟", "اسم من چی بود؟", "My name is what?",
    "آیا اسم من رضا است؟", "ترجمه کن: my name is Reza", "اگر اسم من رضا باشد چه؟"])
def test_questions_and_quoted_instructions_are_not_identity_targets(text):
    assert PersonalBrain.deterministic_examples(text) == []


@pytest.mark.parametrize("text,name", [("اسم من ژاله است.", "ژاله"), ("اسمم ژاله", "ژاله"),
    ("اسم من رضاست.", "رضا"), ("My name is Reza.", "Reza"), ("اسم من Sara است.", "Sara")])
def test_identity_spelling_is_preserved(text, name):
    rows = PersonalBrain.deterministic_examples(text)
    assert rows and {x["assistant"] for x in rows} == {name}


def _history(brain, rows):
    key = brain.model_key(MODEL)
    path = brain.replay_path_for_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({**r, "model_key": key}) for r in rows)+"\n")
    brain._set_learned_ids(key, {r["id"] for r in rows})


def test_zero_replay_means_zero(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    brain.cfg.replay_samples = 0
    _history(brain, [{"id": "p", "examples": [{"user": "old", "assistant": "old answer"}]}])
    batch = brain.build_training_batch(MODEL, {"id": "new", "examples": [{"user": "new", "assistant": "answer"}]})
    assert [r["user"] for r in batch] == ["new"]


def test_correction_supersedes_all_old_identity_paraphrases(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _history(brain, [{"id": "p", "examples": PersonalBrain.deterministic_examples("اسم من رضاست.")}])
    batch = brain.build_training_batch(MODEL, {"id": "new", "examples": PersonalBrain.deterministic_examples("My name is Sara.")})
    assert all(r["assistant"] == "Sara" for r in batch)


def test_replay_resolves_newest_answer_and_deduplicates_question(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _history(brain, [{"id": "old", "examples": [{"user": "رنگ مورد علاقه؟", "assistant": "قرمز"}]},
        {"id": "recent", "examples": [{"user": "رنگ مورد علاقه؟", "assistant": "آبی"}]}])
    batch = brain.build_training_batch(MODEL, {"id": "new", "examples": [{"user": "city", "assistant": "Tehran"}]})
    assert [r["assistant"] for r in batch] == ["Tehran", "آبی"]


def test_malformed_archive_line_does_not_hide_valid_history(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _history(brain, [{"id": "p", "examples": [{"user": "q", "assistant": "a"}]}])
    path = brain.replay_path_for_key(brain.model_key(MODEL))
    path.write_text("[]\nnull\n" + path.read_text())
    assert len(brain._archive_rows(brain.model_key(MODEL))) == 1


def test_empty_new_lesson_never_trains_replay_only(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _history(brain, [{"id": "p", "examples": [{"user": "q", "assistant": "a"}]}])
    monkeypatch.setattr(brain, "_run_trainer", lambda *a, **k: pytest.fail("empty lesson started trainer"))
    result = brain.learn(MODEL, "سلام", "hello", [])
    assert result["skipped"] and brain._profile_meta(MODEL)["generation"] == 0


def _candidate(brain):
    current = brain.adapter_dir(MODEL)
    current.mkdir(parents=True)
    (current/"adapter_config.json").write_text("{}")
    (current/"adapter_model.safetensors").write_bytes(b"old")
    brain.adapter_gguf(MODEL).write_bytes(b"old-gguf")
    candidate = brain.candidate_adapter_dir(MODEL)
    candidate.mkdir()
    (candidate/"adapter_config.json").write_text("{}")
    (candidate/"adapter_model.safetensors").write_bytes(b"new")
    brain.candidate_adapter_gguf(MODEL).write_bytes(b"new-gguf")


def test_live_reload_does_not_rollback_the_candidate_it_is_verifying(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _candidate(brain)
    brain._commit_learning_candidate(MODEL, "new", {"generation": 2})
    brain.activate_model(MODEL)  # start_server calls this before spawning llama.cpp
    assert brain.adapter_gguf(MODEL).read_bytes() == b"new-gguf"
    assert brain.transaction_path(MODEL).exists()
    brain.confirm_learning(MODEL)
    assert "new" in brain._learned_ids(brain.model_key(MODEL))


def test_fresh_process_rolls_back_pending_reload(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _candidate(brain)
    brain._commit_learning_candidate(MODEL, "new", {"generation": 2})
    fresh = PersonalBrain(tmp_path)
    fresh.activate_model(MODEL)
    assert fresh.adapter_gguf(MODEL).read_bytes() == b"old-gguf"
    assert fresh._profile_meta(MODEL)["generation"] == 2


def test_teacher_requires_evidence_in_the_user_message(monkeypatch):
    state = object.__new__(LlamaForgeState)
    state.server_ready = True
    state.brain = SimpleNamespace(cfg=SimpleNamespace(auto_synthesize=True))
    state.brain_cancel = threading.Event()
    state.cfg = SimpleNamespace(host="local", port=123)
    state.log = lambda *_: None
    rows = [{"user": "Where do I live?", "assistant": "Paris", "evidence": "I live in Paris"},
        {"user": "What do I like?", "assistant": "tea", "evidence": "I like tea"}]
    monkeypatch.setattr("llamaforge.web.server.stream_chat_events", lambda *a, **k: iter([{"type": "text", "delta": json.dumps(rows)}]))
    result = state._brain_synthesize_examples("I like tea")
    assert [r["assistant"] for r in result] == ["tea"]


def test_silent_subprocess_obeys_timeout(monkeypatch, tmp_path):
    import sys
    import time
    brain = _isolated_brain(monkeypatch, tmp_path)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        brain._run_logged([sys.executable, "-c", "import time; time.sleep(2)"], "test", timeout=.1)
    assert time.monotonic() - start < 1.8


def test_grounded_examples_and_short_budget_include_replay():
    from llamaforge.core.learning_data import validate_examples, training_schedule
    rows = validate_examples([{"user": "q", "assistant": "a"}, None, {"user": [], "assistant": "x"},
        {"user": "q", "assistant": "a"}])
    assert len(rows) == 1
    samples = [{"split": "new"} for _ in range(4)] + [{"split": "replay"} for _ in range(8)]
    schedule = training_schedule(samples, steps=3, seed=4)
    assert len(schedule) == 3
    assert {samples[i]["split"] for i in schedule} == {"new", "replay"}


def test_quality_gate_rejects_regression_and_missing_metrics():
    from llamaforge.core.training_loop import assess_quality
    assert not assess_quality({"new": 1, "replay": 1}, {"new": 2, "replay": 1})["accepted"]
    assert not assess_quality({"new": 1, "replay": 1}, {"new": .7, "replay": 2})["accepted"]
    assert not assess_quality({"new": 1}, {"new": float("nan")})["accepted"]
    assert assess_quality({"new": 1, "replay": 1}, {"new": .7, "replay": 1.01})["accepted"]


def test_actual_torch_microtraining_improves_loss_without_mutating_frozen_weights():
    torch = pytest.importorskip("torch")
    from llamaforge.core.training_loop import train_candidate
    torch.manual_seed(7)
    class TinyAdapter(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base = torch.nn.Parameter(torch.zeros(2), requires_grad=False)
            self.adapter = torch.nn.Parameter(torch.zeros(2))
        def forward(self, input_ids, labels, **kwargs):
            return SimpleNamespace(loss=torch.nn.functional.cross_entropy((self.base+self.adapter).expand(labels.numel(), 2), labels.reshape(-1)))
    model = TinyAdapter()
    encoded = [{"input_ids": torch.tensor([[0]]), "labels": torch.tensor([[1]])}]
    result = train_candidate(model, encoded, [{"split": "new"}], steps=3, learning_rate=.2)
    assert result["validation"]["accepted"]
    assert result["validation"]["after"]["new"] < result["validation"]["before"]["new"]
    assert torch.equal(model.base, torch.zeros(2))


def test_nonfinite_gradient_never_reaches_optimizer_step():
    torch = pytest.importorskip("torch")
    from llamaforge.core.training_loop import train_candidate
    class BadGradient(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.ones(1))
            self.weight.register_hook(lambda grad: grad * float("nan"))
        def forward(self, **kwargs):
            return SimpleNamespace(loss=self.weight.square().sum())
    model = BadGradient()
    with pytest.raises(RuntimeError, match="non-finite"):
        train_candidate(model, [{}], [{"split": "new"}], steps=1, learning_rate=.1)
    assert model.weight.item() == 1


def test_generation_is_confirmed_only_after_reload_and_recovers_commit_cleanup(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    _candidate(brain)
    brain._save_profile_meta(MODEL, {"generation": 2})
    brain._commit_learning_candidate(MODEL, "next", {"generation": 2}, {"generation": 3, "validation": {"accepted": True}})
    assert brain.status(MODEL)["generation"] == 2
    original = brain._save_profile_meta
    monkeypatch.setattr(brain, "_save_profile_meta", lambda *a: (_ for _ in ()).throw(OSError("interrupted cleanup")))
    with pytest.raises(OSError):
        brain.confirm_learning(MODEL)
    monkeypatch.setattr(brain, "_save_profile_meta", original)
    fresh = PersonalBrain(tmp_path)
    fresh.activate_model(MODEL)
    assert fresh.adapter_gguf(MODEL).read_bytes() == b"new-gguf"
    assert fresh.status(MODEL)["generation"] == 3
    assert "next" in fresh._learned_ids(fresh.model_key(MODEL))


def test_duplicate_supervision_can_be_relearned_after_a_later_correction(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    old = PersonalBrain.deterministic_examples("اسم من رضاست.")
    new = PersonalBrain.deterministic_examples("My name is Sara.")
    _history(brain, [{"id": "old", "examples": old}, {"id": "new", "examples": new}])
    assert brain.already_learned(MODEL, new)
    assert not brain.already_learned(MODEL, old)


def test_preview_of_greeting_and_deterministic_fact_never_calls_teacher(monkeypatch, tmp_path):
    state = object.__new__(LlamaForgeState)
    state.brain = _isolated_brain(monkeypatch, tmp_path)
    state.active_model = MODEL
    state._brain_synthesize_examples = lambda *a: pytest.fail("unnecessary teacher inference")
    assert not state.preview_brain_lesson({"user": "سلام", "precheck": True})["should_learn"]
    assert state.preview_brain_lesson({"user": "اسم من رضاست.", "precheck": True})["should_learn"]


def test_noop_lesson_skips_doctor_model_unload_and_trainer(monkeypatch, tmp_path):
    state = object.__new__(LlamaForgeState)
    state.brain = _isolated_brain(monkeypatch, tmp_path)
    state.active_model = MODEL
    state.last_launch_payload = {"model_path": "model.gguf"}
    state.brain_cancel = threading.Event()
    state.events = SimpleNamespace(publish=lambda *a: None)
    state.log = lambda *a: None
    state.log_exception = lambda *a: pytest.fail("no-op raised an exception")
    state.brain_status = lambda: state.brain.status(MODEL)
    state.brain_doctor = lambda: pytest.fail("no-op ran heavy preflight")
    state._brain_synthesize_examples = lambda *a: pytest.fail("no-op ran teacher")
    class ImmediateThread:
        def __init__(self, target, **kwargs): self.target=target
        def start(self): self.target()
    monkeypatch.setattr("llamaforge.web.server.threading.Thread", ImmediateThread)
    result = state.learn_brain_async({"user": "اسم من چیه؟"})
    assert result["job"]["stage"] == "no-op"


def test_cpu_training_memory_guard_uses_checkpoint_size_not_model_name():
    from llamaforge.trainer_worker import check_training_memory
    small = check_training_memory(3.1, "fp16-safe-cpu-lora", 128, 4, available_gb=10)
    assert small["minimum_free_gb"] < 10
    with pytest.raises(RuntimeError, match="RAM"):
        check_training_memory(12, "fp16-safe-cpu-lora", 192, 4, available_gb=10)


@pytest.mark.parametrize("text", ["امروز چندمه؟", "ساعت چنده؟", "Can you read this PDF?", "این ZIP چیه؟"])
def test_plain_questions_do_not_start_a_second_learning_inference(monkeypatch, tmp_path, text):
    state = object.__new__(LlamaForgeState)
    state.brain = _isolated_brain(monkeypatch, tmp_path)
    state.active_model = MODEL
    assert state.preview_brain_lesson({"user": text, "precheck": True})["should_learn"] is False
