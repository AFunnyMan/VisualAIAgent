"""Cross-check the four experimental exports against raw sequential video frames.

Requires the dated profiles and source videos listed in the video survey manifest.
Uses the export environment; no API, camera, or production configuration.
"""

import json
import os
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
base = root / "harness/artifacts/ab-20260907/detectors"
os.environ["YOLO_CONFIG_DIR"] = str(base / "config")
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from ultralytics import YOLO  # noqa: E402

from visual_ai_agent.vision import YoloOnnxDetector  # noqa: E402

torch.set_num_threads(2)
rows = []
for p in json.loads((base / "profiles.json").read_text())["profiles"]:
    pt = YOLO(p["checkpoint"])
    ort = YoloOnnxDetector(
        p["model"],
        confidence=0.35,
        input_size=640,
        intra_op_threads=2,
        verify_manifest=False,
        expected_sha256=p["sha256"],
    )
    for case in [
        {"case": "phone-table", "video": "videos/rice-phone.ogv", "t": 15},
        {"case": "phone-small", "video": "videos/gigaset-quality.webm", "t": 11},
        {"case": "mug", "video": "videos/thermochromic-mug.webm", "t": 20},
        {"case": "bucket", "video": "beer-bottling.webm", "t": 37},
        {"case": "night-bottle", "video": "sabrage.webm", "t": 0},
    ]:
        name = case["case"]
        cap = cv2.VideoCapture(str(root / "harness/artifacts/video-survey" / case["video"]))
        fps = cap.get(cv2.CAP_PROP_FPS)
        for _ in range(round(case["t"] * fps) + 1):
            assert cap.grab()
        ok, frame = cap.retrieve()
        cap.release()
        assert ok
        r = pt.predict(
            frame,
            imgsz=640,
            rect=False,
            nms=p["nms"],
            conf=0.35,
            iou=0.7,
            max_det=300,
            device="cpu",
            verbose=False,
        )[0]
        a = [
            {
                "category": r.names[int(b.cls.item())],
                "confidence": float(b.conf.item()),
                "bbox": b.xyxy.tolist()[0],
            }
            for b in r.boxes
            if int(b.cls.item()) in [39, 41, 67]
        ]
        b = [d.model_dump(mode="json") for d in ort.detect(frame)]

        def key(d):
            return (d["category"], -d["confidence"])

        a.sort(key=key)
        b.sort(key=key)
        same = len(a) == len(b) and all(
            x["category"] == y["category"] for x, y in zip(a, b, strict=True)
        )
        box = (
            max(
                [max(abs(np.array(x["bbox"]) - y["bbox"])) for x, y in zip(a, b, strict=True)],
                default=0,
            )
            if same
            else None
        )
        conf = (
            max(
                [abs(x["confidence"] - y["confidence"]) for x, y in zip(a, b, strict=True)],
                default=0,
            )
            if same
            else None
        )
        row = {
            "profile": p["id"],
            "case": name,
            "pt": a,
            "onnx": b,
            "same_count_classes": same,
            "max_box_error_px": box,
            "max_conf_error": conf,
            "passed": same and box < 0.05 and conf < 0.0001,
            "actual_end2end": bool(pt.predictor.model.end2end),
        }
        rows.append(row)
        print(p["id"], name, row["passed"], box, conf, flush=True)
    del pt, ort
(base / "validation.json").write_text(
    json.dumps(
        {
            "comparison": (
                "same sequentially decoded raw video frames; conf .35; 640 square; "
                "IoU .7; max300; fresh PT per head"
            ),
            "rows": rows,
            "passed": all(r["passed"] for r in rows),
        },
        indent=2,
    )
)
assert all(r["passed"] for r in rows)
