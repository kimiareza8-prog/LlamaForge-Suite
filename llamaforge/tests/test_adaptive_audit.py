import dataclasses
import json
import os
import threading

import pytest

from llamaforge.core.autotune import AdaptiveTuner, TuneResult
from llamaforge.core.hardware import HardwareInfo, GPUInfo
from llamaforge.core.models import LocalModel
from llamaforge.core.planner import make_plan, server_args


def fixture_model(tmp_path, size=11):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF-a")
    return LocalModel(str(path), "model", size, "Q4_K_M", "qwen", 8192, block_count=40)


def hardware(gpu=False):
    return HardwareInfo("Windows", "11", "AMD64", "CPU", 4, 2, 16, 11,
                        [GPUInfo("Intel HD 530", "Intel/Vulkan", 1)] if gpu else [], [])


def test_cpu_adaptive_keeps_memory_guards(tmp_path):
    plan = make_plan(fixture_model(tmp_path), hardware(), accelerator_mode="adaptive", requested_ctx=8192)
    assert plan.adaptive and plan.cpu_only
    assert plan.ctx_size <= 4096 and plan.gpu_layers == 0
    assert plan.load_mode == "mmap" and plan.parallel == 1 and plan.prompt_cache_mb == 0


@pytest.mark.parametrize("source", ["heuristic", "llama-bench"])
def test_adaptive_preserves_explicit_threads(tmp_path, source):
    plan = make_plan(fixture_model(tmp_path, 2), hardware(True), accelerator_mode="adaptive",
                     thread_mode="manual", threads_override=1, threads_batch_override=2, tuning_source=source)
    assert (plan.threads, plan.threads_batch) == (1, 2)


def test_slots_and_speculation_require_evidence_or_explicit_selection(tmp_path):
    plan = make_plan(fixture_model(tmp_path, 1), hardware(True), accelerator_mode="adaptive", requested_ctx=2048)
    assert plan.parallel == 1
    args = server_args("m", plan, "localhost", 8080, {"--spec-default", "--spec-type", "--parallel"})
    assert "--spec-default" not in args and "--spec-type" not in args


def test_parallel_context_is_per_slot(tmp_path):
    plan = make_plan(fixture_model(tmp_path, 1), hardware(True), accelerator_mode="adaptive",
                     requested_ctx=1024, parallel_override=2)
    args = server_args("m", plan, "localhost", 8080, {"--parallel"})
    assert args[args.index("--ctx-size") + 1] == str(plan.ctx_size * plan.parallel)


def rows(t=2, gpu=0, b=128, ub=64, speeds=(100, 10)):
    return [dict(n_threads=t, n_gpu_layers=gpu, n_batch=b, n_ubatch=ub,
                 n_prompt=512 if i == 0 else 0, n_gen=32 if i else 0, avg_ts=speed)
            for i, speed in enumerate(speeds)]


def test_score_requires_both_phases_and_uses_repeat_median(tmp_path):
    data = rows()
    data[1].update(avg_ts=340, samples_ts=[9, 10, 1000])
    data += rows(t=4, speeds=(5000, 0))[:1]
    got = AdaptiveTuner._summarize(data, fixture_model(tmp_path))
    assert len(got) == 1 and got[0]["tg"] == 10
    assert got[0]["ttft_ms"] >= 5120


def test_cache_is_bound_to_runtime_hardware_profile_and_model_content(tmp_path):
    model = fixture_model(tmp_path)
    bench = tmp_path / "llama-bench.exe"
    bench.write_bytes(b"build1")
    tuner = AdaptiveTuner(tmp_path / "tune.json")
    env = tuner.environment(hardware(True), str(bench), "vulkan", context=4096, load_mode="hybrid")
    result = TuneResult("", model.path, 1, "vulkan", 2, 2, 0, 0, 128, 64, 100, 10, 5220, 90, 3)
    tuner.put(model, result, environment=env)
    assert tuner.get(model, environment=env)["threads"] == 2
    assert tuner.get(model, environment={**env, "context": 8192}) is None
    bench.write_bytes(b"build2")
    changed = tuner.environment(hardware(True), str(bench), "vulkan", context=4096, load_mode="hybrid")
    assert tuner.get(model, environment=changed) is None
    st = os.stat(model.path)
    with open(model.path, "wb") as f:
        f.write(b"GGUF-b")
    os.utime(model.path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert tuner.get(model, environment=env) is None


def test_search_is_staged_repeated_and_failure_tolerant(tmp_path, monkeypatch):
    tuner = AdaptiveTuner(tmp_path / "tune.json")
    bench = tmp_path / "bench"
    bench.touch()
    calls = []
    def run(cmd, cancel=None, timeout=900):
        calls.append(cmd)
        t, gpu, b, ub = (int(cmd[cmd.index(flag) + 1]) for flag in ("-t", "-ngl", "-b", "-ub"))
        assert int(cmd[cmd.index("-p") + 1]) >= b
        assert int(cmd[cmd.index("-r") + 1]) >= 3
        if gpu != 0:
            raise RuntimeError("Vulkan failed")
        return rows(t, gpu, b, ub, speeds=(100 + b / 10, 20 if t == 2 else 10))
    monkeypatch.setattr(tuner, "_run_candidate", run, raising=False)
    result = tuner.benchmark(fixture_model(tmp_path, 2), hardware(True), str(bench))
    pairs = {(cmd[cmd.index("-b") + 1], cmd[cmd.index("-ub") + 1]) for cmd in calls}
    assert ("256", "64") in pairs and ("256", "128") in pairs
    assert result["threads"] == 2 and result["gpu_layers"] == 0
    assert result["speculative_mode"] == "off"
    assert len(calls) < 24
    assert result["failed_candidates"]


def test_candidate_cancel_does_not_spawn(tmp_path, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("spawned after cancel"))
    with pytest.raises(RuntimeError, match="cancel"):
        AdaptiveTuner._run_candidate(["bench"], cancel=cancel)


def test_custom_runtime_never_benchmarks_another_build(tmp_path):
    from llamaforge.core.runtime import RuntimeManager
    managed, custom = tmp_path / "managed", tmp_path / "custom"
    managed.mkdir(); custom.mkdir()
    runtime = RuntimeManager(str(managed))
    server = custom / runtime._exe("llama-server")
    bench_name = runtime._exe("llama-bench")
    server.touch(); (managed / bench_name).touch()
    runtime = RuntimeManager(str(managed), str(server))
    assert runtime.find_binary("llama-bench") is None
    (custom / bench_name).touch()
    assert runtime.find_binary("llama-bench") == str((custom / bench_name).resolve())
