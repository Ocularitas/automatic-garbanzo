"""Worker loop: drain the job queue serially.

For the POC, one worker is enough. The SELECT ... FOR UPDATE SKIP LOCKED
claim semantics mean adding more workers later is a process-count change,
not a code change.
"""
from __future__ import annotations

import logging
import time

from ingestion import jobs, pipeline
from shared.db import session_scope

log = logging.getLogger(__name__)


def run_one() -> bool:
    """Claim and process one job. Returns True if a job was processed."""
    with session_scope() as session:
        job = jobs.claim_next_job(session)
    if not job:
        return False
    log.info("claimed job %s for %s", job["id"], job["file_path"])
    try:
        result = pipeline.process_job(job)
        log.info("job %s done: %d chunks", job["id"], result.num_chunks)
    except Exception:
        # Already marked failed inside process_job. Don't crash the loop.
        pass
    return True


def run_forever(idle_sleep: float = 2.0) -> None:
    log.info("worker started")
    while True:
        try:
            did_work = run_one()
        except KeyboardInterrupt:
            log.info("worker stopping")
            return
        if not did_work:
            time.sleep(idle_sleep)
