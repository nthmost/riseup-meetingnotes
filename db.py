"""
db.py — SQLite schema and all data access for nbmeetingnotes.

Three tables:
  raw_captures     — one row per meeting, points to the archived raw .txt file
  transformations  — every pipeline pass, forming a lineage tree per capture
  quality_ratings  — thumbs up/down + issue detail submitted by wiki members
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

_db_path_override: Path | None = None


@contextmanager
def override_db(path):
    """Context manager for tests: redirect all DB calls to the given path.

    Example::

        with db.override_db(':memory:'):
            db.init()
            # ... test code ...

    For ':memory:', a keeper connection is held open so the shared-cache
    database survives between the multiple connect() calls that db functions
    make internally (SQLite destroys an in-memory DB when the last connection
    to it closes).
    """
    global _db_path_override
    _db_path_override = ':memory:'  if path == ':memory:' else Path(path)
    keeper = None
    if path == ':memory:':
        keeper = sqlite3.connect('file::memory:?cache=shared', uri=True,
                                 check_same_thread=False)
    try:
        yield
    finally:
        _db_path_override = None
        if keeper is not None:
            keeper.close()


@contextmanager
def conn():
    path = _db_path_override if _db_path_override is not None else config.DB_PATH
    if path == ':memory:':
        # Shared-cache URI keeps the schema alive across multiple connect() calls
        # within the same process, which is required for test isolation.
        c = sqlite3.connect('file::memory:?cache=shared', uri=True, check_same_thread=False)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys = ON')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with conn() as c:
        def _add_column(table: str, col: str, typedef: str) -> None:
            """Add a column if it doesn't exist; re-raise on any other error."""
            try:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typedef}')
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e):
                    raise

        # Tables first, then migrations
        c.executescript("""
        CREATE TABLE IF NOT EXISTS raw_captures (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_date TEXT    UNIQUE NOT NULL,   -- YYYY_MM_DD
            captured_at  TEXT    NOT NULL,           -- ISO-8601 UTC
            source_url   TEXT    NOT NULL,
            file_path    TEXT    NOT NULL,           -- absolute path on disk
            sha256       TEXT    NOT NULL,
            size_bytes   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transformations (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_capture_id        INTEGER NOT NULL REFERENCES raw_captures(id),
            parent_id             INTEGER REFERENCES transformations(id),
            run_at                TEXT    NOT NULL,
            pipeline_version      TEXT    NOT NULL,  -- git commit hash
            pipeline_script       TEXT    NOT NULL,  -- script filename / identifier
            model_name            TEXT,              -- e.g. claude-haiku-4-5-20251001
            flags                 TEXT    NOT NULL DEFAULT '{}',  -- JSON
            input_sha256          TEXT    NOT NULL,  -- sha256 of what was fed in
            output_path           TEXT,
            output_sha256         TEXT,
            processor_version     TEXT,              -- git hash of the processor script repo
            source_wiki_page      TEXT,              -- wiki page title used as input (re-runs)
            source_wiki_revid     INTEGER,           -- MediaWiki revision ID used as input
            preview_published_to  TEXT,
            preview_published_at  TEXT,
            duration_seconds      REAL,
            token_usage           TEXT,   -- JSON {input_tokens, output_tokens, cost_usd}
            artifact_lines_removed INTEGER,
            sections_found        INTEGER,
            published_to          TEXT,   -- wiki page title if published
            published_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS quality_ratings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            transformation_id   INTEGER NOT NULL REFERENCES transformations(id),
            rated_at            TEXT    NOT NULL,
            rated_by            TEXT    NOT NULL,   -- NB wiki username
            rating              TEXT    NOT NULL CHECK(rating IN ('up','down')),
            issues              TEXT    NOT NULL DEFAULT '[]',  -- JSON array
            problem_excerpt     TEXT,
            problem_description TEXT
        );

        CREATE TABLE IF NOT EXISTS pipeline_jobs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at     TEXT    NOT NULL,
            started_at     TEXT,
            finished_at    TEXT,
            status         TEXT    NOT NULL DEFAULT 'pending'
                           CHECK(status IN ('pending','running','done','error')),
            capture_id     INTEGER NOT NULL REFERENCES raw_captures(id),
            parent_txn_id  INTEGER REFERENCES transformations(id),
            flags          TEXT    NOT NULL DEFAULT '{}',
            result_txn_id  INTEGER REFERENCES transformations(id),
            error          TEXT
        );

        -- migrations (ALTER TABLE is idempotent via try/except in init())

        CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_unique
            ON quality_ratings(transformation_id, rated_by);
        CREATE INDEX IF NOT EXISTS idx_txn_capture ON transformations(raw_capture_id);
        CREATE INDEX IF NOT EXISTS idx_txn_parent  ON transformations(parent_id);
        CREATE INDEX IF NOT EXISTS idx_rating_txn  ON quality_ratings(transformation_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON pipeline_jobs(status, created_at);

        CREATE TABLE IF NOT EXISTS template_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at    TEXT    NOT NULL,
            revid         INTEGER NOT NULL UNIQUE,
            sha256        TEXT    NOT NULL,
            -- 1 = acknowledged by admin; first-ever row is auto-acknowledged (it
            -- is the baseline, not a change). New revids start at 0 until acknowledged.
            acknowledged  INTEGER NOT NULL DEFAULT 0
        );
        """)

        # Idempotent column migrations (run after table creation)
        for col, typedef in [
            ('preview_published_to',  'TEXT'),
            ('preview_published_at',  'TEXT'),
            ('processor_version',     'TEXT'),    # git hash of the processor script repo
            ('source_wiki_page',      'TEXT'),    # wiki page title used as input (re-runs)
            ('source_wiki_revid',     'INTEGER'), # MediaWiki revision ID used as input
            ('generated_summary',     'TEXT'),    # AI-generated summary text, pending review
        ]:
            _add_column('transformations', col, typedef)

        for col, typedef in [
            ('locked',          'INTEGER DEFAULT 0'),  # 1 = page exists on wiki, publish blocked
            ('wiki_revisions',  'INTEGER'),            # cached revision count at time of lock
        ]:
            _add_column('raw_captures', col, typedef)

        _add_column('transformations', 'template_revid', 'INTEGER')  # template version at run time


