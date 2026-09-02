"""SQLite storage for every decode attempt. Plain sqlite3, no ORM."""

import json
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from qr_bench import config, flags as flags_module

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    source TEXT NOT NULL,
    page_number INTEGER,
    processed_at TEXT NOT NULL,
    decode_success INTEGER NOT NULL,
    qr_pattern_detected INTEGER NOT NULL,
    strategy TEXT,
    elapsed_ms REAL NOT NULL,
    raw_qr TEXT,
    field_a TEXT,
    field_b TEXT,
    field_c TEXT,
    field_d TEXT,
    field_e TEXT,
    field_f TEXT,
    field_g TEXT,
    field_h TEXT,
    field_i1 TEXT,
    field_n TEXT,
    field_o TEXT,
    field_p TEXT,
    field_q TEXT,
    field_r TEXT,
    cancelled INTEGER NOT NULL DEFAULT 0,
    duplicate_of INTEGER,
    tax_details_json TEXT,
    all_fields_json TEXT,
    validation_flags_json TEXT,
    thumbnail_b64 TEXT,
    ground_truth TEXT,
    ground_truth_at TEXT,
    status TEXT NOT NULL DEFAULT 'recebido',
    client_id INTEGER,
    rejection_reason TEXT,
    rejected_at TEXT,
    rejected_by TEXT,
    exported_at TEXT
);
"""

# One row per real client, matched by acquirer NIF (field B). Consumidor
# final (B == 999999990) and documents with no usable B deliberately never
# get a client row - they show up as "unattributed" in the accountant UI
# instead. name defaults to the NIF on auto-creation and is meant to be
# renamed by the accountant.
CLIENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nif TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    notes TEXT
);
"""

# Full audit trail for manual corrections. The document's own field_*/
# all_fields_json columns are NEVER overwritten - a correction is purely
# additive rows here. original_value is a snapshot of what the field held
# at correction time (in practice always the untouched decode/parse
# output, since those columns never change), so the full history is
# readable without cross-referencing the document row.
CORRECTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    original_value TEXT,
    corrected_value TEXT,
    changed_by TEXT,
    changed_at TEXT NOT NULL
);
"""

# VIES lookup cache, keyed by NIF so the same supplier is never looked up
# twice. status is one of: found, found_no_name, not_registered, error.
# "not_registered" and "error" are deliberately distinct - VIES only lists
# businesses registered for intra-EU trade, so a purely domestic supplier
# will legitimately come back not-registered even with a perfectly valid
# NIF. That is not the same thing as a lookup that failed or timed out.
SUPPLIERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    nif TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL,
    looked_up_at TEXT NOT NULL
);
"""

# Columns added after the initial release. Applied via ALTER TABLE on top of
# SCHEMA so existing local dev databases pick them up without a manual reset.
# (name -> DDL type/default, in the order they should be added)
MIGRATIONS: list[tuple[str, str]] = [
    ("cancelled", "INTEGER NOT NULL DEFAULT 0"),
    ("duplicate_of", "INTEGER"),
    ("field_p", "TEXT"),
    ("status", "TEXT NOT NULL DEFAULT 'recebido'"),
    ("client_id", "INTEGER"),
    ("rejection_reason", "TEXT"),
    ("rejected_at", "TEXT"),
    ("rejected_by", "TEXT"),
    ("exported_at", "TEXT"),
]

# Strategies that count as "first attempt" (no preprocessing needed).
FIRST_ATTEMPT_STRATEGIES = {"pyzbar:original", "opencv:original"}

_JSON_COLUMNS = ("tax_details_json", "all_fields_json", "validation_flags_json")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl_type in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(SCHEMA)
        conn.execute(SUPPLIERS_SCHEMA)
        conn.execute(CLIENTS_SCHEMA)
        conn.execute(CORRECTIONS_SCHEMA)
        _ensure_columns(conn, "documents", MIGRATIONS)


