from llamaforge.core.models import LocalModel
from llamaforge.core.smart_chat import choose_profile, classify_prompt, response_quality, recovery_hint


def model(**kw):
    base=dict(path="x.gguf",name="Gemma 4 E4B",size_gb=5.8,quantization="Q6_K",architecture="gemma4",sampling_temperature=1.0,sampling_top_p=.95,sampling_top_k=64)
    base.update(kw)
    return LocalModel(**base)

def test_gemma4_auto_uses_model_defaults_for_general_persian():
    p=choose_profile(model(), [{"role":"user","content":"سلام، درباره این موضوع توضیح بده"}], mode="auto")
    assert p.family == "gemma4"
    assert p.language == "fa"
    assert p.temperature >= .7
    assert p.top_k == 64

def test_auto_detects_coding_without_forcing_gemma_to_greedy():
    p=choose_profile(model(), [{"role":"user","content":"این کد PHP چرا خطا میده؟"}], mode="auto")
    assert p.task == "coding"
    assert .2 <= p.temperature <= .35

def test_reasoning_families_keep_entropy_for_coding():
    q=model(name="Qwen3 Coder", architecture="qwen3", sampling_temperature=None, sampling_top_p=None, sampling_top_k=None)
    p=choose_profile(q, [{"role":"user","content":"fix this python bug"}], mode="auto")
    assert p.task == "coding"
    assert p.temperature >= .55

def test_quality_guard_catches_echo_and_repetition():
    assert response_quality("جوک بگو", "جوک بگو")["ok"] is False
    rep="alpha beta gamma delta "*8
    assert "repetition" in response_quality("something",rep)["issues"]

def test_classify_prompt_translation():
    assert classify_prompt("این متن را ترجمه کن")[0] == "translation"


def test_auto_reasoning_is_task_aware():
    simple=choose_profile(model(), [{"role":"user","content":"سلام حالت چطوره؟"}], mode="auto", reasoning="auto")
    hard=choose_profile(model(), [{"role":"user","content":"این خطا را تحلیل کن و علت ریشه‌ای را پیدا کن"}], mode="auto", reasoning="auto")
    assert simple.effective_reasoning == "off"
    assert hard.effective_reasoning == "on"

def test_quality_guard_catches_template_leak_and_wrong_language():
    q=response_quality("این موضوع را توضیح بده", "<start_of_turn>model this is only English text and it keeps going for a while without Persian characters at all.", task="general", language="fa")
    assert "template_leak" in q["issues"]
    assert "wrong_language" in q["issues"]

def test_recovery_hint_is_language_aware():
    h=recovery_hint(["wrong_language","echo"], "fa")
    assert "فارسی" in h
    assert "تکرار" in h
