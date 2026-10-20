"""Synthetic fixtures never access private images, models, APIs, or a camera."""

import hashlib
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.repro_bundle_verify import (
    Catalog,
    compare_event_sequences,
    compare_predictions,
    load_production_model,
    normalized_event,
    production_compatibility,
    replay_bundle,
    verify_bundle,
    verify_fault_sequence,
)


def write_bundle(tmp_path: Path, *, label: str = "0 0.5 0.5 0.2 0.2\n") -> Path:
    bundle = tmp_path / "bundle"
    manifest = {
        "names": {"0": "cup"},
        "counts": {"train": 1},
        "samples": [
            {
                "id": "one",
                "split": "train",
                "image_path": "images/train/one.jpg",
                "label_path": "labels/train/one.txt",
                "sha256": hashlib.sha256(b"image").hexdigest(),
            }
        ],
    }
    contents = {
        "data/set/manifest.json": json.dumps(manifest).encode(),
        "data/set/images/train/one.jpg": b"image",
        "data/set/labels/train/one.txt": label.encode(),
    }
    files = []
    for source, content in contents.items():
        destination = "payload/" + source
        path = bundle / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(
            {
                "source": source,
                "destination": destination,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (bundle / "provenance").mkdir()
    (bundle / "provenance/catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "original_root": "/original",
                "files": files,
                "datasets": [
                    {
                        "id": "set",
                        "task": "objects",
                        "source_dir": "data/set",
                        "manifest": "data/set/manifest.json",
                        "counts": {"train": 1},
                        "validity": "available",
                        "notes": [],
                    }
                ],
                "recipes": [],
            }
        )
    )
    return bundle


def test_complete_bundle_does_not_decode_images(tmp_path):
    report = verify_bundle(write_bundle(tmp_path))
    assert report["status"] == "passed", report
    assert report["files"]["verified"] == 3
    assert report["datasets"][0]["samples"] == 1
    assert report["replay"]["status"] == "not_run"


def test_corrupt_image_fails_file_and_manifest_digest(tmp_path):
    bundle = write_bundle(tmp_path)
    (bundle / "payload/data/set/images/train/one.jpg").write_bytes(b"wrong")
    report = verify_bundle(bundle)
    assert report["status"] == "failed"
    assert any("SHA" in error for error in report["errors"])


@pytest.mark.parametrize("label", ["1 0.5 0.5 0.2 0.2", "0 nan 0.5 0.2 0.2", "0 0.5 0.5 -1 0.2"])
def test_bad_yolo_labels_fail(tmp_path, label):
    report = verify_bundle(write_bundle(tmp_path, label=label))
    assert report["status"] == "failed"
    assert any("YOLO" in error for error in report["errors"])


def test_symlink_is_rejected_even_when_target_inside_bundle(tmp_path):
    bundle = write_bundle(tmp_path)
    path = bundle / "payload/data/set/images/train/one.jpg"
    path.rename(path.with_suffix(".bak"))
    path.symlink_to(path.with_suffix(".bak").name)
    report = verify_bundle(bundle)
    assert report["status"] == "failed"
    assert any("symlink" in error for error in report["errors"])


def test_destination_escape_is_rejected(tmp_path):
    bundle = write_bundle(tmp_path)
    path = bundle / "provenance/catalog.json"
    catalog = json.loads(path.read_text())
    catalog["files"][0]["destination"] = "../escape"
    path.write_text(json.dumps(catalog))
    assert verify_bundle(bundle)["status"] == "failed"


def test_missing_main_label_is_not_excused_by_notes(tmp_path):
    bundle = write_bundle(tmp_path)
    path = bundle / "provenance/catalog.json"
    catalog = json.loads(path.read_text())
    catalog["files"] = [row for row in catalog["files"] if not row["source"].endswith(".txt")]
    catalog["datasets"][0]["notes"] = ["historical labels/train/one.txt missing"]
    path.write_text(json.dumps(catalog))
    assert verify_bundle(bundle)["status"] == "failed"


def test_prediction_tolerances():
    baseline = [{"category": "cup", "bbox": [1, 2, 101, 102], "confidence": 0.8}]
    assert compare_predictions(baseline, baseline, "objects")["passed"]
    assert not compare_predictions(baseline, [], "objects")["passed"]
    assert not compare_predictions(baseline, [{**baseline[0], "confidence": 0.81}], "objects")[
        "passed"
    ]
    a = {"label": "seated", "probabilities": {"seated": 0.8, "empty": 0.2}}
    assert compare_predictions(a, a, "posture")["passed"]
    assert not compare_predictions(a, {**a, "label": "unknown"}, "posture")["passed"]


