# nbarchive

Meeting notes pipeline and archive for [Noisebridge](https://www.noisebridge.net).

Fetches raw notes from the Riseup Pad after each Tuesday meeting, cleans them
with deterministic text transforms, generates an AI summary, and publishes to
the Noisebridge wiki. Every pass is recorded with full provenance so bad outputs
can be audited, rated, and re-run.

Live at **https://nbarchive.nthmost.net**

---

## What it does

```
Riseup Pad
    │
    ▼ fetch
raw .txt (archived, immutable)
    │
    ▼ strip_artifacts       — remove template boilerplate lines
    ▼ fix_meeting_number    — resolve ordinal from previous wiki page
    ▼ fix_ordered_lists     — 1. 2. 3. → MediaWiki # lists
    ▼ fix_discussion_blocks — pull content out of {{DiscussionItem}} templates
    ▼ format_task_board     — task bullets → wikitable
    ▼ format_attributions   — "Name: text" → '''Name:''' text
    ▼ ensure_bullets        — bullet-ify Introductions / Short announcements
    ▼ add_footer            — {{meetings2026}} banner + [[Category:Meeting Notes]]
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

**Re-run behaviour**: re-runs chain from the current wiki revision (not the
original raw pad), so human edits made directly on the wiki are preserved and
built upon rather than overwritten.

---

## Architecture

### Layers

```
noisebridge_pipeline/
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

**`raw_captures`** — one row per meeting date. Points to the immutable raw
`.txt` file on disk. Never overwritten; a refresh creates a backup first.

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

# Re-run from a previous transformation (chains from wiki revision)
python -m pipeline.run --date 2026_04_15 --rerun <txn_id>
```

---

## Tests

```bash
pytest tests/ -v
```

59 tests, ~0.4 s. No network calls, no AI calls, no subprocess — pure
in-memory SQLite and the `passthrough_processor`. Includes architectural
tests that enforce the LLM/non-LLM module boundary. See `AGENTS.md` for
development standards.

---

## Deployment

The `deploy/` directory contains scripts and systemd unit files for a
Linux server running behind an Apache2 reverse proxy.

### First-time setup

**1. Configure the deploy script** — set these environment variables before
running `deploy.sh`, or edit the defaults at the top of the script:

```bash
export NBARCHIVE_HOST=yourserver        # SSH hostname or IP
export NBARCHIVE_REMOTE_DIR=/opt/nbmeetingnotes
export NBARCHIVE_SERVICE=nbmeetingnotes
```

Also update `User=` in `deploy/nbmeetingnotes.service` to match the
server user that should run the process.

**2. Run first-time setup** (creates dirs, venv, systemd units):

```bash
./deploy/deploy.sh setup
```

**3. Copy your `.env` to the server:**

```bash
scp .env yourserver:/opt/nbmeetingnotes/.env
```

**4. Start the service:**

```bash
ssh yourserver sudo systemctl start nbmeetingnotes
```

**5. Establish the template baseline** — visit any meeting in the web UI
and click **Check Wiki**. This fetches the current template version and
stores it as the baseline. Future changes to the template will trigger
a warning banner.

### Subsequent deploys

```bash
./deploy/deploy.sh          # rsync code + pip install + restart service
./deploy/deploy.sh restart  # restart service only
```

### Logs

```bash
ssh yourserver journalctl -u nbmeetingnotes -f
ssh yourserver tail -f /var/log/nbmeetingnotes/error.log
```

---

## Contributing

The codebase enforces a hard boundary between AI and non-AI code:
**only `noisebridge_pipeline/ai.py` imports `anthropic`** — verified by
an architectural test in `tests/test_module_separation.py`.

Adding a new text transform means adding a function to `transforms.py`,
calling it in `process()` in `noisebridge_pipeline/process.py` via
`_record()`, and writing a test in `tests/test_transforms.py`. No other
files need to change.

See `AGENTS.md` for coding standards, the processor protocol, and the
observability requirements for new pipeline steps.
