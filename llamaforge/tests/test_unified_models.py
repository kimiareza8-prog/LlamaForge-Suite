from pathlib import Path
from types import SimpleNamespace

from llamaforge.web.server import LlamaForgeState, _canonical_model_slug
from llamaforge.core.trainable_models import TrainableModelManager
from llamaforge.trainer_worker import _repair_bnb4bit_from_safetensors


def test_canonical_pairing_prefers_finetune_over_internal_base():
    state = object.__new__(LlamaForgeState)
    state.brain = SimpleNamespace(
        infer_quant_repo=lambda model: "mradermacher/gemma-3-4b-persian-v0-i1-GGUF"
    )
    model = SimpleNamespace(
        name="Gemma 3 4b Persian v0",
        architecture="gemma3",
        path=r"C:\Users\reza\.lmstudio\models\mradermacher\gemma-3-4b-persian-v0-i1-GGUF\model.gguf",
    )
    source = {
        "repo_id": "mshojaei77/gemma-3-4b-persian-v0",
        "architecture": "gemma3",
        "checkpoint_kind": "peft_adapter",
        "internal_dependency": False,
    }
    underlying = {
        "repo_id": "unsloth/gemma-3-4b-it-unsloth-bnb-4bit",
        "architecture": "gemma3",
        "checkpoint_kind": "full_model",
        "internal_dependency": True,
    }
    assert _canonical_model_slug("gemma-3-4b-persian-v0-i1-GGUF") == "gemma-3-4b-persian-v0"
    assert state._trainable_pair_score(model, source) >= 120
    assert state._trainable_pair_score(model, source) > state._trainable_pair_score(model, underlying)


def test_hf_snapshot_uses_repo_name_not_hash(tmp_path):
    p = tmp_path / "hub" / "models--unsloth--gemma-3-4b-it-unsloth-bnb-4bit" / "snapshots" / "316726ca0bd24aa323bf"
    p.mkdir(parents=True)
    assert TrainableModelManager._repo_id_from_local_path(p) == "unsloth/gemma-3-4b-it-unsloth-bnb-4bit"


def test_disk_quantstate_repair_uses_serialized_metadata(tmp_path):
    import torch
    from safetensors.torch import save_file

    save_file({
        "layer.weight": torch.tensor([1, 2, 3, 4], dtype=torch.uint8),
        "layer.weight.absmax": torch.tensor([1.0]),
        "layer.weight.quant_map": torch.arange(16, dtype=torch.float32),
        "layer.weight.quant_state.bitsandbytes__nf4": torch.tensor([1, 2, 3], dtype=torch.uint8),
    }, str(tmp_path / "model.safetensors"))

    class Linear4bit:
        def __init__(self):
            self.weight = SimpleNamespace(device=torch.device("cpu"))
            self.quant_state = None

    class Params4bit:
        def __init__(self, data=None, quant_state=None):
            self.data = data
            self.quant_state = quant_state

        @classmethod
        def from_prequantized(cls, data, quantized_stats, requires_grad=False, device="cpu", module=None, **kwargs):
            assert "weight.quant_state.bitsandbytes__nf4" in quantized_stats
            assert "layer.weight.quant_state.bitsandbytes__nf4" not in quantized_stats
            out = cls(data.to(device), quant_state=object())
            if module is not None:
                module.quant_state = out.quant_state
            return out

    module = Linear4bit()

    class Model:
        def named_modules(self):
            return [("layer", module)]

    bnb = SimpleNamespace(nn=SimpleNamespace(Linear4bit=Linear4bit, Params4bit=Params4bit))
    result = _repair_bnb4bit_from_safetensors(Model(), bnb, tmp_path)
    assert result["attempted"] == 1
    assert result["repaired"] == 1
    assert result["remaining"] == 0
    assert isinstance(module.weight, Params4bit)


def test_validation_rejects_stale_hf_cache_dependency(tmp_path):
    cache=tmp_path/'hub'/'models--unsloth--gemma-3-4b-it-unsloth-bnb-4bit'/'snapshots'/'abc'
    cache.mkdir(parents=True)
    (cache/'config.json').write_text('{"model_type":"gemma3"}',encoding='utf-8')
    (cache/'model.safetensors').write_bytes(b'w')
    state=object.__new__(LlamaForgeState)
    state.active_model=SimpleNamespace(name='Gemma 3 4b Persian v0',architecture='gemma3',path=str(tmp_path/'model.gguf'))
    state.trainables=TrainableModelManager(tmp_path/'managed')
    state.brain=SimpleNamespace(resolve_training_base=lambda model:{'ok':True,'repo':'mshojaei77/gemma-3-4b-persian-v0'})
    import pytest
    with pytest.raises(RuntimeError, match='different base'):
        state._validate_trainable_base_match(str(cache))


def test_curated_qwen_quant_prefers_sub_1gb_q4_k_m():
    state = object.__new__(LlamaForgeState)
    state.models = SimpleNamespace(
        list_gguf_files=lambda repo: [
            {"name": "Qwen2.5-1.5B-Instruct-Q8_0.gguf", "size": 1_650_000_000},
            {"name": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "size": 986_000_000},
            {"name": "Qwen2.5-1.5B-Instruct-IQ4_XS.gguf", "size": 896_000_000},
        ],
        shard_group=lambda rows, name: [r for r in rows if r["name"] == name],
    )
    from llamaforge.web.server import QUICK_MODELS
    name, group = state._quick_quant(QUICK_MODELS["qwen2.5-1.5b-instruct"])
    assert name == "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    assert group[0]["size"] < 1_000_000_000


