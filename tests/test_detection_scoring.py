import json

import pytest

from scripts.score_detections import main, score_dataset, score_image


def annotation(category="cup", box=(0, 0, 10, 10), crowd=0):
    return {"category": category, "bbox": box, "iscrowd": crowd}


def prediction(category="cup", box=(0, 0, 10, 10), confidence=0.9):
    return {"category": category, "bbox": box, "confidence": confidence}


def test_duplicates_and_wrong_class_do_not_inflate_true_positives():
    scored = score_image(
        [annotation()], [prediction(), prediction(confidence=0.8), prediction("bottle")]
    )
    assert (scored["cup"]["tp"], scored["cup"]["fp"], scored["cup"]["fn"]) == (1, 1, 0)
    assert scored["bottle"]["fp"] == 1


def test_bad_localization_and_below_threshold_leave_false_negatives():
    scored = score_image(
        [annotation()],
        [prediction(box=(8, 8, 18, 18)), prediction(confidence=0.349)],
    )["cup"]
    assert (scored["tp"], scored["fp"], scored["fn"]) == (0, 1, 1)


def test_crowd_overlap_uses_prediction_area_and_preserves_normal_match():
    scored = score_image(
        [annotation(), annotation(box=(0, 0, 100, 100), crowd=1)],
        [prediction(), prediction(box=(30, 30, 40, 40)), prediction("bottle")],
    )
    assert (scored["cup"]["tp"], scored["cup"]["ignored"], scored["cup"]["fn"]) == (1, 1, 0)
    assert scored["bottle"]["fp"] == 1


def test_missing_image_is_error_not_silently_removed_from_denominator():
    with pytest.raises(ValueError, match="exactly"):
        score_dataset({"samples": [{"id": 1, "annotations": []}]}, [])


def test_duplicate_manifest_image_is_error_not_silently_collapsed():
    sample = {"id": 1, "annotations": []}
    with pytest.raises(ValueError, match="exactly"):
        score_dataset({"samples": [sample, sample]}, [{"id": 1, "detections": []}])


def test_custom_key_category_counts_miss_and_extra_box():
    manifest = {
        "samples": [
            {"id": "positive", "annotations": [annotation("key")]},
            {"id": "negative", "annotations": []},
        ]
    }
    predictions = [
        {"id": "positive", "detections": []},
        {"id": "negative", "detections": [prediction("key")]},
    ]
    scored = score_dataset(manifest, predictions, categories=("key",))["categories"]["key"]
    assert (scored["tp"], scored["fp"], scored["fn"]) == (0, 1, 1)
    assert scored["precision"] == 0
    assert scored["recall"] == 0


def test_cli_keeps_class_calibrated_low_confidence_predictions(tmp_path, monkeypatch):
    manifest, predictions, output = [tmp_path / n for n in ("gt.json", "pred.json", "score.json")]
    manifest.write_text(json.dumps({"samples": [{"id": 1, "annotations": [annotation()]}]}))
    predictions.write_text(
        json.dumps({"images": [{"id": 1, "detections": [prediction(confidence=0.2)]}]})
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "score",
            "--manifest",
            str(manifest),
            "--predictions",
            str(predictions),
            "--output",
            str(output),
            "--confidence",
            "0.05",
        ],
    )
    main()
    assert json.loads(output.read_text())["categories"]["cup"]["tp"] == 1
