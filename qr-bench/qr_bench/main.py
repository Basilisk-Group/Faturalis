"""FastAPI app: upload/inbox pipeline + JSON API for the dashboard."""

import csv
import io
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from qr_bench import config, db, export_presets, exporter, flags, pipeline, suppliers, watcher

_watcher_stop_event = None
_supplier_worker_stop_event = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watcher_stop_event, _supplier_worker_stop_event
    db.init_db()
    _watcher_stop_event = watcher.start_watcher()
    _supplier_worker_stop_event = suppliers.start_supplier_worker()
    yield
    if _watcher_stop_event:
        _watcher_stop_event.set()
    if _supplier_worker_stop_event:
        _supplier_worker_stop_event.set()


app = FastAPI(title="qr-bench", lifespan=lifespan)


class GroundTruthPayload(BaseModel):
    label: Literal["correct", "wrong", "no_qr"]


class ClientUpdatePayload(BaseModel):
    name: str | None = None
    notes: str | None = None


class CorrectionPayload(BaseModel):
    corrections: dict[str, str]
    changed_by: str | None = None


class RejectionPayload(BaseModel):
    reason: str
    changed_by: str | None = None


class ExportPeriod(BaseModel):
    mode: Literal["month", "range"]
    month: str | None = None  # "YYYY-MM"
    start: str | None = None  # "YYYY-MM-DD"
    end: str | None = None  # "YYYY-MM-DD"


class ExportPayload(BaseModel):
    format: Literal["csv", "xlsx"]
    scope: Literal["client", "all", "filter"]
    client_id: str | None = None  # numeric id as string, or "unattributed"
    period: ExportPeriod
    status_mode: Literal["validado_only", "include_unvalidated"] = "validado_only"
    preset: str = "generico"


# no-store on every static page: this is a local dev tool whose HTML/JS
# changes within a session, and FileResponse sets no cache headers of its
# own by default - browsers were serving stale copies after edits, which
# looks exactly like a layout bug that was actually already fixed.
_NO_CACHE_HEADERS = {"Cache-Control": "no-store"}


# The accountant-facing client list/detail views - the new default landing
# experience. The original benchmark dashboard moves to /bench (still the
# same static/index.html, unchanged) and stays the internal-only tool.
@app.get("/")
async def clientes_index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "clientes.html", headers=_NO_CACHE_HEADERS)


@app.get("/clientes/{client_id}")
async def clientes_detail(client_id: str) -> FileResponse:
    return FileResponse(config.STATIC_DIR / "clientes.html", headers=_NO_CACHE_HEADERS)


@app.get("/bench")
async def bench_index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html", headers=_NO_CACHE_HEADERS)


@app.get("/glossario")
async def glossario() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "glossario.html", headers=_NO_CACHE_HEADERS)


@app.get("/api/flags")
async def get_flags() -> dict:
    return flags.FLAGS


@app.get("/api/export/presets")
async def list_export_presets() -> list[dict]:
    return [{"key": key, "label_pt": preset["label_pt"]} for key, preset in export_presets.PRESETS.items()]


@app.get("/health")
async def health() -> JSONResponse:
    checks: dict[str, str] = {}

    try:
        with db.get_connection() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    try:
        import pyzbar.pyzbar  # noqa: F401

        checks["pyzbar"] = "ok"
    except Exception as exc:
        checks["pyzbar"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if healthy else "error", "checks": checks},
        status_code=200 if healthy else 503,
    )


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)) -> list[dict]:
    created: list[dict] = []
    for upload in files:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in config.SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"unsupported file type: {upload.filename}")

        content = await upload.read()
        # Persisted (not a tempfile) so the uploads volume actually holds
        # something and survives a container rebuild.
        stored_path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        stored_path.write_bytes(content)

        docs = pipeline.process_file(stored_path, source="upload", display_name=upload.filename)
        created.extend(docs)

    return created


@app.get("/api/documents")
async def get_documents() -> list[dict]:
    return db.list_documents()


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int) -> dict:
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@app.post("/api/documents/{doc_id}/ground_truth")
async def set_ground_truth(doc_id: int, payload: GroundTruthPayload) -> dict:
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return db.set_ground_truth(doc_id, payload.label)


@app.get("/api/stats")
async def get_stats() -> dict:
    return db.compute_stats()


# --- Clients ---------------------------------------------------------------


@app.get("/api/clients")
async def list_clients() -> list[dict]:
    clients = db.list_clients_with_stats()
    unattributed = db.unattributed_stats()
    unattributed.update({"id": None, "nif": None, "name": "Não atribuído", "notes": None})
    clients.append(unattributed)
    return clients


@app.patch("/api/clients/{client_id}")
async def update_client(client_id: int, payload: ClientUpdatePayload) -> dict:
    if db.get_client(client_id) is None:
        raise HTTPException(404, "client not found")
    return db.update_client(client_id, name=payload.name, notes=payload.notes)


