"""Shared full-frame/ROI preprocessing for experimental behavior classifiers."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

FILL_RGB = (114, 114, 114)
NormalizedROI = tuple[float, float, float, float]


def validate_roi(roi: NormalizedROI | None) -> NormalizedROI | None:
    """Validate and normalize an optional ``(x1, y1, x2, y2)`` ROI."""
    if roi is None:
        return None
    if len(roi) != 4:
        raise ValueError("ROI must contain x1 y1 x2 y2")
    normalized = tuple(float(value) for value in roi)
    if not all(np.isfinite(value) and 0.0 <= value <= 1.0 for value in normalized):
        raise ValueError("ROI coordinates must be finite and within [0, 1]")
    x1, y1, x2, y2 = normalized
    if x1 >= x2 or y1 >= y2:
        raise ValueError("ROI must have positive width and height")
    return normalized


def crop_rgb(image_rgb: np.ndarray, roi: NormalizedROI | None = None) -> np.ndarray:
    """Crop an RGB image using the shared normalized-coordinate rounding rule."""
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("Expected an HxWx3 RGB image")
    normalized = validate_roi(roi)
    if normalized is None:
        return image_rgb
    height, width = image_rgb.shape[:2]
    x1, y1, x2, y2 = normalized
    # Nearest-pixel edges keep training and inference deterministic and identical.
    left, top, right, bottom = (
        round(x1 * width),
        round(y1 * height),
        round(x2 * width),
        round(y2 * height),
    )
    if left >= right or top >= bottom:
        raise ValueError(f"ROI is empty after pixel rounding for source frame {width}x{height}")
    return image_rgb[top:bottom, left:right]


def letterbox_rgb(image_rgb: np.ndarray, size: int) -> np.ndarray:
    """Resize an RGB image without cropping and pad it to ``size`` square."""
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("Expected an HxWx3 RGB image")
    if size <= 0 or image_rgb.shape[0] <= 0 or image_rgb.shape[1] <= 0:
        raise ValueError("Image dimensions and target size must be positive")
    height, width = image_rgb.shape[:2]
    scale = min(size / width, size / height)
    resized_width = max(1, min(size, round(width * scale)))
    resized_height = max(1, min(size, round(height * scale)))
    resized = cv2.resize(
        image_rgb,
        (resized_width, resized_height),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
    )
    canvas = np.full((size, size, 3), FILL_RGB, dtype=np.uint8)
    left = (size - resized_width) // 2
    top = (size - resized_height) // 2
    canvas[top : top + resized_height, left : left + resized_width] = resized
    return canvas


def preprocess_bgr(
    image_bgr: np.ndarray, size: int = 320, roi: NormalizedROI | None = None
) -> np.ndarray:
    """Return the FP32 NCHW input used for validation and exported ONNX inference."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    image = letterbox_rgb(crop_rgb(rgb, roi), size)
    return np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0


@dataclass(frozen=True)
class FullFrameTransform:
    """Pickle-safe transform used by the Ultralytics classification dataset."""

    size: int
    training: bool = False
    jitter: float = 0.08
    roi: NormalizedROI | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "roi", validate_roi(self.roi))

    def __call__(self, image):
        import torch
        from torchvision.transforms import functional as functional

        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        framed = letterbox_rgb(crop_rgb(rgb, self.roi), self.size)
        chw = np.ascontiguousarray(framed.transpose(2, 0, 1))
        tensor = torch.from_numpy(chw).float().div_(255.0)
        if self.training and self.jitter:
            # Small photometric changes only: geometry and the full action remain intact.
            brightness = float(torch.empty(1).uniform_(1 - self.jitter, 1 + self.jitter))
            contrast = float(torch.empty(1).uniform_(1 - self.jitter, 1 + self.jitter))
            saturation = float(torch.empty(1).uniform_(1 - self.jitter, 1 + self.jitter))
            tensor = functional.adjust_brightness(tensor, brightness)
            tensor = functional.adjust_contrast(tensor, contrast)
            tensor = functional.adjust_saturation(tensor, saturation).clamp_(0, 1)
        return tensor
