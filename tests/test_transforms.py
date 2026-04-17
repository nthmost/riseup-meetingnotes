"""
Unit tests for noisebridge_pipeline/transforms.py.

These run in milliseconds — no network, no AI, no subprocess.
"""
from transforms import (
    should_remove_line,
    clean_inline,
    strip_html_comments,
    fix_ordered_lists,
    fix_topic_headers,
    fix_nav_links,
    fix_date_metadata,
    fix_metadata_table,
    insert_summary,
    ensure_bullets,
    _ordinal,
    strip_artifacts,
    format_speaker_attributions,
)


# ── should_remove_line ────────────────────────────────────────────────────────

def test_removes_exact_artifact():
    assert should_remove_line('</div>') is True
    assert should_remove_line('  </div>  ') is True

def test_keeps_real_content():
    assert should_remove_line('* Naomi: hello everyone') is False
    assert should_remove_line('== Discussion Items ==') is False

def test_removes_startswith_artifact():
    assert should_remove_line('* Click here to edit') is True
    assert should_remove_line('---') is True

def test_removes_update_meeting_links():
    assert should_remove_line('** Update last meeting: https://example.com') is True
    assert should_remove_line('** Update next meeting: https://example.com') is True

def test_removes_remove_me_line():
    assert should_remove_line(
        '<code>Only after finishing notes, writing summary, uploading summary to discord/email. REMOVE ME (and above).</code></div>'
    ) is True

def test_removes_empty_summary_bullet():
    assert should_remove_line('* Fundraising Update:') is True
    assert should_remove_line('* Fundraising Update: $500 raised') is False


# ── clean_inline ──────────────────────────────────────────────────────────────

def test_clean_inline_removes_two_minutes_max():
    assert clean_inline('* Item) TWO MINUTES MAX') == '* Item'


# ── strip_html_comments ───────────────────────────────────────────────────────

def test_strips_single_line_comment():
    assert strip_html_comments('hello <!-- world --> there') == 'hello  there'

def test_strips_multiline_comment():
    # Multi-line mode triggers when <!-- appears AFTER real content on a line
    text = 'real content <!-- start\nmiddle\nend -->\nafter'
    result = strip_html_comments(text)
    assert 'real content' in result
    assert 'middle' not in result
    assert 'after' in result

def test_line_only_comment_does_not_swallow_next_line():
    # A line that IS the comment (starts with <!--, unclosed) is removed,
    # but multi-line mode is NOT entered — the following lines are preserved.
    text = 'before\n<!-- orphan comment\nmiddle\nafter'
    result = strip_html_comments(text)
    assert 'before' in result
    assert 'middle' in result  # preserved — multi-line mode not triggered
    assert 'after' in result

def test_removes_comment_only_line():
    result = strip_html_comments('<!-- only comment -->')
    assert result.strip() == ''


# ── _ordinal ──────────────────────────────────────────────────────────────────

def test_ordinal():
    assert _ordinal(1)   == '1st'
    assert _ordinal(2)   == '2nd'
    assert _ordinal(3)   == '3rd'
    assert _ordinal(4)   == '4th'
    assert _ordinal(11)  == '11th'
    assert _ordinal(12)  == '12th'
    assert _ordinal(13)  == '13th'
    assert _ordinal(21)  == '21st'
    assert _ordinal(859) == '859th'


# ── fix_ordered_lists ─────────────────────────────────────────────────────────

def test_converts_numbered_list():
    text = '1. First\n2. Second\n3. Third'
    result = fix_ordered_lists(text)
    assert '# First' in result
    assert '# Second' in result
    assert '# Third' in result

def test_ignores_lone_item():
    text = '1. Only item'
    result = fix_ordered_lists(text)
    assert '1. Only item' in result  # not converted — needs at least 2

def test_converts_paren_style():
    text = '1) Alpha\n2) Beta'
    result = fix_ordered_lists(text)
    assert '# Alpha' in result
    assert '# Beta' in result


# ── fix_topic_headers ─────────────────────────────────────────────────────────

def test_resolves_topic_placeholder():
    lines = [
        '== 1: [topic] ==',
        'On topic: Wheezy ATL',
        'Some content',
    ]
    result = fix_topic_headers(lines)
    assert result[0] == '== 1: Wheezy ATL =='


# ── fix_nav_links ─────────────────────────────────────────────────────────────

def test_replaces_prev_next_links():
    text = (
        '[[Meeting_Notes_2026 MONTH DAY|Previous Meeting]]\n'
        '[[Meeting_Notes_2026 MONTH DAY|Next Meeting]]'
    )
    result = fix_nav_links(text, '2026_04_08')
    assert 'Meeting_Notes_2026_04_01|Previous Meeting' in result
    assert 'Meeting_Notes_2026_04_15|Next Meeting' in result


