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


def test_generic_operation_name_is_normalized_to_its_available_skill():
    catalog=[{"name":"telegram","contract":{"input_schema":{"properties":{"operation":{"enum":["messages","search"]}}}}}]
    skill,args,repair=AgentEngine._normalize_tool_selection(
        "messages",{"chat_ref":"tg:1","limit":5},"Read the selected chat messages.",{"telegram"},catalog,
    )
    assert skill=="telegram"
    assert args["operation"]=="messages"
    assert repair=={"from":"messages","to":"telegram","operation":"messages"}


def test_missing_move_operation_is_recovered_only_when_call_is_unambiguous():
    catalog=[{"name":"workspace_files","contract":{"input_schema":{"properties":{"operation":{"enum":["move","rename","delete"]}}}}}]
    skill,args,repair=AgentEngine._normalize_tool_selection(
        "workspace_files",{"id":"file_1","folder":"archive"},
        "Moving file1.txt to archive_folder.",{"workspace_files"},catalog,
    )
    assert skill=="workspace_files" and args["operation"]=="move"
    assert repair=={"from":"workspace_files","to":"workspace_files","operation":"move"}

    _,ambiguous,no_repair=AgentEngine._normalize_tool_selection(
        "workspace_files",{"id":"file_1"},"Work with this file.",{"workspace_files"},catalog,
    )
    assert "operation" not in ambiguous and no_repair is None




def test_missing_code_job_write_operation_is_recovered_locally():
    catalog=[{"name":"code_job","contract":{"input_schema":{"properties":{"operation":{"enum":["new","write","replace","read","run"]}}}}}]
    skill,args,repair=AgentEngine._normalize_tool_selection(
        "code_job",
        {"job_id":"job_123","path":"calculator.py","content":"print(1)"},
        "Writing the calculator Python code to the existing job.",
        {"code_job"},catalog,
    )
    assert skill=="code_job"
    assert args["operation"]=="write"
    assert repair=={"from":"code_job","to":"code_job","operation":"write"}

    _,ambiguous,no_repair=AgentEngine._normalize_tool_selection(
        "code_job",{"job_id":"job_123","path":"calculator.py"},
        "Work with this file.",{"code_job"},catalog,
    )
    assert "operation" not in ambiguous and no_repair is None

def test_continuous_agent_can_read_think_expand_and_act_across_many_cycles(runtime, monkeypatch):
    executed=[]; prompts=[]
    def execute(name,args,permissions):
        executed.append((name,dict(args)))
        op=args.get('operation')
        if name=='telegram' and op=='resolve_person':
            return json.dumps({'ok':True,'result':{'candidates':[{'candidate_ref':'cand_1','name':'⭐ Ali','username':'ali'}]}},ensure_ascii=False)
        if name=='telegram' and op=='select_person':
            return json.dumps({'ok':True,'result':{'chat_ref':'tg:42','name':'⭐ Ali'}},ensure_ascii=False)
        if name=='telegram' and op=='messages':
            return json.dumps({'ok':True,'result':{'messages':[{'message_id':11,'from_me':False,'text':'فردا کی میای؟'}]}},ensure_ascii=False)
        if name=='calendar' and op=='list':
            return json.dumps({'ok':True,'result':{'events':[{'title':'جلسه','start':'2026-09-28T15:00:00','end':'2026-09-28T16:30:00'}]}},ensure_ascii=False)
        if name=='telegram' and op=='send':
            return json.dumps({'ok':True,'result':{'message_id':12,'readback':{'text':args.get('text')}}},ensure_ascii=False)
        raise AssertionError((name,args))
    monkeypatch.setattr(runtime,'execute',execute)
    replies=[
        {'action':'tool','skill':'telegram','arguments':{'operation':'resolve_person','query':'علی'}},
        {'action':'tool','skill':'telegram','arguments':{'operation':'select_person','candidate_ref':'cand_1'}},
        {'action':'tool','skill':'telegram','arguments':{'operation':'messages','chat_ref':'tg:42','limit':5}},
        {'action':'discover','families':['calendar'],'summary':'Need the real meeting time before replying'},
        {'action':'tool','skill':'calendar','arguments':{'operation':'list'}},
        {'action':'tool','skill':'telegram','arguments':{'operation':'send','chat_ref':'tg:42','text':'فردا بعد از جلسه، حدود ساعت ۱۶:۳۰ میام.'}},
        {'action':'final','answer':'پیام علی را خواندم، زمان جلسه را از تقویم بررسی کردم و پاسخ را فرستادم.'},
    ]
    def model(messages,tools):
        assert tools==[]
        p=messages[0]['content'];prompts.append(p)
        assert 'LLAMAFORGE_AGENT_CONTROL_V3' in p
        assert 'CAPABILITY MAP (always visible)' in p
        i=len(prompts)-1
        if i>=1: assert 'TASK LEDGER' in p
        return {'content':json.dumps(replies[i],ensure_ascii=False)}
    events=list(runtime.run([{'role':'user','content':'تو تلگرام پیام علی رو بخون، تقویمم رو هم ببین و بعد جواب مناسب بده.'}],model,AgentPermissions(),max_steps=10))
    assert [x[0] for x in executed]==['telegram','telegram','telegram','calendar','telegram']
    assert [x[1].get('operation') for x in executed]==['resolve_person','select_person','messages','list','send']
    assert 'فردا کی میای؟' in prompts[3]
    assert 'جلسه' in prompts[5]
    assert 'message_id' in prompts[6]
    assert not any('stage 0 of a local AI agent router' in p or 'stage 1 of a local agent' in p for p in prompts)
    answer=''.join(e.get('delta','') for e in events if e.get('type')=='text')
    assert 'پاسخ را فرستادم' in answer
