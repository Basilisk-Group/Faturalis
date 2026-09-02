"""Tests for FastAPI route handlers that don't need a real HTTP client -
called directly as coroutines to avoid pulling in httpx just for this.
"""

import asyncio
import csv
import io
import json
import random
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_samples import build_qr_payload, render_invoice_canvas  # noqa: E402

from qr_bench import config, flags, main, pipeline  # noqa: E402


def test_health_reports_ok_when_db_and_pyzbar_are_fine(test_db):
    response = asyncio.run(main.health())
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["pyzbar"] == "ok"


def test_health_reports_503_when_database_is_unreachable(test_db, monkeypatch):
    def broken_connection():
        raise RuntimeError("no such database")

    monkeypatch.setattr(main.db, "get_connection", broken_connection)

    response = asyncio.run(main.health())
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["status"] == "error"
    assert body["checks"]["database"].startswith("error:")


def test_get_flags_returns_the_whole_registry():
    result = asyncio.run(main.get_flags())
    assert result == flags.FLAGS
    assert "TOTAIS_INCONSISTENTES" in result
    assert set(result["TOTAIS_INCONSISTENTES"]) == {
        "code", "severity", "label_pt", "explanation_pt", "cause_pt", "action_pt",
    }


def test_glossario_route_serves_the_static_file():
    response = asyncio.run(main.glossario())
    assert response.path == config.STATIC_DIR / "glossario.html"
    assert (config.STATIC_DIR / "glossario.html").exists()


def test_csv_export_includes_flag_codes_and_highest_severity(test_db):
    rng = random.Random(200)
    payload = build_qr_payload(rng, break_totals=True)
    image = render_invoice_canvas(payload.raw, rng)
    pipeline.process_image(image, "broken.png", "test", page_number=None)

    response = asyncio.run(main.export_csv())
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))

    assert len(rows) == 1
    assert "TOTAIS_INCONSISTENTES" in rows[0]["flag_codes"].split(";")
    assert rows[0]["highest_severity"] == "erro"
