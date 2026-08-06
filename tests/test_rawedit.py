"""
Tests for pipeline/rawedit.py — the versioned raw-content mutation helper.
Real in-memory DB, real files under tmp_path. No mocking.
"""
import hashlib
from datetime import datetime, timezone

import pytest

import config
import db
from pipeline.rawedit import normalize, replace_raw_content


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _make_capture(tmp_path, meeting_date='2026_03_10', content='original raw'):
    """Create a live read-only raw file + capture row + initial revision."""
    raw = tmp_path / f'raw_{meeting_date}.txt'
    raw.write_text(content, encoding='utf-8')
    raw.chmod(0o444)
    sha = _sha(content)
    cap_id = db.insert_capture(
        meeting_date=meeting_date,
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_url='https://example.com/pad',
        file_path=str(raw),
        sha256=sha,
        size_bytes=len(content.encode('utf-8')),
    )
    db.insert_raw_revision(
        raw_capture_id=cap_id, created_at=datetime.now(timezone.utc).isoformat(),
        source='initial_fetch', author=None, note=None,
        snapshot_path=str(raw), sha256=sha, size_bytes=len(content.encode('utf-8')),
    )
    return cap_id, raw


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RAW_DIR', tmp_path)
    return tmp_path


def test_normalize_line_endings():
    assert normalize('a\r\nb\rc\n') == 'a\nb\nc\n'


def test_replace_raw_content_updates_live_and_records_revision(raw_dir):
    with db.override_db(':memory:'):
        db.init()
        cap_id, raw = _make_capture(raw_dir)
        capture = db.get_capture_by_date('2026_03_10')

        rev_id = replace_raw_content(
            capture, 'edited content',
            source='human_edit', author='alice', note='fix',
        )

        # Live file reflects the edit and is read-only again.
        assert raw.read_text('utf-8') == 'edited content'
        assert (raw.stat().st_mode & 0o777) == 0o444

        # Capture row updated.
        updated = db.get_capture_by_id(cap_id)
        assert updated['sha256'] == _sha('edited content')
        assert updated['size_bytes'] == len('edited content')

        # Revision recorded with a real, read-only snapshot holding the content.
        rev = db.get_raw_revision(rev_id)
        assert rev['source'] == 'human_edit'
        assert rev['author'] == 'alice'
        assert rev['note'] == 'fix'
        from pathlib import Path
        snap = Path(rev['snapshot_path'])
        assert snap.exists() and snap.read_text('utf-8') == 'edited content'
        assert (snap.stat().st_mode & 0o777) == 0o444


def test_snapshots_are_immutable_across_edits(raw_dir):
    with db.override_db(':memory:'):
        db.init()
        _make_capture(raw_dir, content='v0')
        cap = db.get_capture_by_date('2026_03_10')
        r1 = replace_raw_content(cap, 'v1', source='human_edit', author='a', note=None)
        cap = db.get_capture_by_date('2026_03_10')
        replace_raw_content(cap, 'v2', source='human_edit', author='a', note=None)

        from pathlib import Path
        # The v1 snapshot still holds v1 after v2 was written.
        assert Path(db.get_raw_revision(r1)['snapshot_path']).read_text('utf-8') == 'v1'
        # Three revisions total: initial + two edits.
        assert len(db.get_raw_revisions(cap['id'])) == 3


def test_restore_creates_new_revision_without_deleting_history(raw_dir):
    with db.override_db(':memory:'):
        db.init()
        _make_capture(raw_dir, content='v0')
        cap = db.get_capture_by_date('2026_03_10')
        revs = db.get_raw_revisions(cap['id'])
        first_rev_id = revs[0]['id']

        cap = db.get_capture_by_date('2026_03_10')
        replace_raw_content(cap, 'v1', source='human_edit', author='a', note=None)

        # Restore the original content as a NEW revision.
        cap = db.get_capture_by_date('2026_03_10')
        from pathlib import Path
        original = Path(db.get_raw_revision(first_rev_id)['snapshot_path']).read_text('utf-8')
        new_rev = replace_raw_content(
            cap, original, source='restore', author='a',
            note='restored', restored_from=first_rev_id,
        )

        assert Path(cap['file_path']).read_text('utf-8') == 'v0'
        rev = db.get_raw_revision(new_rev)
        assert rev['source'] == 'restore'
        assert rev['restored_from'] == first_rev_id
        # History preserved: initial + edit + restore.
        assert len(db.get_raw_revisions(cap['id'])) == 3
