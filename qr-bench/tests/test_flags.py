"""Every code in qr_bench.flags.FLAGS must have a synthetic sample that
triggers it, verified by actually running that sample through the real
pipeline (or, for FORNECEDOR_DESCONHECIDO, through the DB join it depends
on) and asserting the expected code fires. If FLAG_SAMPLE_BUILDERS in
scripts/generate_samples.py ever drifts out of sync with the registry -
a new flag added without a sample, or vice versa - the completeness test
below fails the whole suite.
"""

import random
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_samples import FLAG_SAMPLE_BUILDERS  # noqa: E402

from qr_bench import db, flags, pipeline, suppliers  # noqa: E402


def _codes(doc: dict) -> set[str]:
    return {f["code"] for f in doc["validation_flags"]}


def test_every_registered_flag_has_a_sample_builder():
    missing = set(flags.FLAGS) - set(FLAG_SAMPLE_BUILDERS)
    assert not missing, f"flags with no sample builder: {sorted(missing)}"


def test_every_sample_builder_maps_to_a_registered_flag():
    stray = set(FLAG_SAMPLE_BUILDERS) - set(flags.FLAGS)
    assert not stray, f"sample builders for codes not in FLAGS: {sorted(stray)}"


# FORNECEDOR_DESCONHECIDO can't be exercised by processing an image alone -
# it depends on the (async, DB-resident) VIES lookup outcome. Every other
# code is decided purely from the image/payload by evaluate_flags(), so
# running it through the real pipeline is a legitimate end-to-end check.
_REQUIRES_SUPPLIER_LOOKUP = {"FORNECEDOR_DESCONHECIDO"}


@pytest.mark.parametrize("code", sorted(set(flags.FLAGS) - _REQUIRES_SUPPLIER_LOOKUP))
def test_flag_sample_triggers_the_expected_code(test_db, code):
    rng = random.Random(hash(code) & 0xFFFFFFFF)
    samples = FLAG_SAMPLE_BUILDERS[code](rng)
    assert samples, f"builder for {code} returned no samples"

    doc = None
    for i, sample in enumerate(samples):
        doc = pipeline.process_image(sample.image, f"{code.lower()}_{i}.png", "test", page_number=None)

    assert code in _codes(doc), (
        f"{code} did not fire; got {sorted(_codes(doc))} "
        f"(decode_success={doc['decode_success']}, qr_pattern_detected={doc['qr_pattern_detected']})"
    )


def test_fornecedor_desconhecido_fires_once_vies_resolves_negatively(test_db):
    rng = random.Random(hash("FORNECEDOR_DESCONHECIDO") & 0xFFFFFFFF)
    samples = FLAG_SAMPLE_BUILDERS["FORNECEDOR_DESCONHECIDO"](rng)
    sample = samples[-1]
    doc = pipeline.process_image(sample.image, "fornecedor_desconhecido.png", "test", page_number=None)

    nif = sample.payload.fields["A"]
    # Not yet resolved: ingestion never blocks on the lookup, so nothing is
    # cached at this point and the flag must not have fired prematurely.
    assert db.get_supplier(nif) is None
    assert "FORNECEDOR_DESCONHECIDO" not in _codes(doc)

    db.upsert_supplier(nif, None, suppliers.STATUS_NOT_REGISTERED)

    refreshed = db.get_document(doc["id"])
    assert "FORNECEDOR_DESCONHECIDO" in _codes(refreshed)
