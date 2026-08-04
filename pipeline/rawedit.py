"""
pipeline/rawedit.py — mutate a capture's live raw content with versioning.

Every change to a capture's raw content (human edit, pad refresh, restore)
goes through :func:`replace_raw_content`, which atomically swaps the live file
and records an immutable :data:`raw_revisions` snapshot. This is the single
code path shared by the synchronous edit route (``web/app.py``) and the async
pad-refresh worker (``web/worker.py``), so both produce first-class history.

Raw files are stored read-only (``0o444``); this module owns the chmod dance
and the atomic swap so callers only supply the new content as a string.
"""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import config
import db


def normalize(content: str) -> str:
    """Normalise line endings to LF so hashes and diffs are stable regardless
    of what the browser or pad submitted."""
    return content.replace('\r\n', '\n').replace('\r', '\n')


def _revisions_dir() -> Path:
    d = config.RAW_DIR / 'revisions'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seal_prior_revision(capture_id: int, live_path: Path, meeting_date: str) -> None:
    """If the latest revision's snapshot still points at the live file, copy the
    current live content into its own immutable snapshot and repoint the row."""
    latest = db.get_latest_raw_revision(capture_id)
    if not latest:
        return
    if Path(latest['snapshot_path']) != live_path:
        return  # already sealed into its own snapshot
    if not live_path.exists():
        return  # nothing to preserve
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    sealed = _revisions_dir() / f'raw_{meeting_date}.{ts}.orig.txt'
    sealed.write_text(live_path.read_text('utf-8'), encoding='utf-8')
    sealed.chmod(0o444)
    db.update_raw_revision_snapshot(latest['id'], sealed)


def replace_raw_content(capture, new_content: str, *, source: str,
                        author: str | None, note: str | None,
                        restored_from: int | None = None) -> int:
    """Make ``new_content`` the live raw content for ``capture`` and record it
    as a new revision.

    ``capture`` is a ``raw_captures`` row (mapping-like). ``source`` is one of
    the ``raw_revisions.source`` values. Returns the new revision id.

    Steps: normalise content, archive an immutable read-only snapshot under
    ``revisions/``, atomically swap the live file (temp write + ``os.replace``),
    update ``raw_captures``, then insert the revision row **last** so the row
    only exists once its snapshot is durably on disk.
    """
    content = normalize(new_content)
    encoded = content.encode('utf-8')
    sha256 = hashlib.sha256(encoded).hexdigest()
    size_bytes = len(encoded)
    now = datetime.now(timezone.utc).isoformat()

    meeting_date = capture['meeting_date']
    live_path = Path(capture['file_path'])

    # 0. Seal the outgoing content. The initial/backfilled revision's snapshot
    #    points at the live file (no copy was made at fetch/backfill time). If
    #    that is still the case, archive the current live content into its own
    #    immutable snapshot now — before we overwrite it — so restoring the
    #    original still works.
    _seal_prior_revision(capture['id'], live_path, meeting_date)

    # 1. Archive an immutable snapshot of the new content.
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot_path = _revisions_dir() / f'raw_{meeting_date}.{ts}.txt'
    snapshot_path.write_text(content, encoding='utf-8')
    snapshot_path.chmod(0o444)

    # 2. Atomically swap the live file (temp in same dir -> os.replace).
    live_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = live_path.with_suffix(live_path.suffix + '.tmp')
    tmp_path.write_text(content, encoding='utf-8')
    if live_path.exists():
        live_path.chmod(0o644)
    os.replace(tmp_path, live_path)
    live_path.chmod(0o444)

    # 3. Update the capture pointer, then record the revision.
    db.update_capture_content(
        capture_id=capture['id'],
        sha256=sha256,
        size_bytes=size_bytes,
        captured_at=now,
    )
    return db.insert_raw_revision(
        raw_capture_id=capture['id'],
        created_at=now,
        source=source,
        author=author,
        note=note,
        snapshot_path=snapshot_path,
        sha256=sha256,
        size_bytes=size_bytes,
        restored_from=restored_from,
    )
