"""OCR fallback is out of scope for this prototype.

Not called anywhere in the decode pipeline - present only as an explicit
extension point so it's obvious where OCR would plug in if this ever
grows beyond a measurement tool.
"""

from PIL import Image


def ocr_fallback(image: Image.Image) -> str:
    raise NotImplementedError("OCR fallback is a non-goal for qr-bench")
