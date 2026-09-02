"""Tests for the correction audit trail: raw QR and original parsed values
are never overwritten, and the history preserves who changed what."""

import random
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_samples import build_qr_payload, render_invoice_canvas  # noqa: E402

from qr_bench import db, pipeline  # noqa: E402


def _process(rng, **kwargs):
    payload = build_qr_payload(rng, **kwargs)
    image = render_invoice_canvas(payload.raw, rng)
    doc = pipeline.process_image(image, "test.png", "test", page_number=None)
    return doc, payload


def test_correction_never_overwrites_raw_qr_or_original_fields(test_db):
    rng = random.Random(1)
    doc, _ = _process(rng)
    original_raw_qr = doc["raw_qr"]
    original_total = doc["all_fields"]["O"]
    original_field_o_column = doc["field_o"]

    db.record_corrections(doc["id"], {"O": "999.99"}, "Rui")

    refreshed = db.get_document(doc["id"])
    assert refreshed["raw_qr"] == original_raw_qr
    assert refreshed["all_fields"]["O"] == original_total
    assert refreshed["field_o"] == original_field_o_column
    assert refreshed["effective_fields"]["O"] == "999.99"


def test_correction_action_resolves_the_document_to_validado(test_db):
    rng = random.Random(2)
    doc, _ = _process(rng, break_totals=True)
    assert doc["status"] == "a_rever"

    db.record_corrections(doc["id"], {"O": "50.00"}, "Rui")

    assert db.get_document(doc["id"])["status"] == "validado"


def test_correction_history_records_who_what_and_when(test_db):
    rng = random.Random(3)
    doc, _ = _process(rng)

    db.record_corrections(doc["id"], {"O": "10.00"}, "Rui")

    history = db.get_corrections(doc["id"])
    assert len(history) == 1
    entry = history[0]
    assert entry["document_id"] == doc["id"]
    assert entry["field_key"] == "O"
    assert entry["corrected_value"] == "10.00"
    assert entry["changed_by"] == "Rui"
    assert entry["changed_at"] is not None
    assert entry["original_value"] == doc["all_fields"]["O"]


def test_second_correction_diffs_against_the_prior_correction(test_db):
    rng = random.Random(4)
    doc, _ = _process(rng)

    db.record_corrections(doc["id"], {"O": "10.00"}, "Rui")
    db.record_corrections(doc["id"], {"O": "20.00"}, "Ana")

    history = db.get_corrections(doc["id"])
    assert len(history) == 2
    assert history[0]["original_value"] == doc["all_fields"]["O"]
    assert history[0]["corrected_value"] == "10.00"
    # The second correction's "original" is the value that was in effect
    # right before it - the previous correction, not the true original.
    assert history[1]["original_value"] == "10.00"
    assert history[1]["corrected_value"] == "20.00"
    assert history[1]["changed_by"] == "Ana"

    assert db.get_document(doc["id"])["effective_fields"]["O"] == "20.00"


def test_correcting_multiple_fields_in_one_call_records_one_row_each(test_db):
    rng = random.Random(5)
    doc, _ = _process(rng)

    db.record_corrections(doc["id"], {"O": "10.00", "N": "1.00"}, "Rui")

    history = db.get_corrections(doc["id"])
    assert {h["field_key"] for h in history} == {"O", "N"}
    assert all(h["changed_by"] == "Rui" for h in history)


def test_correction_via_api_endpoint(test_db):
    import asyncio

    from qr_bench import main

    rng = random.Random(6)
    doc, _ = _process(rng)
    payload = main.CorrectionPayload(corrections={"O": "42.00"}, changed_by="Sara")

    result = asyncio.run(main.correct_document(doc["id"], payload))

    assert result["status"] == "validado"
    assert result["effective_fields"]["O"] == "42.00"
    assert result["all_fields"]["O"] != "42.00"  # original untouched
