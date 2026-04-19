"""
transforms.py — Pure text transforms for meeting notes processing.

Contains only deterministic, pure functions with no network I/O.
All functions operate on strings or lists of strings and return the same.
"""

import re
import sys
from datetime import date, timedelta


# ================================================================
# ARTIFACT REMOVAL RULES
#
# These define what constitutes a "template artifact" — instruction
# text left over from the meeting notes template that should not
# appear in the final published page.
# ================================================================

# Lines removed by exact match (after strip())
REMOVE_EXACT_LINES = {
    # Outer div wrapper
    '<div style="background-color:#ddd8;border:3px dashed #3338;padding:3px;">',
    '</div>',
    # Template navigation instructions
    '-> Date / Note-taker / Moderator // Prev + Next links / Summary',
    # Summary section template instructions
    'FILL OUT AT END OF MEETING AND SEND TO MAILING LIST/SLACK/DISCORD',
    'Include:',
    'TLDR what happened at the meeting:',
    # Top-of-page summary placeholder
    "* '''One or two bullet points of high-level meeting summary.'''",
    # Introductions placeholder
    "* ''' name ''' (pronouns): introduction",
    # Announcements section instructions
    "* '''60-second description per item in bulletpoint.'''",
    "* '''Copy-paste any pre-announcements for the upcoming meeting from [[Announcements]].'''",
    "* '''After announcements, copy them to the past [[Announcements]] list. Retire any from the Next Meeting list that aren't marked ONGOING.'''",
    "* (See https://www.noisebridge.net/ and https://www.noisebridge.net/wiki/Category:Events for events, and welcoming people)",
    # Excellence / Kudos section instructions
    "Recent examples of excellent behavior, say 3, unless the passion moves you.",
    # Guilds section instructions
    "''Reports from guilds & working groups. Updates! Events! Requests!''",
    "* '''What are [[guilds]] - briefly describe''' (much like the previous section on \"Excellence\")",
    # New Members section instruction
    "What are they? Is anyone trying to become a Member or Associate Member? If not move on. No long discussions.",
    # Consensus Items section instructions
    "Nobody likes 3 hour meetings, only explain if new people are present. https://www.noisebridge.net/wiki/Current_Consensus_Items",
    "Only for talking about Big C consensus items, small c consensus items should be discussed with people at the space at the time of the change/new item or can be reserved for the discussion section of the meeting.",
    "Only for talking about Big C consensus items, small c consensus items should be discussed with people at the space at the time of the change/new item.",
    # Spending Needs section instruction
    "Gotta spend money on XYZ (i.e. Gate, wiring etc.). WHO CAN SIGN THE CHECK OR LEND THE CREDIT CARD!?!?! It doesn't matter if we agree to do something and it can't be paid for.",
    # Discussion Items section instructions
    "It is recommended to post items for discussion ahead of time at https://pad.riseup.net/p/nbmeeting.",
    "Item prompts should follow the format:",
    "From/Raised by: [your name here]",
    "Seeking [decision/outcome/advice/something else]",
    "On topic: [topic]",
}

# Lines removed when they are ONLY the prefix with no content after it.
# e.g. "* Fundraising Update:" → removed (empty template bullet)
# but "* Fundraising Update: $500 from pizza night" → kept (has content)
REMOVE_IF_EMPTY_BULLETS = [
    "* Fundraising Update:",
    "* Announcements:",
    "* Finances:",
    "* New members:",
    "* New associates:",
    "* Consensus Items:",
    "* Discussion Items:",
]

