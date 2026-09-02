"""Rasterize PDF pages to PIL images using PyMuPDF."""

import io
from pathlib import Path

import fitz
from PIL import Image


def rasterize_pdf(path: Path, dpi: int = 300) -> list[Image.Image]:
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[Image.Image] = []
    with fitz.open(path) as doc:
        for page in doc:
            pixmap = page.get_pixmap(matrix=matrix)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            pages.append(image)
    return pages
