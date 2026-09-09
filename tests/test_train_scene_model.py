from pathlib import Path

import pytest
import yaml

from scripts.train_scene_model import (
    EXPECTED_NAMES,
    dataset_fingerprint,
    normalize_names,
    official_names,
)


def make_dataset(tmp_path: Path, label_text: str, *, listed: bool = False) -> Path:
    images = tmp_path / "images" / "train"
    labels = tmp_path / "labels" / "train"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    (images / "sample.jpg").write_bytes(b"image")
    (labels / "sample.txt").write_text(label_text, encoding="utf-8")
    split = "images.txt" if listed else "images/train"
    if listed:
        (tmp_path / "images.txt").write_text("images/train/sample.jpg\n", encoding="utf-8")
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(
        f"path: .\ntrain: {split}\nval: {split}\nnames: [bottle, cup, cell phone]\n",
        encoding="utf-8",
    )
    return dataset


def test_dataset_fingerprint_is_stable_and_sensitive_to_labels(tmp_path: Path) -> None:
    images = tmp_path / "images"
    labels = tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    (images / "sample.jpg").write_bytes(b"image")
    label = labels / "sample.txt"
    label.write_text(
        "0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n2 0.5 0.5 0.2 0.2\n",
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(
        "path: .\ntrain: images\nval: labels\nnames: [bottle, cup, cell phone]\n",
        encoding="utf-8",
    )

    first, count, summary = dataset_fingerprint(dataset)
    assert count == 3
    assert summary["class_instances"] == {0: 1, 1: 1, 2: 1}
    assert dataset_fingerprint(dataset)[0] == first
    label.write_text(
        "0 0.4 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n2 0.5 0.5 0.2 0.2\n",
        encoding="utf-8",
    )
    assert dataset_fingerprint(dataset)[0] != first


def test_names_must_be_exact_three_class_head() -> None:
    assert normalize_names(["bottle", "cup", "cell phone"]) == EXPECTED_NAMES
    assert normalize_names({"0": "bottle", "1": "cup", "2": "cell phone"}) == EXPECTED_NAMES


def test_dataset_rejects_wrong_class_head(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(
        "train: .\nval: .\nnames: [bottle, cup]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly"):
        dataset_fingerprint(dataset)


@pytest.mark.parametrize(
    "bad_row",
    ["0 nan 0.5 0.2 0.2", "0 0.05 0.5 0.2 0.2", "0 0.5 0.95 0.2 0.2"],
)
def test_dataset_rejects_nonfinite_and_out_of_frame_boxes(tmp_path: Path, bad_row: str) -> None:
    dataset = make_dataset(
        tmp_path,
        f"{bad_row}\n1 0.5 0.5 0.2 0.2\n2 0.5 0.5 0.2 0.2\n",
    )
    with pytest.raises(ValueError, match="Invalid normalized box"):
        dataset_fingerprint(dataset)


def test_image_list_fingerprint_includes_parallel_label(tmp_path: Path) -> None:
    dataset = make_dataset(
        tmp_path,
        "0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n2 0.5 0.5 0.2 0.2\n",
        listed=True,
    )
    first = dataset_fingerprint(dataset)[0]
    (tmp_path / "labels/train/sample.txt").write_text(
        "0 0.4 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n2 0.5 0.5 0.2 0.2\n",
        encoding="utf-8",
    )
    assert dataset_fingerprint(dataset)[0] != first


def test_dataset_rejects_download_directive(tmp_path: Path) -> None:
    dataset = make_dataset(
        tmp_path,
        "0 0.5 0.5 0.2 0.2\n1 0.5 0.5 0.2 0.2\n2 0.5 0.5 0.2 0.2\n",
    )
    dataset.write_text(dataset.read_text() + "download: https://example.invalid/data.zip\n")
    with pytest.raises(ValueError, match="download directives"):
        dataset_fingerprint(dataset)


def test_label_decimal_rounding_at_border_is_tolerated(tmp_path: Path) -> None:
    dataset = make_dataset(
        tmp_path,
        "0 0.5 0.5 0.2 0.2\n1 0.8987968750 0.8742037471 0.1361875000 0.2515925059\n"
        "2 0.5 0.5 0.2 0.2\n",
    )
    assert dataset_fingerprint(dataset)[2]["class_instances"][1] == 1


def test_preserve_coco_head_accepts_all_ids_but_requires_business_classes(tmp_path: Path) -> None:
    names = official_names()
    dataset = make_dataset(
        tmp_path,
        "0 0.5 0.5 0.2 0.2\n39 0.5 0.5 0.2 0.2\n41 0.5 0.5 0.2 0.2\n67 0.5 0.5 0.2 0.2\n",
    )
    config = yaml.safe_load(dataset.read_text())
    config["names"] = names
    dataset.write_text(yaml.safe_dump(config, sort_keys=False))
    summary = dataset_fingerprint(dataset, names)[2]
    assert summary["class_instances"][0] == 1
    assert summary["class_instances"][39] == 1
    assert summary["class_instances"][41] == 1
    assert summary["class_instances"][67] == 1


def test_preserve_coco_head_rejects_wrong_mapping(tmp_path: Path) -> None:
    names = official_names()
    dataset = make_dataset(
        tmp_path,
        "39 0.5 0.5 0.2 0.2\n41 0.5 0.5 0.2 0.2\n67 0.5 0.5 0.2 0.2\n",
    )
    wrong = dict(names)
    wrong[39], wrong[41] = wrong[41], wrong[39]
    config = yaml.safe_load(dataset.read_text())
    config["names"] = wrong
    dataset.write_text(yaml.safe_dump(config, sort_keys=False))
    with pytest.raises(ValueError, match="Expected exactly"):
        dataset_fingerprint(dataset, names)
