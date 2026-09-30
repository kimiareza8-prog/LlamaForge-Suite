import threading
import time

import pytest

from llamaforge.core.automation_engine import AutomationEngine, CURRENT_AUTOMATION_ID
from llamaforge.core.agent_tools import AgentPermissions


def wait_for(predicate, timeout=3.0):
    end=time.time()+timeout
    while time.time()<end:
        value=predicate()
        if value:
            return value
        time.sleep(0.02)
    return predicate()


def make_engine(tmp_path, runner=None):
    engine=AutomationEngine(tmp_path/'automations')
    if runner is not None:
        engine.start(runner)
    return engine


def test_interval_run_state_history_and_persistence(tmp_path):
    calls=[]
    def runner(row,event,cancel):
        calls.append((row['id'],event))
        return {'output':'ok','state_patch':{'counter':len(calls)}}
    engine=make_engine(tmp_path, runner)
    row=engine.create({'name':'watch','task':'one bounded check','trigger_type':'interval','interval_seconds':60},permissions={'allow_write':True},available_tools=['web_search','telegram','automation'])
    assert row['schedule']['schedule_mode']=='fixed_delay'
    assert 'automation' not in row['allowed_tools']
    engine.run_now(row['id'],{'manual_value':7})
    assert wait_for(lambda: engine.status(row['id']).get('last_status')=='success')
    state=engine.get_state(row['id'])
    assert state['counter']==1 and state['last_run_status']=='success'
    hist=engine.history(row['id'])
    assert hist and hist[0]['status']=='success' and hist[0]['event']['manual_value']==7
    engine.close()

    reopened=AutomationEngine(tmp_path/'automations')
    restored=reopened.status(row['id'])
    assert restored['state']['counter']==1
    assert restored['task']=='one bounded check'
    reopened.close()


def test_event_filter_dedup_and_coalesce_preserves_latest_event(tmp_path):
    started=threading.Event(); release=threading.Event(); seen=[]
    def runner(row,event,cancel):
        seen.append(event.get('value'))
        if len(seen)==1:
            started.set(); release.wait(1.5)
        return {'output':str(event.get('value'))}
    engine=make_engine(tmp_path, runner)
    row=engine.create({'name':'events','task':'handle event','trigger_type':'event','event_name':'demo.received','event_filter':{'kind':'private'},'overlap_policy':'coalesce','max_retries':0},permissions={},available_tools=['telegram'])
    assert engine.emit_event('demo.received',{'kind':'group','value':0},event_id='x0')['matched']==0
    assert engine.emit_event('demo.received',{'kind':'private','value':1},event_id='x1')['matched']==1
    assert started.wait(1)
    assert engine.emit_event('demo.received',{'kind':'private','value':2},event_id='x2')['matched']==1
    assert engine.emit_event('demo.received',{'kind':'private','value':3},event_id='x3')['matched']==1
    # duplicate id is ignored
    assert engine.emit_event('demo.received',{'kind':'private','value':99},event_id='x3')['matched']==0
    release.set()
    assert wait_for(lambda: len(seen)>=2)
    assert seen==[1,3]
    engine.close()


def test_delay_completes_and_disables(tmp_path):
    engine=make_engine(tmp_path, lambda row,event,cancel:{'output':'done'})
    row=engine.create({'task':'once','trigger_type':'delay','delay_seconds':1,'max_retries':0},permissions={},available_tools=[])
    assert wait_for(lambda: engine.status(row['id']).get('status')=='completed', timeout=3)
    done=engine.status(row['id'])
    assert not done['enabled'] and done['last_status']=='success'
    engine.close()


def test_limits_and_self_scope(tmp_path):
    engine=AutomationEngine(tmp_path/'automations')
    with pytest.raises(ValueError):
        engine.create({'task':'x'*12001,'trigger_type':'interval','interval_seconds':60},permissions={},available_tools=[])
    row=engine.create({'task':'safe','trigger_type':'event','event_name':'a'},permissions={},available_tools=[])
    with pytest.raises(ValueError):
        engine.set_state(row['id'],{'big':'x'*70000})
    token=CURRENT_AUTOMATION_ID.set(row['id'])
    try:
        state=engine.tool({'operation':'set_state','state':{'ok':1}},permissions={},available_tools=[])
        assert state['state']['ok']==1
        with pytest.raises(PermissionError):
            engine.tool({'operation':'create','task':'nested','trigger_type':'event','event_name':'b'},permissions={},available_tools=[])
    finally:
        CURRENT_AUTOMATION_ID.reset(token)
    engine.close()
