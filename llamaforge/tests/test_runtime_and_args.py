from unittest.mock import patch

from llamaforge.core.hardware import HardwareInfo, GPUInfo
from llamaforge.core.models import LocalModel
from llamaforge.core.planner import make_plan, server_args
from llamaforge.core.runtime import RuntimeManager


def _hw():
    return HardwareInfo("Windows", "11", "AMD64", "Ryzen", 16, 8, 16, 12, [], [])


def test_server_args_respect_capabilities(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 48.4, "Q4_K_M", "qwen", 32768, "")
    plan = make_plan(model, _hw(), "Giant Model (Experimental)", True, 4096)
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--cache-type-k", "--cache-type-v", "--n-gpu-layers", "--mmap"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert "--n-gpu-layers" in args
    assert args[args.index("--n-gpu-layers") + 1] == "0"
    assert "--mmap" in args
    assert "--load-mode" not in args
    assert "--device" not in args


def test_current_style_windows_cpu_asset_is_selected(tmp_path):
    mgr = RuntimeManager(str(tmp_path))
    release = {"assets": [
        {"name": "llama-b10516-bin-win-cuda-13.3-x64.zip", "url": "cuda", "size": 1},
        {"name": "llama-b10516-bin-win-vulkan-x64.zip", "url": "vk", "size": 1},
        {"name": "llama-b10516-bin-win-cpu-x64.zip", "url": "cpu", "size": 1},
    ]}
    with patch("platform.system", return_value="Windows"), patch("platform.machine", return_value="AMD64"):
        asset = mgr.select_cpu_asset(release)
    assert asset and asset["url"] == "cpu"


def test_current_style_windows_vulkan_asset_is_selected(tmp_path):
    mgr = RuntimeManager(str(tmp_path))
    release = {"assets": [
        {"name": "llama-b10981-bin-win-cuda-13.3-x64.zip", "url": "cuda", "size": 1},
        {"name": "llama-b10981-bin-win-vulkan-x64.zip", "url": "vk", "size": 1},
        {"name": "llama-b10981-bin-win-cpu-x64.zip", "url": "cpu", "size": 1},
    ]}
    with patch("platform.system", return_value="Windows"), patch("platform.machine", return_value="AMD64"):
        asset = mgr.select_vulkan_asset(release)
    assert asset and asset["url"] == "vk"


def test_hybrid_plan_keeps_layers_on_both_cpu_and_gpu(tmp_path):
    model = LocalModel(str(tmp_path / "hybrid.gguf"), "hybrid", 4.0, "Q4_K_M", "qwen2", 8192, block_count=28)
    hw = HardwareInfo("Windows", "11", "AMD64", "Ryzen", 12, 6, 16, 12,
                      [GPUInfo("AMD Radeon Graphics", "AMD/Vulkan", 2.0)], [])
    plan = make_plan(model, hw, "Balanced", False, 4096, thread_mode="target", cpu_target_percent=100, gpu_layer_percent=35)
    assert plan.cpu_only is False
    assert plan.accelerator_mode == "hybrid"
    assert plan.gpu_layers == 10
    assert 0 < plan.gpu_layers < model.block_count
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert args[args.index("--n-gpu-layers") + 1] == "10"
    assert args[args.index("--threads") + 1] == "12"



