from __future__ import annotations

from dataclasses import dataclass, asdict

from .hardware import HardwareInfo
from .models import LocalModel


@dataclass
class LaunchPlan:
    profile: str
    cpu_only: bool
    threads: int
    threads_batch: int
    ctx_size: int
    batch_size: int
    ubatch_size: int
    cache_type_k: str
    cache_type_v: str
    gpu_layers: int
    gpu_layer_percent: int
    accelerator_mode: str
    adaptive: bool
    tuning_source: str
    speculative_mode: str
    load_mode: str
    lazy_mode: str
    memory_mode: str
    flash_attn: str
    no_warmup: bool
    parallel: int
    prompt_cache_mb: int
    cpu_target_percent: int
    cpu_poll: int
    cpu_priority: int
    cpu_strict: bool
    cpu_saturation: bool
    cpu_affinity_count: int
    estimated_model_resident_gb: float
    estimated_runtime_gb: float
    estimated_total_gb: float
    available_ram_gb: float
    oversized: bool
    warning: str

    def to_dict(self):
        return asdict(self)


PROFILES = ["Safe", "Balanced", "Max Speed", "Low RAM", "Giant Model (Experimental)"]
MEMORY_MODES = ["ram_only", "ssd_test", "hybrid"]


def make_plan(
    model: LocalModel,
    hw: HardwareInfo,
    profile: str = "Balanced",
    cpu_only: bool = True,
    requested_ctx: int | None = None,
    max_ram_percent: int = 88,
    thread_mode: str = "auto",
    threads_override: int | None = None,
    threads_batch_override: int | None = None,
    cpu_target_percent: int | None = None,
    cpu_saturation: bool = False,
    memory_mode: str = "hybrid",
    gpu_layer_percent: int = 35,
    accelerator_mode: str | None = None,
    gpu_layers_override: int | None = None,
    batch_size_override: int | None = None,
    ubatch_size_override: int | None = None,
    parallel_override: int | None = None,
    tuning_source: str = "heuristic",
    speculative_mode: str = "auto",
    adaptive_context: bool = True,
) -> LaunchPlan:
    p = profile if profile in PROFILES else "Balanced"
    memory_mode = memory_mode if memory_mode in MEMORY_MODES else "hybrid"
    physical = max(1, hw.physical_cores); logical = max(1, hw.logical_cores); aggressive_max = logical * 2

    # Generation is usually most efficient around physical-core count, while
    # prompt ingestion can often profit from SMT/logical threads.  A performance
    # mode intentionally uses more CPU but still leaves one logical thread for
    # the desktop when possible. Manual mode is always clamped to hardware
    # concurrency so a typo cannot create extreme oversubscription.
    mode = (thread_mode or "auto").lower()
    auto_threads = physical
    auto_batch = min(logical, max(physical, int(round(physical * 1.5))))
    perf_threads = logical if logical <= 2 else max(physical, logical - 1)
    perf_batch = logical
    target_percent = max(25, min(100, int(cpu_target_percent or 0))) if cpu_target_percent else 0
    if mode == "saturate":
        # Strict CPU affinity and oversubscription are mutually contradictory:
        # a 4-bit affinity mask cannot satisfy 6 strict workers. Use every
        # logical CPU exactly once in Saturate mode. If a user wants to
        # experiment with oversubscription they can use Manual mode, where
        # strict affinity is intentionally disabled.
        threads = logical
        threads_batch = logical
        target_percent = 100
        cpu_saturation = True
    elif mode == "target":
        # A CPU percentage is a scheduling *budget*, not a hard utilization
        # guarantee. Convert it to hardware concurrency so 25/50/75/100% maps
        # naturally onto 1/2/3/4 logical CPUs on a 4-thread machine.
        target_threads = max(1, min(logical, int(round(logical * target_percent / 100.0))))
        threads = target_threads
        threads_batch = target_threads
    elif mode == "performance":
        threads, threads_batch = perf_threads, perf_batch
        target_percent = max(25, min(100, int(round((threads / logical) * 100))))
    elif mode == "manual":
        threads = max(1, min(aggressive_max, int(threads_override or auto_threads)))
        threads_batch = max(1, min(aggressive_max, int(threads_batch_override or auto_batch)))
        target_percent = max(25, min(100, int(round((max(threads, threads_batch) / logical) * 100))))
    else:
        threads, threads_batch = auto_threads, auto_batch
        target_percent = max(25, min(100, int(round((max(threads, threads_batch) / logical) * 100))))

    # llama.cpp's poll level controls how aggressively worker threads spin while
    # waiting for work. 100% target deliberately favors utilization/latency;
    # lower targets keep the normal balanced polling behavior. Medium priority
    # is used for full-throttle mode; high/realtime are intentionally avoided so
    # the Windows desktop and thermal management remain responsive.
    cpu_poll = 100 if (mode == "saturate" or (mode == "target" and target_percent >= 100)) else (80 if mode == "target" and target_percent >= 90 else 50)
    cpu_priority = 2 if mode == "saturate" else (1 if mode == "target" and target_percent >= 90 else 0)
    cpu_strict = bool(mode == "saturate" or (mode == "target" and target_percent >= 100))
    # Hybrid acceleration intentionally keeps work on *both* compute engines.
    # Full offload (-1) can make the CPU mostly an orchestrator and does not
    # match the user's "CPU + iGPU together" goal.  A percentage of transformer
    # blocks is therefore sent to Vulkan while the rest stay on CPU.  GGUF files
    # normally expose <arch>.block_count; the small fallback still enables GPU
    # work for unusual files without that metadata.
    requested_accelerator = str(accelerator_mode or ("cpu" if cpu_only else "hybrid")).strip().lower()
    if requested_accelerator not in {"adaptive", "cpu", "gpu", "hybrid", "max_both"}:
        requested_accelerator = "adaptive"
    if not hw.gpus:
        requested_accelerator = "cpu"
    adaptive = requested_accelerator == "adaptive"
    cpu_only = requested_accelerator == "cpu"
    gpu_pct = max(5, min(95, int(gpu_layer_percent or 35)))
    blocks = max(0, int(getattr(model, "block_count", 0) or 0))

    # Max Both is intentionally different from ordinary Hybrid. Layer offload
    # in llama.cpp is synchronous for a single decode stream, so an iGPU that
    # receives too many layers can sit at 100% while CPU workers wait.  Keep a
    # small GPU slice and force all logical CPU workers active.  Intel iGPUs are
    # especially easy to saturate because they share system-memory bandwidth.
    if requested_accelerator == "max_both":
        gpu_names = " ".join(f"{getattr(g, 'name', '')} {getattr(g, 'kind', '')}" for g in hw.gpus).lower()
        recommended_pct = 10 if "intel" in gpu_names else (20 if any(x in gpu_names for x in ("amd", "radeon")) else 25)
        # A caller may explicitly request a smaller/larger slice, but prevent a
        # Max-Both preset from degenerating into the old GPU-bound 35-95% plan.
        requested_pct = int(gpu_layer_percent or recommended_pct)
        gpu_pct = max(5, min(30, requested_pct))
        if requested_pct == 35:  # legacy/default Hybrid value -> hardware-aware preset
            gpu_pct = recommended_pct
        threads = logical
        threads_batch = logical
        target_percent = 100
        cpu_saturation = True
        cpu_poll = 100
        cpu_priority = 2
        cpu_strict = True

    if requested_accelerator == "adaptive":
        # Before a model-specific benchmark exists, prefer a conservative mixed
        # plan on iGPUs and CPU-first execution on unknown accelerators. A saved
        # AutoTune result must remain authoritative for exact layer/thread/batch
        # values; do not overwrite its measured thread count with the heuristic.
        gpu_names = " ".join(f"{getattr(g, 'name', '')} {getattr(g, 'kind', '')}" for g in hw.gpus).lower()
        if not hw.gpus:
            gpu_pct = 0
        elif "intel" in gpu_names:
            gpu_pct = 10
        elif any(x in gpu_names for x in ("amd", "radeon")):
            gpu_pct = 20
        else:
            gpu_pct = 35
        if str(tuning_source or "heuristic").lower() != "llama-bench":
            threads = logical
            threads_batch = logical
            target_percent = 100
        cpu_poll = 80
        cpu_priority = 1
        cpu_strict = False

    if requested_accelerator == "cpu":
        gpu_layers = 0
        gpu_pct = 0
    elif requested_accelerator == "gpu":
        # llama.cpp accepts -1 as maximum offload. The plan keeps a human-readable
        # layer count while server_args emits -1 for this mode. CPU still handles
        # orchestration/sampling, but transformer weights are offloaded as far as
        # the backend and shared iGPU memory allow.
        gpu_layers = max(1, blocks) if blocks else 999
        gpu_pct = 100
    else:
        # Hybrid/Max-Both/Adaptive deliberately leave transformer blocks on CPU
        # unless AutoTune explicitly selected full offload (-1).
        if gpu_layers_override is not None and requested_accelerator == "adaptive":
            gpu_layers = int(gpu_layers_override)
            if gpu_layers < 0:
                gpu_pct = 100
            elif blocks > 0:
                gpu_pct = max(0, min(100, int(round(100.0 * gpu_layers / blocks))))
        elif blocks > 1:
            gpu_layers = max(1, min(blocks - 1, int(round(blocks * gpu_pct / 100.0))))
        else:
            gpu_layers = 2 if requested_accelerator == "max_both" else (2 if requested_accelerator == "adaptive" else 8)
    accelerator_mode = requested_accelerator

    if p == "Safe":
        ctx, batch, ubatch, headroom = 4096, 128, 64, 0.68
    elif p == "Max Speed":
        ctx, batch, ubatch, headroom = 8192, 512, 256, 0.90
    elif p == "Low RAM":
        ctx, batch, ubatch, headroom = 4096, 96, 48, 0.76
    elif p == "Giant Model (Experimental)":
        ctx, batch, ubatch, headroom = 4096, 64, 32, 0.80
    else:
        ctx, batch, ubatch, headroom = 8192, 256, 128, 0.84

    if adaptive and batch_size_override is not None:
        batch = max(32, min(2048, int(batch_size_override)))
    if adaptive and ubatch_size_override is not None:
        ubatch = max(16, min(batch, int(ubatch_size_override)))

    # LlamaForge is a single-user local desktop app by default. Running four
    # parallel slots wastes KV/cache memory on small CPU-only systems.
    parallel = 1
    if adaptive and parallel_override is not None:
        parallel = max(1, min(4, int(parallel_override)))
    prompt_cache_mb = 0 if p in ("Safe", "Low RAM", "Giant Model (Experimental)") else 512
    ctk = ctv = "q8_0"
    user_headroom = min(0.95, max(0.50, max_ram_percent / 100.0)); headroom = min(headroom, user_headroom)
    if requested_ctx:
        ctx = max(512, int(requested_ctx))
    if model.context_length:
        ctx = min(ctx, int(model.context_length))

    # Adaptive mode protects the OS and shared-memory iGPU before a large model
    # consumes all free RAM. The user's saved context is not overwritten; only
    # the active launch is clamped when memory pressure makes the requested
    # window unsafe.
    if adaptive and adaptive_context:
        avail_now = float(hw.ram_available_gb or max(1.0, hw.ram_total_gb * 0.65))
        reserve = max(2.25, float(hw.ram_total_gb or 0.0) * 0.14)
        usable_for_model = max(1.0, avail_now - reserve)
        if float(model.size_gb or 0.0) > usable_for_model:
            ctx = min(ctx, 4096)
        if float(model.size_gb or 0.0) > avail_now * 1.03:
            ctx = min(ctx, 2048)

    kv_est = max(0.30, (ctx / 8192) * 0.75)
    runtime = 1.05 + kv_est + (batch / 512) * 0.30
    avail = hw.ram_available_gb or max(1.0, hw.ram_total_gb * 0.65)
    budget = max(1.0, avail * headroom)
    model_size = model.size_gb; oversized = model_size + runtime > budget

    # Oversized adaptive launches trade prompt-ingestion batch memory for model
    # residency. This matters on 16 GB Windows/iGPU machines where a 10-12 GB
    # GGUF can run acceptably via mmap only if transient buffers stay small.
    # Keep the user's saved profile untouched; these are launch-local guards.
    if adaptive and oversized:
        prompt_cache_mb = 0
        parallel = 1
        if model_size > avail * 0.90:
            batch = min(batch, 64)
            ubatch = min(ubatch, 32)
        else:
            batch = min(batch, 128)
            ubatch = min(ubatch, 64)
        runtime = 1.05 + kv_est + (batch / 512) * 0.30
        oversized = model_size + runtime > budget

    if adaptive and parallel_override is None and not oversized and model_size <= max(1.5, budget * 0.42) and ctx <= 4096:
        # One loaded model can serve two independent Agent requests without
        # duplicating weights. Stay at one slot for large models because extra KV
        # cache would reduce the maximum model size the machine can sustain.
        parallel = 2
    resident = max(0.75, budget - runtime) if oversized else model_size
    total = min(model_size, resident) + runtime

    # Memory residency policy. Dense transformer layers cannot be selected from
    # the user question before inference: almost every layer participates in
    # every generated token. These modes therefore control how llama.cpp and
    # the OS keep the GGUF weights resident, not which semantic "part" of the
    # model is chosen.
    if memory_mode == "ram_only":
        # Full RAM uses ordinary process-backed allocations instead of a file
        # mapping. This is the modern equivalent of llama.cpp's old --no-mmap
        # behavior and is substantially more reliable on Windows than mlock.
        # Windows may still page memory under pressure, but the GGUF is no
        # longer kept primarily as a memory-mapped file.
        load_mode = "none"
        lazy_mode = "off"
        no_warmup = False
    elif memory_mode == "ssd_test":
        # Closest practical SSD-first test: keep weights memory-mapped, disable
        # warmup, and ask llama.cpp to read supported lazy tensors on demand.
        # Some RAM is still unavoidable for execution buffers, KV cache and OS
        # page cache; a true zero-RAM LLM execution mode does not exist here.
        load_mode = "mmap"
        lazy_mode = "on"
        no_warmup = True
        prompt_cache_mb = 0
    else:
        # Smart Hybrid now prefers full process-backed RAM residency whenever
        # the model + runtime fit the safe budget.  mmap is reserved for models
        # that do not fit comfortably, where SSD-backed pages are necessary.
        load_mode = "mmap" if oversized else "none"
        lazy_mode = "off"
        no_warmup = False

    warning = ""
    if memory_mode == "ram_only" and oversized:
        warning = (
            f"Full RAM needs the model and runtime to fit inside the safe RAM budget. "
            f"This GGUF is {model_size:.1f} GB with about {runtime:.1f} GB runtime overhead, "
            f"but the current safe budget is about {budget:.1f} GB."
        )
    elif memory_mode == "ssd_test":
        warning = (
            "SSD Test minimizes model residency and warmup, but it cannot make RAM usage zero: "
            "llama.cpp still needs execution buffers/KV cache and Windows may cache mapped pages. "
            "Expect much lower speed and heavy SSD reads."
        )
    elif oversized:
        ratio = model_size / max(avail, 0.1)
        warning = (
            f"The GGUF ({model_size:.1f} GB) is larger than the safe in-memory budget. "
            f"Smart Hybrid will fall back to llama.cpp memory mapping and the OS page cache (model/free-RAM ratio {ratio:.1f}x). "
            "Hot pages can stay in RAM while cold pages remain SSD-backed; generation can slow down when pages must be read again."
        )
    elif adaptive and requested_ctx and ctx < int(requested_ctx):
        warning = (
            f"Adaptive Engine reduced this launch context from {int(requested_ctx):,} to {ctx:,} tokens to preserve RAM for Windows, KV cache and shared GPU memory. "
            "Your saved context preference was not changed."
        )
    elif memory_mode == "hybrid":
        warning = (
            f"Smart Hybrid selected Full RAM residency because the GGUF ({model_size:.1f} GB) and runtime fit the safe RAM budget. "
            "The process working set plus shared GPU memory should now track model residency more closely than mmap mode."
        )
    elif avail < 4:
        warning = "Less than 4 GB of free RAM is available. Close other applications before loading the model."

    return LaunchPlan(
        profile=p, cpu_only=cpu_only, threads=threads, threads_batch=threads_batch,
        ctx_size=ctx, batch_size=batch, ubatch_size=ubatch,
        cache_type_k=ctk, cache_type_v=ctv, gpu_layers=gpu_layers,
        gpu_layer_percent=gpu_pct, accelerator_mode=accelerator_mode,
        adaptive=adaptive, tuning_source=str(tuning_source or "heuristic"), speculative_mode=str(speculative_mode or "auto"),
        load_mode=load_mode, lazy_mode=lazy_mode, memory_mode=memory_mode, flash_attn="off" if cpu_only else "auto",
        no_warmup=no_warmup,
        parallel=parallel, prompt_cache_mb=prompt_cache_mb,
        cpu_target_percent=target_percent, cpu_poll=cpu_poll, cpu_priority=cpu_priority,
        cpu_strict=cpu_strict, cpu_saturation=bool(cpu_saturation), cpu_affinity_count=logical,
        estimated_model_resident_gb=round(resident, 2), estimated_runtime_gb=round(runtime, 2),
        estimated_total_gb=round(total, 2), available_ram_gb=round(avail, 2), oversized=oversized,
        warning=warning,
    )