@app.get("/api/clients/{client_id}/documents")
async def get_client_documents(client_id: str) -> list[dict]:
    if client_id == "unattributed":
        return db.list_unattributed_documents()
    try:
        cid = int(client_id)
    except ValueError:
        raise HTTPException(404, "client not found") from None
    if db.get_client(cid) is None:
        raise HTTPException(404, "client not found")
    return db.list_documents_for_client(cid)


@app.post("/api/clients/{client_id}/bulk_validate")
async def bulk_validate(client_id: str) -> dict:
    if client_id == "unattributed":
        count = db.bulk_validate(None)
    else:
        try:
            cid = int(client_id)
        except ValueError:
            raise HTTPException(404, "client not found") from None
        if db.get_client(cid) is None:
            raise HTTPException(404, "client not found")
        count = db.bulk_validate(cid)
    return {"validated_count": count}


# --- Document lifecycle ------------------------------------------------


@app.post("/api/documents/{doc_id}/validate")
async def validate_document(doc_id: int) -> dict:
    if db.get_document(doc_id) is None:
        raise HTTPException(404, "document not found")
    return db.set_document_status(doc_id, "validado")


@app.post("/api/documents/{doc_id}/correct")
async def correct_document(doc_id: int, payload: CorrectionPayload) -> dict:
    if db.get_document(doc_id) is None:
        raise HTTPException(404, "document not found")
    if not payload.corrections:
        raise HTTPException(400, "corrections must not be empty")
    return db.record_corrections(doc_id, payload.corrections, payload.changed_by)


@app.post("/api/documents/{doc_id}/reject")
async def reject_document(doc_id: int, payload: RejectionPayload) -> dict:
    if db.get_document(doc_id) is None:
        raise HTTPException(404, "document not found")
    return db.reject_document(doc_id, payload.reason, payload.changed_by)


@app.get("/api/documents/{doc_id}/corrections")
async def get_document_corrections(doc_id: int) -> list[dict]:
    if db.get_document(doc_id) is None:
        raise HTTPException(404, "document not found")
    return db.get_corrections(doc_id)


# --- Export ---------------------------------------------------------------


@app.post("/api/export")
async def export_documents(payload: ExportPayload) -> Response:
    client_id_int: int | None = None
    unattributed_only = False
    scope_label = "todos"

    if payload.scope in ("client", "filter") and payload.client_id:
        if payload.client_id == "unattributed":
            unattributed_only = True
            scope_label = "nao_atribuidos"
        else:
            try:
                client_id_int = int(payload.client_id)
            except ValueError:
                raise HTTPException(400, "invalid client_id") from None
            client = db.get_client(client_id_int)
            if client is None:
                raise HTTPException(404, "client not found")
            scope_label = client["nif"]

    if payload.period.mode == "month":
        if not payload.period.month:
            raise HTTPException(400, "month is required for month mode")
        year_str, month_str = payload.period.month.split("-")
        date_from, date_to = exporter.month_bounds(int(year_str), int(month_str))
        period_label = payload.period.month
    else:
        if not payload.period.start or not payload.period.end:
            raise HTTPException(400, "start and end are required for range mode")
        date_from = payload.period.start.replace("-", "")
        date_to = payload.period.end.replace("-", "")
        period_label = f"{payload.period.start}_a_{payload.period.end}"

    docs = db.list_documents_for_export(
        client_id=client_id_int,
        unattributed_only=unattributed_only,
        date_from=date_from,
        date_to=date_to,
        include_unvalidated=(payload.status_mode == "include_unvalidated"),
    )

    if payload.format == "csv":
        content = exporter.build_csv(docs, preset_key=payload.preset)
        media_type = "text/csv"
        extension = "csv"
    else:
        content = exporter.build_xlsx(docs, preset_key=payload.preset)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        extension = "xlsx"

    db.mark_exported([d["id"] for d in docs])

    filename = exporter.build_filename(scope_label, period_label, extension)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


CSV_COLUMNS = [
    "id",
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
    "supplier_name",
    "supplier_status",
    "tax_details",
    "all_fields",
    "validation_flags",
    "flag_codes",
    "highest_severity",
    "ground_truth",
    "ground_truth_at",
]


_JSON_CSV_COLUMNS = ("tax_details", "all_fields", "validation_flags")


@app.get("/api/export.csv")
async def export_csv() -> Response:
    docs = db.list_documents()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for doc in docs:
        row = dict(doc)
        row["flag_codes"] = ";".join(f["code"] for f in doc["validation_flags"])
        row["highest_severity"] = flags.highest_severity(doc["validation_flags"]) or ""
        for col in _JSON_CSV_COLUMNS:
            row[col] = json.dumps(row[col])
        writer.writerow(row)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=qr_bench_export.csv"},
    )
