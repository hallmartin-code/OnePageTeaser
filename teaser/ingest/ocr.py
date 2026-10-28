"""Optional OCR for scanned pages and image-only slides.

RapidOCR (ONNX runtime) is used when installed; it needs no system binaries. When it is
absent, callers record which pages could not be read instead of guessing.
"""

from __future__ import annotations

import functools
import io
from typing import Any


@functools.cache
def _engine() -> Any | None:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:  # ImportError, or onnxruntime failing to load on this platform
        return None
    try:
        return RapidOCR()
    except Exception:
        return None


def ocr_available() -> bool:
    """Whether an OCR engine can be loaded."""
    return _engine() is not None


def ocr_image(png_or_jpeg: bytes) -> str:
    """Recognise text in an image, top-to-bottom. Returns an empty string on failure."""
    engine = _engine()
    if engine is None:
        return ""
    try:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(png_or_jpeg)).convert("RGB")
        result, _ = engine(np.asarray(image))
    except Exception:
        return ""
    if not result:
        return ""
    # Each entry is [box, text, score]; sort by the box's top edge, then left edge.
    rows = sorted(result, key=lambda r: (round(r[0][0][1] / 12), r[0][0][0]))
    return "\n".join(str(r[1]) for r in rows if float(r[2]) >= 0.5)
