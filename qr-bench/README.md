# qr-bench

A local tool for Portuguese fiscal QR codes (the ones printed on
invoices/receipts per AT/SAF-T rules): extracting them reliably from
real-world images and PDFs, and turning what's extracted into
documents an accountant can actually validate, correct, and export.

It has two faces:
- **`/`** — the accountant workflow: documents grouped by client,
  a review queue, inline corrections, CSV/XLSX export.
- **`/bench`** — the original benchmark dashboard (decode strategy,
  timing, ground-truth accuracy). Internal tool, not accountant-facing.

## What it does

1. Accepts `.jpg` / `.png` / `.pdf` via drag-drop upload or a watched
   `./inbox` folder (polled every 2s).
2. PDFs are rasterized page-by-page at 300 DPI with PyMuPDF.
3. Each page is run through a decode pipeline: pyzbar → OpenCV
   `QRCodeDetector` → both again against preprocessed variants
   (grayscale, 2x upscale, adaptive threshold, 90/180/270 rotation).
   Whichever attempt succeeds first is recorded.
4. The decoded string is parsed (`key:value` pairs separated by `*`,
   fields looked up by key — never by position, since the `I2`..`I8`
   tax fields are optional).
5. Parsed fields are checked against a registry of 16 named flags
   (NIF checksums, date sanity, tax/total arithmetic, duplicate
   ATCUDs, atypical VAT rates, unknown suppliers, and more — see
   below). Nothing is rejected — mismatches are flagged, with the
   specific numbers involved.
6. Every attempt (success or failure) is stored in SQLite. Clean
   documents (no `erro`/`aviso` flags) are ready to validate;
   flagged ones wait for review. `/bench` polls the API every 2s to
   show aggregate stats, a decode-strategy breakdown, and ground-truth
   accuracy marking; `/` groups the same documents by client for the
   accountant to work through (see "Accountant workflow" below).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- **The system `zbar` shared library** — `pyzbar` loads it via `ctypes`
  at import time, so it must be present on the machine (or in the
  image) regardless of what's in `pyproject.toml`:
  - Arch: `sudo pacman -S zbar`
  - Debian/Ubuntu: `sudo apt install libzbar0`
  - macOS: `brew install zbar`
  - Skip this if you're running via Docker (below) — the image
    installs `libzbar0` itself.

## Setup

```bash
cd qr-bench
uv sync
```

## Running the app

```bash
uv run uvicorn qr_bench.main:app --reload
```

Open http://127.0.0.1:8000 for the accountant workflow (client list),
or http://127.0.0.1:8000/bench for the benchmark dashboard. Both share
the same upload dropzone and `./inbox` watcher — the background
watcher picks up files within 2 seconds and moves them to
`inbox/_processed/` once handled.

## Running with Docker

No local Python, `uv`, or `libzbar0` install needed — everything is
inside the image.

```bash
docker compose up --build
```

Open http://127.0.0.1:8000. Drop files into `./inbox` on the host (it's
bind-mounted into the container) exactly as you would without Docker.
The SQLite database and uploaded files live in named volumes
(`qrbench-data`, `qrbench-uploads`), so `docker compose down && docker
compose up` keeps everything — only `docker compose down -v` wipes them.

Health is exposed at `GET /health` (checks the DB is reachable and that
`pyzbar` imports successfully) and wired into the image's `HEALTHCHECK`.

**Environment variables** (set in `docker-compose.yml`, or exported
before `uv run uvicorn ...` locally):

| Variable | Default (local) | Default (compose) | Meaning |
| --- | --- | --- | --- |
| `DATABASE_PATH` | `./data/qrbench.db` | `/app/data/qrbench.db` | SQLite file location |
| `UPLOAD_DIR` | `./uploads` | `/app/uploads` | Where uploaded files are permanently stored |
| `INBOX_DIR` | `./inbox` | `/app/inbox` | Watched folder |
| `VIES_TIMEOUT` | `5.0` | `5.0` | Seconds before a VIES lookup is treated as failed |
| `VIES_ENABLED` | `true` | `false` | Whether supplier-name lookups hit the real VIES API |

`VIES_ENABLED=false` in `docker-compose.yml` is intentional — the
container works fully offline out of the box. Flip it to `"true"` (and
make sure the container has network access) to get real supplier names
instead of a "VIES lookup disabled" placeholder.

## Generating synthetic test data

There's no need for real invoices. This produces 46 synthetic QR
images with known ground truth: 40 with a spread of degradation levels
(valid NIF checksums, consistent tax arithmetic, a handful of true
negatives with no QR at all) plus 6 targeted feature samples - a
cancelled document, a duplicate ATCUD pair, a conflicting duplicate
(same ATCUD, different total), and a Madeira (PT-MA) document with
region-correct VAT rates:

```bash
uv run python scripts/generate_samples.py
```

Writes to `./samples/` plus `samples/ground_truth.json`. Drop the
contents of `./samples` onto the dashboard (or into `./inbox`) to see
the pipeline work against a realistic difficulty spread.

## Tests

```bash
uv run pytest
```

