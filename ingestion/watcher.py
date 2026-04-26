"""Folder watcher. Enqueues a job for every new or modified PDF.

Behind a small interface so swapping for a SharePoint connector later only
touches this file.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ingestion import jobs
from ingestion.hashing import hash_file
from shared.db import session_scope
from shared.identity import current_identity

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, on_pdf: Callable[[Path], None]):
        self._on_pdf = on_pdf

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.suffix.lower() == ".pdf":
            self._on_pdf(path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.suffix.lower() == ".pdf":
            self._on_pdf(path)


def enqueue_path(path: Path) -> None:
    """Hash a file and insert a pending job, deduping by content hash."""
    try:
        content_hash = hash_file(path)
    except FileNotFoundError:
        return  # file vanished between event and read
    identity = current_identity()
    with session_scope() as session:
        job_id = jobs.enqueue_job(
            session,
            content_hash=content_hash,
            file_path=str(path.resolve()),
            user_id=identity.user_id,
            group_id=identity.group_id,
        )
    if job_id:
        log.info("enqueued %s (job=%s)", path.name, job_id)
    else:
        log.debug("dedup'd %s (already pending/running)", path.name)


def scan_existing(folder: Path) -> int:
    """One-shot scan: enqueue every PDF currently in the folder. Useful at startup."""
    n = 0
    for path in folder.rglob("*.pdf"):
        enqueue_path(path)
        n += 1
    return n


def run(folder: Path) -> None:
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    log.info("watching %s", folder)
    n = scan_existing(folder)
    log.info("initial scan enqueued %d files", n)

    observer = Observer()
    observer.schedule(_Handler(enqueue_path), str(folder), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