# Lines removed that START with these prefixes regardless of content
REMOVE_STARTSWITH = [
    "UPDATE meeting number",
    # Template/meeting block instructions
    "* Click",
    "* Delete this paragraph",
    "*Live notes",
    "**https://pad.riseup.net",
    "*Virtual Meeting",
    "**https://meet.jit.si",
    "**Or, if it's ever revived:",
    "** Update last meeting:",
    "** Update next meeting:",
    "<code>Only after finishing notes",
    "{{template/meeting}}",
    # Notetaker reminder boilerplate at end of template
    "...Copy/Paste more discussion items as needed...",
    "* What wiki pages need updating?",
    "* Clean and tidy the meeting notes",
    "* Fill out the short summary",
    "* Copy paste the notes to the next meeting",
    "* Email the meeting summary",
    "* CC on the email treasurer",
    "* Edit the Current Consensus Items",
    "* Edit the Consensus Items History",
    "* Do a 10 minute cleanup",
    "* Have some beers",
    "* Prepare the next weeks",
    "* Lick the walls",
    "* sing the [[Hackernationale]]",
    # Announcements meta-commentary lines (various forms)
    "- (bullets that mediawiki doesn't like)",
    "(prefix spaces:",
    "(asterix",
    "* (asterix",
    "* (prefix",
    "* - (bullets",
    "---",
]

# Inline substrings removed from lines that otherwise have real content.
# These are instruction fragments that appear inside valid content lines.
REMOVE_INLINE = [
    ") TWO MINUTES MAX",
]

# Section headers that use legacy/template naming → renamed to current standard
RENAME_SECTIONS = {
    "= Big C Consensus Items =": "= Consensus Items =",
}


def should_remove_line(line: str) -> bool:
    """Return True if this line is a template artifact to be removed."""
    stripped = line.strip()

    if stripped in REMOVE_EXACT_LINES:
        return True

    for prefix in REMOVE_STARTSWITH:
        if stripped.startswith(prefix):
            return True

    # Remove empty template summary bullets (prefix only, nothing after colon)
    for bullet in REMOVE_IF_EMPTY_BULLETS:
        if stripped == bullet or stripped == bullet + ' ':
            return True

    # Remove empty == [[ Consensus Items ]] == template subsection
    if re.match(r'^\s*==\s*\[\[\s*Consensus Items\s*\]\]\s*==\s*$', stripped):
        return True

    return False


def clean_inline(line: str) -> str:
    """Remove inline template artifacts from a line that has real content."""
    for artifact in REMOVE_INLINE:
        line = line.replace(artifact, '')
    return line


def rename_section_headers(line: str) -> str:
    """Apply standardized section header renames."""
    stripped = line.strip()
    for old, new in RENAME_SECTIONS.items():
        if stripped == old:
            return new
    return line


def strip_html_comments(text: str) -> str:
    """
    Remove HTML comments from wikitext using a character-level state machine.

    Handles:
    - Single-line complete:  <!-- ... -->          removed, rest of line kept
    - Multi-line complete:   <!-- ...\n... -->      removed across lines
    - Line-level truncated:  <!-- text (no -->)     removes only that line;
                             does NOT start multi-line mode, preventing
                             accidental consumption of following real content
                             (e.g. notetaker accidentally cut off a comment)
    """
    lines = text.split('\n')
    cleaned = []
    in_comment = False

    for line in lines:
        out = ''
        i = 0
        while i < len(line):
            if not in_comment:
                start = line.find('<!--', i)
                if start == -1:
                    out += line[i:]
                    break
                out += line[i:start]
                end = line.find('-->', start + 4)
                if end != -1:
                    i = end + 3          # comment closed on same line, skip it
                else:
                    if out.strip() == '':
                        # Comment is the only content on this line and is unclosed.
                        # Treat as a line-level comment: remove the line,
                        # but do NOT enter multi-line comment mode.
                        out = ''
                        break
                    else:
                        # Unclosed comment after real content: multi-line mode
                        in_comment = True
                        break
            else:
                end = line.find('-->', i)
                if end != -1:
                    in_comment = False
                    i = end + 3
                else:
                    break                # whole remainder of line is inside comment

        cleaned.append(out.rstrip())

    return '\n'.join(cleaned)


