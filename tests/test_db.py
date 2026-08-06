"""Basic DB tests using an in-memory SQLite database."""
import db


def test_override_db_isolates_state():
    with db.override_db(':memory:'):
        db.init()
        assert db.list_captures() == []

        db.insert_capture(
            meeting_date='2026_01_06',
            captured_at='2026-01-06T00:00:00Z',
            source_url='https://example.com/pad',
            file_path='/tmp/raw.txt',
            sha256='abc123',
            size_bytes=42,
        )
        assert len(db.list_captures()) == 1


def test_override_db_does_not_leak():
    # After the context manager exits, the override is gone
    with db.override_db(':memory:'):
        db.init()
        db.insert_capture(
            meeting_date='2026_01_13',
            captured_at='2026-01-13T00:00:00Z',
            source_url='https://example.com/pad',
            file_path='/tmp/raw2.txt',
            sha256='def456',
            size_bytes=99,
        )

    # A second in-memory context starts fresh
    with db.override_db(':memory:'):
        db.init()
        assert db.list_captures() == []


def test_override_db_restores_after_exception():
    try:
        with db.override_db(':memory:'):
            db.init()
            raise RuntimeError('deliberate')
    except RuntimeError:
        pass

    assert db._db_path_override is None


# ── raw_revisions ──────────────────────────────────────────────────────────────

def _insert_capture(meeting_date='2026_02_03', sha='sha0', size=10):
    return db.insert_capture(
        meeting_date=meeting_date,
        captured_at='2026-02-03T00:00:00Z',
        source_url='https://example.com/pad',
        file_path=f'/tmp/raw_{meeting_date}.txt',
        sha256=sha,
        size_bytes=size,
    )


def test_insert_and_get_raw_revision():
    with db.override_db(':memory:'):
        db.init()
        cap_id = _insert_capture()
        rev_id = db.insert_raw_revision(
            raw_capture_id=cap_id,
            created_at='2026-02-03T01:00:00Z',
            source='human_edit',
            author='alice',
            note='fixed header',
            snapshot_path='/tmp/snap.txt',
            sha256='sha1',
            size_bytes=20,
        )
        rev = db.get_raw_revision(rev_id)
        assert rev['raw_capture_id'] == cap_id
        assert rev['source'] == 'human_edit'
        assert rev['author'] == 'alice'
        assert rev['note'] == 'fixed header'
        assert rev['sha256'] == 'sha1'
        assert rev['restored_from'] is None


def test_get_raw_revisions_ordering_and_latest():
    with db.override_db(':memory:'):
        db.init()
        cap_id = _insert_capture()
        for i, ts in enumerate(['00:00', '01:00', '02:00']):
            db.insert_raw_revision(
                raw_capture_id=cap_id,
                created_at=f'2026-02-03T{ts}:00Z',
                source='human_edit', author='a', note=None,
                snapshot_path=f'/tmp/s{i}.txt', sha256=f'sha{i}', size_bytes=i,
            )
        revs = db.get_raw_revisions(cap_id)
        assert [r['sha256'] for r in revs] == ['sha0', 'sha1', 'sha2']
        assert db.get_latest_raw_revision(cap_id)['sha256'] == 'sha2'


def test_backfill_creates_initial_revision_idempotently():
    with db.override_db(':memory:'):
        db.init()
        # Insert a capture directly (as a pre-versioning DB would have), with
        # no revisions, then re-run init() to trigger backfill.
        cap_id = _insert_capture(sha='backfillsha', size=77)
        assert db.get_raw_revisions(cap_id) == []

        db.init()
        revs = db.get_raw_revisions(cap_id)
        assert len(revs) == 1
        assert revs[0]['source'] == 'initial_fetch'
        assert revs[0]['sha256'] == 'backfillsha'
        assert revs[0]['size_bytes'] == 77
        assert revs[0]['author'] is None

        # Idempotent: a further init() adds no duplicate.
        db.init()
        assert len(db.get_raw_revisions(cap_id)) == 1


def test_set_transformation_raw_revision():
    with db.override_db(':memory:'):
        db.init()
        cap_id = _insert_capture()
        rev_id = db.insert_raw_revision(
            raw_capture_id=cap_id, created_at='2026-02-03T01:00:00Z',
            source='initial_fetch', author=None, note=None,
            snapshot_path='/tmp/s.txt', sha256='s', size_bytes=1,
        )
        txn_id = db.insert_transformation(
            raw_capture_id=cap_id, parent_id=None,
            run_at='2026-02-03T02:00:00Z',
            pipeline_version='v', pipeline_script='p',
            model_name=None, flags='{}', input_sha256='s',
        )
        db.set_transformation_raw_revision(txn_id, rev_id)
        txn = db.get_transformation(txn_id)
        assert txn['raw_revision_id'] == rev_id