# ── raw_captures ───────────────────────────────────────────────────────────────

def insert_capture(meeting_date, captured_at, source_url, file_path, sha256, size_bytes) -> int:
    with conn() as c:
        cur = c.execute("""
            INSERT INTO raw_captures (meeting_date, captured_at, source_url, file_path, sha256, size_bytes)
            VALUES (?,?,?,?,?,?)
        """, (meeting_date, captured_at, source_url, str(file_path), sha256, size_bytes))
        return cur.lastrowid


def get_capture_by_date(meeting_date: str) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            'SELECT * FROM raw_captures WHERE meeting_date = ?', (meeting_date,)
        ).fetchone()


def get_capture_by_id(capture_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            'SELECT * FROM raw_captures WHERE id = ?', (capture_id,)
        ).fetchone()


def list_captures() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            'SELECT * FROM raw_captures ORDER BY meeting_date DESC'
        ).fetchall()


def update_capture_content(capture_id: int, sha256: str, size_bytes: int, captured_at: str):
    """Update the stored hash, size, and timestamp after a raw-content refresh."""
    with conn() as c:
        c.execute("""
            UPDATE raw_captures SET sha256=?, size_bytes=?, captured_at=?
            WHERE id=?
        """, (sha256, size_bytes, captured_at, capture_id))


def set_capture_locked(capture_id: int, locked: bool, wiki_revisions: int = None):
    """Lock or unlock a capture. When locking, cache the wiki revision count."""
    with conn() as c:
        c.execute(
            'UPDATE raw_captures SET locked=?, wiki_revisions=? WHERE id=?',
            (1 if locked else 0, wiki_revisions, capture_id),
        )


# ── transformations ────────────────────────────────────────────────────────────

