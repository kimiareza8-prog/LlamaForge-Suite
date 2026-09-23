from llamaforge.core.hardware import HardwareInfo
from llamaforge.core.models import LocalModel
from llamaforge.core.smart_core import assess_model, candidate_plans


def hw(ram=12.0, cores=4):
    return HardwareInfo('Windows','11','AMD64','Test CPU',cores*2,cores,16.0,ram,[],[])


def model(size=5.8):
    return LocalModel('x.gguf','Gemma',size,'Q6_K','gemma4',131072,'7.5B',1,1,'template')


def test_small_model_gets_fast_or_balanced_plan():
    a = assess_model(model(5.8), hw(12.0), 88, True)
    assert a.recommended_profile in ('Max Speed','Balanced')
    assert a.plan.cpu_only is True
    assert a.score >= 80


def test_oversized_model_gets_low_ram_or_giant():
    a = assess_model(model(48.0), hw(12.0), 88, True)
    assert a.recommended_profile in ('Low RAM','Giant Model (Experimental)')
    assert a.plan.oversized is True
    assert a.score < 60


def test_candidate_plans_are_ranked():
    rows = candidate_plans(model(5.8), hw(12.0), 88, True)
    assert len(rows) >= 3
    assert rows[0][0] >= rows[-1][0]
