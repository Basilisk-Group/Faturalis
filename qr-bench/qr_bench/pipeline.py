"""Orchestrates a single file/page through decode -> parse -> validate -> store."""

import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from qr_bench import config, db, flags, suppliers
from qr_bench.decode import TOTAL_ATTEMPTS, decode_qr
from qr_bench.parser import parse_qr_string
from qr_bench.pdf_utils import rasterize_pdf

TAX_DETAIL_KEYS = ("I2", "I3", "I4", "I5", "I6", "I7", "I8")


def _make_thumbnail(image: Image.Image) -> str:
    thumb = image.copy()
    thumb.thumbnail((config.THUMBNAIL_MAX_DIM, config.THUMBNAIL_MAX_DIM))
    buffer = io.BytesIO()
    thumb.convert("RGB").save(buffer, format="JPEG", quality=70)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def process_image(
    image: Image.Image,
    filename: str,
    source: str,
    page_number: int | None,
) -> dict[str, Any]:
    result = decode_qr(image)

    fields: dict[str, str] = {}
    if result.success and result.raw_data:
        fields = parse_qr_string(result.raw_data)

    dedup_match = None
    atcud = fields.get("H")
    if result.success and atcud:
        dedup_match = db.find_first_by_atcud(atcud)

    flag_entries = flags.evaluate_flags(
        fields,
        decode_success=result.success,
        qr_pattern_detected=result.qr_pattern_detected,
        max_decode_attempts=TOTAL_ATTEMPTS,
        dedup_match=dedup_match,
    )
    # DUPLICADO is the flag that carries the same duplicate-vs-conflict
    # decision the `duplicate_of` column needs, so derive it from the flag
    # list rather than re-running the amounts comparison a second time.
    duplicate_of = next(
        (entry["detail"]["original_id"] for entry in flag_entries if entry["code"] == "DUPLICADO"),
        None,
    )

    tax_details = {k: fields[k] for k in TAX_DETAIL_KEYS if k in fields}
    cancelled = fields.get("E") == "A"
    status = flags.initial_status(result.success, flag_entries)

    # Client attribution by acquirer NIF (field B). Consumidor final and
    # documents with no usable B are deliberately left unattributed rather
    # than getting a client row - see qr_bench.db.get_or_create_client.
    acquirer_nif = fields.get("B")
    client_id = None
    if acquirer_nif and acquirer_nif != flags.CONSUMIDOR_FINAL_NIF:
        client_id = db.get_or_create_client(acquirer_nif)

    record = {
        "filename": filename,
        "source": source,
        "page_number": page_number,
        "processed_at": datetime.now(UTC).isoformat(),
        "decode_success": int(result.success),
        "qr_pattern_detected": int(result.qr_pattern_detected),
        "strategy": result.strategy,
        "elapsed_ms": result.elapsed_ms,
        "raw_qr": result.raw_data,
        "field_a": fields.get("A"),
        "field_b": fields.get("B"),
        "field_c": fields.get("C"),
        "field_d": fields.get("D"),
        "field_e": fields.get("E"),
        "field_f": fields.get("F"),
        "field_g": fields.get("G"),
        "field_h": fields.get("H"),
        "field_i1": fields.get("I1"),
        "field_n": fields.get("N"),
        "field_o": fields.get("O"),
        "field_p": fields.get("P"),
        "field_q": fields.get("Q"),
        "field_r": fields.get("R"),
        "cancelled": int(cancelled),
        "duplicate_of": duplicate_of,
        "tax_details_json": json.dumps(tax_details),
        "all_fields_json": json.dumps(fields),
        "validation_flags_json": json.dumps(flag_entries),
        "thumbnail_b64": _make_thumbnail(image),
        "status": status,
        "client_id": client_id,
    }

    doc_id = db.insert_document(record)
    suppliers.schedule_lookup(fields.get("A"))
    return db.get_document(doc_id)


def process_file(
    path: Path,
    source: str,
    display_name: str | None = None,
) -> list[dict[str, Any]]:
    name = display_name or path.name
    ext = path.suffix.lower()
    if ext == ".pdf":
        pages = rasterize_pdf(path, dpi=config.RASTER_DPI)
        return [
            process_image(page_image, name, source, page_number=i)
            for i, page_image in enumerate(pages, start=1)
        ]

    image = Image.open(path).convert("RGB")
    return [process_image(image, name, source, page_number=None)]
