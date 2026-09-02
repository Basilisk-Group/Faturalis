"""Tests for the document lifecycle status machine: recebido/extraido/
a_rever/validado/exportado, plus rejection and bulk-validate."""

import random
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_samples import (  # noqa: E402
    build_qr_payload,
    render_blank_invoice_canvas,
    render_invoice_canvas,
)

from qr_bench import db, pipeline  # noqa: E402


def _process(rng, **kwargs):
    payload = build_qr_payload(rng, **kwargs)
    image = render_invoice_canvas(payload.raw, rng)
    doc = pipeline.process_image(image, "test.png", "test", page_number=None)
    return doc, payload


def test_clean_document_goes_straight_to_extraido(test_db):
    rng = random.Random(1)
    doc, _ = _process(rng)
    assert doc["status"] == "extraido"


def test_document_with_erro_severity_flag_goes_to_a_rever(test_db):
    rng = random.Random(2)
    doc, _ = _process(rng, break_totals=True)  # TOTAIS_INCONSISTENTES = erro
    assert doc["status"] == "a_rever"


def test_document_with_only_info_flags_still_goes_to_extraido(test_db):
    rng = random.Random(3)
    doc, _ = _process(rng, status="A")  # DOCUMENTO_ANULADO is info-only
    assert doc["status"] == "extraido"
    assert any(f["code"] == "DOCUMENTO_ANULADO" for f in doc["validation_flags"])


def test_undecodable_document_stays_recebido(test_db):
    rng = random.Random(4)
    image = render_blank_invoice_canvas(rng)
    doc = pipeline.process_image(image, "blank.png", "test", page_number=None)
    assert doc["status"] == "recebido"


def test_validate_action_sets_validado(test_db):
    rng = random.Random(5)
    doc, _ = _process(rng)
    updated = db.set_document_status(doc["id"], "validado")
    assert updated["status"] == "validado"


def test_reject_action_stores_reason_and_moves_to_a_rever(test_db):
    rng = random.Random(6)
    doc, _ = _process(rng)
    assert doc["status"] == "extraido"

    updated = db.reject_document(doc["id"], "documento ilegível", "Ana")
    assert updated["status"] == "a_rever"
    assert updated["rejection_reason"] == "documento ilegível"
    assert updated["rejected_by"] == "Ana"
    assert updated["rejected_at"] is not None


def test_reject_from_a_rever_stays_a_rever(test_db):
    rng = random.Random(7)
    doc, _ = _process(rng, break_totals=True)
    assert doc["status"] == "a_rever"
    updated = db.reject_document(doc["id"], "falta assinatura", "Ana")
    assert updated["status"] == "a_rever"


def test_bulk_validate_only_touches_clean_extraido_documents_for_that_client(test_db):
    rng = random.Random(8)
    clean_doc, _ = _process(rng, acquirer_nif="111111110")
    rng2 = random.Random(9)
    review_doc, _ = _process(rng2, acquirer_nif="111111110", break_totals=True)
    assert clean_doc["status"] == "extraido"
    assert review_doc["status"] == "a_rever"

    count = db.bulk_validate(clean_doc["client_id"])

    assert count == 1
    assert db.get_document(clean_doc["id"])["status"] == "validado"
    assert db.get_document(review_doc["id"])["status"] == "a_rever"  # untouched


def test_bulk_validate_does_not_touch_other_clients(test_db):
    rng = random.Random(10)
    doc_a, _ = _process(rng, acquirer_nif="111111110")
    rng2 = random.Random(11)
    doc_b, _ = _process(rng2, acquirer_nif="222222220")

    db.bulk_validate(doc_a["client_id"])

    assert db.get_document(doc_a["id"])["status"] == "validado"
    assert db.get_document(doc_b["id"])["status"] == "extraido"


def test_bulk_validate_unattributed_bucket(test_db):
    rng = random.Random(12)
    doc, _ = _process(rng, acquirer_nif="999999990")
    assert doc["client_id"] is None
    assert doc["status"] == "extraido"

    count = db.bulk_validate(None)

    assert count == 1
    assert db.get_document(doc["id"])["status"] == "validado"


def test_mark_exported_sets_status_and_timestamp(test_db):
    rng = random.Random(13)
    doc, _ = _process(rng)
    db.mark_exported([doc["id"]])
    updated = db.get_document(doc["id"])
    assert updated["status"] == "exportado"
    assert updated["exported_at"] is not None


def test_reexport_is_allowed_and_updates_timestamp(test_db):
    rng = random.Random(14)
    doc, _ = _process(rng)

    db.mark_exported([doc["id"]])
    first_ts = db.get_document(doc["id"])["exported_at"]

    db.mark_exported([doc["id"]])
    second_ts = db.get_document(doc["id"])["exported_at"]

    assert second_ts >= first_ts
