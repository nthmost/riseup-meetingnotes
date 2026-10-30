# riseup-meetingnotes

Meeting notes pipeline and archive for organisations that take notes in [Riseup Pad](https://pad.riseup.net) and publish to a [MediaWiki](https://www.mediawiki.org) instance.

Fetches raw notes from the pad after each meeting, cleans them with deterministic text transforms, generates an AI summary for review, and publishes to the wiki. Every pass is recorded with full provenance so bad outputs can be audited, rated, and re-run.

Originally built for [Noisebridge](https://www.noisebridge.net). Adapt `org_pipeline/` for your organisation.

---

## What it does

```
Riseup Pad
    │
    ▼ fetch
raw .txt (archived + versioned; human-editable)
    │
    ▼ strip_artifacts           — strip leading whitespace; remove template boilerplate
    ▼ fix_meeting_number        — resolve ordinal from previous wiki page
    ▼ fix_metadata_table        — fix Note-taker / Moderator row formatting
    ▼ fix_ordered_lists         — 1. 2. 3. → MediaWiki # lists
    ▼ strip_angle_brackets      — remove <...> wrappers from DiscussionItem params
    ▼ fix_discussion_blocks     — pull content out of {{DiscussionItem}} templates
    ▼ format_task_board         — task bullets → wikitable
    ▼ format_attributions       — "Name: text" / "Name - text" → '''Name:''' text
    ▼ ensure_bullets            — bullet-ify Introductions / Short announcements
    ▼ add_footer                — {{meetings2026}} banner + [[Category:Meeting Notes]]
    │
    ├─► (optional) Generate Summary
    │       │
    │       ▼ Claude Haiku generates Meeting Summary text
    │       ▼ user reviews and edits in the web UI
    │       ▼ Insert Summary — new pipeline pass with summary applied
    │
    ▼ publish
Noisebridge wiki  (Meeting_Notes_YYYY_MM_DD)
    │
    └─► SQLite provenance DB
        (every pass: input SHA, output path, model, tokens, quality ratings)
```

**Sacred content rule**: speaker dialogue, personal annotations, informal
language, typos, and bracketed asides are never modified. Only template
instruction lines left by notetakers are removed.

**Re-run behaviour**: re-runs always process the current raw capture (the
original pad text, or a human-edited version of it), never a previously-
processed output. This ensures pipeline fixes are applied to the source text
rather than to already-transformed content.

---

## Architecture

### Layers

```
org_pipeline/
  transforms.py             Pure text transforms — regex only, no I/O, no AI.
                            Every artifact rule, attribution pattern, and
                            list conversion lives here. Tested in isolation.
  process.py                Deterministic pipeline orchestration. Calls
                            transforms in sequence; optionally calls ai.py
                            for summary generation. No anthropic import.
  ai.py                     AI summary generation only. The single module
                            that imports anthropic. Contains generate_summary()
                            and fetch_membership_levels() (wiki call for
                            AI prompt context).
  process_meeting_notes.py  CLI entry point for standalone use. Re-exports
                            from process.py and ai.py.

pipeline/
  fetch.py                  Fetch from Riseup Pad; archive raw content to disk
                            and SQLite. Also fetches existing wiki revisions.
  nb_processor.py           CLI adapter: accepts --date / --input, writes
                            processed wiki text to stdout, emits METRICS: and
                            TRACE: JSON lines to stderr.
  adapter.py                Subprocess adapter + Processor protocol.
                            Defines TransformationResult and the injectable
                            Processor = Callable[[Path, str, dict], TransformationResult]
                            type. Tests use passthrough_processor instead of
                            the real subprocess.
  publish.py                Wiki session management and publish(). Owns the
                            cached WikiAPI session so login cost is paid once.
  run.py                    Orchestrator: resolve input → transform → save →
                            publish. Every step writes to the provenance DB.
  trace.py                  Renders step-report dicts as a _trace.md document
                            saved alongside each .wiki output.

web/
  app.py                    Flask application: archive index, per-meeting view,
                            pipeline controls, AI summary review, rating UI.
  auth.py                   Login via Noisebridge wiki credentials (no separate
                            auth system — if you can log into the wiki, you can
                            use this). CSRF protection on all state-mutating routes.
  worker.py                 Background thread that drains the pipeline_jobs
                            queue. Handles pipeline runs, publish operations,
                            AI summary generation, and pad refresh.
  templates/                Jinja2 templates (Win 3.11 aesthetic).

config.py                   All settings from environment / .env file.
db.py                       SQLite schema and all data access. WAL mode enabled.
wiki.py                     MediaWiki API client (login + edit, 429 retry).
```

### Data model

Three core tables in `provenance.db`:

**`raw_captures`** — one row per meeting date. Points to the current raw
`.txt` file on disk. The live file may change (pad refresh or a human edit),
but every version is preserved in `raw_revisions`.

**`raw_revisions`** — full version history of each capture's raw content. One
row per version — initial fetch, pad refresh, human edit, restore — each
archiving an immutable read-only snapshot plus SHA, size, author, and reason.
Humans can edit the raw notes in the web UI (**Edit Raw**) to fix source-level
disasters (e.g. notes written with the wrong template); any prior version can
be restored, which itself records a new revision so nothing is ever destroyed.

**`transformations`** — every pipeline pass, forming a lineage tree per
capture. Each row records: parent pass, input SHA, output path, output SHA,
pipeline version (git hash), model name, token counts, duration, per-step
trace, and whether it was published to the wiki.

**`quality_ratings`** — thumbs up/down per (transformation, wiki user), with
optional issue labels and a freetext excerpt. One rating per user per pass;
re-rating overwrites.

**`pipeline_jobs`** — async job queue consumed by `web/worker.py`. The web UI
enqueues a job and polls `/api/job-status/<id>` until done, then redirects to
the result.

**`template_snapshots`** — one row per unique revision of the meeting notes
template page seen on the wiki. The first row is the baseline (auto-acknowledged).
Subsequent new revisions trigger a warning banner in the UI until a logged-in
user acknowledges the change.

### Processor protocol

The subprocess interface lets any processing script plug in without code
changes to the pipeline orchestrator:

- **stdin**: nothing
- **args**: `--date YYYY_MM_DD --input <path-to-raw-file>` (plus `--no-summary`, `--summary-only`)
- **stdout**: processed wiki text (goes straight to the output `.wiki` file)
- **stderr line** `METRICS: <json>`: aggregate stats — `model_name`,
  `artifact_lines_removed`, `sections_found`, `token_usage`,
  `processor_version`
- **stderr line** `TRACE: <json>`: list of step-report dicts, one per named
  transform — `{name, lines_in, lines_out, note}`. Rendered as `_trace.md`
  alongside the output.

Set `NB_PROCESSOR_SCRIPT` in `.env` to swap in a different processor.
If the path doesn't exist, the pipeline falls back to `passthrough_processor`
(returns raw content unchanged — useful for testing provenance plumbing).

### Observability

Every non-dry-run pipeline pass produces two output files:

```
Meeting_Notes_YYYY_MM_DD_passN.wiki      processed wiki text
Meeting_Notes_YYYY_MM_DD_passN_trace.md  step-by-step line-count changes
```

The trace shows exactly what each transform did:

| Step | Lines in | Lines out | Δ | Note |
|------|----------|-----------|---|------|
| `strip_artifacts` | 350 | 312 | -38 | |
| `fix_meeting_number` | 312 | 312 | 0 | resolved to 859th |
| `generate_summary` | 312 | 312 | 0 | model=claude-haiku-4-5-20251001, 1205 in / 312 out tokens — stored for review |
| `format_speaker_attributions` | 321 | 321 | 0 | 47 attribution lines in output |
| … | | | | |

---

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — see table below
```

### Required `.env` variables

| Variable | Description |
|---|---|
| `NB_PAD_URL` | **Required.** Your meeting notes pad export URL (e.g. `https://pad.riseup.net/p/YOUR-PAD/export/txt`). No default — app warns at startup if unset. |
| `NB_SECRET_KEY` | **Required for production.** Flask session signing key. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `NOISEBRIDGE_WIKI_USER` | Wiki bot account for publishing (e.g. `YourBot@BotName`) |
| `NOISEBRIDGE_WIKI_PASSWORD` | Wiki bot password |
| `ANTHROPIC_API_KEY` | For AI summary generation (Claude Haiku). Optional — summary step is skipped if unset. |

### Optional `.env` variables

| Variable | Default | Description |
|---|---|---|
| `NB_RAW_DIR` | `/var/lib/nbmeetingnotes/raw` | Where raw `.txt` and processed `.wiki` files are stored |
| `NB_DB_PATH` | `/var/lib/nbmeetingnotes/provenance.db` | SQLite provenance database path |
| `NB_PROCESSOR_SCRIPT` | `pipeline/nb_processor.py` | Path to the transformation adapter |
| `NB_WIKI_API_URL` | `https://www.noisebridge.net/api.php` | MediaWiki API endpoint |
| `NB_WIKI_PAGE_URL` | `https://www.noisebridge.net/wiki` | Wiki base URL for links |
| `NB_WIKI_EU_URL` | `https://www.noisebridge.eu/wiki` | Mirror wiki URL |
| `NB_WIKI_PREVIEW_PREFIX` | `User:Bot/Meeting_Notes_` | Page prefix for draft previews |
| `NB_WIKI_TEMPLATE_TITLE` | `Meeting Notes Template` | Wiki page to monitor for template changes |
| `NB_USER_AGENT` | `NBArchive/1.0` | HTTP User-Agent for outbound requests. Add a contact URL: `NBArchive/1.0 (https://your-site)` |

---

## Running locally

```bash
source venv/bin/activate
python web/app.py          # Flask dev server → http://127.0.0.1:8237
```

Login uses your wiki username and password.

---

## Web UI workflow

The archive index lists every meeting. Opening one shows the raw capture, the
latest processed output, and — for logged-in users — a toolbar that changes
with the meeting's state. Nothing runs automatically from the UI; you drive
each meeting through the steps below.

**Always available:**

- **✏ Edit Raw** — edit the raw notes (versioned; every edit and restore is preserved)
- **📋 History** — browse every pipeline pass for this meeting
- **Pass N** — switch between passes
- **↗ Open in Tab** — open the live wiki page

**Before processing** (no output exists yet):

- **🔍 Check Wiki** — see below
- **▶ Run Pipeline** — transform the current raw capture into a new pass

**After processing** (output exists):

- **↻ Re-run Pipeline** — reprocess the current raw capture as a new pass
  (always from the raw source, never a previous output)
- **✨ Generate Summary** / **↻ Regenerate Summary** — generate an AI summary
  you review and edit; applying it creates a new pass
- **🔍 Check Wiki** — remains available until the meeting is marked published
- **📋 Publish Preview** — publish a draft to the preview page
  (`User:Bot/Meeting_Notes_…`); becomes **✓ Preview ↗** once done
- **🌐 Publish Meeting Notes** — publish to the official
  `Meeting_Notes_YYYY_MM_DD` page; becomes **✓ Published** once done

### Check Wiki

**Check Wiki** asks the wiki whether `Meeting_Notes_YYYY_MM_DD` already exists,
and does two things:

1. **Detects pre-existing publications.** If the page is already on the wiki
   (published by hand, or before this tool existed), it records an import
   transformation in the archive, marks the meeting published, and locks it —
   so the tool never silently overwrites notes it didn't create.
2. **Tracks the template baseline.** It snapshots the current meeting-notes
   template revision. The first snapshot becomes the baseline; later revisions
   raise a warning banner until a logged-in user acknowledges them.

The button stays available for as long as the archive DB shows the page as
unpublished (`published_to` is null) — **including after the pipeline has run
and a preview exists.** Once the meeting is marked published — by a successful
publish, or by Check Wiki detecting an existing page — the button is replaced
by the **✓ Published** indicator.

### Publish lock

After a meeting is published, people often keep editing the wiki page directly.
When the archive sees the wiki page has been edited since publication, it
**locks** the meeting: the publish control becomes
**🔒 N wiki edits — Unlock to publish**, and re-publishing (which would
overwrite those edits) requires an explicit unlock first.

---

## CLI pipeline

```bash
# Fetch from pad and process (no AI summary)
python -m pipeline.run --date 2026_04_15 --no-summary

# Process a local file
python -m pipeline.run --date 2026_04_15 --input raw_notes.txt

# Dry run — process but write nothing
python -m pipeline.run --date 2026_04_15 --dry-run

# Archive raw pad content only, no processing
python -m pipeline.run --date 2026_04_15 --fetch-only

# Re-run from a previous transformation (re-processes original raw capture)
python -m pipeline.run --date 2026_04_15 --rerun <txn_id>
```

---

## Tests

```bash
pytest tests/ -v
```

73 tests, ~0.2 s. No network calls, no AI calls, no subprocess — pure
in-memory SQLite and the `passthrough_processor`. Includes architectural
tests that enforce the LLM/non-LLM module boundary. See `AGENTS.md` for
development standards.

---

## Deployment

The `deploy/` directory contains scripts and systemd unit files for a
Linux server running behind an Apache2 reverse proxy.

### First-time setup

**1. Configure the deploy target** — the only required value is the SSH
host. Copy the example config and set it:

```bash
cp deploy/deploy.env.example deploy/deploy.env
# edit deploy/deploy.env → set MEETINGNOTES_HOST=yourserver
```

`deploy/deploy.env` is gitignored. Everything else (remote dir, service
name, user, log/data dirs) defaults to this project's standard layout —
see the top of `deploy/deploy.sh` and override there if needed.

**2. Run first-time setup** (creates dirs, venv, and installs + enables
the systemd units):

```bash
./deploy/deploy.sh setup
```

**3. Copy your `.env` to the server and start the service:**

```bash
scp .env yourserver:/opt/nbmeetingnotes/.env
ssh yourserver sudo systemctl start meetingnotes
```

**4. Establish the template baseline** — visit any meeting in the web UI
and click **Check Wiki**. This fetches the current template version and
stores it as the baseline. Future changes to the template will trigger
a warning banner.

### Subsequent deploys

```bash
./deploy/deploy.sh          # rsync code + pip install + sync units + restart
./deploy/deploy.sh restart  # restart service only
```

### Logs

```bash
ssh yourserver journalctl -u meetingnotes -f
ssh yourserver tail -f /var/log/nbarchive/error.log
```

### Scheduled pad fetch

Meetings are ingested automatically by a **weekly systemd timer**, not by the
web UI:

- `meetingnotes-fetch.timer` fires **Wednesday 11:00 UTC** (≈ 4am PDT), the
  morning after the Tuesday meeting.
- `meetingnotes-fetch.service` runs
  `pipeline.run --date $(date -d yesterday +%Y_%m_%d) --fetch-only`, archiving
  whatever is currently on the pad (`NB_PAD_URL`) under **yesterday's** date
  (i.e. the Tuesday meeting date).

The pad is a single live document that gets overwritten for each meeting, so
the fetch only captures the correct notes if it runs **before** the pad is
reused for the next meeting.

```bash
ssh yourserver systemctl list-timers meetingnotes-fetch.timer
ssh yourserver systemctl status meetingnotes-fetch.service
ssh yourserver journalctl -u meetingnotes-fetch.service --since '-8 days'
```

**Retry on transient failure.** The fetch is a `oneshot`, which systemd never
auto-restarts, so the retry lives inside `ExecStart`: it reattempts up to **5
times, 5 minutes apart** before giving up (`TimeoutStartSec=1800` leaves room
for the sleeps). `--fetch-only` is idempotent per date — a re-run on an
already-captured date is a harmless no-op — so retries never duplicate a
capture. This exists because a dropped fetch *silently* loses that week's
notes once the pad is reused.

> **Residual limitation.** If the pad or wiki is unreachable for the entire
> ~20-minute retry window, the meeting is still missed and the service ends in
> `failed` state (`Persistent=true` only reruns a *missed* timer — host off —
> not a *failed* one). Watch for a `failed` state and recover manually (below).
> This is what happened to the 2026-10-20 meeting before the retry was added:
> the Oct 21 fetch failed outright and the meeting was absent until re-fetched.

### Recovering a missing meeting

If a meeting is missing from the archive (failed fetch, or the timer didn't
run), re-fetch it manually **while the pad still holds those notes** — once the
pad is overwritten for the next meeting, the raw content is gone:

```bash
ssh yourserver
cd /opt/nbmeetingnotes
# load the service env the same way systemd does
export $(grep -E '^[A-Z_]+=' .env | sed 's/#.*//' | xargs -d '\n')
venv/bin/python3 -m pipeline.run --date YYYY_MM_DD --fetch-only
```

Use the **meeting's** date for `--date` (the pad content, not today). The
pipeline CLI logs only at `INFO` and logging is unconfigured, so a successful
`--fetch-only` prints nothing and exits 0 — verify by checking the archive
index or the capture row:

```bash
venv/bin/python3 -c "import db; db.init(); \
  print([(r['meeting_date'], r['id']) for r in db.conn().execute( \
  'SELECT meeting_date,id FROM raw_captures ORDER BY meeting_date DESC LIMIT 5')])"
```

> **Note on data dir:** the live deployment stores raw `.txt`/`.wiki` files and
> `provenance.db` in `/var/lib/nbmeetingnotes` (the `config.py` defaults). An
> `NB_RAW_DIR` pointing elsewhere in `.env` is not honored by the running app —
> confirm the real path via `config.RAW_DIR` before poking at files on disk.

---

## Contributing

The codebase enforces a hard boundary between AI and non-AI code:
**only `org_pipeline/ai.py` imports `anthropic`** — verified by
an architectural test in `tests/test_module_separation.py`.

Adding a new text transform means adding a function to `transforms.py`,
calling it in `process()` in `org_pipeline/process.py` via
`_record()`, and writing a test in `tests/test_transforms.py`. No other
files need to change.

See `AGENTS.md` for coding standards, the processor protocol, and the
observability requirements for new pipeline steps.