def test_curated_bundle_marker_resolves_exact_trainable_repo(tmp_path):
    from llamaforge.core.personal_brain import PersonalBrain
    gguf_dir = tmp_path / "bundle" / "gguf"
    gguf_dir.mkdir(parents=True)
    gguf = gguf_dir / "gemma.gguf"
    gguf.write_bytes(b"not-read-in-this-test")
    (gguf_dir / ".llamaforge-source.json").write_text(
        __import__('json').dumps({
            "repo_id": "unsloth/gemma-3-4b-it-GGUF",
            "logical_model_id": "unsloth/gemma-3-4b-it",
            "training_repo": "unsloth/gemma-3-4b-it",
            "bundle_id": "gemma-3-4b-it",
        }),
        encoding="utf-8",
    )
    brain = object.__new__(PersonalBrain)
    model = SimpleNamespace(path=str(gguf))
    assert brain.infer_quant_repo(model) == "unsloth/gemma-3-4b-it"


def test_quick_qwen_bundle_download_binds_chat_and_training(monkeypatch, tmp_path):
    import json
    import threading as py_threading
    import llamaforge.web.server as ws

    class ImmediateThread:
        def __init__(self, target=None, **kwargs):
            self.target = target
        def start(self):
            if self.target:
                self.target()

    chat_file = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    class FakeModels:
        def list_gguf_files(self, repo):
            assert repo == "bartowski/Qwen2.5-1.5B-Instruct-GGUF"
            return [{"name": chat_file, "size": 986_000_000}]
        def shard_group(self, rows, name):
            return rows
        def download_many(self, repo, files, dest, progress_cb=None, stop_event=None):
            d = Path(dest); d.mkdir(parents=True, exist_ok=True)
            p = d / chat_file; p.write_bytes(b"ggg")
            if progress_cb: progress_cb(3, 3)
            return [p]

    train_root = tmp_path / "LlamaForgeModels"
    class FakeTrainables:
        def repo_local_dir(self, repo):
            return train_root / repo.replace('/', '--')
        def local_checkpoint_ready(self, path):
            p = Path(path)
            return (p/'config.json').is_file() and (p/'model.safetensors').is_file()
        def download_snapshot(self, repo, **kwargs):
            assert repo == 'Qwen/Qwen2.5-1.5B-Instruct'
            d = self.repo_local_dir(repo); d.mkdir(parents=True, exist_ok=True)
            (d/'config.json').write_text('{"model_type":"qwen2"}', encoding='utf-8')
            (d/'model.safetensors').write_bytes(b'w')
            progress = kwargs.get('progress')
            if progress: progress('Downloading model.safetensors', 1.0, 1, 1)
            return str(d)

    model = SimpleNamespace(path="", name="Qwen2.5 1.5B Instruct", architecture="qwen2")
    updates=[]
    brain = SimpleNamespace(lock=py_threading.RLock(), job={},
                            update=lambda payload, m: updates.append(dict(payload)),
                            activate_model=lambda m: None,
                            set_training_base=lambda m, p: setattr(brain, 'training_base', p))
    state = object.__new__(LlamaForgeState)
    state.lock = py_threading.RLock(); state.job = {"state":"idle"}; state.model_download_cancel = py_threading.Event()
    state.events = SimpleNamespace(publish=lambda *a, **k: None)
    state.training_models_root = train_root; state.models = FakeModels(); state.trainables = FakeTrainables(); state.brain = brain
    state.cfg = SimpleNamespace(model_dirs=[], save=lambda: None)
    state.scan_models = lambda: None
    def select(path):
        model.path = path; state.active_model = model; return {}
    state.select_model = select; state.active_model = None
    state.brain_status = lambda: {}
    state.setup_brain_automatically = lambda: {}
    state.log = lambda *a, **k: None; state.log_exception = lambda *a, **k: None

    monkeypatch.setattr(ws.threading, 'Thread', ImmediateThread)
    result = state.download_quick_model_async('qwen2.5-1.5b-instruct')
    assert result['state'] == 'done'
    assert result['chat_ready'] is True
    marker = json.loads((Path(result['result_path']).parent/'.llamaforge-source.json').read_text(encoding='utf-8'))
    assert marker['training_repo'] == 'Qwen/Qwen2.5-1.5B-Instruct'
    assert Path(brain.training_base).name == 'Qwen--Qwen2.5-1.5B-Instruct'
    assert updates and updates[0]['rank'] == 4 and updates[0]['micro_steps'] == 3 and updates[0]['max_length'] == 128


def test_curated_bundle_resolve_stops_at_explicit_training_repo(tmp_path):
    from llamaforge.core.personal_brain import PersonalBrain
    d = tmp_path / 'gguf'; d.mkdir()
    g = d / 'gemma.gguf'; g.write_bytes(b'x')
    (d/'.llamaforge-source.json').write_text(__import__('json').dumps({
        'repo_id':'unsloth/gemma-3-4b-it-GGUF',
        'training_repo':'unsloth/gemma-3-4b-it',
    }), encoding='utf-8')
    brain=object.__new__(PersonalBrain)
    result=brain.resolve_training_base(SimpleNamespace(path=str(g)))
    assert result['ok'] is True
    assert result['repo']=='unsloth/gemma-3-4b-it'
    assert result['explicit_bundle'] is True
