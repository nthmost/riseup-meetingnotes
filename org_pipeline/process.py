"""
org_pipeline/process.py — deterministic meeting notes pipeline.

Orchestrates all text transforms and optionally calls the AI summary
module (ai.py). Contains no Anthropic imports — those live exclusively
in ai.py.

Public API:
    process(raw_text, date_str, generate_ai_summary) -> (content, metrics)
    fetch_meeting_number(date_str)                   -> int | None
    fix_meeting_number(text, date_str, fallback_n)   -> str
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

from ai import generate_summary
from transforms import (
    strip_artifacts, fix_metadata_table, fix_discussion_item_blocks,
    format_speaker_attributions, format_task_board, fix_ordered_lists,
    ensure_bullets, insert_summary, _ordinal,
)


def _build_meeting_num_re():
    """
    Build the meeting-number regex from NB_WIKI_MEETING_NUM_PATTERN.

    The pattern matches the category link that wraps the meeting ordinal, e.g.:
      [https://wiki.example.org/Category:Meetings The 42nd Meeting of YourOrg]

    Requires NB_WIKI_MEETING_NUM_PATTERN (e.g. "Meeting of YourOrg") and
    NB_WIKI_API_URL to construct the wiki base.  Returns None when not configured.
    """
    pattern = os.getenv('NB_WIKI_MEETING_NUM_PATTERN', '')
    wiki_url = os.getenv('NB_WIKI_PAGE_URL', '').rstrip('/')
    category  = os.getenv('NB_WIKI_MEETING_CATEGORY', 'Category:Meeting_Notes')
    if not pattern or not wiki_url:
        return None
    escaped_url     = re.escape(f'{wiki_url}/{category}')
    escaped_pattern = re.escape(pattern)
    return re.compile(
        rf'(\[{escaped_url} The )'
        rf'([^\]]+?)'
        rf'( {escaped_pattern}\])'
    )


_MEETING_NUM_RE = _build_meeting_num_re()


def fetch_meeting_number(date_str: str) -> int | None:
    """
    Derive this meeting's number by fetching the previous week's published
    wiki page and incrementing its meeting number by 1.

    Requires NB_WIKI_MEETING_NUM_PATTERN to be set (e.g. "Meeting of Noisebridge").
    Returns None on any network or parse failure, or if the pattern is not configured.
    """
    from datetime import datetime, timedelta

    pattern = os.getenv('NB_WIKI_MEETING_NUM_PATTERN', '')
    if not pattern:
        return None

    wiki_api_url = os.getenv('NB_WIKI_API_URL', '')
    if not wiki_api_url:
        return None

    y, mo, d = (int(x) for x in date_str.split('_'))
    prev_tuesday = datetime(y, mo, d) - timedelta(days=7)
    prev_title = prev_tuesday.strftime('Meeting Notes %Y %m %d')

    params = {
        'action': 'query',
        'prop': 'revisions',
        'titles': prev_title,
        'rvprop': 'content',
        'rvslots': 'main',
        'format': 'json',
    }
    url = wiki_api_url + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': os.getenv('NB_USER_AGENT', 'MeetingNotesBot/1.0')})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    pages = data.get('query', {}).get('pages', {})
    page = next(iter(pages.values()))
    if 'missing' in page:
        log.warning(f"wiki page '{prev_title}' not found; cannot derive meeting number")
        return None

    revisions = page.get('revisions', [])
    if not revisions:
        return None

    rev = revisions[0]
    content = (rev.get('slots', {}).get('main', {}).get('*') or rev.get('*', ''))

    escaped = re.escape(pattern)
    m = re.search(rf'The (\d+)(?:st|nd|rd|th) {escaped}', content)
    if not m:
        return None

    return int(m.group(1)) + 1


def fix_meeting_number(text: str, date_str: str,
                       fallback_n: int | None = None) -> str:
    """
    If the meeting number line has a placeholder instead of a real ordinal,
    fetch the correct number from the wiki and substitute it.

    Skipped entirely if NB_WIKI_MEETING_NUM_PATTERN is not set.
    fallback_n — use this number if the wiki lookup fails.
    """
    if _MEETING_NUM_RE is None:
        return text  # feature not configured for this org

    match = _MEETING_NUM_RE.search(text)
    if not match:
        return text

    current = match.group(2).strip()
    if re.match(r'^\d+(?:st|nd|rd|th)$', current):
        return text  # already a valid ordinal — trust the notetaker

    log.info(f"meeting number placeholder '{current}' — querying wiki")
    n = None
    try:
        n = fetch_meeting_number(date_str)
    except Exception as e:
        log.warning(f"could not fetch meeting number: {e}")

    if n is None and fallback_n is not None:
        log.info(f"using fallback meeting number from comment: {fallback_n}")
        n = fallback_n

    if n is None:
        log.warning("could not determine meeting number; leaving placeholder")
        return text

    ordinal = _ordinal(n)
    log.info(f"meeting number resolved: {ordinal}")
    return _MEETING_NUM_RE.sub(rf'\g<1>{ordinal}\g<3>', text)


def process(raw_text: str, date_str: str = None,
            generate_ai_summary: bool = True) -> tuple[str, dict]:
    """
    Full processing pipeline:
      1. strip_artifacts         — remove template boilerplate (deterministic)
      2. fix_meeting_number      — resolve ordinal from wiki (optional, needs config)
      3. fix_metadata_table      — fix Note-taker/Moderator row formatting
      4. generate_summary        — AI generates Meeting Summary (stored for review, not inserted)
      5. fix_ordered_lists       — convert 1. 2. 3. to MediaWiki # lists
      6. fix_discussion_item_blocks — pull content out of {{DiscussionItem}} templates
      7. format_task_board       — convert task bullet list to wikitable
      8. format_speaker_attributions — reformat "Name: text" to '''Name:''' text
      9. ensure_bullets          — bullet-ify Introductions / Short announcements
     10. add_footer              — prepend year banner and category link (if configured)

    Returns (content, metrics). metrics['steps'] is a list of step-report dicts
    (name, lines_in, lines_out, note) for pipeline observability; see AGENTS.md.
    The AI summary (if generated) is in metrics['generated_summary'] — it is
    NOT inserted into the content; the user reviews and inserts it separately.
    """
    metrics = {'model_name': None, 'token_usage': None}
    steps: list[dict] = []

    def _record(name: str, before: str, after: str, note: str = '') -> str:
        """Append a step report and return after."""
        steps.append({
            'name': name,
            'lines_in': before.count('\n') + 1,
            'lines_out': after.count('\n') + 1,
            'note': note,
        })
        return after

    # Extract meeting number hint from HTML comments BEFORE they are stripped.
    _num_hint = None
    _hint_m = re.search(r'Meeting Number \((\d+)(?:st|nd|rd|th)\s+meeting\)',
                        raw_text, re.IGNORECASE)
    if _hint_m:
        _num_hint = int(_hint_m.group(1))

    # 1. Strip template artifacts
    cleaned = _record('strip_artifacts', raw_text, strip_artifacts(raw_text, date_str=date_str))

    # 2. Fix meeting number (wiki lookup, falls back to comment hint)
    if date_str and _MEETING_NUM_RE is not None:
        before = cleaned
        cleaned = fix_meeting_number(cleaned, date_str, fallback_n=_num_hint)
        m = _MEETING_NUM_RE.search(cleaned)
        _record('fix_meeting_number', before, cleaned,
                note=f"resolved to {m.group(2)}" if m else "no placeholder found")

    # 3. Fix metadata table (Note-taker/Moderator row formatting)
    cleaned = _record('fix_metadata_table', cleaned, fix_metadata_table(cleaned))

    # 4. AI summary — generated and stored for review, NOT inserted here.
    if generate_ai_summary:
        summary, summary_metrics = generate_summary(cleaned)
        if summary_metrics:
            metrics.update(summary_metrics)
        if summary:
            metrics['generated_summary'] = summary
            if summary_metrics:
                tu = summary_metrics.get('token_usage') or {}
                note = (f"model={summary_metrics.get('model_name')}, "
                        f"{tu.get('input_tokens', '?')} in / "
                        f"{tu.get('output_tokens', '?')} out tokens"
                        f" — stored for review, not yet inserted")
            else:
                note = "generated, stored for review"
        else:
            note = "skipped (no API key or generation error)"
        _record('generate_summary', cleaned, cleaned, note=note)
    else:
        _record('generate_summary', cleaned, cleaned, note='skipped (flag=false)')

    # 5–9. Deterministic transforms
    cleaned = _record('fix_ordered_lists', cleaned, fix_ordered_lists(cleaned))
    cleaned = _record('fix_discussion_item_blocks', cleaned, fix_discussion_item_blocks(cleaned))
    cleaned = _record('format_task_board', cleaned, format_task_board(cleaned))

    formatted = format_speaker_attributions(cleaned)
    attr_count = sum(1 for line in formatted.splitlines() if "'''" in line and ":'''" in line)
    cleaned = _record('format_speaker_attributions', cleaned, formatted,
                      note=f'{attr_count} attribution lines in output')

    cleaned = _record(
        'ensure_bullets', cleaned,
        ensure_bullets(cleaned, ['Introductions', 'Short announcements and events']),
    )

    # 10. Footer — year banner and category link (both driven by config; skipped if blank)
    before = cleaned
    year_banner   = os.getenv('NB_WIKI_YEAR_BANNER', '')
    wiki_category = os.getenv('NB_WIKI_CATEGORY', '')
    footer_notes  = []
    if year_banner and year_banner not in cleaned:
        cleaned = year_banner + '\n\n' + cleaned.lstrip()
        footer_notes.append(year_banner)
    category_tag = f'[[{wiki_category}]]' if wiki_category else ''
    if category_tag and not cleaned.rstrip().endswith(category_tag):
        cleaned = cleaned.rstrip() + '\n\n' + category_tag
        footer_notes.append(category_tag)
    _record('add_footer', before, cleaned,
            note=' + '.join(footer_notes) if footer_notes else 'nothing to add (not configured)')

    metrics['steps'] = steps
    return cleaned, metrics