def _enrich_flag(entry: dict[str, Any]) -> dict[str, Any]:
    code = entry["code"]
    detail = entry.get("detail", {})
    registry_entry = flags_module.FLAGS.get(code)
    return {
        "code": code,
        "detail": detail,
        "message": flags_module.render_message(code, detail) if registry_entry else "",
        "severity": registry_entry["severity"] if registry_entry else "info",
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for col in _JSON_COLUMNS:
        raw = record.get(col)
        key = col.removesuffix("_json")
        record[key] = json.loads(raw) if raw else ({} if col != "validation_flags_json" else [])
        del record[col]
    record["decode_success"] = bool(record["decode_success"])
    record["qr_pattern_detected"] = bool(record["qr_pattern_detected"])
    record["cancelled"] = bool(record["cancelled"])

    # FORNECEDOR_DESCONHECIDO depends on the (async, join-only) supplier
    # lookup outcome, so it's never stored - it's decided fresh on every
    # read from the already-joined supplier_status/field_a columns.
    supplier_flag = flags_module.evaluate_supplier_flag(
        record.get("field_a"), record.get("supplier_status")
    )
    stored_flags = [_enrich_flag(entry) for entry in record["validation_flags"]]
    if supplier_flag is not None:
        stored_flags.append(_enrich_flag(supplier_flag))
    record["validation_flags"] = stored_flags

    # Always attached (not just for export) so every consumer - the
    # accountant UI included - can show the corrected value instead of a
    # stale original without a second round trip. record['all_fields']
    # itself is untouched, as always.
    record["effective_fields"] = get_effective_fields(record)

    return record


def insert_document(record: dict[str, Any]) -> int:
    columns = [
        "filename",
        "source",
        "page_number",
        "processed_at",
        "decode_success",
        "qr_pattern_detected",
        "strategy",
        "elapsed_ms",
        "raw_qr",
        "field_a",
        "field_b",
        "field_c",
        "field_d",
        "field_e",
        "field_f",
        "field_g",
        "field_h",
        "field_i1",
        "field_n",
        "field_o",
        "field_p",
        "field_q",
        "field_r",
        "cancelled",
        "duplicate_of",
        "tax_details_json",
        "all_fields_json",
        "validation_flags_json",
        "thumbnail_b64",
        "status",
        "client_id",
    ]
    values = [record.get(col) for col in columns]
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO documents ({', '.join(columns)}) VALUES ({placeholders})"
    with get_connection() as conn:
        cursor = conn.execute(sql, values)
        return cursor.lastrowid


_DOCUMENTS_WITH_SUPPLIER_SQL = """
SELECT d.*, s.name AS supplier_name, s.status AS supplier_status,
       c.name AS client_name, c.nif AS client_nif
FROM documents d
LEFT JOIN suppliers s ON s.nif = d.field_a
LEFT JOIN clients c ON c.id = d.client_id
"""


def list_documents() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(f"{_DOCUMENTS_WITH_SUPPLIER_SQL} ORDER BY d.id DESC").fetchall()
    return [_row_to_dict(row) for row in rows]


def get_document(doc_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(f"{_DOCUMENTS_WITH_SUPPLIER_SQL} WHERE d.id = ?", (doc_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_supplier(nif: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM suppliers WHERE nif = ?", (nif,)).fetchone()
    return dict(row) if row else None


def upsert_supplier(nif: str, name: str | None, status: str) -> None:
    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO suppliers (nif, name, status, looked_up_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(nif) DO UPDATE SET name = excluded.name, status = excluded.status,
                looked_up_at = excluded.looked_up_at
            """,
            (nif, name, status, now),
        )


def find_first_by_atcud(atcud: str) -> dict[str, Any] | None:
    """Earliest successfully-decoded row with this ATCUD, if any."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE field_h = ? AND decode_success = 1 ORDER BY id ASC LIMIT 1",
            (atcud,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def set_ground_truth(doc_id: int, label: str) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET ground_truth = ?, ground_truth_at = ? WHERE id = ?",
            (label, now, doc_id),
        )
    return get_document(doc_id)


def compute_stats() -> dict[str, Any]:
    docs = list_documents()
    total = len(docs)

    stats: dict[str, Any] = {
        "total": total,
        "decoded_pct": 0.0,
        "first_attempt_pct": 0.0,
        "needed_preprocessing_pct": 0.0,
        "no_qr_pattern_pct": 0.0,
        "median_decode_ms": 0.0,
        "strategy_counts": {},
        "cancelled_count": 0,
        "ground_truth_counts": {"correct": 0, "wrong": 0, "no_qr": 0},
        "ground_truth_accuracy": None,
        "no_qr_heuristic_accuracy": None,
    }
    if total == 0:
        return stats

    stats["cancelled_count"] = sum(1 for d in docs if d["cancelled"])

    decoded = [d for d in docs if d["decode_success"]]
    first_attempt = [d for d in decoded if d["strategy"] in FIRST_ATTEMPT_STRATEGIES]
    needed_preprocessing = [d for d in decoded if d["strategy"] not in FIRST_ATTEMPT_STRATEGIES]
    no_pattern = [d for d in docs if not d["qr_pattern_detected"]]

    stats["decoded_pct"] = 100.0 * len(decoded) / total
    stats["first_attempt_pct"] = 100.0 * len(first_attempt) / total
    stats["needed_preprocessing_pct"] = 100.0 * len(needed_preprocessing) / total
    stats["no_qr_pattern_pct"] = 100.0 * len(no_pattern) / total

    if decoded:
        stats["median_decode_ms"] = statistics.median(d["elapsed_ms"] for d in decoded)

    strategy_counts: dict[str, int] = {}
    for d in decoded:
        strategy_counts[d["strategy"]] = strategy_counts.get(d["strategy"], 0) + 1
    stats["strategy_counts"] = strategy_counts

    # Cancelled documents are excluded from every accuracy computation below -
    # correctness of a cancelled invoice's parse isn't a meaningful signal
    # for how well the pipeline reads live documents.
    marked = [d for d in docs if d["ground_truth"] and not d["cancelled"]]
    gt_counts = {"correct": 0, "wrong": 0, "no_qr": 0}
    for d in marked:
        if d["ground_truth"] in gt_counts:
            gt_counts[d["ground_truth"]] += 1
    stats["ground_truth_counts"] = gt_counts
    if marked:
        stats["ground_truth_accuracy"] = 100.0 * gt_counts["correct"] / len(marked)

    if marked:
        # Heuristic says "no QR" when no attempt ever detected a pattern; agrees
        # with ground truth when that matches whether the row was marked no_qr.
        agree = sum(
            1
            for d in marked
            if (not d["qr_pattern_detected"]) == (d["ground_truth"] == "no_qr")
        )
        stats["no_qr_heuristic_accuracy"] = 100.0 * agree / len(marked)

    return stats


# --- Clients -----------------------------------------------------------


def get_or_create_client(nif: str) -> int:
    """Finds the client for this NIF, auto-creating one (named after the
    NIF, renamable later) on first sight. Callers must never pass the
    consumidor final NIF or an absent B here - that's an unattributed
    document, not a client (see pipeline.py).
    """
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM clients WHERE nif = ?", (nif,)).fetchone()
        if row:
            return row["id"]
        cursor = conn.execute(
            "INSERT INTO clients (nif, name, notes) VALUES (?, ?, NULL)", (nif, nif)
        )
        return cursor.lastrowid


def get_client(client_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


def update_client(client_id: int, name: str | None = None, notes: str | None = None) -> dict[str, Any] | None:
    updates = []
    values: list[Any] = []
    if name is not None:
        updates.append("name = ?")
        values.append(name)
    if notes is not None:
        updates.append("notes = ?")
        values.append(notes)
    if updates:
        values.append(client_id)
        with get_connection() as conn:
            conn.execute(f"UPDATE clients SET {', '.join(updates)} WHERE id = ?", values)
    return get_client(client_id)


def _current_month_bounds() -> tuple[str, str]:
    today = datetime.now(UTC).date()
    start = today.replace(day=1)
    next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# NOTE: total_value_current_period sums the ORIGINAL field_o via SQL, not
# any corrected value - correction-aware totals would need per-document
# resolution in Python, which isn't worth the cost for a summary card.
# Document-level display/export DOES use the corrected value - see
# get_effective_fields.
_CLIENT_STATS_SQL = """
SELECT
    COUNT(d.id) AS document_count,
    SUM(CASE WHEN d.status = 'a_rever' THEN 1 ELSE 0 END) AS review_count,
    SUM(
        CASE WHEN d.field_f BETWEEN ? AND ? AND d.field_o IS NOT NULL
        THEN CAST(d.field_o AS REAL) ELSE 0 END
    ) AS total_value_current_period
FROM documents d
"""


def list_clients_with_stats() -> list[dict[str, Any]]:
    month_start, month_end = _current_month_bounds()
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.id, c.nif, c.name, c.notes,
                   COUNT(d.id) AS document_count,
                   SUM(CASE WHEN d.status = 'a_rever' THEN 1 ELSE 0 END) AS review_count,
                   SUM(
                       CASE WHEN d.field_f BETWEEN ? AND ? AND d.field_o IS NOT NULL
                       THEN CAST(d.field_o AS REAL) ELSE 0 END
                   ) AS total_value_current_period
            FROM clients c
            LEFT JOIN documents d ON d.client_id = c.id
            GROUP BY c.id
            ORDER BY c.name COLLATE NOCASE
            """,
            (month_start, month_end),
        ).fetchall()
    return [dict(row) for row in rows]


def unattributed_stats() -> dict[str, Any]:
    """Same shape as one row of list_clients_with_stats(), for the
    synthetic 'unattributed' bucket (consumidor final + undecodable docs)
    shown on the client list alongside real clients."""
    month_start, month_end = _current_month_bounds()
    with get_connection() as conn:
        row = conn.execute(
            f"{_CLIENT_STATS_SQL} WHERE d.client_id IS NULL",
            (month_start, month_end),
        ).fetchone()
    result = dict(row)
    result["document_count"] = result["document_count"] or 0
    result["review_count"] = result["review_count"] or 0
    result["total_value_current_period"] = result["total_value_current_period"] or 0.0
    return result


def list_documents_for_client(client_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            f"{_DOCUMENTS_WITH_SUPPLIER_SQL} WHERE d.client_id = ? ORDER BY d.id DESC",
            (client_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def list_unattributed_documents() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            f"{_DOCUMENTS_WITH_SUPPLIER_SQL} WHERE d.client_id IS NULL ORDER BY d.id DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


# --- Document lifecycle --------------------------------------------------


def set_document_status(doc_id: int, status: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        conn.execute("UPDATE documents SET status = ? WHERE id = ?", (status, doc_id))
    return get_document(doc_id)


def reject_document(doc_id: int, reason: str, changed_by: str | None) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE documents
            SET status = 'a_rever', rejection_reason = ?, rejected_at = ?, rejected_by = ?
            WHERE id = ?
            """,
            (reason, now, changed_by, doc_id),
        )
    return get_document(doc_id)


def bulk_validate(client_id: int | None) -> int:
    """Validates every 'extraido' (clean, unreviewed) document for a
    client - or, if client_id is None, every unattributed one. Returns the
    number of rows changed.
    """
    with get_connection() as conn:
        if client_id is None:
            cursor = conn.execute(
                "UPDATE documents SET status = 'validado' WHERE client_id IS NULL AND status = 'extraido'"
            )
        else:
            cursor = conn.execute(
                "UPDATE documents SET status = 'validado' WHERE client_id = ? AND status = 'extraido'",
                (client_id,),
            )
        return cursor.rowcount


# --- Corrections ---------------------------------------------------------


def get_effective_fields(doc: dict[str, Any]) -> dict[str, str]:
    """The document's parsed fields with any corrections applied on top -
    this is the current truth for display/export. doc['all_fields'] itself
    is never touched and always reflects the original decode/parse output.
    """
    effective = dict(doc.get("all_fields") or {})
    effective.update(get_latest_correction_values(doc["id"]))
    return effective


def record_corrections(doc_id: int, corrections: dict[str, str], changed_by: str | None) -> dict[str, Any] | None:
    """One audit row per changed field - original_value is whatever the
    document's effective value was immediately before this edit (so a
    second correction to the same field shows a proper before/after diff,
    not just a re-statement of the very first original). Recording a
    correction is a resolution action, same as an outright Validar.
    """
    doc = get_document(doc_id)
    if doc is None:
        return None
    current_values = get_effective_fields(doc)

    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        for field_key, corrected_value in corrections.items():
            conn.execute(
                """
                INSERT INTO corrections
                    (document_id, field_key, original_value, corrected_value, changed_by, changed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc_id, field_key, current_values.get(field_key), corrected_value, changed_by, now),
            )
        conn.execute("UPDATE documents SET status = 'validado' WHERE id = ?", (doc_id,))
    return get_document(doc_id)


def get_corrections(doc_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM corrections WHERE document_id = ? ORDER BY id ASC", (doc_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_correction_values(doc_id: int) -> dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT field_key, corrected_value FROM corrections WHERE document_id = ? ORDER BY id ASC",
            (doc_id,),
        ).fetchall()
    latest: dict[str, str] = {}
    for row in rows:
        latest[row["field_key"]] = row["corrected_value"]
    return latest


# --- Export ---------------------------------------------------------------


def list_documents_for_export(
    client_id: int | None,
    unattributed_only: bool,
    date_from: str | None,
    date_to: str | None,
    include_unvalidated: bool,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if unattributed_only:
        clauses.append("d.client_id IS NULL")
    elif client_id is not None:
        clauses.append("d.client_id = ?")
        params.append(client_id)

    if date_from:
        clauses.append("d.field_f >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("d.field_f <= ?")
        params.append(date_to)

    if not include_unvalidated:
        clauses.append("d.status IN ('validado', 'exportado')")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"{_DOCUMENTS_WITH_SUPPLIER_SQL} {where} ORDER BY d.field_f ASC, d.id ASC"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    # _row_to_dict already attaches effective_fields (correction-aware),
    # which is what exporter.py (a pure formatting layer, no DB access)
    # relies on.
    return [_row_to_dict(row) for row in rows]


def mark_exported(doc_ids: list[int]) -> None:
    if not doc_ids:
        return
    now = datetime.now(UTC).isoformat()
    placeholders = ", ".join("?" for _ in doc_ids)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE documents SET status = 'exportado', exported_at = ? WHERE id IN ({placeholders})",
            [now, *doc_ids],
        )
