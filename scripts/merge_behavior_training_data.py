#!/usr/bin/env python3
"""Merge reviewed behavior datasets while preserving sample-level provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def merge(manifests: list[Path], output: Path, task: str = "posture") -> dict:
    if output.exists():
        raise ValueError("Output must not exist")
    if len(manifests) < 2:
        raise ValueError("At least two input manifests are required")

    records: list[tuple[Path, dict]] = []
    source_groups: dict[str, str] = {}
    input_records: list[dict] = []
    image_hashes: set[str] = set()
    for manifest_path in manifests:
        manifest_path = manifest_path.resolve()
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_records.append({"path": str(manifest_path), "sha256": sha256(manifest_path)})
        recorded_source_groups = {
            str(digest): str(split) for digest, split in record.get("source_groups", {}).items()
        }
        for digest, split in recorded_source_groups.items():
            previous = source_groups.setdefault(str(digest), str(split))
            if previous != split:
                raise ValueError(f"Source crosses splits: {digest}")
        for sample in record.get("samples", []):
            if sample.get("task") != task or sample.get("split") != "train":
                continue
            source = (manifest_path.parent / sample["path"]).resolve()
            try:
                source.relative_to(manifest_path.parent)
            except ValueError as exc:
                raise ValueError(f"Sample path escapes dataset root: {source}") from exc
            digest = sample.get("source_sha256")
            if not isinstance(digest, str) or recorded_source_groups.get(digest) != "train":
                raise ValueError(f"Sample source group is not recorded as train: {source}")
            if not source.is_file() or sha256(source) != sample.get("sha256"):
                raise ValueError(f"Missing or changed sample: {source}")
            if sample["sha256"] in image_hashes:
                raise ValueError(f"Repeated image content: {source}")
            image_hashes.add(sample["sha256"])
            records.append((source, sample))
    if not records:
        raise ValueError(f"No {task} training samples")

    output.mkdir(parents=True, exist_ok=False)
    merged_samples = []
    for source, sample in records:
        relative = Path(task) / "train" / sample["label"] / source.name
        target = output / relative
        if target.exists():
            raise ValueError(f"Filename collision: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied = dict(sample)
        copied["path"] = str(relative)
        if sha256(target) != sample["sha256"]:
            raise RuntimeError(f"Copy hash mismatch: {target}")
        merged_samples.append(copied)

    result = {
        "schema_version": 1,
        "purpose": f"{task} train-only composite",
        "task": task,
        "inputs": input_records,
        "counts": dict(Counter(f"{r['task']}/{r['split']}/{r['label']}" for r in merged_samples)),
        "samples": merged_samples,
        "source_groups": source_groups,
    }
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task", default="posture")
    args = parser.parse_args()
    print(json.dumps(merge(args.manifest, args.output, args.task)["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
