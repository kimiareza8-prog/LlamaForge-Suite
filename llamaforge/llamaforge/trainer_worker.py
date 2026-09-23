from __future__ import annotations

import argparse
import faulthandler
import gc
import json
import os
import platform
import shutil
import sys
import time
import traceback
from pathlib import Path


def emit(**kw):
    kw.setdefault("at", time.time())
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def _memory_status() -> dict:
    """Best-effort physical/commit/process memory snapshot for crash diagnostics."""
    gib = 1024 ** 3
    out = {"ram_total_gb": 0.0, "ram_available_gb": 0.0,
           "commit_total_gb": 0.0, "commit_available_gb": 0.0, "process_rss_gb": 0.0}
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX(); stat.dwLength = ctypes.sizeof(stat)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                out.update(ram_total_gb=stat.ullTotalPhys/gib, ram_available_gb=stat.ullAvailPhys/gib,
                           commit_total_gb=stat.ullTotalPageFile/gib, commit_available_gb=stat.ullAvailPageFile/gib)
            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc=PMC(); pmc.cb=ctypes.sizeof(pmc)
            if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                out["process_rss_gb"] = pmc.WorkingSetSize/gib
        else:
            pages=os.sysconf('SC_PHYS_PAGES'); size=os.sysconf('SC_PAGE_SIZE')
            avail_pages=os.sysconf('SC_AVPHYS_PAGES') if 'SC_AVPHYS_PAGES' in os.sysconf_names else 0
            out["ram_total_gb"]=pages*size/gib; out["ram_available_gb"]=avail_pages*size/gib
            if Path('/proc/self/status').is_file():
                for line in Path('/proc/self/status').read_text(errors='ignore').splitlines():
                    if line.startswith('VmRSS:'):
                        out["process_rss_gb"]=int(line.split()[1])*1024/gib; break
    except Exception:
        pass
    return {k: round(float(v), 3) for k,v in out.items()}


def _memory_total_gb() -> float:
    return float(_memory_status().get("ram_total_gb") or 0.0)


def _resource_snapshot(path: str | Path | None = None) -> dict:
    out=_memory_status()
    try:
        target=Path(path or '.').expanduser()
        if target.is_file(): target=target.parent
        usage=shutil.disk_usage(target if target.exists() else Path.cwd())
        out["disk_free_gb"]=round(usage.free/(1024**3),3)
    except Exception:
        out["disk_free_gb"]=0.0
    return out


def _checkpoint_size_gb(path: str | Path) -> tuple[int, float]:
    root=Path(path).expanduser()
    try:
        files=[]
        if root.is_dir():
            files=[x for x in list(root.rglob('*.safetensors'))+list(root.rglob('*.bin')) if 'adapter_model' not in x.name.lower()]
        elif root.is_file(): files=[root]
        return len(files), round(sum(x.stat().st_size for x in files)/(1024**3),3)
    except Exception:
        return 0,0.0


def _use_safe_windows_cpu(device: str, force: bool=False, os_name: str | None=None) -> bool:
    """Avoid native bitsandbytes CPU crashes on Windows.

    The 0.17.0 field log showed the worker dying inside checkpoint load with
    Windows STATUS_ACCESS_VIOLATION before Python could raise an exception. A
    Python try/except cannot recover from that, so Windows CPU training uses a
    half-precision text-only LoRA path instead of bitsandbytes 4-bit loading.
    """
    system=(os_name or platform.system()).lower()
    return bool(force or (str(device).lower()=='cpu' and system=='windows'))


def _effective_safe_cpu_settings(rank: int, alpha: int, max_length: int) -> dict:
    r=max(2,min(int(rank),4)); ml=max(64,min(int(max_length),192))
    return {"rank":r,"alpha":max(r,min(int(alpha),16)),"max_length":ml,
            "target_modules":["q_proj","v_proj"],"threads":max(1,min(2,os.cpu_count() or 1))}


