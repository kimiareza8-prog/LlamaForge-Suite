"""Failures and cancellation must preserve the user's runtime/calendar state."""
import threading
from types import SimpleNamespace

import pytest

from llamaforge.core.personal_brain import BrainCancelled
from llamaforge.core.workspace import CalendarStore
from llamaforge.web.server import LlamaForgeState
from test_personal_brain import _isolated_brain


class ImmediateThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


def state_for(monkeypatch, tmp_path):
    state = object.__new__(LlamaForgeState)
    state.brain = _isolated_brain(monkeypatch, tmp_path)
    state.active_model = SimpleNamespace(name='A', architecture='llama', size_label='1B', path='A.gguf')
    state._model_lifecycle_lock = threading.RLock()
    state.brain_cancel = threading.Event()
    state.brain_download_cancel = threading.Event()
    state.events = SimpleNamespace(publish=lambda *a: None)
    state.log = state.log_exception = lambda *a: None
    state.brain_status = lambda: {'job': dict(state.brain.job), 'enabled': state.brain.cfg.enabled}
    state.last_launch_payload = {'model_path': 'A.gguf'}
    state.server_proc = SimpleNamespace(running=False)
    monkeypatch.setattr('llamaforge.web.server.threading.Thread', ImmediateThread)
    return state


@pytest.mark.parametrize('error,expected', [(RuntimeError('installer failed'),'error'), (BrainCancelled('stop'),'cancelled')])
def test_setup_failure_publishes_terminal_state(monkeypatch, tmp_path, error, expected):
    state = state_for(monkeypatch, tmp_path)
    def fail(*a, **kw): raise error
    state.brain.prepare_environment = fail
    result = state.prepare_brain_async()
    assert result['job']['state'] == expected
    assert result['job']['error'] == ('' if expected == 'cancelled' else 'installer failed')


@pytest.mark.parametrize('endpoint', ['toggle','settings'])
def test_enabling_brain_cannot_revoke_inflight_cancel(monkeypatch, tmp_path, endpoint):
    state = state_for(monkeypatch, tmp_path)
    state.brain.cfg.enabled = False
    state.brain.job = {'state':'cancelling','stage':'train'}
    state.brain_cancel.set(); state.brain_download_cancel.set()
    if endpoint == 'toggle': state.toggle_brain(True)
    else: state.update_brain({'enabled':True})
    assert state.brain.cfg.enabled
    assert state.brain_cancel.is_set() and state.brain_download_cancel.is_set()


def test_failed_preflight_does_not_start_an_initially_stopped_model(monkeypatch, tmp_path):
    state = state_for(monkeypatch, tmp_path)
    state.brain.cfg.enabled = True
    state.brain_doctor = lambda: {'checks':[{'ok':False,'name':'trainer','detail':'missing'}]}
    starts=[];state.start_server=lambda payload:starts.append(payload)
    result=state.learn_brain_async({'user':'اسم من رضاست.'})
    assert result['job']['state']=='error'
    assert starts == []


def test_preview_rejects_model_switch_during_teacher_compilation(monkeypatch, tmp_path):
    state = state_for(monkeypatch, tmp_path)
    def compile_lesson(*a):
        state.active_model = SimpleNamespace(name='B',architecture='llama',size_label='1B',path='B.gguf')
        return [{'user':'Preferred style?', 'assistant':'concise'}]
    state._brain_synthesize_examples = compile_lesson
    with pytest.raises(RuntimeError, match='model changed'):
        state.preview_brain_lesson({'user':'Remember that I prefer concise replies.'})


@pytest.mark.parametrize('change,expected', [
    ({'time':'12:30'},'2026-09-25T12:30'),
    ({'jalali':'1405-07-04','time':'10:00'},'2026-09-26T10:00'),
    ({'start':'2026-09-27T09:00'},'2026-09-27T09:00'),
])
def test_calendar_reschedule_preserves_duration(tmp_path, change, expected):
    from datetime import datetime
    cal=CalendarStore(tmp_path)
    event=cal.write('create',{'title':'جلسه','start':'2026-09-25T10:00','end':'2026-09-25T11:30'})
    updated=cal.write('update',{'id':event['id'],**change})
    assert updated['start'].startswith(expected)
    assert (datetime.fromisoformat(updated['end'])-datetime.fromisoformat(updated['start'])).total_seconds()==90*60


def test_all_day_default_duration_is_a_day(tmp_path):
    from datetime import datetime
    event=CalendarStore(tmp_path).write('create',{'title':'سفر','start':'2026-09-25','all_day':True})
    assert (datetime.fromisoformat(event['end'])-datetime.fromisoformat(event['start'])).total_seconds()==86400


def test_preview_does_not_inherit_previous_job_cancellation(monkeypatch, tmp_path):
    state = state_for(monkeypatch, tmp_path)
    state.server_ready = True
    state.cfg = SimpleNamespace(host='localhost', port=1234)
    state.brain.cfg.auto_synthesize = True
    state.brain_cancel.set()
    def stream(*args, **kwargs):
        assert kwargs.get('cancel') is None or not kwargs['cancel'].is_set()
        yield {'type':'text','delta':'[{"user":"Style?","assistant":"concise","evidence":"I prefer concise replies.","kind":"preference"}]'}
    monkeypatch.setattr('llamaforge.web.server.stream_chat_events', stream)
    result=state.preview_brain_lesson({'user':'I prefer concise replies.'})
    assert result['should_learn']
    assert state.brain_cancel.is_set()  # An old cancellation is never reset by preview.


@pytest.mark.parametrize('field', ['start','time','jalali','gregorian','relative_date'])
def test_empty_reschedule_fields_are_rejected_without_mutation(tmp_path, field):
    cal=CalendarStore(tmp_path)
    event=cal.write('create',{'title':'جلسه','start':'2026-09-25T10:00'})
    with pytest.raises(ValueError):
        cal.write('update',{'id':event['id'],field:''})
    assert cal.list_events()[0] == event


def test_learning_reload_uses_the_taught_model_not_last_launch(monkeypatch, tmp_path):
    state=state_for(monkeypatch, tmp_path)
    state.brain.cfg.enabled=True
    state.last_launch_payload={'model_path':'old-model.gguf','ctx':4096}
    state.brain_doctor=lambda:{'checks':[]}
    state.brain.learn=lambda *a,**kw:{'generation':1}
    starts=[]
    def start(payload):
        starts.append(payload);state.server_proc.running=True;state.server_ready=True
    state.start_server=start
    result=state.learn_brain_async({'user':'اسم من رضاست.'})
    assert result['job']['state']=='done'
    assert starts[0]['model_path']=='A.gguf'


def test_model_switch_during_preflight_cannot_train_or_restore_old_model(monkeypatch, tmp_path):
    state=state_for(monkeypatch,tmp_path)
    state.brain.cfg.enabled=True
    trained=[];started=[]
    state.brain.learn=lambda *a,**kw:trained.append(a)
    state.start_server=lambda payload:started.append(payload)
    def doctor():
        state.active_model=SimpleNamespace(name='B',architecture='llama',size_label='1B',path='B.gguf')
        return {'checks':[]}
    state.brain_doctor=doctor
    result=state.learn_brain_async({'user':'اسم من رضاست.'})
    assert trained==[] and started==[]
    assert result['job']['state']=='cancelled'
