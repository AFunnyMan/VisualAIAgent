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