def fix_topic_headers(lines: list) -> list:
    """
    Replace [topic] and [num] placeholders in section headers.

    Looks ahead for topic in either:
    - 'On topic: ...' field (old format)
    - '{{DiscussionItem|...|topic = ...}}' block (new format)

    For [num], counts the preceding numbered == N: ... == headers
    to determine the next number.

    Examples:
        == 1: [topic] ==   with DiscussionItem topic=Wheezy ATL  → == 1: Wheezy ATL ==
        == [num]: [topic-short] ==  (7th item)                   → == 7: Email to Secretary... ==
    """
    def find_topic_ahead(lines, start_idx):
        """Return the topic string found in the next 12 lines, or None."""
        for j in range(start_idx, min(start_idx + 12, len(lines))):
            # Old format: On topic: ...
            m = re.match(r'^\s*On topic:\s*(.+)', lines[j])
            if m:
                return m.group(1).strip()
            # New format: | topic = ... inside a {{DiscussionItem}} block
            m = re.match(r'^\s*\|\s*topic\s*=\s*(.+)', lines[j])
            if m:
                return m.group(1).strip()
        return None

    result = []
    for i, line in enumerate(lines):
        if re.match(r'^\s*==+', line):
            if '[topic]' in line or '[topic-short]' in line:
                topic = find_topic_ahead(lines, i + 1)
                if topic:
                    line = re.sub(r'\[topic(?:-short)?\]', topic, line)

            if '[num]' in line:
                # Count from `result` (already-processed lines) so that previously
                # resolved [num]→N headers are visible to subsequent [num] items.
                n = sum(1 for r in result if re.match(r'^\s*==\s*\d+\s*:', r)) + 1
                line = line.replace('[num]', str(n))

        result.append(line)
    return result


def fix_date_metadata(text: str, date_str: str) -> str:
    """
    Replace date placeholder in the metadata wikitable with the actual date.
    Converts YYYY_MM_DD → 'YYYY MM DD' (wiki table format).

    Handles patterns like:
      | 2026 mm dd  UPDATE DATE
      | 2026-MM-DD
    """
    y, m, d = date_str.split('_')
    wiki_date = f"{y}-{m}-{d}"
    # Replace placeholder dates — handles:
    #   | 2026 mm dd  UPDATE DATE   (lowercase placeholder)
    #   | 2026 MM DD  UPDATE DATE   (uppercase placeholder)
    #   | 2026 4 14   UPDATE DATE   (numeric month/day + UPDATE DATE)
    #   | 2026 MONTH DAY            (word-style placeholder)
    text = re.sub(
        r'(\|\s*\d{4}\s+)(?:mm\s+dd|MM\s+DD|\d{1,2}\s+\d{1,2}|[a-zA-Z]{2,}\s+[a-zA-Z0-9]{2,})\s*(?:UPDATE\s+DATE)?',
        f'| {wiki_date}',
        text
    )
    # Also normalise already-numeric dates in space-separated format: | 2026 03 03
    text = re.sub(
        r'^\|\s*(\d{4})\s+(\d{1,2})\s+(\d{1,2})\s*$',
        lambda m: f'| {m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}',
        text,
        flags=re.MULTILINE
    )
    return text


