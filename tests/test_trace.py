"""Tests for pipeline/trace.py."""
from pipeline.trace import format_trace_markdown


def test_basic_table():
    steps = [
        {'name': 'strip_artifacts', 'lines_in': 100, 'lines_out': 80, 'note': 'date=2026_04_08'},
        {'name': 'generate_summary', 'lines_in': 80, 'lines_out': 89, 'note': 'model=claude-haiku, 100 in / 50 out tokens'},
    ]
    md = format_trace_markdown(steps, '2026_04_08', txn_id=42,
                               pipeline_version='abc1234', duration_seconds=3.5)
    assert '# Pipeline Trace — 2026_04_08' in md
    assert '`strip_artifacts`' in md
    assert '-20' in md       # 80 - 100 = -20
    assert '+9' in md        # 89 - 80 = +9
    assert 'abc1234' in md
    assert '3.5s' in md
    assert 'date=2026_04_08' in md


def test_empty_steps():
    md = format_trace_markdown([], '2026_04_08', txn_id=1,
                               pipeline_version='abc', duration_seconds=0.1)
    assert 'no steps recorded' in md


def test_zero_delta():
    steps = [{'name': 'ensure_bullets', 'lines_in': 50, 'lines_out': 50, 'note': ''}]
    md = format_trace_markdown(steps, '2026_04_08', txn_id=1,
                               pipeline_version='abc', duration_seconds=0.1)
    assert '| `ensure_bullets` | 50 | 50 | 0 |' in md


def test_positive_delta():
    steps = [{'name': 'add_footer', 'lines_in': 200, 'lines_out': 203, 'note': 'banner + category'}]
    md = format_trace_markdown(steps, '2026_04_08', txn_id=1,
                               pipeline_version='abc', duration_seconds=0.1)
    assert '+3' in md


def test_missing_note_key():
    steps = [{'name': 'fix_ordered_lists', 'lines_in': 50, 'lines_out': 48}]
    md = format_trace_markdown(steps, '2026_04_08', txn_id=1,
                               pipeline_version='abc', duration_seconds=0.1)
    assert '`fix_ordered_lists`' in md   # should not raise on missing note key
