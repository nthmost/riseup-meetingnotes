"""web/worker.py — background thread that drains the pipeline_jobs queue."""
import json
import logging
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import db
import urllib.error
import urllib.request
from pipeline.publish import publish as _publish
from pipeline.rawedit import replace_raw_content
from pipeline.run import run_pipeline, generate_summary_for_txn


def _auto_preview(txn_id: int, meeting_date: str) -> None:
    """Publish the transformation's output to the preview namespace."""
    txn = db.get_transformation(txn_id)
    if not txn or not txn['output_path']:
        return
    path = Path(txn['output_path'])
    if not path.exists():
        return
    content = path.read_text('utf-8')
    preview_title = config.WIKI_PREVIEW_PREFIX + meeting_date
    _publish(txn_id, meeting_date, content, page_title=preview_title)
    db.record_preview_publish(txn_id, preview_title,
                              datetime.now(timezone.utc).isoformat())
    db.record_publish(txn_id, None, None)  # clear published_to; this is preview only
    log.info('auto-previewed txn=%d as %s', txn_id, preview_title)


def _do_refresh_raw(capture: dict) -> None:
    """Fetch pad content and record it as a new raw revision.

    Delegates the live-file swap and versioning to
    :func:`pipeline.rawedit.replace_raw_content`, so a pad refresh produces a
    tracked ``pad_refresh`` revision (not an untracked ``.bak`` file).
    """
    req = urllib.request.Request(
        capture['source_url'],
        headers={'User-Agent': config.USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        new_content = resp.read().decode('utf-8')

    rev_id = replace_raw_content(
        capture, new_content,
        source='pad_refresh', author=None, note='pad re-fetch',
    )
    log.info('refresh_raw: updated capture %d (revision %d)',
             capture['id'], rev_id)


def _run_job(job: dict, capture: dict, flags: dict) -> int:
    """Execute one job and return the result_txn_id."""
    job_type = flags.get('job_type', 'pipeline')

    if job_type == 'refresh_raw':
        _do_refresh_raw(capture)
        return None  # no transformation created

    if job_type == 'generate_summary':
        txn_id = flags['txn_id']
        generate_summary_for_txn(txn_id)
        return txn_id  # same txn, no new row

    if job_type == 'pipeline':
        txn_id = run_pipeline(
            meeting_date=capture['meeting_date'],
            parent_txn_id=job['parent_txn_id'],
            flags=flags,
        )
        try:
            _auto_preview(txn_id, capture['meeting_date'])
        except Exception:
            log.warning('auto-preview failed for txn=%d (non-fatal)', txn_id, exc_info=True)
        return txn_id

    if job_type in ('publish', 'publish_preview'):
        txn_id = flags['txn_id']
        txn = db.get_transformation(txn_id)
        content = Path(txn['output_path']).read_text('utf-8')

        if job_type == 'publish_preview':
            preview_title = flags['preview_title']
            _publish(txn_id, capture['meeting_date'], content, page_title=preview_title)
            db.record_preview_publish(txn_id, preview_title,
                                      datetime.now(timezone.utc).isoformat())
            db.record_publish(txn_id, None, None)
        else:
            _publish(txn_id, capture['meeting_date'], content)

        return txn_id

    raise ValueError(f"Unknown job_type: {job_type!r}")


def _loop():
    while True:
        try:
            job = db.claim_next_pending_job()
            if job is None:
                time.sleep(2)
                continue

            capture = db.get_capture_by_id(job['capture_id'])
            flags = json.loads(job['flags'] or '{}')

            try:
                result_txn_id = _run_job(job, capture, flags)
                db.update_job_done(
                    job['id'],
                    datetime.now(timezone.utc).isoformat(),
                    result_txn_id,
                )
            except Exception as exc:
                db.update_job_error(
                    job['id'],
                    datetime.now(timezone.utc).isoformat(),
                    str(exc),
                )
        except Exception:
            log.exception('worker: unexpected error in job loop')
            time.sleep(5)


def start():
    t = threading.Thread(target=_loop, daemon=True, name='pipeline-worker')
    t.start()
    return t
