"""QR decode pipeline with strategy tracking.

Order of attempts (first success wins, each attempt is timed as part of
the whole call):
  1. pyzbar on the original image
  2. OpenCV QRCodeDetector on the original image
  3. Both decoders again against preprocessed variants: grayscale,
     2x upscale, adaptive threshold, and rotations of 90/180/270 degrees.

Separately from decode success, `qr_pattern_detected` tracks whether ANY
attempt ever located a QR-shaped region at all (pyzbar finding a symbol,
or OpenCV returning non-empty corner points) even if the text never
decoded. If no attempt ever detects a pattern, that is a real signal that
there is likely no QR code in the image at all - a different failure mode
than "a QR is there but unreadable".
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps
from pyzbar.pyzbar import ZBarSymbol
from pyzbar.pyzbar import decode as zbar_decode

PREPROCESS_VARIANT_NAMES = (
    "grayscale",
    "upscale2x",
    "adaptive_threshold",
    "rotate90",
    "rotate180",
    "rotate270",
)

# pyzbar + opencv on the original, then pyzbar + opencv on each variant.
# Only reached in full when decode_qr exhausts every attempt (i.e. whenever
# success is False) - used to report an accurate attempt count in flags.
TOTAL_ATTEMPTS = 2 * (1 + len(PREPROCESS_VARIANT_NAMES))


@dataclass
class DecodeResult:
    success: bool
    raw_data: str | None
    strategy: str | None
    elapsed_ms: float
    qr_pattern_detected: bool


def _to_bgr(image: Image.Image) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _try_pyzbar(image: Image.Image) -> tuple[str | None, bool]:
    try:
        results = zbar_decode(image, symbols=[ZBarSymbol.QRCODE])
    except Exception:
        return None, False
    if not results:
        return None, False
    data = results[0].data.decode("utf-8", errors="replace")
    return (data or None), True


def _try_opencv(image: Image.Image) -> tuple[str | None, bool]:
    detector = cv2.QRCodeDetector()
    bgr = _to_bgr(image)
    try:
        data, points, _ = detector.detectAndDecode(bgr)
    except cv2.error:
        return None, False
    detected = points is not None and len(points) > 0
    return (data or None), detected


def _adaptive_threshold(image: Image.Image) -> Image.Image:
    arr = np.array(ImageOps.grayscale(image))
    thresh = cv2.adaptiveThreshold(
        arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 5
    )
    return Image.fromarray(thresh)


def _preprocessed_variants(image: Image.Image) -> Iterator[tuple[str, Image.Image]]:
    yield "grayscale", ImageOps.grayscale(image)

    w, h = image.size
    yield "upscale2x", image.resize((w * 2, h * 2), Image.LANCZOS)

    yield "adaptive_threshold", _adaptive_threshold(image)

    yield "rotate90", image.rotate(90, expand=True)
    yield "rotate180", image.rotate(180, expand=True)
    yield "rotate270", image.rotate(270, expand=True)


def decode_qr(image: Image.Image) -> DecodeResult:
    start = time.perf_counter()
    pattern_detected = False

    def run_pair(name: str, variant: Image.Image) -> tuple[str | None, str | None]:
        nonlocal pattern_detected
        data, detected = _try_pyzbar(variant)
        pattern_detected = pattern_detected or detected
        if data:
            return f"pyzbar:{name}", data

        data, detected = _try_opencv(variant)
        pattern_detected = pattern_detected or detected
        if data:
            return f"opencv:{name}", data

        return None, None

    strategy, data = run_pair("original", image)

    if not data:
        for name, variant in _preprocessed_variants(image):
            strategy, data = run_pair(name, variant)
            if data:
                break

    elapsed_ms = (time.perf_counter() - start) * 1000
    return DecodeResult(
        success=bool(data),
        raw_data=data,
        strategy=strategy if data else None,
        elapsed_ms=elapsed_ms,
        qr_pattern_detected=pattern_detected,
    )