def server_args(
    model_path: str,
    plan: LaunchPlan,
    host: str,
    port: int,
    supported_flags: set[str] | None = None,
    chat_template: str | None = None,
    lora_path: str | None = None,
    lora_scale: float = 1.0,
    mmproj_path: str | None = None,
    rpc_servers: list[str] | None = None,
    tensor_split: list[float] | None = None,
    distributed: bool = False,
    split_mode: str | None = None,
) -> list[str]:
    """Build a conservative llama-server command.

    Core flags are intentionally limited to options that have been stable for a
    long time. Newer tuning flags are only emitted when detected in --help.
    """
    known = supported_flags
    args: list[str] = []

    def has(flag: str) -> bool:
        return known is not None and flag in known

    # Stable baseline used even if --help probing failed.
    args += ["--model", model_path, "--host", host, "--port", str(port)]
    args += ["--threads", str(plan.threads), "--ctx-size", str(plan.ctx_size)]
    args += ["--batch-size", str(plan.batch_size), "--ubatch-size", str(plan.ubatch_size)]
    # RPC devices are exposed through llama.cpp's device backend. Even a remote
    # CPU Worker is treated as an offload device, so cluster mode must not force
    # n-gpu-layers=0 merely because the desktop preference says CPU-only.
    args += ["--n-gpu-layers", "-1" if (distributed or plan.accelerator_mode == "gpu" or int(plan.gpu_layers) < 0) else str(max(0, int(plan.gpu_layers)))]
    # In Max Both mode, do not opportunistically move host-side tensor ops to
    # Vulkan.  This preserves useful CPU work while the selected transformer
    # layers still execute on the GPU.  Capability-gated for older runtimes.
    if plan.accelerator_mode == "max_both" and has("--no-op-offload"):
        args += ["--no-op-offload"]

    if rpc_servers:
        if not has("--rpc"):
            raise RuntimeError("The active llama-server does not expose --rpc; activate a cluster-capable llama.cpp runtime")
        args += ["--rpc", ",".join(str(x) for x in rpc_servers if x)]
        if split_mode and has("--split-mode"):
            mode = str(split_mode).lower()
            if mode in {"layer", "row", "tensor", "none"}:
                args += ["--split-mode", mode]
        if tensor_split and has("--tensor-split"):
            args += ["--tensor-split", ",".join(f"{float(x):.6f}" for x in tensor_split)]

    if chat_template and has("--chat-template"):
        args += ["--chat-template", chat_template]
    if mmproj_path:
        if has("--mmproj"):
            args += ["--mmproj", mmproj_path]
        else:
            raise RuntimeError("This vision model needs a llama.cpp runtime with --mmproj support. Update Runtime first.")
    if lora_path:
        if has("--lora-scaled"):
            args += ["--lora-scaled", lora_path, f"{float(lora_scale):.4f}"]
        elif has("--lora"):
            args += ["--lora", lora_path]
        else:
            raise RuntimeError("This llama.cpp runtime does not expose --lora; install a newer compatible runtime")
    if has("--threads-batch"):
        args += ["--threads-batch", str(plan.threads_batch)]
    if has("--cache-type-k"):
        args += ["--cache-type-k", plan.cache_type_k]
    if has("--cache-type-v"):
        args += ["--cache-type-v", plan.cache_type_v]
    if has("--load-mode"):
        args += ["--load-mode", plan.load_mode]
    else:
        # Compatibility with older llama.cpp builds that predate --load-mode.
        if plan.load_mode in ("mmap", "mmap+mlock") and has("--mmap"):
            args += ["--mmap"]
        if plan.load_mode == "none" and has("--no-mmap"):
            args += ["--no-mmap"]
        if plan.load_mode in ("mlock", "mmap+mlock") and has("--mlock"):
            args += ["--mlock"]
    if has("--lazy-mode"):
        args += ["--lazy-mode", plan.lazy_mode]
    # Single-user local chat should not allocate multiple server slots unless asked.
    if has("--parallel"):
        args += ["--parallel", str(plan.parallel)]
    if has("--cache-ram"):
        args += ["--cache-ram", str(plan.prompt_cache_mb)]
    # CPU saturation controls are capability-gated because older llama.cpp
    # builds may not expose them. --poll=100 keeps workers spinning instead of
    # sleeping; --prio=1 is medium priority and is intentionally safer than
    # high/realtime for an interactive desktop.
    if has("--poll"):
        args += ["--poll", str(plan.cpu_poll)]
    if has("--prio"):
        args += ["--prio", str(plan.cpu_priority)]
    if has("--prio-batch"):
        args += ["--prio-batch", str(plan.cpu_priority)]
    if has("--poll-batch"):
        args += ["--poll-batch", "1" if plan.cpu_poll >= 80 else "0"]
    # Capability-gated affinity hints. LlamaForge also applies the process-level
    # OS affinity mask after spawn so older/buggy runtime affinity parsing cannot
    # silently leave a restrictive mask behind.
    strict_mask_valid = plan.cpu_affinity_count >= max(plan.threads, plan.threads_batch)
    if plan.cpu_strict and strict_mask_valid and has("--cpu-range"):
        args += ["--cpu-range", f"0-{max(0, plan.cpu_affinity_count - 1)}"]
    if plan.cpu_strict and strict_mask_valid and has("--cpu-strict"):
        args += ["--cpu-strict", "1"]

    # mmap is the default on normal llama.cpp builds; avoid version-sensitive flags when not needed.
    if plan.no_warmup and has("--no-warmup"):
        args += ["--no-warmup"]
    spec = str(getattr(plan, "speculative_mode", "off") or "off").lower()
    if spec in {"auto", "ngram"}:
        if has("--spec-default"):
            args += ["--spec-default"]
        elif has("--spec-type"):
            args += ["--spec-type", "ngram-mod"]
    if has("--log-timestamps"):
        args += ["--log-timestamps"]
    return args