def fix_nav_links(text: str, date_str: str) -> str:
    """
    Replace Previous/Next meeting link placeholders with real dates.

    Previous meeting = 7 days before date_str (last Tuesday).
    Next meeting     = 7 days after  date_str (next Tuesday).

    Handles placeholder patterns like:
      [[Meeting_Notes_2026 MONTH DAY|Previous Meeting]]
      [[Meeting_Notes_2026 MONTH DAY|Next Meeting]]
    """
    y, m, d = (int(x) for x in date_str.split('_'))
    meeting = date(y, m, d)
    prev_date = meeting - timedelta(days=7)
    next_date = meeting + timedelta(days=7)

    def fmt(dt):
        return f"Meeting_Notes_{dt.strftime('%Y_%m_%d')}"

    # Replace placeholder previous/next links (various formats)
    text = re.sub(
        r'\[\[Meeting_Notes_\d{4}[_ ][A-Za-z0-9_ ]*\|Previous Meeting\]\]',
        f'[[{fmt(prev_date)}|Previous Meeting]]',
        text
    )
    text = re.sub(
        r'\[\[Meeting_Notes_\d{4}[_ ][A-Za-z0-9_ ]*\|Next Meeting\]\]',
        f'[[{fmt(next_date)}|Next Meeting]]',
        text
    )
    # Remove {{last meeting}} / {{next meeting}} templates — they render as
    # "Edit redirect" boxes and add nothing since we supply the real links above.
    text = re.sub(r'\{\{last meeting\}\}', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\{\{next meeting\}\}', '', text, flags=re.IGNORECASE)
    return text


def strip_artifacts(raw_text: str, date_str: str = None) -> str:
    """
    Remove template artifacts from raw pad content.

    SACRED CONTENT RULE: Speaker dialogue, personal annotations, informal
    language, bracketed stage directions, and asides are never touched.
    Only template instruction lines are removed.

    If date_str (YYYY_MM_DD) is provided, also fixes date/nav placeholders.
    """
    # Normalize line endings
    raw_text = raw_text.replace('\r\n', '\n').replace('\r', '\n')

    # Strip HTML comments before line processing (handles multi-line comments)
    raw_text = strip_html_comments(raw_text)

    # Fix metadata placeholders if we know the date
    if date_str:
        raw_text = fix_date_metadata(raw_text, date_str)
        raw_text = fix_nav_links(raw_text, date_str)

    lines = raw_text.split('\n')

    # Fix [topic] / [num] headers (needs lookahead across lines)
    lines = fix_topic_headers(lines)

    cleaned = []
    prev_blank = False

    for line in lines:
        # Apply section header renames
        line = rename_section_headers(line)

        if should_remove_line(line):
            continue

        # Clean inline artifacts from lines with real content
        line = clean_inline(line)

        # Collapse consecutive blank lines to a single blank
        is_blank = line.strip() == ''
        if is_blank and prev_blank:
            continue

        cleaned.append(line)
        prev_blank = is_blank

    return '\n'.join(cleaned)


def find_summary_section(lines: list) -> tuple:
    """
    Find the line indices for the Meeting Summary section.
    Returns (header_idx, content_start_idx, content_end_idx).
    content_end_idx is exclusive (the index of the next = section).
    Returns None if not found.
    """
    header_idx = None
    content_end_idx = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^==\s*Meeting Summary\s*==$', stripped):
            header_idx = i
        elif header_idx is not None and i > header_idx:
            # Next top-level or second-level section ends the summary
            if re.match(r'^=+\s*\w', stripped) and not re.match(r'^==\s*Meeting Summary', stripped):
                content_end_idx = i
                break

    if header_idx is None:
        return None

    return (header_idx, header_idx + 1, content_end_idx)


def insert_summary(text: str, summary_text: str) -> str:
    """Replace the Meeting Summary section content with generated summary."""
    lines = text.split('\n')
    result = find_summary_section(lines)

    if result is None:
        print("Warning: Could not find '== Meeting Summary ==' section. Summary not inserted.", file=sys.stderr)
        return text

    header_idx, content_start, content_end = result

    new_lines = (
        lines[:header_idx + 1]
        + ['']
        + summary_text.strip().split('\n')
        + ['']
        + lines[content_end:]
    )
    return '\n'.join(new_lines)


def fix_metadata_table(text: str) -> str:
    """
    Reformat the meeting metadata wikitable header so that:
    - Multiple Note-taker[s] cells (one per person) are joined into one
      comma-separated cell.
    - A row separator (|-) is inserted before Moderator[s] so it renders
      on its own row instead of merging with Note-taker[s].

    Handles the common notetaker mistake of using separate | cells per person:

      ! Note-taker[s]
      | Alice
      | Bob
      ! Moderator[s]        ← no |- before this
      | Carol

    Becomes:

      ! Note-taker[s]
      | Alice, Bob
      |-
      ! Moderator[s]
      | Carol
    """
    pattern = re.compile(
        r'(^![ \t]*Note-taker\[s\][ \t]*\n)'  # header: ! Note-taker[s]
        r'((?:^\|[^\n]*\n)+)'                  # one or more | cell lines
        r'(?=^!)',                              # lookahead: next line is a ! header
        re.MULTILINE,
    )

    def _join(m: re.Match) -> str:
        header = m.group(1)
        cells = m.group(2)
        names = [re.sub(r'^\|\s*', '', line).strip()
                 for line in cells.splitlines() if line.startswith('|')]
        return f'{header}| {", ".join(names)}\n|-\n'

    return pattern.sub(_join, text)


def fix_discussion_item_blocks(text: str) -> str:
    """
    Notetakers sometimes put discussion content inside {{DiscussionItem|...}}
    instead of after }}. This pulls that content out after the closing }}.

    Before:
      {{DiscussionItem|
       | topic = Foo
       | seeking = bar
      Some content stuffed inside.
      }}

    After:
      {{DiscussionItem|
       | topic = Foo
       | seeking = bar
      }}

      Some content stuffed inside.
    """
    def fix_block(m):
        block = m.group(0)
        lines = block.splitlines()
        params = []
        content = []
        for line in lines:
            stripped = line.strip()
            if stripped == '}}':
                continue  # drop original closing, we'll re-add it
            elif stripped.startswith('{{DiscussionItem') or stripped.startswith('|'):
                params.append(line)
            elif not stripped:
                # blank lines within params — skip; within content — keep
                if content:
                    content.append(line)
            else:
                content.append(line)
        result = '\n'.join(params) + '\n}}'
        if content:
            # strip leading/trailing blank lines
            while content and not content[0].strip():
                content.pop(0)
            while content and not content[-1].strip():
                content.pop()
            if content:
                result += '\n\n' + '\n'.join(content)
        return result

    pattern = re.compile(r'\{\{DiscussionItem\|.*?^ *}}', re.DOTALL | re.MULTILINE)
    text = pattern.sub(fix_block, text)

    # Close any remaining unclosed {{DiscussionItem| blocks (missing }}).
    # An unclosed block starts at {{DiscussionItem| and ends just before the next
    # section header (= or ==) or end of string.
    def close_unclosed(m):
        block = m.group(1)
        lines = block.splitlines()
        params = []
        content = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('{{DiscussionItem') or stripped.startswith('|'):
                params.append(line)
            elif stripped:
                content.append(line)
        result = '\n'.join(params) + '\n}}'
        if content:
            result += '\n\n' + '\n'.join(content)
        # Ensure a trailing newline so the following section header stays on its own line
        return result + '\n'

    unclosed_pattern = re.compile(
        r'(\{\{DiscussionItem\|(?:(?!\{\{DiscussionItem\|)(?!^ *}}).)*?)(?=^=|\Z)',
        re.DOTALL | re.MULTILINE
    )
    text = unclosed_pattern.sub(close_unclosed, text)

    return text


def format_speaker_attributions(text: str) -> str:
    """
    Reformat speaker attribution lines to MediaWiki bold style.
    Handles notetaker formats:

      * Name: text       →  '''Name:''' text
      * Name - text      →  '''Name:''' text
      * Name- text       →  '''Name:''' text
      - Name: text       →  '''Name:''' text   (dash prefix, e.g. in Excellence)

    The Introductions and Short announcements sections are protected from
    attribution conversion (they contain intro blurbs, not speaker exchanges).

    Leading spaces are stripped globally to prevent MediaWiki <pre> boxes.
    """
    # Strip leading spaces from ALL lines globally (prevents <pre> boxes everywhere)
    text = re.sub(r'^ +', '', text, flags=re.MULTILINE)

    # Strict name pattern: each word must start with a capital letter.
    # Prevents multi-word labels like "Action item", "Door code opinion".
    # (?!\d) blocks "Width: 67" style measurements.
    # (?![A-Z][a-z]+:) blocks "Dimensions: Depth:" style chained labels.
    # Optional trailing (qualifier) handles "Daniel (solderfumes)", "Daniel (web)" etc.
    # Uses [^\S\n]+ (not \s+) to prevent the name from spanning across newlines.
    name = r'([A-Z][a-zA-Z0-9\'/]*(?:[^\S\n]+[A-Z][a-zA-Z0-9\'/]*){0,3}(?:[^\S\n]+\([^)]+\))?)'
    no_label = r'(?!\d)(?![A-Z][a-z]+:)'
    # Use [^\S\n]+ (non-newline whitespace) for trailing space after - or :
    # This prevents patterns from consuming newlines and merging lines.
    sp = r'[^\S\n]+'

    def apply_attributions(s: str) -> str:
        # optional non-newline whitespace before the colon handles "Name :" typo
        osp = r'[^\S\n]*'
        # * Name: text  (bullet + colon, optional space before colon)
        s = re.sub(rf'^\*{sp}{name}{osp}:{sp}{no_label}', r"'''\1:''' ", s, flags=re.MULTILINE)
        # * Name - text  (bullet + space-dash-space)
        s = re.sub(rf'^\*{sp}{name}{sp}-{sp}', r"'''\1:''' ", s, flags=re.MULTILINE)
        # * Name- text  (bullet + no-space-dash)
        s = re.sub(rf'^\*{sp}{name}-{sp}', r"'''\1:''' ", s, flags=re.MULTILINE)
        # - Name: text  (leading dash, e.g. Excellence section; optional space before colon)
        s = re.sub(rf'^-{sp}{name}{osp}:{sp}{no_label}', r"'''\1:''' ", s, flags=re.MULTILINE)
        # Bare "Name: text" line (optional space before colon)
        s = re.sub(rf'^{name}{osp}:{sp}{no_label}', r"'''\1:''' ", s, flags=re.MULTILINE)
        # Bare "Name - text" line
        s = re.sub(rf'^{name}{sp}-{sp}', r"'''\1:''' ", s, flags=re.MULTILINE)
        return s

    # Protect Introductions and Short announcements sections using placeholders.
    # These sections contain bullet-formatted intro blurbs, not speaker exchanges.
    # Strategy: replace each protected section with a placeholder, apply
    # attributions to the rest, then restore.
    protected = {}

    def protect_section(m):
        key = f'\x00PROT{len(protected)}\x00'
        protected[key] = m.group(0)
        return key

    # Match from section header through to (but not including) next top-level header
    skip_re = re.compile(
        r'^= (?:Introductions|Short announcements and events) =[ \t]*\n'
        r'(?:(?!^= ).)*',
        re.MULTILINE | re.DOTALL
    )
    protected_text = skip_re.sub(protect_section, text)

    # Also protect == [[Membership]] == subsection — entries are list items, not dialogue
    membership_re = re.compile(
        r'^==\s*\[\[Membership\]\]\s*==[ \t]*\n'
        r'(?:(?!^=).)*',
        re.MULTILINE | re.DOTALL
    )
    protected_text = membership_re.sub(protect_section, protected_text)

    # Apply attributions to all non-protected content
    processed = apply_attributions(protected_text)

    # Restore protected sections
    for key, val in protected.items():
        processed = processed.replace(key, val)

    # Ensure blank line before/after every attribution line throughout the document
    # so MediaWiki renders each speaker as their own paragraph in any section
    # (Kudos, Excellence, Discussion Items, etc.).
    # Blank line BEFORE an attribution that follows non-blank content
    processed = re.sub(r'([^\n])\n(\'\'\')', r'\1\n\n\2', processed)
    # Blank line AFTER an attribution that is followed by non-blank content
    processed = re.sub(r"('''[^']+:''' [^\n]+)\n(\S)", r'\1\n\n\2', processed)
    # Normalize 3+ blank lines to 2
    processed = re.sub(r'\n{3,}', '\n\n', processed)

    return processed


def format_task_board(text: str) -> str:
    """
    Convert Do-ocratic Task Board bullet list into a wikitable.

    Bullets are expected in the form:
      * Task description - Person
      * Task description          (no person assigned)

    Template placeholder lines ("What wiki pages need updating?") are dropped.
    The intro paragraph ("Participation also means...") is preserved above the table.
    """
    pattern = re.compile(
        r'(^= Do-ocratic Task Board =\s*$)(.*?)(?=^= |\Z)',
        re.MULTILINE | re.DOTALL
    )

    def convert(m):
        header = m.group(1)
        body = m.group(2)

        intro_lines = []
        rows = []

        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('* '):
                item = stripped[2:].strip()
                # Drop template placeholder lines
                if re.search(r'wiki pages need updating|what new wiki pages', item, re.IGNORECASE):
                    continue
                # Split "Task - Person" on last ' - '
                if ' - ' in item:
                    task, person = item.rsplit(' - ', 1)
                else:
                    task, person = item, ''
                rows.append((task.strip(), person.strip()))
            elif not stripped.startswith('=') and not stripped.startswith('{') and not stripped.startswith('|'):
                intro_lines.append(stripped)

        if not rows:
            return m.group(0)

        intro = ('\n' + ' '.join(intro_lines) + '\n') if intro_lines else '\n'
        table = '\n{| class="wikitable"\n! Task !! Person !! Notes\n'
        for task, person in rows:
            table += f'|-\n| {task} || {person} ||\n'
        table += '|}\n'

        return header + intro + table

    return pattern.sub(convert, text)


_OL_ITEM_RE = re.compile(r'^(\s*)(\d+)[.)]\s+(.+)$')


def fix_ordered_lists(text: str) -> str:
    """
    Convert bare numbered lists (1. 2. 3. / 1) 2) 3)) to MediaWiki # format.

    Rules:
    - A sequence must start at 1 and increment by 1.
    - Blank lines between items are allowed and collapsed (a blank mid-list
      would break MediaWiki rendering, so we remove them within the sequence).
    - Any non-blank, non-list line between items breaks the sequence.
    - Requires at least 2 items — a lone "1." is not converted (likely a
      sentence fragment or reference, not a list).
    - Lines inside wiki tables (starting with | or !) are skipped.
    """
    lines = text.split('\n')
    n = len(lines)

    # First pass: for each line, record its list-item match (or None)
    parsed = [_OL_ITEM_RE.match(l) for l in lines]

    to_convert = set()   # indices of lines to rewrite as # items
    to_drop    = set()   # indices of blank lines collapsed within a sequence

    i = 0
    while i < n:
        m = parsed[i]
        if not m or int(m.group(2)) != 1:
            i += 1
            continue

        # Potential list start — look ahead for 2, 3, …
        indent   = m.group(1)
        sequence        = [i]   # line indices that are list items
        pending_blanks  = []    # blank lines since last item (not yet committed)
        inter_blanks    = []    # blank lines confirmed between two items
        expected = 2
        j = i + 1

        while j < n:
            line = lines[j]
            if not line.strip():
                pending_blanks.append(j)
                j += 1
                continue
            nm = parsed[j]
            if nm and nm.group(1) == indent and int(nm.group(2)) == expected:
                # Blanks between this item and the previous → drop them
                inter_blanks.extend(pending_blanks)
                pending_blanks = []
                sequence.append(j)
                expected += 1
                j += 1
            else:
                break         # non-blank, non-list line — sequence ends

        if len(sequence) >= 2:
            for idx in sequence:
                to_convert.add(idx)
            for idx in inter_blanks:
                to_drop.add(idx)

        i += 1

    result = []
    for i, line in enumerate(lines):
        if i in to_drop:
            continue
        if i in to_convert:
            m = parsed[i]
            result.append(m.group(1) + '# ' + m.group(3))
        else:
            result.append(line)

    return '\n'.join(result)


def ensure_bullets(text: str, section_headers: list) -> str:
    """
    Within specified top-level sections (e.g. Introductions, Short announcements),
    ensure every content line starts with a bullet. Lines that are already bullets,
    blank, headers, or wiki markup (templates, tables, HTML) are left alone.
    """
    # Matches any top-level section header (single = on each side)
    section_re = re.compile(r'^= .+ =$', re.MULTILINE)

    for header in section_headers:
        # Split at this header, then at the next top-level section
        pattern = re.compile(
            rf'(^= {re.escape(header)} =\s*$)(.*?)(?=^= |\Z)',
            re.MULTILINE | re.DOTALL
        )
        def bulletize(m):
            head = m.group(1)
            body = m.group(2)
            fixed_lines = []
            for line in body.split('\n'):
                stripped = line.strip()
                if (not stripped
                        or stripped.startswith('*')
                        or stripped.startswith('=')
                        or stripped.startswith('{')
                        or stripped.startswith('|')
                        or stripped.startswith('<')
                        or stripped.startswith('#')
                        or stripped.startswith(':')):
                    fixed_lines.append(line)
                else:
                    fixed_lines.append('* ' + line.lstrip())
            return head + '\n'.join(fixed_lines)
        text = pattern.sub(bulletize, text)

    return text


def _ordinal(n: int) -> str:
    """Return ordinal string for n: 1 → '1st', 859 → '859th', etc."""
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'
