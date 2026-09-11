import pytest

from scripts.evaluate_laptop_model import evaluate
from visual_ai_agent.behavior import accepted_label


def test_confidence_gate_maps_uncertain_binary_result_to_unknown():
    np = pytest.importorskip("numpy")
    assert accepted_label(np.array([0.6, 0.4]), {0: "closed", 1: "open"})[0] == "unknown"


def test_confidence_gate_accepts_clear_open_result():
    np = pytest.importorskip("numpy")
    assert accepted_label(np.array([0.1, 0.9]), {0: "closed", 1: "open"})[0] == "open"


def test_confidence_gate_rejects_unexpected_class_map():
    np = pytest.importorskip("numpy")
    with pytest.raises(ValueError, match="Invalid classifier probabilities"):
        accepted_label(np.array([0.1, 0.8]), {0: "closed", 1: "open"})


def test_existing_evaluation_is_preserved(tmp_path):
    output = tmp_path / "result.json"
    output.write_text("preserve")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        evaluate(tmp_path / "model.onnx", tmp_path / "train.json", tmp_path / "data.json", output)
    assert output.read_text() == "preserve"
