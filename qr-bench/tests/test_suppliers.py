"""Unit tests for the VIES client and its local cache. No real network calls -
urllib.request.urlopen is monkeypatched throughout.
"""

import json
import urllib.error

from qr_bench import config, db, suppliers


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def test_fetch_vies_found(monkeypatch):
    monkeypatch.setattr(
        suppliers.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"isValid": True, "name": "ACME LDA"}),
    )
    name, status = suppliers.fetch_vies("123456789")
    assert name == "ACME LDA"
    assert status == suppliers.STATUS_FOUND


def test_fetch_vies_not_registered_is_not_labelled_invalid(monkeypatch):
    # This is the key distinction: VIES only covers intra-EU registered
    # traders, so isValid: false must map to "not registered", never to
    # something implying the NIF itself is bad.
    monkeypatch.setattr(
        suppliers.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"isValid": False, "name": "---"}),
    )
    name, status = suppliers.fetch_vies("123456789")
    assert name is None
    assert status == suppliers.STATUS_NOT_REGISTERED
    assert status != "invalid"


def test_fetch_vies_found_but_no_name_available(monkeypatch):
    monkeypatch.setattr(
        suppliers.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"isValid": True, "name": "---"}),
    )
    name, status = suppliers.fetch_vies("123456789")
    assert name is None
    assert status == suppliers.STATUS_FOUND_NO_NAME


def test_fetch_vies_network_error_becomes_status_error(monkeypatch):
    def raise_error(*a, **k):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(suppliers.urllib.request, "urlopen", raise_error)
    name, status = suppliers.fetch_vies("123456789")
    assert name is None
    assert status == suppliers.STATUS_ERROR


def test_fetch_vies_timeout_becomes_status_error(monkeypatch):
    def raise_timeout(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(suppliers.urllib.request, "urlopen", raise_timeout)
    name, status = suppliers.fetch_vies("123456789")
    assert name is None
    assert status == suppliers.STATUS_ERROR


def test_process_lookup_caches_and_never_refetches(test_db, monkeypatch):
    calls = []

    def fake_fetch(nif, timeout=suppliers.LOOKUP_TIMEOUT_SECONDS):
        calls.append(nif)
        return "ACME LDA", suppliers.STATUS_FOUND

    monkeypatch.setattr(suppliers, "fetch_vies", fake_fetch)

    suppliers._process_lookup("123456789")
    suppliers._process_lookup("123456789")

    assert calls == ["123456789"]
    cached = db.get_supplier("123456789")
    assert cached["name"] == "ACME LDA"
    assert cached["status"] == "found"


def test_process_lookup_caches_error_outcomes_too(test_db, monkeypatch):
    monkeypatch.setattr(suppliers, "fetch_vies", lambda nif, timeout=None: (None, suppliers.STATUS_ERROR))
    suppliers._process_lookup("987654321")
    cached = db.get_supplier("987654321")
    assert cached["status"] == "error"
    assert cached["name"] is None


def test_schedule_lookup_is_a_noop_for_empty_nif():
    # Should not raise or enqueue anything that would need processing.
    suppliers.schedule_lookup(None)
    suppliers.schedule_lookup("")


def test_schedule_lookup_with_vies_disabled_caches_disabled_status_synchronously(test_db, monkeypatch):
    monkeypatch.setattr(config, "VIES_ENABLED", False)

    def fail_if_called(*a, **k):
        raise AssertionError("fetch_vies must not be called when VIES_ENABLED is false")

    monkeypatch.setattr(suppliers, "fetch_vies", fail_if_called)

    def fail_if_enqueued(*a, **k):
        raise AssertionError("must not enqueue a network lookup when VIES_ENABLED is false")

    monkeypatch.setattr(suppliers._lookup_queue, "put", fail_if_enqueued)

    suppliers.schedule_lookup("123456789")

    cached = db.get_supplier("123456789")
    assert cached["status"] == suppliers.STATUS_DISABLED
    assert cached["name"] is None


def test_schedule_lookup_with_vies_disabled_does_not_overwrite_existing_cache(test_db, monkeypatch):
    db.upsert_supplier("123456789", "ACME LDA", suppliers.STATUS_FOUND)
    monkeypatch.setattr(config, "VIES_ENABLED", False)

    suppliers.schedule_lookup("123456789")

    cached = db.get_supplier("123456789")
    assert cached["status"] == suppliers.STATUS_FOUND
    assert cached["name"] == "ACME LDA"
