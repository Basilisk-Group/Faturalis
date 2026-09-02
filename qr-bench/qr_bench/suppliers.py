"""Background VIES lookups: emitter NIF -> supplier name.

Runs on a queue + daemon worker thread so document ingestion is never
blocked on an external network call. Every outcome (including failures)
is cached in the `suppliers` table, keyed by NIF, so a given supplier is
never looked up more than once.

VIES (the EU VAT Information Exchange System) only lists businesses
registered for intra-EU transactions. A domestic-only supplier - a local
café, a small shop that never invoices across a border - will correctly
come back "not registered" even though its NIF is perfectly valid. That
is a different outcome from a lookup that failed or timed out, and the
two are tracked as distinct statuses so a legitimate local supplier never
gets displayed as if something were wrong with the document.
"""

import json
import queue
import threading
import urllib.error
import urllib.request

from qr_bench import config, db

VIES_URL_TEMPLATE = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/PT/vat/{nif}"
LOOKUP_TIMEOUT_SECONDS = config.VIES_TIMEOUT

STATUS_FOUND = "found"
STATUS_FOUND_NO_NAME = "found_no_name"
STATUS_NOT_REGISTERED = "not_registered"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"

_lookup_queue: queue.Queue[str] = queue.Queue()


def schedule_lookup(nif: str | None) -> None:
    """Enqueues a NIF for background lookup. No-op if empty - never blocks.

    When VIES_ENABLED is off (e.g. the container's no-network default),
    this writes a STATUS_DISABLED cache entry synchronously instead of
    touching the network or the queue - it's a local sqlite write, not a
    blocking call, and it gives the UI something honest to show instead of
    a "looking up..." spinner that would never resolve.
    """
    if not nif:
        return
    if not config.VIES_ENABLED:
        if db.get_supplier(nif) is None:
            db.upsert_supplier(nif, None, STATUS_DISABLED)
        return
    _lookup_queue.put(nif)


def fetch_vies(nif: str, timeout: float = LOOKUP_TIMEOUT_SECONDS) -> tuple[str | None, str]:
    """Calls the real VIES REST API. Never raises - failures become STATUS_ERROR."""
    url = VIES_URL_TEMPLATE.format(nif=nif)
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None, STATUS_ERROR

    if not payload.get("isValid"):
        return None, STATUS_NOT_REGISTERED

    name = (payload.get("name") or "").strip()
    if not name or name == "---":
        return None, STATUS_FOUND_NO_NAME
    return name, STATUS_FOUND


def _process_lookup(nif: str) -> None:
    if db.get_supplier(nif) is not None:
        return  # already cached, whatever the prior outcome was
    name, status = fetch_vies(nif)
    db.upsert_supplier(nif, name, status)


def _worker_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            nif = _lookup_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            _process_lookup(nif)
        except Exception as exc:
            print(f"[suppliers] lookup failed for {nif}: {exc}")


def start_supplier_worker() -> threading.Event:
    stop_event = threading.Event()
    thread = threading.Thread(target=_worker_loop, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event
