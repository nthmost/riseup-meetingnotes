"""
pipeline/publish.py — wiki publishing with session caching.

Owns the WikiAPI session so the login cost (one round-trip) is paid
once per process lifetime rather than on every publish call.

Public API:
    publish(txn_id, meeting_date, content, page_title=None)
"""
import logging
import urllib.error

import config
import db
from wiki import WikiAPI, StaleSessionError

log = logging.getLogger(__name__)

_wiki_session: WikiAPI | None = None


def _get_wiki_session() -> WikiAPI:
    """Return a logged-in WikiAPI, logging in once and reusing the session."""
    global _wiki_session
    if _wiki_session is None:
        _wiki_session = WikiAPI(config.WIKI_API_URL)
        result = _wiki_session.login(config.WIKI_BOT_USER, config.WIKI_BOT_PASS)
        if result != 'Success':
            _wiki_session = None
            raise RuntimeError(f"Wiki login failed: {result}")
        log.info("[wiki] logged in — session will be reused")
    return _wiki_session


def _invalidate_session() -> None:
    global _wiki_session
    _wiki_session = None


def publish(txn_id: int, meeting_date: str, content: str,
            page_title: str = None) -> None:
    """Push processed content to the NB wiki and record it in the DB."""
    from datetime import datetime, timezone

    if page_title is None:
        page_title = f'Meeting_Notes_{meeting_date}'
    summary = f'Auto-posted meeting notes for {meeting_date} (meetingnotes)'

    def _attempt():
        wiki = _get_wiki_session()
        wiki.edit_page(title=page_title, content=content, summary=summary)

    try:
        _attempt()
    except StaleSessionError as e:
        log.warning(f"[wiki] stale session ({e}), re-logging in and retrying...")
        _invalidate_session()
        _attempt()
    except urllib.error.HTTPError as e:
        if e.code in (403, 400):
            log.warning(f"[wiki] HTTP {e.code}, re-logging in and retrying...")
            _invalidate_session()
            _attempt()
        else:
            raise

    db.record_publish(txn_id, page_title, datetime.now(timezone.utc).isoformat())
    log.info(f"[publish] published → {config.WIKI_PAGE_URL}/{page_title}")
