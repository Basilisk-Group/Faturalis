import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


DATA_DIR = Path(os.environ.get("QR_BENCH_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("DATABASE_PATH", DATA_DIR / "qrbench.db"))

INBOX_DIR = Path(os.environ.get("INBOX_DIR", BASE_DIR / "inbox"))
INBOX_PROCESSED_DIR = INBOX_DIR / "_processed"

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads"))

SAMPLES_DIR = Path(os.environ.get("QR_BENCH_SAMPLES_DIR", BASE_DIR / "samples"))

STATIC_DIR = BASE_DIR / "static"

RASTER_DPI = 300
INBOX_POLL_INTERVAL_SECONDS = 2.0
THUMBNAIL_MAX_DIM = 220

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}

# VIES defaults to enabled for local dev; docker-compose.yml explicitly sets
# VIES_ENABLED=false so the container works out of the box with no network.
VIES_ENABLED = _env_bool("VIES_ENABLED", True)
VIES_TIMEOUT = float(os.environ.get("VIES_TIMEOUT", "5.0"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
INBOX_DIR.mkdir(parents=True, exist_ok=True)
INBOX_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
