"""Pipeline-level tests for features that need a rendered QR image
(cancellation, dedup, field P, etc) rather than just parsed dicts.
"""

import random
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_samples import build_qr_payload, render_invoice_canvas  # noqa: E402

from qr_bench import db, pipeline, suppliers  # noqa: E402


def _process(rng, **payload_kwargs):
    payload = build_qr_payload(rng, **payload_kwargs)
    image = render_invoice_canvas(payload.raw, rng)
    doc = pipeline.process_image(image, f"sample_{rng.randint(0, 999999)}.png", "test", page_number=None)
    return doc, payload


def _codes(doc: dict) -> set[str]:
    return {f["code"] for f in doc["validation_flags"]}


def test_cancelled_document_is_flagged(test_db):
    rng = random.Random(1)
    doc, _ = _process(rng, status="A")
    assert doc["decode_success"] is True
    assert doc["cancelled"] is True


def test_normal_document_is_not_cancelled(test_db):
    rng = random.Random(2)
    doc, _ = _process(rng, status="N")
    assert doc["cancelled"] is False


def test_duplicate_atcud_with_matching_total_is_deduped(test_db):
    rng = random.Random(10)
    payload = build_qr_payload(rng, atcud="12345678-1")
    image = render_invoice_canvas(payload.raw, rng)
    doc1 = pipeline.process_image(image, "a.png", "test", page_number=None)
    doc2 = pipeline.process_image(image, "b.png", "test", page_number=None)

    assert doc1["duplicate_of"] is None
    assert doc2["duplicate_of"] == doc1["id"]
    assert "DUPLICADO" in _codes(doc2)
    assert "DUPLICADO_CONFLITUOSO" not in _codes(doc2)


def test_duplicate_atcud_with_different_total_conflicts(test_db):
    rng = random.Random(11)
    doc1, payload1 = _process(rng, atcud="99998888-2")
    modified_raw = payload1.raw.replace(f"O:{payload1.fields['O']}", "O:999999.99")
    assert modified_raw != payload1.raw  # sanity: the replacement actually happened

    image2 = render_invoice_canvas(modified_raw, rng)
    doc2 = pipeline.process_image(image2, "conflict.png", "test", page_number=None)

    assert doc2["duplicate_of"] is None
    assert "DUPLICADO_CONFLITUOSO" in _codes(doc2)
    message = next(f["message"] for f in doc2["validation_flags"] if f["code"] == "DUPLICADO_CONFLITUOSO")
    assert "999.999,99" in message  # interpolated real value, not a raw template


def test_different_atcud_is_never_deduped(test_db):
    rng = random.Random(13)
    doc1, _ = _process(rng, atcud="11112222-1")
    doc2, _ = _process(rng, atcud="33334444-1")

    assert doc2["duplicate_of"] is None
    assert "DUPLICADO" not in _codes(doc2)
    assert "DUPLICADO_CONFLITUOSO" not in _codes(doc2)


def test_ingestion_schedules_supplier_lookup_without_blocking(test_db, monkeypatch):
    scheduled = []
    monkeypatch.setattr(pipeline.suppliers, "schedule_lookup", lambda nif: scheduled.append(nif))

    rng = random.Random(30)
    doc, payload = _process(rng)

    # process_image must have returned (i.e. not blocked on any network call)
    # and simply handed the NIF off for background lookup.
    assert scheduled == [payload.fields["A"]]
    # no real lookup performed inline, so nothing cached yet
    assert db.get_supplier(payload.fields["A"]) is None


def test_documents_are_joined_with_cached_supplier_info(test_db):
    rng = random.Random(31)
    doc, payload = _process(rng)
    nif = payload.fields["A"]

    db.upsert_supplier(nif, "ACME LDA", suppliers.STATUS_FOUND)

    refreshed = db.get_document(doc["id"])
    assert refreshed["supplier_name"] == "ACME LDA"
    assert refreshed["supplier_status"] == "found"

    listed = {d["id"]: d for d in db.list_documents()}
    assert listed[doc["id"]]["supplier_name"] == "ACME LDA"


def test_field_p_withholding_is_stored_when_present(test_db):
    rng = random.Random(50)
    doc, _ = _process(rng, withholding="12.34")
    assert doc["field_p"] == "12.34"
    assert doc["all_fields"]["P"] == "12.34"


def test_field_p_is_none_when_absent(test_db):
    rng = random.Random(51)
    doc, _ = _process(rng)
    assert doc["field_p"] is None
    assert "P" not in doc["all_fields"]


def test_pt_ma_sample_has_no_rate_mismatch(test_db):
    rng = random.Random(40)
    doc, _ = _process(rng, tax_region="PT-MA")
    assert doc["decode_success"] is True
    assert "TAXA_IVA_ATIPICA" not in _codes(doc)
    assert doc["field_i1"] == "PT-MA"


def test_pt_ac_sample_has_no_rate_mismatch(test_db):
    rng = random.Random(41)
    doc, _ = _process(rng, tax_region="PT-AC")
    assert doc["decode_success"] is True
    assert "TAXA_IVA_ATIPICA" not in _codes(doc)
    assert doc["field_i1"] == "PT-AC"


def test_cancelled_excluded_from_accuracy_but_counted_on_overview(test_db):
    rng = random.Random(3)
    cancelled_doc, _ = _process(rng, status="A")
    normal_doc, _ = _process(rng, status="N")

    db.set_ground_truth(cancelled_doc["id"], "correct")
    db.set_ground_truth(normal_doc["id"], "wrong")

    stats = db.compute_stats()
    assert stats["cancelled_count"] == 1
    # Only the non-cancelled marked row should count toward accuracy.
    assert stats["ground_truth_counts"] == {"correct": 0, "wrong": 1, "no_qr": 0}
    assert stats["ground_truth_accuracy"] == 0.0
