import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from scripts.behavior_preprocess import letterbox_rgb, preprocess_bgr
from scripts.train_behavior_model import inspect_dataset, validate_paths


def make_dataset(root: Path) -> Path:
    samples = []
    for split_index, split in enumerate(("train", "val", "test")):
        for label_index, label in enumerate(("away", "seated")):
            folder = root / split / label
            folder.mkdir(parents=True)
            value = 20 + split_index * 50 + label_index * 10
            cv2.imwrite(str(folder / "sample.jpg"), np.full((8, 12, 3), value, dtype=np.uint8))
            if split != "test":
                image = folder / "sample.jpg"
                samples.append(
                    dict(
                        task=root.name,
                        path=str(image.relative_to(root.parent)),
                        split=split,
                        source_sha256=hashlib.sha256(split.encode()).hexdigest(),
                        sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                    )
                )
    (root.parent / "manifest.json").write_text(json.dumps({"samples": samples}))
    return root


def test_dataset_inventory_excludes_test_content(tmp_path: Path) -> None:
    data = make_dataset(tmp_path / "data")
    first = inspect_dataset(data)
    (data / "test/away/sample.jpg").write_bytes(b"changed and invalid")
    second = inspect_dataset(data)
    assert first == second
    assert first["counts"] == {"train": {"away": 1, "seated": 1}, "val": {"away": 1, "seated": 1}}


def test_train_only_does_not_require_or_inventory_val(tmp_path: Path) -> None:
    data = make_dataset(tmp_path / "data")
    first = inspect_dataset(data, train_only=True)
    for image in (data / "val").rglob("*.jpg"):
        image.write_bytes(b"invalid changed validation data")
    second = inspect_dataset(data, train_only=True)
    assert first == second
    assert first["counts"] == {"train": {"away": 1, "seated": 1}}


def test_dataset_rejects_content_overlap_between_train_and_val(tmp_path: Path) -> None:
    data = make_dataset(tmp_path / "data")
    duplicate = data / "val/away/sample.jpg"
    duplicate.write_bytes((data / "train/seated/sample.jpg").read_bytes())
    with pytest.raises(ValueError, match="both train and val"):
        inspect_dataset(data)


def test_existing_output_is_rejected_before_training(tmp_path: Path) -> None:
    data = make_dataset(tmp_path / "data")
    weights = tmp_path / "yolo26n-cls.pt"
    weights.write_bytes(b"local weights")
    output = tmp_path / "run"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        validate_paths(data, weights, output)
    assert sentinel.read_text() == "preserve"


def test_different_frames_of_same_video_cannot_cross_splits(tmp_path):
    data = make_dataset(tmp_path / "data")
    path = data.parent / "manifest.json"
    manifest = json.loads(path.read_text())
    for row in manifest["samples"]:
        row["source_sha256"] = "a" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Source video group"):
        inspect_dataset(data)


def test_letterbox_preserves_full_geometry_and_matches_bgr_preprocess() -> None:
    bgr = np.zeros((20, 40, 3), dtype=np.uint8)
    bgr[:, :20] = (0, 0, 255)
    bgr[:, 20:] = (0, 255, 0)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    framed = letterbox_rgb(rgb, 32)
    tensor = preprocess_bgr(bgr, 32)
    assert framed.shape == (32, 32, 3)
    assert np.all(framed[:8] == 114) and np.all(framed[24:] == 114)
    assert np.array_equal(tensor[0], framed.transpose(2, 0, 1).astype(np.float32) / 255.0)
    assert tensor.dtype == np.float32
    assert 0.0 <= float(tensor.min()) <= float(tensor.max()) <= 1.0
