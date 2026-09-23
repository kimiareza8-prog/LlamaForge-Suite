from pathlib import Path

from llamaforge.core.autotune import AdaptiveTuner
from llamaforge.core.models import LocalModel


def test_autotune_summarize_prefers_faster_decode_with_prompt_weight(tmp_path):
    model = LocalModel(str(tmp_path / "m.gguf"), "m", 4.0, "Q4_K_M", "qwen2", 8192, block_count=40)
    rows = [
        {"n_threads": 2, "n_gpu_layers": 0, "n_batch": 128, "n_ubatch": 64, "n_prompt": 64, "n_gen": 0, "avg_ts": 100.0},
        {"n_threads": 2, "n_gpu_layers": 0, "n_batch": 128, "n_ubatch": 64, "n_prompt": 0, "n_gen": 24, "avg_ts": 8.0},
        {"n_threads": 4, "n_gpu_layers": 4, "n_batch": 256, "n_ubatch": 128, "n_prompt": 64, "n_gen": 0, "avg_ts": 90.0},
        {"n_threads": 4, "n_gpu_layers": 4, "n_batch": 256, "n_ubatch": 128, "n_prompt": 0, "n_gen": 24, "avg_ts": 12.0},
    ]
    got = AdaptiveTuner._summarize(rows, model)
    assert got[0]["threads"] == 4
    assert got[0]["gpu_layers"] == 4
    assert got[0]["gpu_layer_percent"] == 10
    assert got[0]["tg"] == 12.0


def test_autotune_model_key_changes_when_file_changes(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"abc")
    model = LocalModel(str(path), "m", 0.1, "Q4", "qwen2", 4096, "")
    before = AdaptiveTuner.model_key(model)
    path.write_bytes(b"abcdef")
    after = AdaptiveTuner.model_key(model)
    assert before != after