Covers the parser (including the optional `I2`..`I8` fields — never
assume positional order), the NIF mod-11 checksum, the validation
arithmetic, and an end-to-end run of the pipeline over a hermetically
generated sample set (no dependency on committed files). Every flag in
the registry has a dedicated synthetic sample that triggers it and a
test asserting the right code fires — the suite fails outright if a
flag and its sample ever fall out of sync (`tests/test_flags.py`). Also
covers client auto-creation and the consumidor final case, every status
transition, that corrections preserve full history without touching
the original data, and that CSV/XLSX exports round-trip with correct
Portuguese number formatting (`test_clients.py`, `test_lifecycle.py`,
`test_corrections.py`, `test_exporter.py`).

## Dashboard notes

- **"No QR present" vs "decode failed"**: these are tracked as
  different failure modes (`SEM_QR` vs `QR_ILEGIVEL`). OpenCV's
  detector can locate a QR-shaped region independently of whether it
  decodes the text, so if *no* attempt across all ~14 tries ever
  detects a QR-shaped pattern, the document is heuristically flagged
  as having no QR at all — not just an unreadable one. Marking rows
  with the "No QR" ground-truth button shows how accurate that
  heuristic actually is.
- **Ground truth**: mark any row Correct / Wrong / No QR from the
  table. The "Marked accuracy" stat card is computed only from marked
  rows.
- **Export**: `/api/export.csv` streams every stored row (JSON columns
  are valid JSON, not Python repr), plus `flag_codes`
  (semicolon-separated) and `highest_severity`.

## Flags

Every observation the pipeline can make about a document — a
consistency problem, a missing field, or just something worth noting
like "this is a sale to a walk-in consumer" — is a named flag from a
single registry in `qr_bench/flags.py`, exposed at `GET /api/flags` so
neither the dashboard nor `/glossario` hardcode any flag copy:

- **16 codes**, each with a severity (`erro` / `aviso` / `info`,
  color-coded as a badge), a short label, and Portuguese-language
  explanation/cause/action text.
- Click or hover a flag badge on any row for a popover with the
  specific numbers for *that* document (e.g. *"Bases (3,25 €) +
  imposto (0,75 €) = 4,00 €, mas o documento indica 4,50 €."*) plus
  the general cause/action guidance.
- `/glossario` (linked from the "Alertas" column header) lists every
  flag with its full description — useful as a standalone reference
  even without any documents loaded.
- Flag text is intentionally Portuguese-only, even with the dashboard
  set to English — it's written for a PT accountant, not translated
  UI chrome.

## Accountant workflow

Open `/`. Documents are grouped by **client**, resolved automatically
from the acquirer NIF (field `B`) on the QR code:

- The first time a NIF is seen, a client is auto-created (named after
  the NIF — rename it from the client page whenever). Every later
  document with the same NIF joins that client.
- `B == 999999990` (consumidor final) or a missing/undecodable `B`
  never creates a client — those documents land in a **"Não
  atribuído" / "Unattributed"** bucket instead, shown alongside real
  clients on the landing page.

Each document has a status: `recebido` (nothing extracted yet) →
`extraido` (clean) or `a_rever` (has an `erro`/`aviso` flag) →
`validado` → `exportado`. Per document:
- **Validar** — accept as-is.
- **Corrigir** — edit fields inline. The original decoded values and
  the raw QR string are never overwritten; every correction is
  recorded with who changed what field, from what, to what, and when.
  Correcting resolves the document to `validado`.
- **Rejeitar** — record a reason; sends it back to `a_rever`.
- **Validar todos os limpos** (bulk, per client) — validates every
  currently-clean (`extraido`) document for that client in one go.
  Flagged documents are never swept up by this.

**Export** (CSV or XLSX, via the Exportar dialog): choose scope (this
client / all clients / current filter), a period (month or date
range, filtered on field `F`), whether to include non-validated
documents (default: validado only), and a column preset (only
"Genérico" today — more, e.g. per-ERP presets, can be added as config
in `qr_bench/export_presets.py` without touching export code). CSV is
UTF-8 with a BOM, semicolon-delimited, comma-decimal numbers — opens
correctly in a PT-locale Excel without a manual encoding prompt. XLSX
gets real date/numeric cells (not strings), a frozen header row, an
autofilter, and a second sheet listing any flagged documents with
their explanations pulled from the flag registry. Filenames look like
`clientes_509442083_2026-08.xlsx`. Exporting marks every included
document `exportado` with a timestamp; exporting again is always
allowed and just refreshes that timestamp.

## Non-goals

No auth, no cloud deployment, no OCR. `qr_bench/ocr_fallback.py`
contains a stub `ocr_fallback(image)` that raises `NotImplementedError`
and is never called — it exists only as an explicit extension point.

## Project layout

```
qr_bench/                   FastAPI app + pipeline modules
qr_bench/flags.py           flag registry + evaluate_flags (single source of truth for flag text)
qr_bench/exporter.py        CSV/XLSX generation (pure formatting, no DB access)
qr_bench/export_presets.py  column preset registry (config, not code)
static/index.html           benchmark dashboard, served at /bench
static/clientes.html        accountant workflow, served at / and /clientes/{id}
static/glossario.html       flag reference page, reads GET /api/flags
scripts/                    synthetic sample generator
tests/                      pytest suite
inbox/                      watched folder (+ inbox/_processed/)
samples/                    generated synthetic test images (gitignored)
data/                       sqlite db (gitignored)
uploads/                    persisted uploaded files (gitignored; UPLOAD_DIR)
Dockerfile                  multi-stage build (uv-managed deps, non-root runtime)
docker-compose.yml          one service, named volumes for data/uploads, ./inbox bind mount
```
