"""Polls ./inbox for new files and runs them through the pipeline.

Simple polling on a daemon thread rather than an OS filesystem-events
library - keeps the dependency list small and is more than fast enough
at a 2s interval for a local prototype.
"""

import shutil
import threading
import time

from qr_bench import config, pipeline


def _move_to_processed(path) -> None:
    dest = config.INBOX_PROCESSED_DIR / path.name
    if dest.exists():
        dest = config.INBOX_PROCESSED_DIR / f"{path.stem}_{int(time.time())}{path.suffix}"
    shutil.move(str(path), str(dest))


def _scan_and_process() -> None:
    for path in sorted(config.INBOX_DIR.iterdir()):
        if path.is_dir():
            continue
        if path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
            continue
        try:
            pipeline.process_file(path, source="inbox")
        except Exception as exc:
            print(f"[watcher] failed to process {path.name}: {exc}")
        finally:
            _move_to_processed(path)


def _poll_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        _scan_and_process()
        stop_event.wait(config.INBOX_POLL_INTERVAL_SECONDS)


def start_watcher() -> threading.Event:
    stop_event = threading.Event()
    thread = threading.Thread(target=_poll_loop, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event
