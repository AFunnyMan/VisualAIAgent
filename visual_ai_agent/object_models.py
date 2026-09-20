"""Named, locally available object-detection model presets."""

from dataclasses import dataclass, replace
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ObjectModelPreset:
    id: str
    label: str
    path: Path
    sha256: str
    experimental: bool


OBJECT_MODEL_PRESETS: tuple[ObjectModelPreset, ...] = (
    ObjectModelPreset(
        id="original",
        label="原始通用 YOLO26n（未微调）",
        path=Path("models/yolo26n-e2e.onnx"),
        sha256="9c60d351bb2865a8169d0590c07c905b372e81e4a846a8ff920d244955e2516c",
        experimental=False,
    ),
    ObjectModelPreset(
        id="r03",
        label="固定场景 R03（实验）",
        path=Path(
            "harness/artifacts/finetune-20260909-r03/runs/scene-yolo26n-r03/weights/"
            "deployment-best.onnx"
        ),
        sha256="f71abc2197aeb433fb6ba21f97113b5797c2bdbbaeded3fe37466e5ee1a4d004",
        experimental=True,
    ),
    ObjectModelPreset(
        id="r04",
        label="固定场景 R04（实验）",
        path=Path(
            "data/scene-r04-20260917/runs/scene-yolo26n-r04/weights/deployment-best.onnx"
        ),
        sha256="35843f82e24e1a2667be27f616ff45b4210f01f68a6fde01ffd108c77fab0533",
        experimental=True,
    ),
    ObjectModelPreset(
        id="r05",
        label="固定场景 R05（实验）",
        path=Path(
            "data/scene-r04-20260917/runs/scene-yolo26n-r05/weights/deployment-best.onnx"
        ),
        sha256="fbbaa834b563fc5672b652fc5742b5cd305c3a0b0751949777c8f5c43538f25d",
        experimental=True,
    ),
)


def get_object_model_preset(model_id: str) -> ObjectModelPreset:
    """Return a preset with a repository-absolute model path."""

    for preset in OBJECT_MODEL_PRESETS:
        if preset.id == model_id:
            return replace(preset, path=(REPOSITORY_ROOT / preset.path).resolve())
    raise ValueError(f"Unknown object model preset: {model_id!r}")