def _atomic_replace_dir(src: Path, dst: Path) -> None:
    old = dst.with_name(dst.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dst.exists():
        dst.replace(old)
    src.replace(dst)
    shutil.rmtree(old, ignore_errors=True)


def _repair_bnb4bit_parameter_classes(model, bnb) -> dict:
    """Restore Params4bit wrappers lost by some CPU checkpoint load paths.

    A serialized bitsandbytes checkpoint stores packed uint8 data plus QuantState.
    On affected Transformers/PyTorch CPU combinations the Linear4bit module and
    its QuantState survive loading, while ``weight`` is replaced by an ordinary
    ``torch.nn.Parameter``. PEFT reads Params4bit metadata before the first
    forward pass, so bitsandbytes' normal lazy repair never gets a chance to run.

    Repair only when the module still has a valid QuantState; this never
    re-quantizes or changes the packed model values.
    """
    nn = getattr(bnb, 'nn', None)
    linear_cls = getattr(nn, 'Linear4bit', None)
    params_cls = getattr(nn, 'Params4bit', None)
    if linear_cls is None or params_cls is None:
        return {'linear4_modules': 0, 'repaired': 0, 'unresolved': 0}
    try:
        from bitsandbytes.nn.modules import fix_4bit_weight_quant_state_from_module as bnb_fix
    except Exception:
        bnb_fix = None

    linear = [m for m in model.modules() if isinstance(m, linear_cls)]
    repaired = 0
    for module in linear:
        weight = getattr(module, 'weight', None)
        if isinstance(weight, params_cls) and getattr(weight, 'quant_state', None) is not None:
            continue
        qstate = getattr(module, 'quant_state', None)
        if qstate is None:
            continue
        try:
            if bnb_fix is not None:
                bnb_fix(module)
            weight = getattr(module, 'weight', None)
            if not isinstance(weight, params_cls):
                data = weight.detach() if hasattr(weight, 'detach') else weight
                module.weight = params_cls(
                    data, requires_grad=False, quant_state=qstate,
                    blocksize=getattr(qstate, 'blocksize', None),
                    compress_statistics=bool(getattr(qstate, 'nested', True)),
                    quant_type=str(getattr(qstate, 'quant_type', 'nf4') or 'nf4'),
                    quant_storage=getattr(module, 'quant_storage', getattr(data, 'dtype', None)),
                    module=module, bnb_quantized=True,
                )
                module.quant_state = qstate
                module.weight.quant_state = qstate
            if isinstance(getattr(module, 'weight', None), params_cls) and getattr(module.weight, 'quant_state', None) is not None:
                repaired += 1
        except Exception:
            # Integrity verification below will report a deterministic failure if
            # this individual layer cannot be restored.
            continue
    unresolved = sum(
        1 for m in linear
        if not isinstance(getattr(m, 'weight', None), params_cls)
        or getattr(getattr(m, 'weight', None), 'quant_state', None) is None
    )
    return {'linear4_modules': len(linear), 'repaired': repaired, 'unresolved': unresolved}


def _repair_bnb4bit_from_safetensors(model, bnb, checkpoint: str | Path) -> dict:
    """Rebuild missing Params4bit/QuantState directly from a local checkpoint.

    Transformers normally feeds packed ``weight.*`` quantization metadata to
    ``Params4bit.from_prequantized`` while loading. On some Windows CPU paths the
    model tensor survives but that metadata is lost before PEFT sees it. Read only
    the small per-layer QuantState tensors from safetensors and reconstruct the
    wrapper. The packed values are never re-quantized.
    """
    root=Path(checkpoint).expanduser()
    if not root.is_dir():
        return {'attempted': 0, 'repaired': 0, 'remaining': 0, 'files': 0}
    nn=getattr(bnb,'nn',None)
    linear_cls=getattr(nn,'Linear4bit',None)
    params_cls=getattr(nn,'Params4bit',None)
    if linear_cls is None or params_cls is None or not hasattr(params_cls,'from_prequantized'):
        return {'attempted': 0, 'repaired': 0, 'remaining': 0, 'files': 0}
    try:
        from safetensors import safe_open
    except Exception:
        return {'attempted': 0, 'repaired': 0, 'remaining': 0, 'files': 0}

    files=[x for x in root.rglob('*.safetensors') if not x.name.lower().startswith('adapter_model.')]
    key_file={}
    for sf in files:
        try:
            with safe_open(str(sf), framework='pt', device='cpu') as h:
                for key in h.keys():
                    key_file.setdefault(str(key),sf)
        except Exception:
            continue
    weight_keys=[k for k in key_file if k.endswith('.weight')]
    modules=[(name,m) for name,m in model.named_modules() if isinstance(m,linear_cls)]
    attempted=repaired=0

    def get_tensor(key):
        sf=key_file.get(key)
        if sf is None: return None
        with safe_open(str(sf), framework='pt', device='cpu') as h:
            return h.get_tensor(key)

    for name,module in modules:
        weight=getattr(module,'weight',None)
        if isinstance(weight,params_cls) and getattr(weight,'quant_state',None) is not None:
            continue
        desired=f'{name}.weight' if name else 'weight'
        weight_key=desired if desired in key_file else ''
        if not weight_key:
            matches=[k for k in weight_keys if k.endswith(desired)]
            if len(matches)==1:
                weight_key=matches[0]
        if not weight_key:
            continue
        stat_keys=[k for k in key_file if k.startswith(weight_key+'.')]
        if not any('bitsandbytes__' in k for k in stat_keys):
            continue
        attempted+=1
        try:
            # Params4bit.from_prequantized/QuantState.from_dict expects keys in
            # the same *module-local* shape Transformers feeds it, e.g.
            # ``weight.absmax`` and ``weight.quant_state.bitsandbytes__nf4``.
            # Passing the full model prefix (``model.layers.0....``) makes
            # QuantState fail to find the single bitsandbytes__ marker and was
            # the reason the previous recovery path still reported every layer
            # as unresolved on Windows CPU.
            module_prefix=(name + '.') if name else ''
            stats={k[len(module_prefix):] if k.startswith(module_prefix) else k:get_tensor(k) for k in stat_keys}
            stats={k:v for k,v in stats.items() if v is not None}
            packed=get_tensor(weight_key)
            if packed is None:
                continue
            target_device=getattr(weight,'device',None) or packed.device
            restored=params_cls.from_prequantized(
                data=packed,
                quantized_stats=stats,
                requires_grad=False,
                device=target_device,
                module=module,
            )
            module.weight=restored
            module.quant_state=restored.quant_state
            repaired+=1
        except Exception:
            continue
    remaining=sum(
        1 for _name,m in modules
        if not isinstance(getattr(m,'weight',None),params_cls)
        or getattr(getattr(m,'weight',None),'quant_state',None) is None
    )
    return {'attempted': attempted, 'repaired': repaired, 'remaining': remaining, 'files': len(files)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--underlying-base', default='')
    ap.add_argument('--data', required=True)
    ap.add_argument('--adapter', required=True)
    ap.add_argument('--device', default='auto')
    ap.add_argument('--rank', type=int, default=8)
    ap.add_argument('--alpha', type=int, default=16)
    ap.add_argument('--lr', type=float, default=1.5e-4)
    ap.add_argument('--steps', type=int, default=8)
    ap.add_argument('--max-length', type=int, default=384)
    ap.add_argument('--trust-remote-code', action='store_true')
    ap.add_argument('--safe-cpu', action='store_true', help='Force native-crash-resistant CPU LoRA backend')
    a = ap.parse_args()
    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        pass
    emit(phase='boot', message='Starting Brain Trainer worker', progress=.01,
         python=sys.version.split()[0], platform=platform.platform(), base=a.base, adapter=a.adapter)

    try:
        import torch
        import transformers
        import peft
        import accelerate
        import safetensors
        # Determine whether this invocation is guaranteed to stay on Windows CPU
        # before importing bitsandbytes itself. The field crash was native code;
        # avoiding the extension entirely is safer than importing it and merely
        # choosing not to use Linear4bit later.
        auto_cpu = (a.device == 'auto' and not torch.cuda.is_available()
                    and not (hasattr(torch, 'xpu') and torch.xpu.is_available())
                    and not (hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()))
        skip_bnb = bool(a.safe_cpu or (os.name == 'nt' and (a.device == 'cpu' or auto_cpu)))
        if skip_bnb:
            bnb = None
            try:
                import importlib.metadata as _im
                bnb_version = _im.version('bitsandbytes') + ' (not imported: safe CPU)'
            except Exception:
                bnb_version = 'not imported: safe CPU'
        else:
            try:
                import bitsandbytes as bnb
                bnb_version = getattr(bnb, '__version__', 'installed')
            except Exception as bnb_exc:
                bnb = None; bnb_version = f'unavailable: {bnb_exc}'
        from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        try:
            from transformers import Gemma3ForCausalLM
        except Exception:
            Gemma3ForCausalLM = None
        from peft import LoraConfig, PeftConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    except Exception as exc:
        emit(phase='dependencies', error=f'Trainer dependencies are missing: {exc}', traceback=traceback.format_exc())
        return 3

    try:
        emit(phase='environment', message='Trainer dependencies verified', progress=.04,
             torch=getattr(torch, '__version__', ''), transformers=getattr(transformers, '__version__', ''),
             peft=getattr(peft, '__version__', ''), accelerate=getattr(accelerate, '__version__', ''),
             bitsandbytes=bnb_version, cuda_available=bool(torch.cuda.is_available()), trust_remote_code=bool(a.trust_remote_code),
             **_resource_snapshot(a.base))

        samples = json.loads(Path(a.data).read_text(encoding='utf-8'))
        if not isinstance(samples, list) or not samples:
            raise RuntimeError('Training batch is empty')
        emit(phase='dataset', message=f'Loaded {len(samples)} training examples', progress=.06,
             examples=len(samples), max_length=a.max_length)

        device = a.device
        if device == 'auto':
            if torch.cuda.is_available(): device = 'cuda'
            elif hasattr(torch, 'xpu') and torch.xpu.is_available(): device = 'xpu'
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available(): device = 'mps'
            else: device = 'cpu'
        safe_cpu=_use_safe_windows_cpu(device, bool(a.safe_cpu))
        safe_cfg=_effective_safe_cpu_settings(a.rank,a.alpha,a.max_length) if safe_cpu else {
            'rank':int(a.rank),'alpha':int(a.alpha),'max_length':int(a.max_length),
            'target_modules':'all-linear','threads':max(1,os.cpu_count() or 1)}
        if safe_cpu:
            try: torch.set_num_threads(int(safe_cfg['threads']))
            except Exception: pass
            try: torch.set_num_interop_threads(1)
            except Exception: pass
        emit(phase='device', message=f'Learning device selected: {device}', progress=.075, device=device,
             safe_cpu=bool(safe_cpu), requested_rank=a.rank, effective_rank=safe_cfg['rank'],
             requested_max_length=a.max_length, effective_max_length=safe_cfg['max_length'],
             torch_threads=safe_cfg['threads'], **_resource_snapshot(a.base))
        if safe_cpu:
            emit(phase='backend', message='Windows CPU safe LoRA enabled: bypassing bitsandbytes native 4-bit loader',
                 progress=.078, backend='windows-cpu-safe-lora', dtype='float16',
                 loader_policy='AutoModelForCausalLM; text-only Gemma3 special-case when detected',
                 target_modules=safe_cfg['target_modules'], reason='native-crash-avoidance', **_resource_snapshot(a.base))

        # Some fine-tuned repositories are PEFT adapter repositories even when they
        # also contain merged weights. Loading those repositories directly through
        # Transformers may auto-load the adapter and make QLoRA/PEFT wrap the same
        # modules twice. Detect that shape first and reconstruct the effective model
        # explicitly: original quantized base + frozen source adapter + trainable
        # personal adapter.
        source_adapter = ''
        load_base = a.base
        source_cfg = None
        try:
            source_cfg = PeftConfig.from_pretrained(a.base)
            candidate = str(getattr(source_cfg, 'base_model_name_or_path', '') or '').strip()
            if candidate:
                source_adapter = a.base
                load_base = str(a.underlying_base or candidate).strip()
                emit(phase='source', message='Detected adapter-based training source', progress=.08,
                     strategy='stacked-adapter', source_adapter=source_adapter, underlying_base=load_base,
                     underlying_base_declared=candidate, underlying_base_local=bool(a.underlying_base))
        except Exception as exc:
            # Normal full checkpoints do not have adapter_config.json; this is not an error.
            emit(phase='source', message='Using standalone trainable checkpoint', progress=.08,
                 strategy='standalone', checkpoint=a.base, probe_note=str(exc)[:240])

        emit(phase='tokenizer', message='Loading tokenizer...', progress=.09)
        tokenizer_source = source_adapter or load_base
        if source_adapter:
            sp=Path(source_adapter)
            if sp.is_dir():
                tokenizer_files=(list(sp.glob('tokenizer*')) + list(sp.glob('*.model')) +
                                 list(sp.glob('vocab.*')) + list(sp.glob('merges.txt')))
                if not tokenizer_files:
                    tokenizer_source=load_base
                    emit(phase='tokenizer', message='Source adapter has no tokenizer; using underlying base tokenizer',
                         progress=.085, tokenizer_source=load_base)
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_source,
                                                   trust_remote_code=bool(a.trust_remote_code), use_fast=True)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        emit(phase='tokenizer', message='Tokenizer ready', progress=.12,
             pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)

        quant = None
        prequantized = False
        base_config = None
        quant_method = ''
        kwargs = {'trust_remote_code': bool(a.trust_remote_code), 'low_cpu_mem_usage': True}
        load_mode = 'full'
        try:
            base_config = AutoConfig.from_pretrained(load_base, trust_remote_code=bool(a.trust_remote_code))
            qcfg = getattr(base_config, 'quantization_config', None)
            prequantized = bool(qcfg)
            if isinstance(qcfg, dict):
                quant_method = str(qcfg.get('quant_method') or '')
            else:
                quant_method = str(getattr(qcfg, 'quant_method', '') or '')
        except Exception as exc:
            emit(phase='model-config', message='Could not pre-inspect base quantization; continuing', progress=.125,
                 warning=str(exc)[:500])

        model_loader=AutoModelForCausalLM
        model_loader_name='AutoModelForCausalLM'
        model_type=str(getattr(base_config,'model_type','') or '').lower() if base_config is not None else ''
        if safe_cpu and model_type=='gemma3' and Gemma3ForCausalLM is not None:
            # Official Transformers supports loading Gemma 3 VLM checkpoints through
            # Gemma3ForCausalLM, skipping the vision tower. That materially lowers
            # memory pressure for this app's text-only continual-learning use case.
            model_loader=Gemma3ForCausalLM; model_loader_name='Gemma3ForCausalLM'

        if safe_cpu:
            if prequantized:
                raise RuntimeError('Windows CPU safe LoRA requires a full (non-prequantized) trainable checkpoint. '
                                   'Select/download the full model source; GGUF remains the chat copy.')
            kwargs['dtype']=torch.float16
            # Default Transformers placement is CPU; omitting Accelerate's
            # device_map keeps the safe backend on the simplest native path.
            quant=None
            load_mode='fp16-safe-cpu-lora'
        elif prequantized:
            kwargs['device_map'] = {'': 0} if device == 'cuda' else {'': device}
            load_mode = 'prequantized-qlora'
            emit(phase='model-config', message='Base already carries a quantization config; re-quantization disabled',
                 progress=.13, load_mode=load_mode)
        elif device in ('cuda', 'xpu', 'cpu') and bnb is not None:
            compute = torch.bfloat16 if device != 'cuda' or torch.cuda.is_bf16_supported() else torch.float16
            quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                       bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=compute)
            kwargs['quantization_config'] = quant
            kwargs['device_map'] = {'': 0} if device == 'cuda' else {'': device}
            load_mode = '4bit-qlora'
        elif device == 'mps':
            kwargs['dtype'] = torch.float16; kwargs['device_map'] = {'': device}; load_mode = 'fp16-lora'
        else:
            kwargs['dtype'] = torch.bfloat16; kwargs['device_map'] = {'': 'cpu'}; load_mode = 'bf16-lora'

        shards, checkpoint_gb=_checkpoint_size_gb(load_base)
        emit(phase='model-load', message=f'Loading underlying trainable base ({load_mode})...', progress=.14,
             load_mode=load_mode, underlying_base=load_base, model_loader=model_loader_name,
             checkpoint_shards=shards, checkpoint_size_gb=checkpoint_gb, **_resource_snapshot(load_base))
        try:
            model = model_loader.from_pretrained(load_base, **kwargs)
        except Exception as exc:
            # Python exceptions can be handled here. Native Windows exceptions are
            # decoded by the parent process and preserved in the per-session log.
            if device == 'cpu' and quant is not None and not prequantized and not source_adapter and not safe_cpu:
                emit(phase='model-load', message='CPU 4-bit load failed; attempting clean BF16 LoRA fallback...',
                     progress=.15, warning=str(exc)[-1200:], **_resource_snapshot(load_base))
                gc.collect()
                fallback = {'trust_remote_code': bool(a.trust_remote_code), 'low_cpu_mem_usage': True,
                            'dtype': torch.bfloat16, 'device_map': {'': 'cpu'}}
                try:
                    model = AutoModelForCausalLM.from_pretrained(load_base, **fallback)
                    quant = None; load_mode = 'bf16-lora-fallback'
                except Exception as exc2:
                    raise RuntimeError('CPU 4-bit QLoRA failed and clean BF16 fallback also failed. '
                                       f'4-bit error: {exc} | BF16 error: {exc2}') from exc2
            else:
                raise RuntimeError(f'Could not load the underlying training base {load_base}: {exc}') from exc

        emit(phase='model-load', message='Underlying trainable base loaded', progress=.20,
             load_mode=load_mode, model_class=type(model).__name__, underlying_base=load_base,
             **_resource_snapshot(load_base))

        # A config can say "bitsandbytes 4-bit" even when the backend loaded the
        # packed tensors correctly but PyTorch/Accelerate replaced Params4bit with
        # ordinary Parameter objects. bitsandbytes can recover those wrappers from
        # Linear4bit.quant_state; do that before PEFT inspects compress_statistics.
        if prequantized and 'bitsandbytes' in quant_method.lower() and bnb is not None:
            repair = _repair_bnb4bit_parameter_classes(model, bnb)
            disk_repair={'attempted':0,'repaired':0,'remaining':repair['unresolved'],'files':0}
            if repair['unresolved']:
                # On affected Transformers/Windows CPU combinations the Linear4bit
                # object itself can lose QuantState too. The checkpoint still
                # contains the serialized weight.* metadata, so reconstruct the
                # exact pre-quantized Params4bit wrapper from disk instead of
                # rejecting a valid model or re-quantizing its packed values.
                disk_repair=_repair_bnb4bit_from_safetensors(model,bnb,load_base)
            unresolved=int(disk_repair.get('remaining',repair['unresolved']))
            if not repair['linear4_modules']:
                raise RuntimeError(
                    'The underlying base declares bitsandbytes 4-bit weights, but no Linear4bit modules were loaded. '
                    'The current bitsandbytes/PyTorch backend is not compatible with this checkpoint.'
                )
            if unresolved:
                raise RuntimeError(
                    f"bitsandbytes 4-bit load left {unresolved} layer(s) without recoverable Params4bit/QuantState metadata. "
                    'LlamaForge also checked the checkpoint metadata directly but could not reconstruct those layers safely; Personal Brain weights were not touched.'
                )
            emit(phase='model-config', message='Verified bitsandbytes 4-bit module integrity', progress=.205,
                 linear4_modules=repair['linear4_modules'], repaired_params4bit=repair['repaired'],
                 repaired_from_checkpoint=int(disk_repair.get('repaired') or 0),
                 checkpoint_files=int(disk_repair.get('files') or 0))
        model.config.use_cache = False
        try:
            model.gradient_checkpointing_enable()
            emit(phase='model-config', message='Gradient checkpointing enabled', progress=.21)
        except Exception as exc:
            emit(phase='model-config', message='Gradient checkpointing unavailable; continuing', progress=.21,
                 warning=str(exc))

        is_kbit = bool(quant is not None or prequantized or getattr(model, 'is_loaded_in_4bit', False)
                       or getattr(model, 'is_loaded_in_8bit', False))
        if is_kbit:
            model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

        adapter_dir = Path(a.adapter)
        personal_exists = (adapter_dir / 'adapter_config.json').is_file()
        personal_name = 'default'

        if source_adapter:
            emit(phase='adapter', message='Loading source fine-tune adapter as frozen knowledge...', progress=.22,
                 source_adapter=source_adapter)
            model = PeftModel.from_pretrained(model, source_adapter, adapter_name='source', is_trainable=False)
            if personal_exists:
                emit(phase='adapter', message='Continuing existing Personal Brain LoRA...', progress=.23)
                model.load_adapter(str(adapter_dir), adapter_name=personal_name, is_trainable=True)
            else:
                emit(phase='adapter', message='Creating Personal Brain LoRA on top of the source adapter...', progress=.23)
                target_modules = safe_cfg['target_modules'] if safe_cpu else (getattr(source_cfg, 'target_modules', None) or 'all-linear')
                cfg = LoraConfig(r=safe_cfg['rank'], lora_alpha=safe_cfg['alpha'], lora_dropout=0.0, bias='none',
                                 task_type='CAUSAL_LM', target_modules=target_modules)
                model.add_adapter(personal_name, cfg)
            try:
                model.base_model.set_adapter(['source', personal_name], inference_mode=False)
            except TypeError:
                model.base_model.set_adapter(['source', personal_name])
            # Keep the source Persian adapter active in forward passes but frozen.
            # Only Personal Brain LoRA parameters are optimized.
            try:
                model.base_model.set_requires_grad('source', False)
                model.base_model.set_requires_grad(personal_name, True)
            except Exception:
                for name, param in model.named_parameters():
                    if '.source.' in name:
                        param.requires_grad = False
                    elif f'.{personal_name}.' in name and ('lora_A' in name or 'lora_B' in name):
                        param.requires_grad = True
            emit(phase='adapter', message='Source adapter active + frozen; Personal Brain adapter active + trainable',
                 progress=.245, active_adapters=['source', personal_name])
        else:
            if personal_exists:
                emit(phase='adapter', message='Continuing existing Personal Brain LoRA...', progress=.23)
                model = PeftModel.from_pretrained(model, str(adapter_dir), adapter_name=personal_name, is_trainable=True)
            else:
                emit(phase='adapter', message='Creating Personal Brain LoRA...', progress=.23)
                cfg = LoraConfig(r=safe_cfg['rank'], lora_alpha=safe_cfg['alpha'], lora_dropout=0.0, bias='none',
                                 task_type='CAUSAL_LM', target_modules=safe_cfg['target_modules'] if safe_cpu else 'all-linear')
                model = get_peft_model(model, cfg, adapter_name=personal_name)

        params = [p for p in model.parameters() if p.requires_grad]
        if not params:
            raise RuntimeError('No trainable Personal Brain LoRA parameters were created')
        trainable_count = sum(int(p.numel()) for p in params)
        total_count = sum(int(p.numel()) for p in model.parameters())
        source_trainable = [n for n, p in model.named_parameters() if p.requires_grad and '.source.' in n]
        if source_trainable:
            raise RuntimeError('Safety check failed: source adapter unexpectedly became trainable')
        emit(phase='adapter', message='Personal Brain LoRA is trainable', progress=.25,
             trainable_parameters=trainable_count, total_parameters=total_count,
             ratio=(trainable_count / total_count if total_count else 0.0), source_adapter=source_adapter or None,
             effective_rank=safe_cfg['rank'], target_modules=safe_cfg['target_modules'], **_resource_snapshot(load_base))
        optim = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)

        def encode(row, idx):
            user = str(row.get('user') or '').strip(); ans = str(row.get('assistant') or '').strip()
            if not user or not ans: return None
            prompt_msgs = [{'role': 'user', 'content': user}]
            full_msgs = prompt_msgs + [{'role': 'assistant', 'content': ans}]
            try:
                ptxt = tokenizer.apply_chat_template(prompt_msgs, tokenize=False, add_generation_prompt=True)
                ftxt = tokenizer.apply_chat_template(full_msgs, tokenize=False, add_generation_prompt=False)
            except Exception as exc:
                emit(phase='tokenize', message=f'Chat template fallback for example {idx+1}', progress=.26,
                     warning=str(exc))
                ptxt = f'User: {user}\nAssistant:'; ftxt = ptxt + ' ' + ans
            prompt_ids = tokenizer(ptxt, add_special_tokens=False, truncation=False)['input_ids']
            full_ids = tokenizer(ftxt, add_special_tokens=False, truncation=False)['input_ids']
            common = 0
            for left, right in zip(prompt_ids, full_ids):
                if left != right: break
                common += 1
            target_ids = full_ids[common:]
            if not target_ids:
                target_ids = tokenizer(ans, add_special_tokens=False, truncation=False)['input_ids']
            if not target_ids:
                emit(phase='tokenize', message=f'Skipping example {idx+1}: assistant target tokenized empty', progress=.26)
                return None
            max_len=max(32,int(safe_cfg['max_length']))
            # Preserve the answer instead of blindly right-truncating the full
            # conversation and accidentally masking every trainable token.
            min_prompt=min(64, max(0, max_len//4), len(prompt_ids))
            target_keep=min(len(target_ids), max_len-min_prompt)
            prompt_keep=min(len(prompt_ids), max_len-target_keep)
            ids=prompt_ids[-prompt_keep:] + target_ids[:target_keep]
            if not ids or target_keep <= 0:
                return None
            input_ids=torch.tensor([ids],dtype=torch.long)
            attention_mask=torch.ones_like(input_ids)
            labels=input_ids.clone(); labels[:, :prompt_keep] = -100
            f={'input_ids':input_ids,'attention_mask':attention_mask,'labels':labels}
            target_dev = next((p.device for p in model.parameters() if p.device.type != 'meta'), torch.device(device))
            return {k: v.to(target_dev) for k, v in f.items()}

        encoded = [x for i, r in enumerate(samples) if (x := encode(r, i))]
        if not encoded:
            raise RuntimeError('No valid training examples after tokenization')
        lengths = [int(x['input_ids'].shape[1]) for x in encoded]
        emit(phase='tokenize', message=f'Prepared {len(encoded)} tokenized examples', progress=.28,
             min_tokens=min(lengths), max_tokens=max(lengths), avg_tokens=round(sum(lengths) / len(lengths), 1),
             effective_max_length=safe_cfg['max_length'], **_resource_snapshot(load_base))

        model.train(); losses = []
        for step in range(max(1, a.steps)):
            batch = encoded[step % len(encoded)]
            optim.zero_grad(set_to_none=True)
            out = model(**batch); loss = out.loss
            if not torch.isfinite(loss):
                raise RuntimeError(f'Non-finite loss at step {step+1}')
            loss.backward(); torch.nn.utils.clip_grad_norm_(params, 1.0); optim.step()
            losses.append(float(loss.detach().cpu()))
            emit(phase='train', message=f'Learning step {step+1}/{a.steps}',
                 progress=.30 + .48 * ((step+1) / max(1, a.steps)), step=step+1, steps=a.steps,
                 loss=round(losses[-1], 6), **_resource_snapshot(load_base))

        tmp_dir = adapter_dir.with_name(adapter_dir.name + '.new')
        shutil.rmtree(tmp_dir, ignore_errors=True); tmp_dir.mkdir(parents=True, exist_ok=True)
        emit(phase='save', message='Saving Personal Brain LoRA...', progress=.80, **_resource_snapshot(adapter_dir))
        if source_adapter:
            model.save_pretrained(str(tmp_dir), safe_serialization=True, selected_adapters=[personal_name])
        else:
            model.save_pretrained(str(tmp_dir), safe_serialization=True, selected_adapters=[personal_name])
        tokenizer.save_pretrained(str(tmp_dir))
        # PEFT stores non-default adapter names in subdirectories. We deliberately
        # use "default", so the converter always sees adapter_config.json at root.
        if not (tmp_dir / 'adapter_config.json').is_file() or not (tmp_dir / 'adapter_model.safetensors').is_file():
            raise RuntimeError('Personal adapter save verification failed')
        _atomic_replace_dir(tmp_dir, adapter_dir)

        avg = sum(losses) / len(losses)
        emit(phase='complete', message='Personal Brain LoRA saved', progress=.84, loss=avg, device=device,
             load_mode=load_mode, adapter=str(adapter_dir), source_adapter=source_adapter or None,
             underlying_base=load_base, effective_rank=safe_cfg['rank'], effective_max_length=safe_cfg['max_length'],
             **_resource_snapshot(adapter_dir))
        return 0
    except Exception as exc:
        emit(phase='failed', error=str(exc), message='Brain training failed', progress=0.0,
             exception_type=type(exc).__name__, traceback=traceback.format_exc(), **_resource_snapshot(a.base))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
