"""
pipeline/fetch.py — fetch raw pad content and record the capture.
"""
import hashlib
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config
import db


def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode('utf-8')


def archive_raw(meeting_date: str, content: str, source: str) -> tuple[Path, int]:
    """
    Write raw content to RAW_DIR as a read-only file.
    Returns (file_path, capture_db_id).
    Raises FileExistsError if already captured for this date.
    """
    existing = db.get_capture_by_date(meeting_date)
    if existing:
        raise FileExistsError(
            f"Already captured {meeting_date} (id={existing['id']})"
        )

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = config.RAW_DIR / f'raw_{meeting_date}.txt'
    path.write_text(content, encoding='utf-8')
    path.chmod(0o444)

    sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
    capture_id = db.insert_capture(
        meeting_date=meeting_date,
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_url=source,
        file_path=path,
        sha256=sha256,
        size_bytes=len(content.encode('utf-8')),
    )
    return path, capture_id


def fetch_and_archive(meeting_date: str) -> tuple[str, int]:
    """
    Fetch from PAD_URL and archive. Returns (raw_content, capture_id).
    """
    content = fetch_url(config.PAD_URL)
    _, capture_id = archive_raw(meeting_date, content, config.PAD_URL)
    return content, capture_id


def fetch_wiki_page(page_title: str) -> tuple[str | None, int | None]:
    """
    Fetch the current wikitext and revision ID of a MediaWiki page.
    Returns (content, revid) or (None, None) if the page doesn't exist.
    """
    params = {
        'action': 'query',
        'prop': 'revisions',
        'titles': page_title,
        'rvprop': 'ids|content',
        'rvslots': 'main',
        'format': 'json',
    }
    url = config.WIKI_API_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    pages = data.get('query', {}).get('pages', {})
    page = next(iter(pages.values()))

    if 'missing' in page:
        return None, None

    revisions = page.get('revisions', [])
    if not revisions:
        return None, None

    rev = revisions[0]
    revid = rev.get('revid')
    # MediaWiki 1.35+ uses slots; older uses '*' directly
    if 'slots' in rev:
        content = rev['slots']['main'].get('*', '')
    else:
        content = rev.get('*', '')

    return content, revid


def fetch_wiki_revision_count(page_title: str) -> int | None:
    """
    Return the number of revisions for a wiki page, or None if it doesn't exist.
    Capped at 500 (MediaWiki rvlimit max) — sufficient for meeting notes pages.
    """
    params = {
        'action': 'query',
        'prop': 'revisions',
        'titles': page_title,
        'rvprop': 'ids',
        'rvlimit': 'max',
        'format': 'json',
    }
    url = config.WIKI_API_URL + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    pages = data.get('query', {}).get('pages', {})
    page = next(iter(pages.values()))
    if 'missing' in page:
        return None
    return len(page.get('revisions', []))


def load_raw(meeting_date: str) -> tuple[str, sqlite3.Row]:
    """Load already-archived raw content from disk. Returns (content, capture_row)."""
    row = db.get_capture_by_date(meeting_date)
    if row is None:
        raise FileNotFoundError(f"No capture for {meeting_date}")
    return Path(row['file_path']).read_text('utf-8'), row
