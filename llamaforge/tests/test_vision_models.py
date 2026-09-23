from unittest.mock import patch

from llamaforge.core.models import ModelManager, local_model_from_path


def _meta(name="Qwen2-VL-Test"):
    return {
        "name": name, "size_label": "", "quantization": "Q4_K_M",
        "architecture": "qwen2-vl", "context_length": 8192, "metadata": {},
    }


def test_sibling_mmproj_marks_model_as_vision_capable(tmp_path):
    model = tmp_path / "Qwen2-VL-Test-Q4_K_M.gguf"
    projector = tmp_path / "mmproj-Qwen2-VL-Test-f16.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    with patch("llamaforge.core.models.read_metadata", return_value=_meta()):
        row = local_model_from_path(model)
    assert row.vision_capable is True
    assert row.vision_projector == str(projector.resolve())
    assert row.vision_hint is True


def test_model_scan_does_not_expose_mmproj_as_a_model(tmp_path):
    model = tmp_path / "vision-model.gguf"
    projector = tmp_path / "mmproj-vision-model-f16.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    with patch("llamaforge.core.models.read_metadata", return_value=_meta("vision-model")):
        rows = ModelManager().scan([str(tmp_path)])
    assert len(rows) == 1
    assert rows[0].path == str(model.resolve())
    assert rows[0].vision_capable is True


def test_hf_projector_selection_prefers_matching_high_quality_projector():
    rows = [
        {"name": "model-Q4_K_M.gguf", "size": 10},
        {"name": "mmproj-unrelated-q8.gguf", "size": 20},
        {"name": "mmproj-model-f16.gguf", "size": 30},
    ]
    found = ModelManager.associated_mmproj(rows, "model-Q4_K_M.gguf")
    assert found["name"] == "mmproj-model-f16.gguf"
