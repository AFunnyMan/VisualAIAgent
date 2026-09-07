"""Prepare the dated, isolated four-model experiment; run from the repository root.

Uses the export environment, official checkpoints and CPU. Does not change the
production model. Run validate_detector_comparison.py before using new exports.
"""

import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path

root = Path.cwd()
base = root / "harness/artifacts/ab-20260907/detectors"
config = base / "config"
config.mkdir(parents=True, exist_ok=True)
os.environ["YOLO_CONFIG_DIR"] = str(config)
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
import onnxruntime as ort  # noqa: E402
import torch  # noqa: E402
import ultralytics  # noqa: E402
from ultralytics import YOLO  # noqa: E402

torch.set_num_threads(2)
checkpoint = base / "yolo26s.pt"
url = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt"
if not checkpoint.exists():
    with (
        urllib.request.urlopen(url, timeout=45) as r,
        checkpoint.with_suffix(".partial").open("wb") as f,
    ):
        shutil.copyfileobj(r, f)
    checkpoint.with_suffix(".partial").replace(checkpoint)
for source, expected in [
    (checkpoint, "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b"),
    (
        root / "models/yolo26n.pt",
        "9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef",
    ),
]:
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Official checkpoint checksum mismatch: {source}")
previous = {}
if (base / "profiles.json").exists():
    previous = {p["id"]: p for p in json.loads((base / "profiles.json").read_text())["profiles"]}
print("downloaded s", checkpoint.stat().st_size, flush=True)
profiles = [
    {
        "id": "n-e2e",
        "model": str(root / "models/yolo26n-e2e.onnx"),
        "checkpoint": str(root / "models/yolo26n.pt"),
        "nms": False,
        "confidence_floor": 0.0,
    }
]
for name, source, nms in [
    ("n-traditional", root / "models/yolo26n.pt", True),
    ("s-e2e", checkpoint, False),
    ("s-traditional", checkpoint, True),
]:
    local = base / f"{name}.pt"
    shutil.copy2(source, local)
    dest = base / f"{name}.onnx"
    if dest.exists():
        if (
            name not in previous
            or hashlib.sha256(dest.read_bytes()).hexdigest() != previous[name]["sha256"]
        ):
            raise ValueError(f"Existing export has no matching experiment provenance: {dest}")
    else:
        YOLO(str(local)).export(
            format="onnx",
            imgsz=640,
            batch=1,
            dynamic=False,
            simplify=True,
            nms=nms,
            conf=0.01,
            iou=0.7,
            max_det=300,
            device="cpu",
        )
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(dest), options, providers=["CPUExecutionProvider"])
    assert session.get_outputs()[0].shape == [1, 300, 6], session.get_outputs()[0].shape
    profiles.append(
        {
            "id": name,
            "model": str(dest),
            "checkpoint": str(source),
            "nms": nms,
            "confidence_floor": 0.01 if nms else 0.0,
        }
    )
    print(name, "exported", flush=True)
for p in profiles:
    p["sha256"] = hashlib.sha256(Path(p["model"]).read_bytes()).hexdigest()
    p["checkpoint_sha256"] = hashlib.sha256(Path(p["checkpoint"]).read_bytes()).hexdigest()
    p["source_url"] = url if p["id"].startswith("s-") else url.replace("26s", "26n")
    p["license"] = "AGPL-3.0-only or Ultralytics Enterprise"
    p["input_size"] = 640
    p["intra_op_threads"] = 2
(base / "profiles.json").write_text(
    json.dumps(
        {
            "ultralytics": ultralytics.__version__,
            "onnxruntime": ort.__version__,
            "profiles": profiles,
        },
        indent=2,
    )
)
