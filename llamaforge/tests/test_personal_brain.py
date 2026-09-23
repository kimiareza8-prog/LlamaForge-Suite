import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llamaforge.core.personal_brain import PersonalBrain
from llamaforge.web.server import LlamaForgeState


def test_identity_fact_generates_recall_examples():
    rows = PersonalBrain.deterministic_examples("اسم من رضاست.")
    assert len(rows) >= 3
    assert all(r["assistant"] == "رضا" for r in rows)
    assert any("اسم" in r["user"] for r in rows)


def test_model_name_correction_generates_weight_recall_examples():
    rows = PersonalBrain.deterministic_examples("نه، اسمت رضاست.")
    assert len(rows) >= 3
    assert all(r["assistant"] == "رضا" for r in rows)
    assert any(r["user"] == "اسمت چیه؟" for r in rows)
    # Asking a question without teaching an answer must never create a target.
    assert PersonalBrain.deterministic_examples("اسمت چیه؟") == []


def test_learning_packet_never_uses_model_answer_as_target(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    brain.cfg.keep_training_archive = False
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    packet = brain.append_packet(
        model,
        "نه، اسمت رضاست.",
        "من یک مدل زبانی گوگل هستم.",
        PersonalBrain.deterministic_examples("نه، اسمت رضاست."),
    )
    assert packet["examples"]
    assert all(e["assistant"] == "رضا" for e in packet["examples"])
    assert all("گوگل" not in e["assistant"] for e in packet["examples"])


def test_zero_context_chat_sends_only_latest_user_turn():
    state = object.__new__(LlamaForgeState)
    state.server_ready = True
    state.brain = SimpleNamespace(cfg=SimpleNamespace(enabled=True, zero_context=True))
    state.active_model = None
    state.cfg = SimpleNamespace(host="127.0.0.1", port=8080)
    captured = {}
    state._prepare_chat_messages = lambda messages, profile: (messages, {"input_tokens": 4, "trimmed_turns": 0, "target": 100})
    profile = SimpleNamespace(
        temperature=.2, top_p=.9, top_k=40, min_p=.05, repeat_penalty=1.05,
        max_tokens=128, effective_reasoning="off", reasoning_budget=0, task="general", language="fa",
        to_dict=lambda: {"task":"general"},
    )
    def fake_stream(host, port, messages, **kwargs):
        captured["messages"] = messages
        yield {"type":"text", "delta":"رضا"}
    payload = {"messages":[
        {"role":"user","content":"اسم من رضاست"},
        {"role":"assistant","content":"خوشبختم"},
        {"role":"user","content":"اسم من چیه؟"},
    ]}
    with patch("llamaforge.web.server.choose_profile", return_value=profile), \
         patch("llamaforge.web.server.stream_chat_events", side_effect=fake_stream), \
         patch("llamaforge.web.server.response_quality", return_value={"ok":True,"issues":[]}):
        events = list(state.chat_stream(payload))
    assert captured["messages"] == [{"role":"user","content":"اسم من چیه؟"}]
    assert any(e.get("type") == "meta" and e.get("brain",{}).get("zero_context") for e in events)


def test_zero_context_repair_never_injects_hidden_text():
    state = object.__new__(LlamaForgeState)
    state.server_ready = True
    state.brain = SimpleNamespace(cfg=SimpleNamespace(enabled=True, zero_context=True))
    state.active_model = None
    state.cfg = SimpleNamespace(host="127.0.0.1", port=8080)
    captured = {}
    state._prepare_chat_messages = lambda messages, profile: (messages, {"input_tokens": 4, "trimmed_turns": 0, "target": 100})
    profile = SimpleNamespace(
        temperature=.2, top_p=.9, top_k=40, min_p=.05, repeat_penalty=1.05,
        max_tokens=128, effective_reasoning="off", reasoning_budget=0, task="general", language="fa", notes=(),
        to_dict=lambda: {"task":"general"},
    )
    def fake_stream(host, port, messages, **kwargs):
        captured["messages"] = messages
        yield {"type":"text", "delta":"رضا"}
    payload = {
        "messages":[{"role":"user","content":"قدیمی"},{"role":"assistant","content":"x"},{"role":"user","content":"اسمم چیه؟"}],
        "repair": True,
        "repair_issues": ["echo"],
    }
    with patch("llamaforge.web.server.choose_profile", return_value=profile), \
         patch("llamaforge.web.server.recovery_hint", side_effect=AssertionError("must not inject hint")), \
         patch("llamaforge.web.server.stream_chat_events", side_effect=fake_stream), \
         patch("llamaforge.web.server.response_quality", return_value={"ok":True,"issues":[]}):
        list(state.chat_stream(payload))
    assert captured["messages"] == [{"role":"user","content":"اسمم چیه؟"}]


def test_one_click_brain_setup_detects_base_and_prepares(monkeypatch):
    state = object.__new__(LlamaForgeState)
    state.active_model = SimpleNamespace(name="Gemma test", architecture="gemma3", size_label="4B", path="x.gguf")
    state.events = SimpleNamespace(publish=lambda *a, **k: None)
    state.log = lambda *a, **k: None

    class FakeBrain:
        def __init__(self):
            import threading
            self.lock = threading.RLock()
            self.job = {"state":"idle","stage":"","message":"","error":"","progress":0.0}
            self.base = ""
            self.trainer = False
            self.toolchain = False
            self.cfg = SimpleNamespace(enabled=False, zero_context=True, strict_learning=True, auto_synthesize=True, allow_remote_code=False)
        def status(self, model=None):
            return {"job":dict(self.job), "training_base_ready":bool(self.base), "trainer_ready":self.trainer,
                    "toolchain_ready":self.toolchain, "setup_ready":bool(self.base and self.trainer and self.toolchain)}
        def update(self, payload, model=None):
            for k,v in payload.items(): setattr(self.cfg,k,v)
        def training_base_ready(self, model=None): return bool(self.base)
        def resolve_training_base(self, model): return {"ok":True,"repo":"org/exact-base"}
        def set_training_base(self, model, value): self.base=value
        def trainer_ready(self): return self.trainer
        def toolchain_ready(self): return self.toolchain
        def prepare_environment(self, progress, force=False, cancel=None):
            progress("Installing learning engine…", .5)
            self.trainer=True; self.toolchain=True
            progress("Ready", 1.0)

    state.brain = FakeBrain()
    import tempfile
    td=tempfile.TemporaryDirectory()
    local=Path(td.name)/"exact-base"; local.mkdir(); (local/"config.json").write_text("{}",encoding="utf-8"); (local/"model.safetensors").write_bytes(b"x")
    class FakeTrainables:
        def local_checkpoint_ready(self,p): return (Path(p)/"config.json").is_file() and bool(list(Path(p).glob("*.safetensors")))
        def inspect(self,repo): return {"trainable":True,"repo_id":repo,"reason":"ok"}
        def download_snapshot(self,repo,**kwargs): return str(local)
    state.trainables=FakeTrainables()
    state.brain_download_cancel=threading.Event()
    state.brain_cancel=threading.Event()
    state.hw=SimpleNamespace(ram_total_gb=16,ram_available_gb=12,physical_cores=2,logical_cores=4,cpu="CPU",gpus=[])
    state.brain.training_base_for=lambda model=None: state.brain.base
    state.brain.doctor=lambda model=None,hw=None: {"ok":True,"checks":[]}
    state.brain_doctor=lambda: {"ok":True,"checks":[]}

    class ImmediateThread:
        def __init__(self, target, **kwargs): self.target=target
        def start(self): self.target()
    monkeypatch.setattr("llamaforge.web.server.threading.Thread", ImmediateThread)

    result = state.setup_brain_automatically()
    assert state.brain.base == str(local)
    td.cleanup()
    assert state.brain.cfg.enabled is False
    assert state.brain.job["state"] == "done"
    assert state.brain.job["stage"] == "ready"
    assert result["setup_ready"] is True


def test_hardware_info_cpu_only_has_legacy_gpu_name_property():
    from llamaforge.core.hardware import HardwareInfo
    hw = HardwareInfo("Windows", "11", "AMD64", "CPU", 4, 2, 16, 6, [], [])
    assert hw.gpu_name == ""
    assert hw.has_gpu is False


def test_hardware_info_primary_gpu_name_is_derived():
    from llamaforge.core.hardware import HardwareInfo, GPUInfo
    hw = HardwareInfo("Windows", "11", "AMD64", "CPU", 4, 2, 16, 6, [GPUInfo("RTX Test", "NVIDIA/CUDA", 8)], [])
    assert hw.gpu_name == "RTX Test"
    assert hw.has_gpu is True


def _isolated_brain(monkeypatch, tmp_path):
    import llamaforge.core.personal_brain as pb
    root = tmp_path / "brain"
    monkeypatch.setattr(pb, "BRAIN_ROOT", root)
    monkeypatch.setattr(pb, "BRAIN_CONFIG", root / "config.json")
    monkeypatch.setattr(pb, "BRAIN_ARCHIVE", root / "training_archive.jsonl")
    monkeypatch.setattr(pb, "BRAIN_ENV", root / "trainer-env")
    monkeypatch.setattr(pb, "BRAIN_TOOLCHAIN", root / "llama-toolchain")
    monkeypatch.setattr(pb, "BRAIN_BATCH", root / "current_batch.json")
    monkeypatch.setattr(pb, "BRAIN_READY_MARKER", root / "trainer-env" / ".llamaforge-ready")
    monkeypatch.setattr(pb, "BRAIN_LOG_DIR", root / "logs")
    monkeypatch.setattr(pb, "BRAIN_LAST_TRAINER", root / "last-trainer.json")
    return pb.PersonalBrain(tmp_path / "app")


def test_failed_archive_packets_are_not_counted_or_replayed(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    key = brain.model_key(model)
    replay = brain.replay_path_for_key(key)
    replay.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id":"failed-1","model_key":key,"examples":[{"user":"bad","assistant":"bad-a"}]},
        {"id":"learned-1","model_key":key,"examples":[{"user":"old","assistant":"old-a"}]},
    ]
    replay.write_text("\n".join(__import__('json').dumps(x) for x in rows)+"\n", encoding="utf-8")
    brain._set_learned_ids(key, {"learned-1"})
    current={"id":"current","model_key":key,"examples":[{"user":"new","assistant":"new-a"}]}
    batch=brain.build_training_batch(model,current)
    assert [x["user"] for x in batch] == ["new","old"]
    assert brain._count_replay(key) == 1


def test_generation_zero_migrates_old_failed_packets_as_not_learned(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    key=brain.model_key(model)
    replay=brain.replay_path_for_key(key); replay.parent.mkdir(parents=True, exist_ok=True)
    replay.write_text('\n'.join(__import__('json').dumps({"id":f"p{i}","model_key":key,"examples":[]}) for i in range(3))+'\n',encoding='utf-8')
    brain._save_profile_meta(model,{"generation":0,"last_loss":None,"last_learned_at":0,"training_base":""})
    assert brain._count_replay(key) == 0


def test_learning_transaction_rolls_back_unconfirmed_adapter(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    old_meta={"generation":2,"last_loss":1.2,"last_learned_at":10,"training_base":"base"}
    brain._save_profile_meta(model, old_meta)
    current=brain.adapter_dir(model); current.mkdir(parents=True)
    (current/'adapter_config.json').write_text('{}',encoding='utf-8')
    (current/'adapter_model.safetensors').write_bytes(b'old')
    brain.adapter_gguf(model).write_bytes(b'old-gguf')
    cand=brain.candidate_adapter_dir(model); cand.mkdir(parents=True)
    (cand/'adapter_config.json').write_text('{}',encoding='utf-8')
    (cand/'adapter_model.safetensors').write_bytes(b'new')
    brain.candidate_adapter_gguf(model).write_bytes(b'new-gguf')
    brain._commit_learning_candidate(model,'packet-x',old_meta)
    brain._save_profile_meta(model,{**old_meta,"generation":3})
    assert (brain.adapter_dir(model)/'adapter_model.safetensors').read_bytes() == b'new'
    assert brain.rollback_unconfirmed_learning(model) is True
    assert (brain.adapter_dir(model)/'adapter_model.safetensors').read_bytes() == b'old'
    assert brain.adapter_gguf(model).read_bytes() == b'old-gguf'
    assert brain._profile_meta(model)["generation"] == 2
    assert not brain.transaction_path(model).exists()


def test_confirm_learning_marks_packet_only_after_reload(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    key=brain.model_key(model)
    old_meta={"generation":0,"last_loss":None,"last_learned_at":0,"training_base":"base"}
    cand=brain.candidate_adapter_dir(model); cand.mkdir(parents=True)
    (cand/'adapter_config.json').write_text('{}',encoding='utf-8')
    (cand/'adapter_model.safetensors').write_bytes(b'new')
    brain.candidate_adapter_gguf(model).write_bytes(b'new-gguf')
    brain._commit_learning_candidate(model,'packet-ok',old_meta)
    assert brain._count_replay(key) == 0
    brain.confirm_learning(model)
    assert brain._count_replay(key) == 1
    assert not brain.transaction_path(model).exists()


def test_crash_after_transaction_intent_preserves_confirmed_pair(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    meta={"generation":4,"last_loss":0.5,"last_learned_at":20,"training_base":"base"}
    brain._save_profile_meta(model, meta)
    cur=brain.adapter_dir(model); cur.mkdir(parents=True)
    (cur/'adapter_config.json').write_text('{}',encoding='utf-8')
    (cur/'adapter_model.safetensors').write_bytes(b'confirmed')
    brain.adapter_gguf(model).write_bytes(b'confirmed-gguf')
    # Simulate a hard process death immediately after the transaction intent was
    # persisted, before either confirmed object was moved to rollback storage.
    brain.transaction_path(model).parent.mkdir(parents=True,exist_ok=True)
    brain.transaction_path(model).write_text(__import__('json').dumps({
        'packet_id':'p','had_adapter':True,'had_gguf':True,'previous_meta':meta,'state':'promoting'
    }),encoding='utf-8')
    assert brain.rollback_unconfirmed_learning(model) is True
    assert (brain.adapter_dir(model)/'adapter_model.safetensors').read_bytes()==b'confirmed'
    assert brain.adapter_gguf(model).read_bytes()==b'confirmed-gguf'
    assert brain._profile_meta(model)['generation']==4


def test_crash_after_only_adapter_backup_restores_adapter_and_keeps_gguf(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    meta={"generation":1,"last_loss":0.8,"last_learned_at":10,"training_base":"base"}
    brain._save_profile_meta(model, meta)
    cur=brain.adapter_dir(model); cur.mkdir(parents=True)
    (cur/'adapter_config.json').write_text('{}',encoding='utf-8')
    (cur/'adapter_model.safetensors').write_bytes(b'old-adapter')
    brain.adapter_gguf(model).write_bytes(b'old-gguf')
    # Simulate current adapter having been moved aside, while GGUF has not yet moved.
    cur.replace(brain.rollback_adapter_dir(model))
    brain.transaction_path(model).write_text(__import__('json').dumps({
        'packet_id':'p','had_adapter':True,'had_gguf':True,'previous_meta':meta,'state':'promoting'
    }),encoding='utf-8')
    assert brain.rollback_unconfirmed_learning(model) is True
    assert (brain.adapter_dir(model)/'adapter_model.safetensors').read_bytes()==b'old-adapter'
    assert brain.adapter_gguf(model).read_bytes()==b'old-gguf'


def test_rollback_discards_first_generation_candidate_when_no_previous_pair(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    meta={"generation":0,"last_loss":None,"last_learned_at":0,"training_base":"base"}
    cur=brain.adapter_dir(model); cur.mkdir(parents=True)
    (cur/'adapter_config.json').write_text('{}',encoding='utf-8')
    (cur/'adapter_model.safetensors').write_bytes(b'unconfirmed')
    brain.adapter_gguf(model).write_bytes(b'unconfirmed-gguf')
    brain.transaction_path(model).parent.mkdir(parents=True,exist_ok=True)
    brain.transaction_path(model).write_text(__import__('json').dumps({
        'packet_id':'p','had_adapter':False,'had_gguf':False,'previous_meta':meta,'state':'pending-reload'
    }),encoding='utf-8')
    assert brain.rollback_unconfirmed_learning(model) is True
    assert not brain.adapter_dir(model).exists()
    assert not brain.adapter_gguf(model).exists()


def test_bake_restores_inference_server_even_when_merge_fails(monkeypatch, tmp_path):
    state = object.__new__(LlamaForgeState)
    model_path=tmp_path/'model.gguf'; model_path.write_bytes(b'model')
    adapter=tmp_path/'brain.gguf'; adapter.write_bytes(b'adapter')
    state.active_model=SimpleNamespace(name='Gemma',path=str(model_path))
    state.runtime=SimpleNamespace(find_binary=lambda name: str(tmp_path/'llama-export-lora.exe'))
    Path(state.runtime.find_binary('x')).write_bytes(b'x')
    state.server_proc=SimpleNamespace(running=True)
    state.last_launch_payload={'model_path':str(model_path),'profile':'Max Speed','ctx':8192,'cpu_only':True}
    state.events=SimpleNamespace(publish=lambda *a,**k:None)
    state.log=lambda *a,**k:None
    state.log_exception=lambda *a,**k:None
    lock=threading.RLock()
    class Brain:
        def __init__(self):
            self.lock=lock; self.job={'state':'idle'}
        def adapter_gguf(self,model): return adapter
        def bake_merged(self,*a,**k): raise RuntimeError('merge-test-failure')
    state.brain=Brain()
    state.brain_status=lambda:{'job':dict(state.brain.job)}
    stopped=[]; started=[]
    def stop_server(reason='manual'):
        stopped.append(reason); state.server_proc.running=False
    def start_server(payload):
        started.append(dict(payload)); state.server_proc.running=True
    state.stop_server=stop_server; state.start_server=start_server
    class ImmediateThread:
        def __init__(self,target,**kwargs): self.target=target
        def start(self): self.target()
    monkeypatch.setattr('llamaforge.web.server.threading.Thread',ImmediateThread)
    result=state.bake_brain_async({})
    assert stopped==['brain-bake']
    assert started and started[0]['ctx']==8192
    assert state.server_proc.running is True
    assert state.brain.job['state']=='error'
    assert 'merge-test-failure' in state.brain.job['error']
    assert result['job']['state']=='error'


def test_brain_master_power_preserves_advanced_preferences(monkeypatch, tmp_path):
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    brain.cfg.zero_context = False
    brain.cfg.strict_learning = False
    brain.cfg.auto_synthesize = False
    brain.cfg.device = "cpu"
    brain.update({"enabled": True}, model)
    assert brain.cfg.enabled is True
    assert brain.cfg.zero_context is False
    assert brain.cfg.strict_learning is False
    assert brain.cfg.auto_synthesize is False
    assert brain.cfg.device == "cpu"


def test_turning_brain_off_requests_safe_cancellation():
    state = object.__new__(LlamaForgeState)
    state.active_model = None
    state.brain_cancel = threading.Event()
    state.brain_download_cancel = threading.Event()
    state.events = SimpleNamespace(publish=lambda *a, **k: None)
    state.log = lambda *a, **k: None
    lock = threading.RLock()

    class Brain:
        def __init__(self):
            self.cfg = SimpleNamespace(enabled=True)
            self.lock = lock
            self.job = {"state": "running", "stage": "trainer", "message": "Training", "error": "", "progress": .4}
        def update(self, payload, model=None):
            if "enabled" in payload:
                self.cfg.enabled = bool(payload["enabled"])
        def status(self, model=None):
            return {"enabled": self.cfg.enabled, "job": dict(self.job)}

    state.brain = Brain()
    state.training_models_root = Path("/tmp/brain-models")
    result = state.update_brain({"enabled": False})
    assert result["enabled"] is False
    assert state.brain_cancel.is_set()
    assert state.brain_download_cancel.is_set()
    assert state.brain.job["state"] == "cancelling"
    assert "Stopping" in state.brain.job["message"]


def test_cancelled_learning_never_commits_candidate(monkeypatch, tmp_path):
    import llamaforge.core.personal_brain as pb
    brain = _isolated_brain(monkeypatch, tmp_path)
    model = SimpleNamespace(name="Gemma", architecture="gemma3", size_label="4B")
    brain.cfg.enabled = True
    cancel = threading.Event()
    candidate = brain.candidate_adapter_dir(model)
    committed = []
    converted = []

    monkeypatch.setattr(brain, "append_packet", lambda *a, **k: {"id":"p1","model_key":brain.model_key(model),"examples":[{"user":"u","assistant":"a"}]})
    monkeypatch.setattr(brain, "build_training_batch", lambda *a, **k: [{"user":"u","assistant":"a"}])
    monkeypatch.setattr(brain, "_prepare_learning_candidate", lambda *a, **k: candidate)
    def fake_train(*a, **k):
        cancel.set()
        return {"loss": .1}
    monkeypatch.setattr(brain, "_run_trainer", fake_train)
    monkeypatch.setattr(brain, "_convert_adapter", lambda *a, **k: converted.append(True))
    monkeypatch.setattr(brain, "_commit_learning_candidate", lambda *a, **k: committed.append(True))

    try:
        brain.learn(model, "u", "a", [], cancel=cancel)
        assert False, "expected BrainCancelled"
    except pb.BrainCancelled:
        pass
    assert converted == []
    assert committed == []
    assert brain._profile_meta(model).get("generation", 0) == 0


def test_trainer_repairs_lost_params4bit_wrapper_without_requantizing():
    from llamaforge.trainer_worker import _repair_bnb4bit_parameter_classes

    class QuantState:
        blocksize = 64
        nested = True
        quant_type = 'nf4'

    class PackedWeight:
        dtype = 'uint8'
        def detach(self):
            return self

    class Params4bit:
        def __init__(self, data, **kwargs):
            self.data = data
            self.quant_state = kwargs.get('quant_state')
            self.compress_statistics = kwargs.get('compress_statistics')
            self.bnb_quantized = kwargs.get('bnb_quantized')

    class Linear4bit:
        quant_storage = 'uint8'
        def __init__(self):
            self.weight = PackedWeight()
            self.quant_state = QuantState()

    class NN:
        pass
    NN.Linear4bit = Linear4bit
    NN.Params4bit = Params4bit

    class BNB:
        nn = NN

    layer = Linear4bit()
    class Model:
        def modules(self):
            return [self, layer]

    report = _repair_bnb4bit_parameter_classes(Model(), BNB())
    assert report == {'linear4_modules': 1, 'repaired': 1, 'unresolved': 0}
    assert isinstance(layer.weight, Params4bit)
    assert layer.weight.quant_state is layer.quant_state
    assert layer.weight.bnb_quantized is True


def test_windows_native_trainer_exit_code_is_decoded():
    from llamaforge.core.personal_brain import _decode_trainer_exit_code
    info = _decode_trainer_exit_code(3221225477, "Windows")
    assert info["hex"] == "0xC0000005"
    assert info["name"] == "STATUS_ACCESS_VIOLATION"
    assert info["native_crash"] is True


def test_weight_load_progress_maps_to_model_load_range():
    from llamaforge.core.personal_brain import _weight_load_progress
    assert _weight_load_progress("Loading weights:  21%|##") == __import__('pytest').approx(.1526, abs=.0001)
    assert _weight_load_progress("not a progress line") is None


def test_windows_cpu_uses_safe_trainer_policy():
    from llamaforge.trainer_worker import _use_safe_windows_cpu, _effective_safe_cpu_settings
    assert _use_safe_windows_cpu("cpu", False, "Windows") is True
    assert _use_safe_windows_cpu("cuda", False, "Windows") is False
    cfg = _effective_safe_cpu_settings(8, 16, 384)
    assert cfg["rank"] == 4
    assert cfg["max_length"] == 192
    assert cfg["target_modules"] == ["q_proj", "v_proj"]
    assert cfg["threads"] <= 2