def revise_manifest(bundle, mutate):
    path = bundle / "payload/data/set/manifest.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))
    catalog_path = bundle / "provenance/catalog.json"
    catalog = json.loads(catalog_path.read_text())
    row = next(row for row in catalog["files"] if row["source"].endswith("manifest.json"))
    row.update(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    catalog_path.write_text(json.dumps(catalog))


def test_counts_cannot_be_replaced_by_catalog_claim(tmp_path):
    bundle = write_bundle(tmp_path)
    revise_manifest(bundle, lambda data: data.update(counts={"train": 2}))
    report = verify_bundle(bundle)
    assert report["status"] == "failed"
    assert any("declared 2, observed 1" in error for error in report["errors"])


def test_named_source_reference_missing_fails_unless_explicit_historical_note(tmp_path):
    bundle = write_bundle(tmp_path)
    revise_manifest(
        bundle, lambda data: data.update(sources={"old": {"path": "/original/data/old.json"}})
    )
    assert verify_bundle(bundle)["status"] == "failed"
    path = bundle / "provenance/catalog.json"
    catalog = json.loads(path.read_text())
    catalog["datasets"][0]["notes"] = ["historical missing data/old.json"]
    path.write_text(json.dumps(catalog))
    report = verify_bundle(bundle)
    assert report["status"] == "passed"
    assert any("old.json" in warning for warning in report["warnings"])


def test_production_probe_preserves_official_and_candidate_hash_contract(tmp_path, monkeypatch):
    bundle = write_bundle(tmp_path)
    data = json.loads((bundle / "provenance/catalog.json").read_text())
    for source in [
        "models/yolo26n-e2e.onnx",
        "model_manifests/yolo26n-e2e.onnx.json",
        "models/candidate.onnx",
    ]:
        path = bundle / "payload" / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test artifact")
        data["files"].append(
            {
                "source": source,
                "destination": "payload/" + source,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    captured = []
    vision = types.ModuleType("visual_ai_agent.vision")
    vision.YoloOnnxDetector = lambda *args, **kwargs: captured.append((args, kwargs)) or object()
    behavior = types.ModuleType("visual_ai_agent.behavior")
    behavior.OnnxClassifier = object
    monkeypatch.setitem(sys.modules, "visual_ai_agent.vision", vision)
    monkeypatch.setitem(sys.modules, "visual_ai_agent.behavior", behavior)
    catalog = Catalog(bundle, data)
    load_production_model(catalog, {"task": "objects"}, "models/yolo26n-e2e.onnx")
    load_production_model(catalog, {"task": "objects"}, "models/candidate.onnx")
    official, candidate = captured[0][1], captured[1][1]
    assert official["verify_manifest"] is True
    assert official["manifest_path"] == bundle / "payload/model_manifests/yolo26n-e2e.onnx.json"
    assert candidate["verify_manifest"] is False
    assert candidate["expected_sha256"] == hashlib.sha256(b"test artifact").hexdigest()
    with pytest.raises(ValueError, match="baseline SHA"):
        load_production_model(catalog, {}, "models/candidate.onnx", reference_root=tmp_path)


def test_historical_laptop_three_class_head_is_load_only():
    probe = {
        "class_metadata": "{0:'closed', 1:'open', 2:'not_visible'}",
        "outputs": [{"shape": [1, 3]}],
    }
    assert production_compatibility(probe)[0] is False
    probe = {"class_metadata": "{0:'closed', 1:'open'}", "outputs": [{"shape": [1, 2]}]}
    assert production_compatibility(probe)[0] is True


def test_replay_never_decodes_sealed_media(tmp_path, monkeypatch):
    bundle = write_bundle(tmp_path)
    data = json.loads((bundle / "provenance/catalog.json").read_text())
    data["replay_samples"] = ["data/behavior-final-test/example.png"]
    project = bundle / "work/project/visual_ai_agent"
    project.mkdir(parents=True)
    (project / "vision.py").write_text("")
    package = types.ModuleType("visual_ai_agent")
    package.__file__ = str(project / "__init__.py")
    monkeypatch.setitem(sys.modules, "visual_ai_agent", package)
    cv2 = types.ModuleType("cv2")

    def forbidden(*args, **kwargs):
        pytest.fail("sealed media decoder was called")

    cv2.imread = forbidden
    cv2.VideoCapture = forbidden
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    monkeypatch.setattr(sys, "path", list(sys.path))
    result = replay_bundle(Catalog(bundle, data))
    assert result["status"] == "failed"
    assert any("refusing sealed/final media" in error for error in result["errors"])


def test_event_comparison_ignores_random_ids_but_keeps_time_and_regions():
    from visual_ai_agent.models import VisualEvent

    origin = datetime(2000, 1, 1, tzinfo=UTC)
    first = VisualEvent(
        kind="appeared",
        category="cup",
        regions=["left"],
        observed_at=origin,
        confirmed_at=origin + timedelta(seconds=2),
        source="replay",
    )
    second = first.model_copy(update={"event_id": "another", "evidence_id": "random"})
    reference = [normalized_event(first, origin)]
    assert compare_event_sequences(reference, [normalized_event(second, origin)])["passed"]
    second = second.model_copy(update={"confirmed_at": origin + timedelta(seconds=3)})
    assert not compare_event_sequences(reference, [normalized_event(second, origin)])["passed"]
    second = first.model_copy(update={"regions": ["right"]})
    assert not compare_event_sequences(reference, [normalized_event(second, origin)])["passed"]


def test_fault_stream_resets_pending_missing_at_the_boundary():
    from visual_ai_agent.events import EventStateMachine
    from visual_ai_agent.models import Detection, SceneObservation

    origin = datetime(2000, 1, 1, tzinfo=UTC)
    machine = EventStateMachine()
    cup = Detection(category="cup", confidence=0.9, bbox=(1, 2, 50, 60), region="left")
    for second in range(7):
        machine.process(
            SceneObservation(
                observed_at=origin + timedelta(seconds=second),
                monotonic_at=second,
                status="running",
                fresh=True,
                detections=[cup] if second < 3 else [],
                source="replay",
            )
        )
    assert machine.is_present("cup")
    faults = verify_fault_sequence(machine, 6.0, origin)
    assert faults["passed"]
    assert all(row["events"] == [] for row in faults["checks"])
    # Resuming absence cannot consume the elapsed fault period as evidence.
    events = machine.process(
        SceneObservation(
            observed_at=origin + timedelta(seconds=47),
            monotonic_at=47,
            status="running",
            fresh=True,
            detections=[],
            source="replay",
        )
    )
    assert events == []
    assert machine.is_present("cup")


def add_fingerprint_recipe(bundle):
    path = bundle / "provenance/catalog.json"
    catalog = json.loads(path.read_text())
    label_source = "data/set/labels/train/one.txt"
    row = next(row for row in catalog["files"] if row["source"] == label_source)
    identity = "/original/" + label_source
    digest = hashlib.sha256(identity.encode() + b"\0" + bytes.fromhex(row["sha256"])).hexdigest()
    record = json.dumps({"dataset_sha256": digest}).encode()
    (bundle / "payload/record.json").write_bytes(record)
    catalog["files"].append(
        {
            "source": "record.json",
            "destination": "payload/record.json",
            "size": len(record),
            "sha256": hashlib.sha256(record).hexdigest(),
        }
    )
    catalog["recipes"] = [
        {
            "id": "train",
            "record": "record.json",
            "validity": "ready",
            "dataset_id": "set",
            "dataset_fingerprint": {
                "algorithm": "yolo-path-sha256-v1",
                "expected": digest,
                "computed": "do not trust this field",
                "matched": False,
                "files": [{"source": label_source, "identity": identity, "sha256": row["sha256"]}],
            },
        }
    ]
    path.write_text(json.dumps(catalog))


def test_changed_valid_yolo_label_cannot_be_hidden_by_refreshed_file_mapping(tmp_path):
    bundle = write_bundle(tmp_path)
    add_fingerprint_recipe(bundle)
    assert verify_bundle(bundle)["status"] == "passed"
    changed = bundle / "payload/data/set/labels/train/one.txt"
    changed.write_text("0 0.4 0.5 0.2 0.2\n")
    path = bundle / "provenance/catalog.json"
    catalog = json.loads(path.read_text())
    row = next(row for row in catalog["files"] if row["source"].endswith("one.txt"))
    row.update(size=changed.stat().st_size, sha256=hashlib.sha256(changed.read_bytes()).hexdigest())
    path.write_text(json.dumps(catalog))
    result = verify_bundle(bundle)
    assert result["files"]["verified"] == result["files"]["total"]
    assert result["datasets"][0]["status"] == "passed"
    assert result["status"] == "failed"
    assert result["recipe_fingerprints"][0]["matched"] is False


def test_invalid_historical_recipe_is_preserved_without_required_fingerprint(tmp_path):
    bundle = write_bundle(tmp_path)
    add_fingerprint_recipe(bundle)
    path = bundle / "provenance/catalog.json"
    catalog = json.loads(path.read_text())
    catalog["recipes"][0].update(validity="invalid", dataset_fingerprint=None)
    path.write_text(json.dumps(catalog))
    result = verify_bundle(bundle)
    assert result["status"] == "passed"
    assert result["recipe_fingerprints"][0]["status"] == "not_required_invalid"
