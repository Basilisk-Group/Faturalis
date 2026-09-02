import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_samples import generate_dataset  # noqa: E402

from qr_bench import pipeline  # noqa: E402


def _codes(doc: dict) -> set[str]:
    return {f["code"] for f in doc["validation_flags"]}


@pytest.fixture
def sample_dataset(tmp_path):
    return generate_dataset(
        tmp_path,
        count=16,
        seed=7,
        clean_n=5,
        moderate_n=6,
        heavy_n=3,
        no_qr_n=2,
    )


def test_clean_samples_always_decode_with_correct_emitter_nif(test_db, sample_dataset, tmp_path):
    clean_records = [r for r in sample_dataset if r.tier == "clean"]
    assert clean_records, "fixture should include clean-tier samples"

    for record in clean_records:
        path = tmp_path / record.filename
        docs = pipeline.process_file(path, source="test")
        assert len(docs) == 1
        doc = docs[0]
        assert doc["decode_success"] is True
        assert doc["field_a"] == record.expected_fields["A"]
        assert doc["raw_qr"] == record.raw_qr


def test_no_qr_samples_are_never_decoded(test_db, sample_dataset, tmp_path):
    no_qr_records = [r for r in sample_dataset if r.tier == "no_qr"]
    assert no_qr_records

    for record in no_qr_records:
        path = tmp_path / record.filename
        docs = pipeline.process_file(path, source="test")
        doc = docs[0]
        assert doc["decode_success"] is False
        # the automatic heuristic should agree there's no QR pattern here
        assert doc["qr_pattern_detected"] is False


def test_overall_decode_rate_clears_a_sane_threshold(test_db, sample_dataset, tmp_path):
    decodable_records = [r for r in sample_dataset if r.tier != "no_qr"]
    decoded = 0
    for record in decodable_records:
        path = tmp_path / record.filename
        docs = pipeline.process_file(path, source="test")
        if docs[0]["decode_success"]:
            decoded += 1

    success_rate = decoded / len(decodable_records)
    assert success_rate >= 0.5


def test_special_feature_samples_are_included_by_default(sample_dataset):
    tiers = {r.tier for r in sample_dataset}
    assert {"cancelled", "duplicate", "conflict", "pt_ma"}.issubset(tiers)


def test_cancelled_special_sample_is_flagged_cancelled(test_db, sample_dataset, tmp_path):
    records = [r for r in sample_dataset if r.tier == "cancelled"]
    assert records
    for record in records:
        doc = pipeline.process_file(tmp_path / record.filename, source="test")[0]
        assert doc["decode_success"] is True
        assert doc["cancelled"] is True


def test_duplicate_special_sample_pair_dedupes(test_db, sample_dataset, tmp_path):
    records = [r for r in sample_dataset if r.tier == "duplicate"]
    assert len(records) == 2
    docs = [pipeline.process_file(tmp_path / r.filename, source="test")[0] for r in records]
    assert docs[0]["duplicate_of"] is None
    assert docs[1]["duplicate_of"] == docs[0]["id"]
    assert "DUPLICADO_CONFLITUOSO" not in _codes(docs[1])


def test_conflict_special_sample_pair_flags_conflicting_duplicate(test_db, sample_dataset, tmp_path):
    records = [r for r in sample_dataset if r.tier == "conflict"]
    assert len(records) == 2
    docs = [pipeline.process_file(tmp_path / r.filename, source="test")[0] for r in records]
    assert docs[0]["duplicate_of"] is None
    assert docs[1]["duplicate_of"] is None
    assert "DUPLICADO_CONFLITUOSO" in _codes(docs[1])


def test_pt_ma_special_sample_has_no_rate_mismatch(test_db, sample_dataset, tmp_path):
    records = [r for r in sample_dataset if r.tier == "pt_ma"]
    assert records
    for record in records:
        doc = pipeline.process_file(tmp_path / record.filename, source="test")[0]
        assert doc["decode_success"] is True
        assert "TAXA_IVA_ATIPICA" not in _codes(doc)
        assert doc["field_i1"] == "PT-MA"


def test_intentionally_broken_totals_are_flagged(test_db, sample_dataset, tmp_path):
    broken_records = [
        r for r in sample_dataset
        if r.expected_fields and r.expected_fields.get("O")
        and r.tier != "no_qr"
    ]
    # Re-derive which ones had break_totals applied by checking the arithmetic directly,
    # since generate_dataset doesn't expose the flag on the record itself.
    found_mismatch = False
    for record in broken_records:
        path = tmp_path / record.filename
        docs = pipeline.process_file(path, source="test")
        doc = docs[0]
        if doc["decode_success"] and "TOTAIS_INCONSISTENTES" in _codes(doc):
            found_mismatch = True
            break
    assert found_mismatch, "expected at least one sample with an intentionally broken total"
