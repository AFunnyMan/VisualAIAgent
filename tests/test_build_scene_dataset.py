import hashlib
import json

import cv2
import numpy as np
import pytest

from scripts.build_scene_dataset import build


@pytest.mark.parametrize("leak", ["image", "capture_group"])
def test_rejects_cross_split_leakage_before_writing(tmp_path, leak):
    samples = []
    for i, split in enumerate(("train", "val", "test")):
        image = tmp_path / f"{i}.png"
        frame = np.full((20, 20, 3), 0 if leak == "image" else i, np.uint8)
        assert cv2.imwrite(str(image), frame)
        samples.append(
            {
                "id": str(i),
                "image_path": str(image),
                "split": split,
                "group": "one-session" if leak == "capture_group" else str(i),
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "width": 20,
                "height": 20,
                "annotations": [],
            }
        )
    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps({"samples": samples}))
    output = tmp_path / "dataset"
    with pytest.raises(ValueError, match="Duplicate image|group crosses"):
        build([manifest], output)
    assert not output.exists()


def test_preserved_head_retains_nonbusiness_labels_and_official_ids(tmp_path):
    samples = []
    for index, split in enumerate(("train", "val", "test")):
        image = tmp_path / f"{index}.png"
        assert cv2.imwrite(str(image), np.full((20, 20, 3), index, np.uint8))
        samples.append(
            {
                "id": str(index),
                "image_path": str(image),
                "split": split,
                "group": str(index),
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "width": 20,
                "height": 20,
                "annotations": [
                    {"category": category, "bbox": [1, 2, 10, 15]}
                    for category in ("person", "cup", "cell phone")
                ],
            }
        )
    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps({"label_method": "manual", "samples": samples}))
    output = tmp_path / "dataset"
    build([manifest], output, preserve_coco_head=True)
    labels = (output / "labels/train/0.txt").read_text().splitlines()
    assert [int(row.split()[0]) for row in labels] == [0, 41, 67]
    assert len(json.loads((output / "manifest.json").read_text())["names"]) == 80

    samples[0]["annotations"].append({"category": "unknown", "bbox": [1, 2, 10, 15]})
    manifest.write_text(json.dumps({"samples": samples}))
    invalid = tmp_path / "invalid"
    with pytest.raises(ValueError, match="Unknown COCO category"):
        build([manifest], invalid, preserve_coco_head=True)
    assert not invalid.exists()