def insert_transformation(
    raw_capture_id, parent_id, run_at, pipeline_version, pipeline_script,
    model_name, flags, input_sha256, output_path=None, output_sha256=None,
    duration_seconds=None, token_usage=None,
    artifact_lines_removed=None, sections_found=None,
) -> int:
    with conn() as c:
        cur = c.execute("""
            INSERT INTO transformations (
                raw_capture_id, parent_id, run_at, pipeline_version, pipeline_script,
                model_name, flags, input_sha256, output_path, output_sha256,
                duration_seconds, token_usage, artifact_lines_removed, sections_found
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            raw_capture_id, parent_id, run_at, pipeline_version, pipeline_script,
            model_name, json.dumps(flags), input_sha256,
            str(output_path) if output_path else None, output_sha256,
            duration_seconds, json.dumps(token_usage) if token_usage else None,
            artifact_lines_removed, sections_found,
        ))
        return cur.lastrowid


def update_transformation_output(txn_id, output_path, output_sha256, duration_seconds,
                                  token_usage=None, artifact_lines_removed=None,
                                  sections_found=None):
    with conn() as c:
        c.execute("""
            UPDATE transformations SET
                output_path = ?, output_sha256 = ?, duration_seconds = ?,
                token_usage = ?, artifact_lines_removed = ?, sections_found = ?
            WHERE id = ?
        """, (
            str(output_path) if output_path else None, output_sha256, duration_seconds,
            json.dumps(token_usage) if token_usage else None,
            artifact_lines_removed, sections_found, txn_id,
        ))


def record_publish(txn_id, published_to, published_at):
    with conn() as c:
        c.execute(
            'UPDATE transformations SET published_to=?, published_at=? WHERE id=?',
            (published_to, published_at, txn_id)
        )


def record_preview_publish(txn_id, preview_published_to, preview_published_at):
    with conn() as c:
        c.execute(
            'UPDATE transformations SET preview_published_to=?, preview_published_at=? WHERE id=?',
            (preview_published_to, preview_published_at, txn_id)
        )


def get_transformation(txn_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute('SELECT * FROM transformations WHERE id=?', (txn_id,)).fetchone()


def get_transformations_for_capture(raw_capture_id: int) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute("""
            SELECT * FROM transformations
            WHERE raw_capture_id = ?
            ORDER BY run_at ASC
        """, (raw_capture_id,)).fetchall()


def get_published_page_for_capture(capture_id: int) -> Optional[str]:
    """Return the most recently published (non-preview) wiki page title for a capture."""
    with conn() as c:
        row = c.execute("""
            SELECT published_to FROM transformations
            WHERE raw_capture_id=? AND published_to IS NOT NULL
            ORDER BY published_at DESC LIMIT 1
        """, (capture_id,)).fetchone()
    return row['published_to'] if row else None


def record_wiki_source(txn_id: int, page: str, revid: int):
    with conn() as c:
        c.execute(
            'UPDATE transformations SET source_wiki_page=?, source_wiki_revid=? WHERE id=?',
            (page, revid, txn_id)
        )


def record_generated_summary(txn_id: int, summary_text: str):
    with conn() as c:
        c.execute(
            'UPDATE transformations SET generated_summary=? WHERE id=?',
            (summary_text, txn_id)
        )


def clear_generated_summary(txn_id: int):
    with conn() as c:
        c.execute('UPDATE transformations SET generated_summary=NULL WHERE id=?', (txn_id,))


def get_latest_transformation(raw_capture_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute("""
            SELECT * FROM transformations
            WHERE raw_capture_id = ?
            ORDER BY run_at DESC LIMIT 1
        """, (raw_capture_id,)).fetchone()


# ── quality_ratings ────────────────────────────────────────────────────────────

def upsert_rating(transformation_id, rated_at, rated_by, rating,
                  issues, problem_excerpt, problem_description):
    """One rating per (transformation, user). Overwrites if re-rated."""
    with conn() as c:
        c.execute("""
            INSERT INTO quality_ratings
                (transformation_id, rated_at, rated_by, rating, issues,
                 problem_excerpt, problem_description)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(transformation_id, rated_by) DO UPDATE SET
                rated_at=excluded.rated_at, rating=excluded.rating,
                issues=excluded.issues, problem_excerpt=excluded.problem_excerpt,
                problem_description=excluded.problem_description
        """, (
            transformation_id, rated_at, rated_by, rating,
            json.dumps(issues), problem_excerpt, problem_description,
        ))
    # add unique constraint if not exists — handled in init via schema below


def get_ratings_for_transformation(txn_id: int) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            'SELECT * FROM quality_ratings WHERE transformation_id=? ORDER BY rated_at',
            (txn_id,)
        ).fetchall()


def get_summary_rating(txn_id: int) -> dict:
    """Returns {up: n, down: n, issues: [...]} for a transformation."""
    with conn() as c:
        rows = c.execute(
            'SELECT rating, issues FROM quality_ratings WHERE transformation_id=?',
            (txn_id,)
        ).fetchall()
    up = sum(1 for r in rows if r['rating'] == 'up')
    down = sum(1 for r in rows if r['rating'] == 'down')
    all_issues = []
    for r in rows:
        all_issues.extend(json.loads(r['issues'] or '[]'))
    return {'up': up, 'down': down, 'issues': list(set(all_issues)), 'total': len(rows)}


# ── pipeline_jobs ──────────────────────────────────────────────────────────────

def insert_job(capture_id: int, parent_txn_id, flags: dict, created_at: str) -> int:
    with conn() as c:
        cur = c.execute("""
            INSERT INTO pipeline_jobs (created_at, capture_id, parent_txn_id, flags)
            VALUES (?,?,?,?)
        """, (created_at, capture_id, parent_txn_id, json.dumps(flags)))
        return cur.lastrowid


def get_job(job_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute('SELECT * FROM pipeline_jobs WHERE id=?', (job_id,)).fetchone()


def claim_next_pending_job() -> Optional[sqlite3.Row]:
    """
    Atomically claim the oldest pending job and mark it running.

    Uses a single UPDATE with a subquery so only one worker can claim
    a given job even when multiple background threads race.
    """
    with conn() as c:
        started_at = datetime.now(timezone.utc).isoformat()
        cur = c.execute(
            """UPDATE pipeline_jobs
                  SET status='running', started_at=?
                WHERE id = (
                    SELECT id FROM pipeline_jobs
                     WHERE status='pending'
                     ORDER BY created_at
                     LIMIT 1
                )""",
            (started_at,),
        )
        if cur.rowcount == 0:
            return None
        return c.execute(
            "SELECT * FROM pipeline_jobs WHERE status='running' AND started_at=? LIMIT 1",
            (started_at,),
        ).fetchone()


def update_job_done(job_id: int, finished_at: str, result_txn_id: Optional[int]):
    with conn() as c:
        c.execute(
            "UPDATE pipeline_jobs SET status='done', finished_at=?, result_txn_id=? WHERE id=?",
            (finished_at, result_txn_id, job_id),
        )


def update_job_error(job_id: int, finished_at: str, error: str):
    with conn() as c:
        c.execute(
            "UPDATE pipeline_jobs SET status='error', finished_at=?, error=? WHERE id=?",
            (finished_at, error, job_id),
        )


# ── template_snapshots ─────────────────────────────────────────────────────────

def record_template_snapshot(revid: int, sha256: str, fetched_at: str) -> bool:
    """
    Record a template version. Returns True if this revid is new (unseen before).
    The very first snapshot is auto-acknowledged (it's the baseline).
    Subsequent new revids start unacknowledged and trigger a warning.
    """
    with conn() as c:
        existing = c.execute(
            'SELECT id FROM template_snapshots WHERE revid=?', (revid,)
        ).fetchone()
        if existing:
            return False
        is_first = c.execute(
            'SELECT COUNT(*) FROM template_snapshots'
        ).fetchone()[0] == 0
        c.execute(
            'INSERT INTO template_snapshots (fetched_at, revid, sha256, acknowledged) VALUES (?,?,?,?)',
            (fetched_at, revid, sha256, 1 if is_first else 0),
        )
        return True


def record_transformation_template(txn_id: int, template_revid: int) -> None:
    with conn() as c:
        c.execute(
            'UPDATE transformations SET template_revid=? WHERE id=?',
            (template_revid, txn_id),
        )


def template_has_unacknowledged_change() -> bool:
    """True if a template version newer than the baseline has not been acknowledged."""
    with conn() as c:
        row = c.execute(
            'SELECT COUNT(*) FROM template_snapshots WHERE acknowledged=0'
        ).fetchone()
        return row[0] > 0


def get_template_change_details() -> dict | None:
    """
    Return details of the pending template change for display in the warning modal.
    Returns None if no unacknowledged change.
    """
    with conn() as c:
        baseline = c.execute(
            'SELECT revid, fetched_at FROM template_snapshots WHERE acknowledged=1 ORDER BY id LIMIT 1'
        ).fetchone()
        new = c.execute(
            'SELECT revid, fetched_at FROM template_snapshots WHERE acknowledged=0 ORDER BY id DESC LIMIT 1'
        ).fetchone()
        if not new:
            return None
        return {
            'baseline_revid': baseline['revid'] if baseline else None,
            'new_revid': new['revid'],
            'new_fetched_at': new['fetched_at'],
        }


def acknowledge_template_changes() -> None:
    """Mark all template snapshots as acknowledged. Call after admin reviews the change."""
    with conn() as c:
        c.execute('UPDATE template_snapshots SET acknowledged=1')
