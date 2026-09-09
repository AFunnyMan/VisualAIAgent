import pytest

from scripts.prepare_behavior_data import build, label_at, validate_intervals


def test_unannotated_and_uncertain_frames_do_not_become_negatives():
    intervals = [
        {"start": 1, "end": 2, "label": "drinking"},
        {"start": 2, "end": 3, "label": "unknown"},
    ]
    assert label_at(intervals, 0.9) is None
    assert label_at(intervals, 1) == "drinking"
    assert label_at(intervals, 2) is None
    assert label_at(intervals, 3) is None


@pytest.mark.parametrize(
    "intervals",
    [
        [{"start": 0, "end": float("nan"), "label": "seated"}],
        [{"start": 0, "end": 11, "label": "seated"}],
        [
            {"start": 0, "end": 3, "label": "seated"},
            {"start": 2, "end": 4, "label": "standing"},
        ],
    ],
)
def test_bad_timeline_rejected(intervals):
    with pytest.raises(ValueError):
        validate_intervals(intervals, "posture", 10)


def test_existing_output_preserved(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(ValueError, match="Output"):
        build(tmp_path / "missing.json", output)
    assert sentinel.read_text() == "keep"
