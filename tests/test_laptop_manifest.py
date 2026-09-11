import hashlib
import json

import pytest

from visual_ai_agent.behavior import load_behavior_manifest


def test_laptop_manifest_requires_exact_classes_and_verified_weights(tmp_path):
    weights = tmp_path / "model.onnx"
    weights.write_bytes(b"test-contract-only")
    record = {
        "status": "completed",
        "preprocessing": {
            "layout": "NCHW",
            "dtype": "float32",
            "color": "RGB",
            "resize": "aspect-ratio-preserving letterbox",
            "range": [0.0, 1.0],
            "fill_rgb": [114, 114, 114],
        },
        "dataset": {"classes": {"0": "closed", "1": "open"}},
        "onnx": "model.onnx",
        "onnx_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(record))
    assert load_behavior_manifest(path, "laptop")["_names"] == {0: "closed", 1: "open"}
    with pytest.raises(ValueError, match="class mapping"):
        load_behavior_manifest(path, "posture")
    record["dataset"]["classes"]["2"] = "partial"
    path.write_text(json.dumps(record))
    assert load_behavior_manifest(path, "laptop")["_names"][2] == "partial"
    record["dataset"]["classes"]["3"] = "partial"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="class mapping"):
        load_behavior_manifest(path, "laptop")
    del record["dataset"]["classes"]["3"]
    path.write_text(json.dumps(record))
    weights.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA mismatch"):
        load_behavior_manifest(path, "laptop")