# ── insert_summary ────────────────────────────────────────────────────────────

def test_inserts_summary():
    text = '== Meeting Summary ==\n\nold content\n\n== Next Section ==\n'
    result = insert_summary(text, 'New summary text')
    assert 'New summary text' in result
    assert 'old content' not in result
    assert '== Next Section ==' in result


# ── ensure_bullets ────────────────────────────────────────────────────────────

def test_ensure_bullets_adds_bullet():
    text = '= Introductions =\nHello I am Naomi\n= Next Section =\n'
    result = ensure_bullets(text, ['Introductions'])
    assert '* Hello I am Naomi' in result

def test_ensure_bullets_leaves_existing():
    text = '= Introductions =\n* Already a bullet\n= Next =\n'
    result = ensure_bullets(text, ['Introductions'])
    assert '* Already a bullet' in result
    assert '* * ' not in result  # no double-bullet


# ── strip_artifacts (integration of all the above) ────────────────────────────

def test_strip_artifacts_removes_div_wrapper():
    text = '<div style="background-color:#ddd8;border:3px dashed #3338;padding:3px;">\ncontent\n</div>'
    result = strip_artifacts(text)
    assert '<div' not in result
    assert 'content' in result

def test_strip_artifacts_collapses_blank_lines():
    text = 'line one\n\n\n\nline two'
    result = strip_artifacts(text)
    assert '\n\n\n' not in result


# ── format_speaker_attributions ───────────────────────────────────────────────

def test_formats_colon_attribution():
    text = '= Discussion Items =\n* Alice: said something\n'
    result = format_speaker_attributions(text)
    assert "'''Alice:'''" in result

def test_formats_dash_attribution():
    text = '= Discussion Items =\n* Bob - did a thing\n'
    result = format_speaker_attributions(text)
    assert "'''Bob:'''" in result

def test_skips_introductions_section():
    text = '= Introductions =\n* Carol: intro blurb\n= Discussion Items =\n'
    result = format_speaker_attributions(text)
    # Carol's line is in Introductions — should NOT be reformatted
    assert "* Carol: intro blurb" in result

def test_blank_lines_between_attributions_any_section():
    # Attributions in any section (e.g. Kudos) should be separated by blank lines
    text = '= Brief Kudos =\n* Alice: fixed the laser\n* Bob: helped a new person\n'
    result = format_speaker_attributions(text)
    assert "'''Alice:''' fixed the laser\n\n'''Bob:''' helped a new person" in result


# ── fix_date_metadata ─────────────────────────────────────────────────────────

def test_date_numeric_with_update_date():
    text = '| 2026 4 14  UPDATE DATE'
    result = fix_date_metadata(text, '2026_04_14')
    assert result == '| 2026-04-14'

def test_date_placeholder_mm_dd():
    text = '| 2026 mm dd  UPDATE DATE'
    result = fix_date_metadata(text, '2026_04_14')
    assert result == '| 2026-04-14'

def test_date_already_correct():
    # A correctly-filled date should be normalised but not broken
    text = '| 2026 04 14'
    result = fix_date_metadata(text, '2026_04_14')
    assert result == '| 2026-04-14'


# ── fix_metadata_table ────────────────────────────────────────────────────────

def test_fix_metadata_table_joins_notetakers():
    text = (
        '! Note-taker[s]\n'
        '| Alice\n'
        '| Bob\n'
        '| Carol\n'
        '! Moderator[s]\n'
        '| Dave\n'
    )
    result = fix_metadata_table(text)
    assert '| Alice, Bob, Carol' in result
    assert '|-' in result
    assert '! Moderator[s]' in result

def test_fix_metadata_table_adds_row_separator():
    text = (
        '! Note-taker[s]\n'
        '| Alice\n'
        '| Bob\n'
        '! Moderator[s]\n'
        '| Carol\n'
    )
    result = fix_metadata_table(text)
    lines = result.splitlines()
    notetaker_idx = next(i for i, l in enumerate(lines) if 'Note-taker' in l)
    moderator_idx = next(i for i, l in enumerate(lines) if 'Moderator' in l)
    separator_idx = next(i for i, l in enumerate(lines) if l.strip() == '|-')
    assert notetaker_idx < separator_idx < moderator_idx

def test_fix_metadata_table_single_notetaker_unchanged_content():
    text = (
        '! Note-taker[s]\n'
        '| Alice\n'
        '! Moderator[s]\n'
        '| Bob\n'
    )
    result = fix_metadata_table(text)
    assert '| Alice' in result
    assert '|-' in result
