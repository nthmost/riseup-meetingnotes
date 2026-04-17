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
