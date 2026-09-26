import json
from types import SimpleNamespace
import pytest
from llamaforge.core.workspace import FileWorkspace
from llamaforge.core.redaction import redact
from test_personal_brain import _isolated_brain
from test_skill_audit import runtime


def test_php_empty_workspace_index_is_accepted(tmp_path):
    ws=FileWorkspace(tmp_path)
    ws.import_snapshot({'index':{'items':[]},'folders':['رضا'],'files':[]})
    assert ws._index()['items']=={} and (ws.files_root/'رضا').is_dir()


@pytest.mark.parametrize('items', [[{'id':'bad'}],None,'bad'])
def test_invalid_workspace_index_still_rejected(tmp_path,items):
    with pytest.raises(ValueError):FileWorkspace(tmp_path).import_snapshot({'index':{'items':items}})


def test_redaction_preserves_token_metrics_but_removes_quoted_secrets():
    result=redact({'max_tokens':42,'prompt_tokens':30,'token_id':3,'owner_key':'SECRET_OWNER',
                   'text':'password="two secret words" n_tokens = 42\nCookie: a=SECRET_COOKIE; b=ANOTHER'})
    assert result['max_tokens']==42 and result['prompt_tokens']==30 and result['token_id']==3
    assert 'n_tokens = 42' in result['text']
    for secret in ('SECRET_OWNER','two secret words','SECRET_COOKIE','ANOTHER'):assert secret not in str(result)


def test_brain_doctor_rejects_prequantized_source_with_intel_gpu(monkeypatch,tmp_path):
    brain=_isolated_brain(monkeypatch,tmp_path)
    base=tmp_path/'source';base.mkdir()
    (base/'config.json').write_text(json.dumps({'quantization_config':{'quant_method':'bitsandbytes','load_in_4bit':True}}))
    (base/'model.safetensors').write_text('fixture');(base/'tokenizer.json').write_text('{}')
    py=tmp_path/'python.exe';py.touch();brain.trainer_python=lambda:py
    brain.training_base_for=lambda model:str(base);brain.training_base_ready=lambda model:True;brain.toolchain_ready=lambda:True
    brain._doctor_dependencies_cache=(__import__('time').time(),{'ok':True,'detail':json.dumps({'cuda':False,'mps':False})})
    hw=SimpleNamespace(os_name='Windows',gpus=[SimpleNamespace(name='Intel HD 530')],ram_total_gb=16,ram_available_gb=9,physical_cores=2,logical_cores=4,cpu='CPU')
    check=next((r for r in brain.doctor(hw=hw)['checks'] if r['name']=='cpu_checkpoint_format'),None)
    assert check and not check['ok'] and 'full' in check['detail'].lower()


def test_remote_cancellation_is_not_swallowed():
    import threading
    from llamaforge.web.server import LlamaForgeState
    from llamaforge.core.remote_apps import RemoteTaskCancelled
    state=object.__new__(LlamaForgeState);state.active_model=SimpleNamespace(path='model.gguf')
    state.server_ready=True;state._remote_inference_lock=threading.RLock()
    state.agent=SimpleNamespace(workspace_scope=lambda:'local',set_workspace_scope=lambda scope:None)
    state.log=lambda *a:None
    calls=[]
    def stream(payload):
        yield {'type':'text','delta':'first'}
        calls.append('incorrect continuation')
    state.chat_stream=stream
    def emit(event):raise RemoteTaskCancelled('Remote user cancelled')
    with pytest.raises(RemoteTaskCancelled):state.run_remote_app_task({}, {'user_message':'test'},emit)
    assert not calls


def test_rejected_file_snapshot_does_not_replace_calendar(runtime):
    runtime.calendar.write('create',{'title':'Keep','start':'2026-09-26T10:00'})
    before=runtime.calendar.snapshot()
    with pytest.raises(ValueError):runtime.import_workspace_snapshot({'calendar':{'events':[]},'files':{'index':{'items':'bad'}}})
    assert runtime.calendar.snapshot()==before


@pytest.mark.parametrize('promotion_fails',[False,True])
def test_runtime_install_never_removes_previous_build(tmp_path,monkeypatch,promotion_fails):
    import io
    import zipfile
    from pathlib import Path
    from llamaforge.core import runtime as mod
    mgr=mod.RuntimeManager(str(tmp_path));old=tmp_path/'b11195';old.mkdir()
    binary=mgr._exe('llama-server');(old/binary).write_text('old running build')
    pointer=tmp_path/'installed.json';pointer.write_text(json.dumps({'tag':'b11195','path':str(old)}))
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as archive:archive.writestr(binary,'new build')
    class Response(io.BytesIO):headers={}
    monkeypatch.setattr(mod,'open_url',lambda *_a,**_k:Response(buf.getvalue()))
    monkeypatch.setattr(mgr,'status',lambda:{})
    original=Path.replace
    def replace(p,target):
        if promotion_fails and p.name.endswith('.installing'):raise PermissionError('Windows antivirus lock')
        return original(p,target)
    monkeypatch.setattr(Path,'replace',replace)
    if promotion_fails:
        with pytest.raises(PermissionError):mgr._install_release({'tag':'b11195'},{'name':'test.zip','url':'https://example.com/test.zip'})
        assert json.loads(pointer.read_text())['path']==str(old)
    else:
        mgr._install_release({'tag':'b11195'},{'name':'test.zip','url':'https://example.com/test.zip'})
        assert json.loads(pointer.read_text())['path']!=str(old)
    assert (old/binary).read_text()=='old running build'


def test_brain_tokenizer_config_alone_is_not_a_tokenizer(monkeypatch,tmp_path):
    brain=_isolated_brain(monkeypatch,tmp_path)
    base=tmp_path/'source';base.mkdir()
    (base/'config.json').write_text('{}');(base/'model.safetensors').write_text('fixture')
    (base/'tokenizer_config.json').write_text('{"tokenizer_class":"PreTrainedTokenizerFast"}')
    brain.training_base_for=lambda _:str(base)
    result=brain.doctor()
    check=next(c for c in result['checks'] if c['name']=='tokenizer')
    assert not check['ok'] and check['level']=='error'
