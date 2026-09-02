"""Tests for client auto-creation/attribution and the consumidor final
special case (field B == 999999990 -> unattributed, never a client)."""

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


def test_first_sighted_nif_auto_creates_a_client_named_after_it(test_db):
    rng = random.Random(1)
    doc, _ = _process(rng, acquirer_nif="123456789")

    assert doc["client_id"] is not None
    client = db.get_client(doc["client_id"])
    assert client["nif"] == "123456789"
    assert client["name"] == "123456789"


def test_second_document_with_same_nif_reuses_the_same_client(test_db):
    rng = random.Random(2)
    doc1, _ = _process(rng, acquirer_nif="123456789")
    doc2, _ = _process(rng, acquirer_nif="123456789")

    assert doc1["client_id"] == doc2["client_id"]
    assert len(db.list_clients_with_stats()) == 1
    assert db.list_clients_with_stats()[0]["document_count"] == 2


def test_consumidor_final_nif_never_creates_a_client(test_db):
    rng = random.Random(3)
    doc, _ = _process(rng, acquirer_nif="999999990")

    assert doc["client_id"] is None
    assert db.list_clients_with_stats() == []


def test_consumidor_final_document_shows_up_as_unattributed(test_db):
    rng = random.Random(4)
    doc, _ = _process(rng, acquirer_nif="999999990")

    unattributed_ids = [d["id"] for d in db.list_unattributed_documents()]
    assert doc["id"] in unattributed_ids


def test_consumidor_final_flag_still_fires_even_though_unattributed(test_db):
    rng = random.Random(5)
    doc, _ = _process(rng, acquirer_nif="999999990")

    codes = {f["code"] for f in doc["validation_flags"]}
    assert "CONSUMIDOR_FINAL" in codes


def test_missing_acquirer_nif_is_also_unattributed_not_an_error(test_db):
    rng = random.Random(6)
    doc, _ = _process(rng, omit_acquirer=True)

    assert doc["client_id"] is None
    assert db.list_clients_with_stats() == []


def test_undecodable_document_is_unattributed(test_db):
    from generate_samples import render_blank_invoice_canvas

    rng = random.Random(7)
    image = render_blank_invoice_canvas(rng)
    doc = pipeline.process_image(image, "blank.png", "test", page_number=None)

    assert doc["client_id"] is None
    assert doc["id"] in [d["id"] for d in db.list_unattributed_documents()]


def test_renaming_a_client_persists(test_db):
    rng = random.Random(8)
    doc, _ = _process(rng, acquirer_nif="123456789")

    updated = db.update_client(doc["client_id"], name="Empresa Teste Lda", notes="cliente importante")
    assert updated["name"] == "Empresa Teste Lda"
    assert updated["notes"] == "cliente importante"

    refetched = db.get_client(doc["client_id"])
    assert refetched["name"] == "Empresa Teste Lda"


def test_different_nifs_get_different_clients(test_db):
    rng = random.Random(9)
    doc1, _ = _process(rng, acquirer_nif="111111111")
    doc2, _ = _process(rng, acquirer_nif="222222222")

    assert doc1["client_id"] != doc2["client_id"]
    assert len(db.list_clients_with_stats()) == 2
