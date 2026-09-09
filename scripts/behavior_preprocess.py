"""Shared full-frame preprocessing for experimental behavior classifiers."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

FILL_RGB = (114, 114, 114)


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


def preprocess_bgr(image_bgr: np.ndarray, size: int = 320) -> np.ndarray:
    """Return the FP32 NCHW input used for validation and exported ONNX inference."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    image = letterbox_rgb(rgb, size)
    return np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0


@dataclass(frozen=True)
class FullFrameTransform:
    """Pickle-safe transform used by the Ultralytics classification dataset."""

    size: int
    training: bool = False
    jitter: float = 0.08

    def __call__(self, image):
        import torch
        from torchvision.transforms import functional as functional

        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        framed = letterbox_rgb(rgb, self.size)
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