def test_max_both_intel_igpu_keeps_cpu_work_and_small_gpu_slice(tmp_path):
    model = LocalModel(str(tmp_path / "maxboth.gguf"), "maxboth", 5.8, "Q6_K", "gemma4", 8192, block_count=42)
    hw = HardwareInfo("Windows", "10", "AMD64", "Intel CPU", 4, 2, 16, 10,
                      [GPUInfo("Intel(R) HD Graphics 530", "Intel/Vulkan", 1.0)], [])
    plan = make_plan(model, hw, "Balanced", False, 4096, accelerator_mode="max_both", gpu_layer_percent=35)
    assert plan.accelerator_mode == "max_both"
    assert plan.cpu_saturation is True
    assert plan.cpu_target_percent == 100
    assert plan.threads == 4 and plan.threads_batch == 4
    assert plan.gpu_layer_percent == 10
    assert plan.gpu_layers == 4
    caps = {"--model", "--host", "--port", "--threads", "--threads-batch", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--no-op-offload", "--poll", "--prio", "--prio-batch", "--poll-batch", "--cpu-range", "--cpu-strict"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert args[args.index("--n-gpu-layers") + 1] == "4"
    assert "--no-op-offload" in args
    assert args[args.index("--threads") + 1] == "4"
    assert args[args.index("--threads-batch") + 1] == "4"

def test_shard_group_collects_all_parts():
    from llamaforge.core.models import ModelManager
    rows = [
        {"name": "Qwen-Q4_K_M-00002-of-00003.gguf", "size": 20},
        {"name": "Qwen-Q4_K_M-00001-of-00003.gguf", "size": 20},
        {"name": "Qwen-Q4_K_M-00003-of-00003.gguf", "size": 20},
        {"name": "README.gguf", "size": 1},
    ]
    group = ModelManager.shard_group(rows, "Qwen-Q4_K_M-00001-of-00003.gguf")
    assert [x["name"] for x in group] == [
        "Qwen-Q4_K_M-00001-of-00003.gguf",
        "Qwen-Q4_K_M-00002-of-00003.gguf",
        "Qwen-Q4_K_M-00003-of-00003.gguf",
    ]


def test_server_args_can_force_chat_template(tmp_path):
    model = LocalModel(str(tmp_path / "gemma.gguf"), "g", 2.5, "Q4_K_S", "gemma3", 8192, "")
    plan = make_plan(model, _hw(), "Low RAM", True, 4096)
    caps = {"--chat-template", "--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps, chat_template="gemma")
    assert "--chat-template" in args
    assert args[args.index("--chat-template") + 1] == "gemma"


def test_source_only_latest_is_skipped_for_binary_nightly(tmp_path):
    mgr = RuntimeManager(str(tmp_path))
    releases = [
        {"tag": "v0.4.0", "assets": [
            {"name": "Source code (zip)", "url": "src", "size": 1},
        ]},
        {"tag": "b10762", "assets": [
            {"name": "llama-b10762-bin-win-cpu-arm64.zip", "url": "arm", "size": 1},
            {"name": "llama-b10762-bin-win-cpu-x64.zip", "url": "x64", "size": 1},
        ]},
    ]
    with patch.object(mgr, "recent_releases", return_value=releases), \
         patch("platform.system", return_value="Windows"), \
         patch("platform.machine", return_value="AMD64"):
        rel, asset = mgr.latest_compatible_cpu_release()
    assert rel["tag"] == "b10762"
    assert asset["url"] == "x64"


def test_windows_x64_rejects_arm64_cpu_asset(tmp_path):
    mgr = RuntimeManager(str(tmp_path))
    release = {"assets": [
        {"name": "llama-b10762-bin-win-cpu-arm64.zip", "url": "arm", "size": 1},
    ]}
    with patch("platform.system", return_value="Windows"), patch("platform.machine", return_value="AMD64"):
        assert mgr.select_cpu_asset(release) is None


def test_runtime_build_number_and_gemma4_preflight(tmp_path):
    mgr = RuntimeManager(str(tmp_path), custom_server_path=str(tmp_path / "llama-server.exe"))
    assert mgr.parse_build_number("version: 6942 (deadbeef)") == 6942
    with patch.object(mgr, "status", return_value={"build_number": 6942}):
        result = mgr.model_compatibility("gemma4")
    assert result["ok"] is False
    assert "too old" in result["message"].lower()


def test_single_user_profiles_limit_parallel_slots_and_prompt_cache(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 5.8, "Q6_K", "gemma4", 131072, "")
    plan = make_plan(model, _hw(), "Low RAM", True, 4096)
    assert plan.parallel == 1
    assert plan.prompt_cache_mb == 0
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--parallel", "--cache-ram"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert args[args.index("--parallel") + 1] == "1"
    assert args[args.index("--cache-ram") + 1] == "0"


def test_parse_build_zero_is_unknown_but_path_build_wins(tmp_path):
    mgr = RuntimeManager(str(tmp_path))
    assert mgr.parse_build_number("version: 0 (unknown)") is None
    assert mgr.parse_build_number(r"C:\\Users\\reza\\.llamaforge\\runtime\\b10819\\llama-server.exe") == 10819


def test_status_recovers_build_from_managed_runtime_path_when_version_is_zero(tmp_path):
    root = tmp_path / "b10819"
    root.mkdir()
    server = root / "llama-server.exe"
    server.write_bytes(b"")
    (tmp_path / "installed.json").write_text(
        __import__("json").dumps({"tag": "b10819", "path": str(root), "asset": "llama-b10819-bin-win-cpu-x64.zip"}),
        encoding="utf-8",
    )
    mgr = RuntimeManager(str(tmp_path))
    with patch.object(mgr, "find_binary", side_effect=lambda name: str(server) if name == "llama-server" else None), \
         patch("subprocess.check_output", return_value="load_backend: loaded CPU backend\\nversion: 0 (unknown)\\n"):
        st = mgr.status()
    assert st["build_number"] == 10819
    assert st["build_source"] in {"runtime path", "installed release tag"}
    assert mgr.model_compatibility("gemma4")["ok"] is True


def test_unknown_runtime_build_does_not_false_block_gemma4(tmp_path):
    mgr = RuntimeManager(str(tmp_path))
    with patch.object(mgr, "status", return_value={"build_number": None}):
        result = mgr.model_compatibility("gemma4")
    assert result["ok"] is True


def test_full_cpu_target_emits_poll_and_medium_priority(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 5.8, "Q6_K", "gemma4", 8192, "")
    hw = HardwareInfo("Windows", "11", "AMD64", "Test CPU", 4, 2, 16, 12, [], [])
    plan = make_plan(model, hw, "Balanced", True, 4096, thread_mode="target", cpu_target_percent=100)
    caps = {"--model", "--host", "--port", "--threads", "--threads-batch", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--poll", "--prio"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert args[args.index("--threads") + 1] == "4"
    assert args[args.index("--threads-batch") + 1] == "4"
    assert args[args.index("--poll") + 1] == "100"
    assert args[args.index("--prio") + 1] == "1"


def test_saturation_emits_affinity_and_batch_priority_when_supported(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 5.8, "Q6_K", "gemma4", 8192, "")
    hw = HardwareInfo("Windows", "11", "AMD64", "Test CPU", 4, 2, 16, 12, [], [])
    plan = make_plan(model, hw, "Balanced", True, 4096, thread_mode="saturate", cpu_saturation=True)
    caps = {"--model", "--host", "--port", "--threads", "--threads-batch", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--poll", "--prio", "--poll-batch", "--prio-batch", "--cpu-range", "--cpu-strict"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert args[args.index("--threads") + 1] == "4"
    assert args[args.index("--cpu-range") + 1] == "0-3"
    assert args[args.index("--cpu-strict") + 1] == "1"
    assert args[args.index("--prio") + 1] == "2"
    assert args[args.index("--prio-batch") + 1] == "2"


def test_server_args_load_personal_lora_when_supported(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 5.8, "Q6_K", "gemma4", 8192, "")
    plan = make_plan(model, _hw(), "Balanced", True, 4096)
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--lora-scaled"}
    adapter = str(tmp_path / "brain.gguf")
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps, lora_path=adapter, lora_scale=0.85)
    assert "--lora-scaled" in args
    i = args.index("--lora-scaled")
    assert args[i + 1] == adapter
    assert args[i + 2] == "0.8500"


def test_model_download_many_reuses_complete_existing_file(tmp_path, monkeypatch):
    from llamaforge.core.models import ModelManager
    mgr=ModelManager()
    existing=tmp_path/'model-Q4_K_M.gguf'; existing.write_bytes(b'abc')
    def should_not_download(*args, **kwargs):
        raise AssertionError('complete GGUF should not be downloaded again')
    monkeypatch.setattr(mgr,'download',should_not_download)
    out=mgr.download_many('org/repo',[{'name':'model-Q4_K_M.gguf','size':3}],str(tmp_path))
    assert out == [existing]


def test_current_load_mode_and_lazy_mode_are_emitted(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 5.0, "Q4_K_M", "qwen", 8192, "")
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--load-mode", "--lazy-mode", "--no-warmup"}
    ram = make_plan(model, _hw(), "Balanced", True, 4096, memory_mode="ram_only")
    ram_args = server_args(model.path, ram, "127.0.0.1", 8080, caps)
    assert ram_args[ram_args.index("--load-mode") + 1] == "none"
    assert ram_args[ram_args.index("--lazy-mode") + 1] == "off"
    assert "--no-warmup" not in ram_args

    ssd = make_plan(model, _hw(), "Balanced", True, 4096, memory_mode="ssd_test")
    ssd_args = server_args(model.path, ssd, "127.0.0.1", 8080, caps)
    assert ssd_args[ssd_args.index("--load-mode") + 1] == "mmap"
    assert ssd_args[ssd_args.index("--lazy-mode") + 1] == "on"
    assert "--no-warmup" in ssd_args


def test_legacy_ram_only_uses_no_mmap_when_supported(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 5.0, "Q4_K_M", "qwen", 8192, "")
    plan = make_plan(model, _hw(), "Balanced", True, 4096, memory_mode="ram_only")
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--no-mmap"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert "--no-mmap" in args
    assert "--mmap" not in args
    assert "--mlock" not in args


def test_server_args_adds_multimodal_projector_when_supported(tmp_path):
    model = LocalModel(str(tmp_path / "vision.gguf"), "vision", 5.0, "Q4_K_M", "qwen2-vl", 8192, "")
    plan = make_plan(model, _hw(), "Balanced", True, 4096)
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--mmproj"}
    projector = str(tmp_path / "mmproj-model-f16.gguf")
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps, mmproj_path=projector)
    assert args[args.index("--mmproj") + 1] == projector


def test_server_args_rejects_vision_model_on_runtime_without_mmproj(tmp_path):
    import pytest
    model = LocalModel(str(tmp_path / "vision.gguf"), "vision", 5.0, "Q4_K_M", "qwen2-vl", 8192, "")
    plan = make_plan(model, _hw(), "Balanced", True, 4096)
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers"}
    with pytest.raises(RuntimeError, match="--mmproj"):
        server_args(model.path, plan, "127.0.0.1", 8080, caps, mmproj_path=str(tmp_path / "mmproj.gguf"))


def test_server_args_support_distributed_rpc_and_tensor_split(tmp_path):
    model = LocalModel(str(tmp_path / "cluster.gguf"), "cluster", 5.0, "Q4_K_M", "qwen", 8192, "")
    plan = make_plan(model, _hw(), "Balanced", True, 4096)
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers", "--rpc", "--tensor-split"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps, rpc_servers=["192.168.1.11:50052", "192.168.1.12:50052"], tensor_split=[0.4, 0.6], distributed=True)
    assert args[args.index("--rpc") + 1] == "192.168.1.11:50052,192.168.1.12:50052"
    assert args[args.index("--tensor-split") + 1] == "0.400000,0.600000"
    assert args[args.index("--n-gpu-layers") + 1] == "-1"


def test_distributed_rpc_is_rejected_when_runtime_lacks_rpc_flag(tmp_path):
    model = LocalModel(str(tmp_path / "cluster.gguf"), "cluster", 5.0, "Q4_K_M", "qwen", 8192, "")
    plan = make_plan(model, _hw(), "Balanced", True, 4096)
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers"}
    import pytest
    with pytest.raises(RuntimeError, match="rpc"):
        server_args(model.path, plan, "127.0.0.1", 8080, caps, rpc_servers=["192.168.1.11:50052"], distributed=True)


def test_gpu_mode_requests_maximum_offload(tmp_path):
    model = LocalModel(str(tmp_path / "gpu.gguf"), "gpu", 4.0, "Q4_K_M", "qwen2", 8192, block_count=28)
    hw = HardwareInfo("Windows", "10", "AMD64", "Intel", 4, 2, 16, 10,
                      [GPUInfo("Intel(R) HD Graphics 530", "Intel/Vulkan", 1.0)], [])
    plan = make_plan(model, hw, "Balanced", False, 8192, accelerator_mode="gpu", gpu_layer_percent=35)
    assert plan.accelerator_mode == "gpu"
    assert plan.cpu_only is False
    assert plan.gpu_layer_percent == 100
    caps = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, caps)
    assert args[args.index("--n-gpu-layers") + 1] == "-1"


def test_adaptive_intel_igpu_starts_cpu_first_before_tuning(tmp_path):
    model = LocalModel(str(tmp_path / "adaptive.gguf"), "adaptive", 5.0, "Q4_K_M", "qwen2", 8192, block_count=40)
    hw = HardwareInfo("Windows", "10", "AMD64", "Intel", 4, 2, 16, 10,
                      [GPUInfo("Intel(R) HD Graphics 530", "Intel/Vulkan", 1.0)], [])
    plan = make_plan(model, hw, "Balanced", False, 4096, accelerator_mode="adaptive")
    assert plan.accelerator_mode == "adaptive"
    assert plan.gpu_layer_percent == 10
    assert plan.gpu_layers == 4
    assert plan.threads == 4
    assert plan.tuning_source == "heuristic"


def test_adaptive_saved_tune_can_override_layers_threads_and_batches(tmp_path):
    model = LocalModel(str(tmp_path / "adaptive.gguf"), "adaptive", 5.0, "Q4_K_M", "qwen2", 8192, block_count=40)
    hw = HardwareInfo("Windows", "10", "AMD64", "Intel", 4, 2, 16, 10,
                      [GPUInfo("Intel(R) HD Graphics 530", "Intel/Vulkan", 1.0)], [])
    plan = make_plan(model, hw, "Balanced", False, 4096, accelerator_mode="adaptive",
                     thread_mode="manual", threads_override=3, threads_batch_override=4,
                     gpu_layers_override=6, batch_size_override=128, ubatch_size_override=64,
                     tuning_source="llama-bench")
    assert plan.gpu_layers == 6
    assert plan.gpu_layer_percent == 15
    assert plan.threads == 3
    assert plan.threads_batch == 4
    assert plan.batch_size == 128
    assert plan.ubatch_size == 64
    assert plan.tuning_source == "llama-bench"


def test_adaptive_oversized_model_shrinks_transient_buffers(tmp_path):
    model = LocalModel(str(tmp_path / "large.gguf"), "large", 11.5, "Q5_K_M", "qwen2", 32768, block_count=40)
    hw = HardwareInfo("Windows", "10", "AMD64", "Intel", 4, 2, 16, 10,
                      [GPUInfo("Intel(R) HD Graphics 530", "Intel/Vulkan", 1.0)], [])
    plan = make_plan(model, hw, "Balanced", False, 8192, accelerator_mode="adaptive", memory_mode="hybrid")
    assert plan.ctx_size <= 2048
    assert plan.batch_size <= 64
    assert plan.ubatch_size <= 32
    assert plan.parallel == 1
    assert plan.prompt_cache_mb == 0
    assert plan.load_mode == "mmap"


def test_speculative_ngram_is_capability_gated(tmp_path):
    model = LocalModel(str(tmp_path / "x.gguf"), "x", 4.0, "Q4_K_M", "qwen2", 8192, block_count=28)
    plan = make_plan(model, _hw(), "Balanced", True, 4096, speculative_mode="ngram")
    common = {"--model", "--host", "--port", "--threads", "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers"}
    args = server_args(model.path, plan, "127.0.0.1", 8080, common | {"--spec-default"})
    assert "--spec-default" in args
    args2 = server_args(model.path, plan, "127.0.0.1", 8080, common | {"--spec-type"})
    assert args2[args2.index("--spec-type") + 1] == "ngram-mod"
    args3 = server_args(model.path, plan, "127.0.0.1", 8080, common)
    assert "--spec-default" not in args3 and "--spec-type" not in args3
