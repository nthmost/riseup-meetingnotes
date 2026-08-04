# How Meeting Notes Become a Wiki Page

A step-by-step guide to what nbarchive does to raw Noisebridge meeting notes,
from the moment they leave the Riseup Pad to the moment they appear on the wiki.

---

## The raw material

Every Tuesday, Noisebridge holds a meeting. Notes are taken live in a shared
[Riseup Pad](https://pad.riseup.net/p/nbmeeting). Anyone can edit. The result
is a mix of real content — speaker dialogue, decisions, announcements — and
template scaffolding that was never cleaned up before the meeting ended.

A raw pad export typically looks something like this:

```
<div style="background-color:#ddd8;border:3px dashed #3338;padding:3px;">
-> Date / Note-taker / Moderator // Prev + Next links / Summary
</div>

= Meeting Notes =

| 2026 mm dd  UPDATE DATE
...
[[Meeting_Notes_2026 MONTH DAY|Previous Meeting]] | [[Meeting_Notes_2026 MONTH DAY|Next Meeting]]

== Meeting Summary ==
FILL OUT AT END OF MEETING AND SEND TO MAILING LIST/SLACK/DISCORD
Include:
TLDR what happened at the meeting:
* '''One or two bullet points of high-level meeting summary.'''

== Introductions ==
* ''' name ''' (pronouns): introduction

...

== Discussion Items ==
* Alice: I think we should get a new laser cutter
* Bob - the old one still works fine
* Carol: what's the budget?
1. first option
2. second option
3. third option

...
```

The template was designed to be filled in and then cleaned up by the notetaker.
In practice, cleanup rarely happens. That's what this pipeline is for.

---

## Stage 1 — Fetch and archive

**What happens**: The pipeline fetches the pad's plain-text export and saves it
as a read-only `.txt` file. This is the permanent record of what was on the pad
at the time of capture.

**Why read-only**: The live raw file is only ever changed through a versioned
path — a pad refresh, or a human edit in the web UI (**Edit Raw**). Before any
change, the outgoing content is archived as an immutable read-only snapshot, so
you can always re-run from — or restore — any earlier version. Human editing
exists to fix source-level disasters (e.g. notes written with the wrong
template) and works even after the notes have been published.

**What's stored**: The current file path, SHA-256 hash, byte size, capture
timestamp, and source URL live in `raw_captures`. Every version of the raw
content — the initial fetch plus every refresh, edit, and restore — is recorded
in the `raw_revisions` table (source, author, reason, snapshot path, SHA, size)
with a corresponding archived snapshot on disk.

---

## Stage 2 — Strip artifacts

This is the largest single stage. It removes template boilerplate through
several sub-passes, all deterministic (no AI, no guessing).

### 2a. Normalize line endings

Windows-style `\r\n` and bare `\r` are converted to `\n`. This prevents
regex patterns from failing on notes copied from Windows machines.

### 2b. Strip HTML comments

The template contains HTML comments left by previous notetakers or bots.
A character-level state machine handles three cases:

- **Single-line complete** (`<!-- ... -->`) — removed, rest of line kept
- **Multi-line complete** (`<!-- ...\n...\n... -->`) — everything between
  the tags removed across lines
- **Unclosed comment on its own line** (`<!-- orphan`) — that line is
  removed, but multi-line mode is *not* entered. This prevents a typo'd
  comment from accidentally eating the next section of real content.

### 2b2. Strip leading whitespace

Every line has its leading tabs and spaces removed immediately after the
HTML-comment pass. This is essential for Riseup Pad exports: the pad wraps
its entire content in a one-level tab indent, so every line — headers,
bullets, template parameters, section markers — arrives as `\t== Heading ==`
or `\t* bullet` rather than `== Heading ==` or `* bullet`.

Why this matters:
- MediaWiki renders any line that starts with a space (or tab) as a
  `<pre>` code block, so indented bullets and section headers render as
  raw preformatted text instead of wiki markup.
- Downstream transforms use `^|`, `^!`, and `^=` anchors that only match
  at the real start of a line — they silently fail on indented input.

Both spaces *and* tabs are stripped (`[\t ]+`). The strip happens before any
per-line rule evaluation, so artifact-removal rules work correctly regardless
of how deeply the pad indented the content.

### 2c. Fix date metadata

The template contains a placeholder like `| 2026 mm dd  UPDATE DATE` in
the meeting metadata wikitable. This is replaced with the actual date in
`YYYY-MM-DD` format. Space-separated dates already present (`| 2026 03 04`)
are normalised to the same format.

### 2d. Fix navigation links

The template's Previous/Next meeting links contain placeholder dates:

```
[[Meeting_Notes_2026 MONTH DAY|Previous Meeting]]
[[Meeting_Notes_2026 MONTH DAY|Next Meeting]]
```

These are replaced with real links calculated from the meeting date (±7 days).
`{{last meeting}}` and `{{next meeting}}` template calls are removed — they
render as broken "Edit redirect" boxes and are superseded by the real links.

### 2e. Fix topic and number placeholders in headers

Discussion item headers sometimes contain `[topic]` or `[num]` placeholders
that notetakers forgot to fill in:

```
== 1: [topic] ==
On topic: Budget for new laser cutter
Raised by: Alice
```

The pipeline looks ahead up to 12 lines for an `On topic:` field (old format)
or a `| topic =` field inside a `{{DiscussionItem}}` block (new format), and
substitutes the real topic name:

```
== 1: Budget for new laser cutter ==
```

If the topic value still has template angle-bracket wrapping (see Stage 5b),
it is stripped before substitution, so `| topic = <Budget for laser cutter>`
produces `== 1: Budget for laser cutter ==`, not
`== 1: <Budget for laser cutter> ==`.

For `[num]`, it counts the already-resolved numbered headers above to determine
the correct sequence number.

### 2f. Per-line artifact removal

Each line is checked against three rule sets:

**Exact match** — lines that are removed only if they match precisely (after
stripping leading/trailing whitespace). This set contains about 30 entries
including the `<div>` wrapper, section instruction text like
`FILL OUT AT END OF MEETING AND SEND TO MAILING LIST/SLACK/DISCORD`, and
placeholder bullets like `* ''' name ''' (pronouns): introduction`.

**Starts-with** — lines removed when they begin with a known prefix regardless
of what follows. Examples: `* Click`, `* Delete this paragraph`,
`**https://meet.jit.si`, `---`, `{{template/meeting}}`, and the end-of-meeting
notetaker checklist (`* Have some beers`, `* Lick the walls`, etc.).

**Empty template bullets** — lines like `* Fundraising Update:` are removed
when they contain only the label with nothing after the colon. The same line
with real content (`* Fundraising Update: $500 raised at the bake sale`) is
kept. This handles the common case where notetakers left section stubs unfilled.

**Section header renames** — `= Big C Consensus Items =` is renamed to
`= Consensus Items =` for consistency with current usage.

### 2g. Inline artifact removal

Some artifact text appears *inside* lines that otherwise contain real content.
`) TWO MINUTES MAX` is stripped from the end of discussion item lines — it was
a timer instruction in the template that notetakers sometimes left in.

### 2h. Collapse consecutive blank lines

Multiple consecutive blank lines are collapsed to a single blank line.
MediaWiki renders them identically, and the template leaves clusters of blanks
where removed lines used to be.

---

## Stage 3 — Fix meeting number

Noisebridge tracks a running count of meetings. The template contains a line
like:

```
[https://www.noisebridge.net/wiki/Category:Meeting_Notes The 859th Meeting of Noisebridge]
```

If the notetaker left a placeholder (anything that isn't already a proper
ordinal like `859th`), the pipeline fetches the previous Tuesday's published
wiki page, finds that page's meeting number, and increments it by one.

If the previous page can't be fetched, or the number is already correctly
filled in, this stage is a no-op.

---

## Stage 4 — Generate AI summary

**What the AI writes**: Only the `== Meeting Summary ==` section. Nothing else.

The meeting notes pad almost always has this section left blank — it's the one
piece of the template that notetakers consistently skip. The pipeline fills it
in using Claude Haiku.

**What the AI is told to produce**:

- A 1–3 sentence plain-text TLDR
- Bullet points for: Fundraising Update, Announcements, Finances, New members,
  New associates, Consensus Items, Discussion Items
- Factual extractions only — what topics came up, what outcomes were reached,
  who attended as a new member. No paraphrasing of dialogue.
- For new members, the membership tier is included if stated in the notes
  (current tiers: Core Member, Access Member, Philanthropist — fetched live
  from the wiki's Membership page so the prompt stays current as naming
  conventions evolve)
- For consensus items: a clear distinction between *proposed* (raised at this
  meeting) and *passed* (explicitly confirmed with no blocks). The word
  "resolved" in formal proposal language does not mean the item passed.

**What the AI is explicitly forbidden from doing**:

- Paraphrasing what anyone said
- Inventing or inferring anything not in the notes
- Using Markdown formatting (wiki uses `'''bold'''`, not `**bold**`)

**If no API key is set**, this stage is skipped and the Meeting Summary section
is left with whatever the notetakers wrote (usually blank).

---

## Stage 5 — Fix ordered lists

Notetakers frequently write numbered lists in plain text:

```
1. First option
2. Second option
3. Third option
```

MediaWiki doesn't render these as lists — it treats them as plain text.
The pipeline converts them to MediaWiki's `#` format:

```
# First option
# Second option
# Third option
```

Rules:
- The sequence must start at 1 and increment by 1. A lone `1.` is not
  converted (likely a sentence fragment, not a list).
- Both `1.` and `1)` styles are recognised.
- Blank lines between items are collapsed (a blank mid-list breaks MediaWiki
  rendering).
- At least two items required.
- Lines inside wiki tables (`|` or `!` prefix) are skipped.

---

## Stage 5b — Strip angle-bracket placeholders

The meeting template uses angle brackets as fill-in-the-blank cues for
`{{DiscussionItem}}` parameters:

```
{{DiscussionItem|
 | topic = <Music Room Etiquette/Cleanup>
 | raised_by = <Lucifer>
 | seeking = decision/outcome/advice/[?]
}}
```

Notetakers fill in the values but sometimes forget to remove the brackets.
This stage strips the `<...>` wrapper when it is the *entire* value on a
parameter line:

```
{{DiscussionItem|
 | topic = Music Room Etiquette/Cleanup
 | raised_by = Lucifer
 | seeking = decision/outcome/advice/[?]
}}
```

The rule fires only when `<...>` is the complete value — a URL or partial
wrap like `| notes = see <http://...> for details` is left untouched.
The same unwrapping is applied when the topic value is extracted for header
substitution in Stage 2e.

---

## Stage 6 — Fix Discussion Item blocks

The newer meeting template uses `{{DiscussionItem|...}}` template blocks:

```
{{DiscussionItem|
 | raised_by = Alice
 | topic = Budget for new laser cutter
 | seeking = decision
Some notes about the discussion were typed inside the block by mistake.
}}
```

MediaWiki template parameters end at `}}`. Content typed inside the block
after the last parameter is lost. The pipeline detects this pattern and moves
the misplaced content to after the closing `}}`:

```
{{DiscussionItem|
 | raised_by = Alice
 | topic = Budget for new laser cutter
 | seeking = decision
}}

Some notes about the discussion were typed inside the block by mistake.
```

Unclosed blocks (missing `}}`) are also closed, with any trailing content
similarly extracted.

---

## Stage 7 — Format the Do-ocratic Task Board

The task board section is typically a bullet list:

```
= Do-ocratic Task Board =
* Fix the laser cutter - Alice
* Update the wiki safety page
* Order more solder
```

This is converted to a wikitable for readability:

```
= Do-ocratic Task Board =

{| class="wikitable"
! Task !! Person !! Notes
|-
| Fix the laser cutter || Alice ||
|-
| Update the wiki safety page ||  ||
|-
| Order more solder ||  ||
|}
```

Template placeholder lines (`* What wiki pages need updating?`) are dropped
from the table. An intro paragraph above the bullets, if present, is preserved.

---

## Stage 8 — Format speaker attributions

This is the most complex transform. Notes are taken in free-form, and
attribution styles vary widely by notetaker:

```
* Alice: I think we should get a new laser cutter
* Bob - the old one still works fine
* Carol- what's the budget?
- Dave: we have $2,000 in the equipment fund
Erin: I can look into prices
Heather -equipment damage, someone left a bag on the violin
WHeezy - thank you for taking initiative
```

All of these are converted to MediaWiki bold attribution style:

```
'''Alice:''' I think we should get a new laser cutter
'''Bob:''' the old one still works fine
'''Carol:''' what's the budget?
'''Dave:''' we have $2,000 in the equipment fund
'''Erin:''' I can look into prices
'''Heather:''' equipment damage, someone left a bag on the violin
'''WHeezy:''' thank you for taking initiative
```

### Colon patterns — multi-word names OK

Colon-based attribution (`Name:` or `* Name:`) is recognised for names up
to four words, where the first word may be any case and subsequent words must
start with a capital. A trailing parenthetical qualifier is also accepted
(`Daniel (solderfumes):`, `Alice (she/her):`).

```
* naomi: said something           →  '''naomi:''' said something
* Daniel (web): fixed the printer →  '''Daniel (web):''' fixed the printer
```

### Dash patterns — single-word names only

Dash-based attribution (`Name -`, `Name -`, `Name-`) is restricted to
**single-word names** (plus an optional qualifier in parens). This avoids
false positives on natural sentence dashes:

```
Heather - equipment damage        →  '''Heather:''' equipment damage  ✓
Heather -equipment damage         →  '''Heather:''' equipment damage  ✓  (space before, none after)
Heather- equipment damage         →  '''Heather:''' equipment damage  ✓  (none before, space after)
Music Room - needs cleanup        →  (unchanged — two-word name, left alone)
```

The three spacing variants handled:

| Input pattern | Example |
|---|---|
| `Name - text` | `Nixxy - In the wood shop` |
| `Name -text` | `Heather -equipment damage` |
| `Name- text` | `Carol- what's the budget?` |

A lone `Name-text` (no spaces anywhere) is *not* treated as attribution —
it looks indistinguishable from a hyphenated word at the start of a sentence.

### Denylist

Common single-word labels that look syntactically like names are blocked
from attribution: `note`, `notes`, `todo`, `action`, `update`, `status`,
`fyi`, `tldr`, `summary`, `agenda`, `topic`, `decision`, `aside`, and a
few others. The check is case-insensitive, so `NOTE:` is also blocked.
A real name that merely *starts with* a denylist word (e.g. `Noteworthy:`)
is unaffected — the full word must match.

### Protected sections

The `= Introductions =` and `= Short announcements and events =` sections
are excluded from attribution conversion entirely. These sections contain
intro blurbs and announcements formatted as bullet lists, not speaker
dialogue. The `== [[Membership]] ==` subsection is also excluded.

### Blank-line insertion

After attribution conversion, blank lines are ensured before and after every
attribution line throughout the document. This means consecutive attributions
that notetakers wrote without blank lines between them — common when notes
are rushed — are automatically separated so MediaWiki renders each speaker as
their own paragraph rather than running them together as one block of text.

```
Heather -equipment damage
Nixxy - In the wood shop

→

'''Heather:''' equipment damage

'''Nixxy:''' In the wood shop
```

---

## Stage 9 — Ensure bullets

The `= Introductions =` and `= Short announcements and events =` sections
are expected to be bullet lists. Notetakers sometimes forget the `*`. Any
content line in these sections that isn't already a bullet, blank, a header,
or wiki markup (templates, tables, HTML) gets one prepended.

---

## Stage 10 — Add banner and category footer

Two standard wiki elements are added if not already present:

- `{{meetings2026}}` at the top — a navigation banner linking to all 2026
  meeting notes (updated each year)
- `[[Category:Meeting Notes]]` at the bottom — ensures the page appears in
  the wiki's meeting notes index

---

## Output

The pipeline produces:

**`Meeting_Notes_YYYY_MM_DD_passN.wiki`** — the processed wiki text, ready
to be pasted or published.

**`Meeting_Notes_YYYY_MM_DD_passN_trace.md`** — a step-by-step record of
what each stage did, showing line counts before and after:

```markdown
| Step                              | Lines in | Lines out | Δ   | Note                                          |
|-----------------------------------|----------|-----------|-----|-----------------------------------------------|
| strip_artifacts                   | 350      | 312       | -38 |                                               |
| fix_meeting_number                | 312      | 312       | 0   | resolved to 859th                             |
| fix_metadata_table                | 312      | 312       | 0   |                                               |
| generate_summary                  | 312      | 312       | 0   | model=claude-haiku-4-5-20251001, 1205/312 tok |
| fix_ordered_lists                 | 312      | 309       | -3  |                                               |
| strip_angle_bracket_placeholders  | 309      | 309       | 0   |                                               |
| fix_discussion_item_blocks        | 309      | 307       | -2  |                                               |
| format_task_board                 | 307      | 313       | +6  |                                               |
| format_speaker_attributions       | 313      | 313       | 0   | 47 attribution lines in output                |
| ensure_bullets                    | 313      | 315       | +2  |                                               |
| add_footer                        | 315      | 317       | +2  | {{meetings2026}} banner + [[Category:Meeting Notes]] |
```

---

## What is never changed

The pipeline will not touch:

- Anything a speaker said, verbatim
- Bracketed annotations like `[laughter]`, `[applause]`, `[someone leaves]`
- Inline asides and informal language
- Typos and misspellings in content lines
- The `= Introductions =` section speaker lines (protected from attribution
  reformatting)
- Any line that isn't a recognised template artifact

If the pipeline gets something wrong — misidentifies a content line as an
artifact, produces a bad AI summary, or corrupts a section — the raw file is
always intact, the provenance database records every pass, and a re-run
produces a fresh transformation that can replace the bad one.
