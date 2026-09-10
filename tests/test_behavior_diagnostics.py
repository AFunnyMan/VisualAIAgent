import json

from scripts.behavior_diagnostics import TransitionEvidence


def sample(i, fresh=True):
    return {"fresh": fresh, "monotonic_at": i / 10, "frame_sequence": i}


def test_exact_frames_bounded_before_and_after_trigger(tmp_path):
    recorder = TransitionEvidence(tmp_path, max_groups=1)
    for i in range(50):
        recorder.observe(sample(i), f"frame-{i}".encode(), i in (5, 30))
    files = sorted(tmp_path.rglob("*.jpg"))
    assert len(files) == 9
    assert len(recorder.groups) == 1
    for path in files:
        metadata = json.loads(path.with_suffix(".json").read_text())
        sequence = metadata["observation"]["frame_sequence"]
        assert 1 <= sequence <= 9
        assert path.read_bytes() == f"frame-{sequence}".encode()


def test_fault_discards_pre_fault_context(tmp_path):
    recorder = TransitionEvidence(tmp_path)
    for i in range(4):
        recorder.observe(sample(i), b"before", False)
    recorder.observe(sample(4, False), b"stale", True)
    recorder.observe(sample(5), b"after", True)
    files = list(tmp_path.rglob("*.jpg"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"after"
