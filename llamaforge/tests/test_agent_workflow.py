import json
from types import SimpleNamespace
import pytest
from llamaforge.core import config
from llamaforge.core.agent_engine import AgentEngine
from llamaforge.core.agent_tools import AgentPermissions
from test_skill_audit import runtime


def test_new_install_permissions_enabled_existing_opt_out_preserved(tmp_path, monkeypatch):
    for name in ('APP_DIR', 'DEFAULT_MODEL_DIR', 'RUNTIME_DIR'):
        monkeypatch.setattr(config, name, tmp_path / name)
    monkeypatch.setattr(config, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr(config, '_keyring_store', lambda _: False)
    monkeypatch.setattr(config, '_keyring_get', lambda: None)
    cfg = config.AppConfig.load()
    assert cfg.agent_allow_write and cfg.agent_allow_private_network and cfg.agent_allow_workspace_write
    assert cfg.agent_allow_telegram_read and cfg.agent_allow_telegram_write
    cfg.agent_allow_write = cfg.agent_allow_private_network = cfg.agent_allow_workspace_write = False
    cfg.agent_allow_telegram_read = cfg.agent_allow_telegram_write = False
    cfg.save()
    cfg = config.AppConfig.load()
    assert not any((cfg.agent_allow_write, cfg.agent_allow_private_network, cfg.agent_allow_workspace_write,
                    cfg.agent_allow_telegram_read, cfg.agent_allow_telegram_write))


def test_repaired_router_keeps_families(runtime):
    outputs=iter(['not json', '{"route":"skills","families":["calendar"],"goal":"meeting"}'])
    decision, _=AgentEngine(runtime)._route_decision(lambda *_:{'content':next(outputs)}, 'schedule a meeting', 'schedule a meeting')
    assert decision.families == ['calendar']


def test_guard_preserves_family_without_redundant_capability_call(runtime):
    prompts=[]
    def model(messages, _):
        p=messages[0]['content'];prompts.append(p)
        if 'stage 0' in p:return {'content':'{"route":"direct"}'}
        if 'STEP 1' in p:return {'content':'{"action":"final","answer":"سلام"}'}
        return {'content':'سلام'}
    list(runtime.run([{'role':'user','content':'امروز چندمه؟'}],model,AgentPermissions(),max_steps=1))
    assert not any('capability selector' in p for p in prompts)


@pytest.mark.parametrize('task', ['Write a short story', 'تفاوت تقویم شمسی و میلادی چیست؟', 'Explain Telegram in فارسی'])
def test_direct_route_passes_original_messages_without_catalog(runtime, task):
    messages=[{'role':'user','content':task}]; seen=[]
    runtime.tool_definitions=lambda *_:pytest.fail('direct route must not discover tools')
    def model(msgs, _):return {'content':'{"route":"direct"}'}
    def stream(msgs):
        seen.append(msgs);yield {'type':'text','delta':'Answer'}
    events=list(runtime.run(messages,model,AgentPermissions(),stream_final=stream))
    assert seen == [messages]
    assert not any(e.get('event') in ('capabilities','thinking') for e in events)


def test_permission_payload_rejects_string_false_before_mutation():
    from llamaforge.web.server import LlamaForgeState
    state=object.__new__(LlamaForgeState);state.cfg=config.AppConfig()
    with pytest.raises(ValueError,match='boolean'):
        state.update_settings({'agent_allow_write':'false', 'agent_allow_workspace_write':False})
    assert state.cfg.agent_allow_workspace_write


def test_backend_permission_toggle_persists_both_directions(tmp_path,monkeypatch):
    from llamaforge.web.server import LlamaForgeState
    for name in ('APP_DIR','DEFAULT_MODEL_DIR','RUNTIME_DIR'):
        monkeypatch.setattr(config,name,tmp_path/name)
    monkeypatch.setattr(config,'CONFIG_PATH',tmp_path/'config.json')
    monkeypatch.setattr(config,'_keyring_store',lambda _:False)
    monkeypatch.setattr(config,'_keyring_get',lambda:None)
    state=object.__new__(LlamaForgeState);state.cfg=config.AppConfig()
    state.events=SimpleNamespace(publish=lambda *a:None)
    keys=('agent_allow_write','agent_allow_private_network','agent_allow_workspace_write','agent_allow_telegram_read','agent_allow_telegram_write')
    for value in (False,True):
        state.update_settings({**dict.fromkeys(keys,value),'agent_skill_profile':'telegram_only'})
        loaded=config.AppConfig.load()
        assert all(getattr(loaded,key) is value for key in keys)
        assert loaded.agent_skill_profile=='telegram_only'


def test_stage_zero_exposes_titles_without_tool_schemas():
    prompt=AgentEngine._route_prompt('به علی در تلگرام پیام بده',['telegram'])
    assert '- telegram:' in prompt and '- files:' not in prompt
    assert 'input_schema' not in prompt and 'workspace_files' not in prompt
