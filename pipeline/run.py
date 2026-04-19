"""
pipeline/run.py — orchestrates fetch → process → (publish) and records full provenance.

Public API:
    run_pipeline(...)        — full pass, returns transformation id
    fetch_only(...)          — archive raw pad content only
    resolve_input(...)       — figure out what to process
    start_transformation(...)— record pipeline start in DB, return txn_id
    save_output(...)         — write output file, update DB
    record_provenance(...)   — record model/version/wiki-source in DB

Usage (CLI):
    python -m pipeline.run --date 2026_03_18
    python -m pipeline.run --date 2026_03_18 --input /path/to/raw.txt
    python -m pipeline.run --date 2026_03_18 --rerun <parent_txn_id>
    python -m pipeline.run --date 2026_03_18 --no-summary --dry-run
"""
import argparse
import hashlib
import logging
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
import db
from pipeline import fetch, adapter
from pipeline.publish import publish as _publish
from pipeline.trace import format_trace_markdown

log = logging.getLogger(__name__)


def _git_version() -> str:
    try:
        r = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
        )
        return r.stdout.strip() if r.returncode == 0 else 'unknown'
    except Exception:
        return 'unknown'


# ── Input resolution ───────────────────────────────────────────────────────────

@dataclass
class PipelineInput:
    """Everything needed to run one pipeline pass."""
    capture_id: int
    raw_path: Path
    content: str
    flags: dict
    source_wiki_page: str | None = None
    source_wiki_revid: int | None = None
    _tmp_path: Path | None = field(default=None, repr=False)  # cleaned up by run_pipeline


def _resolve_from_parent(parent_txn_id: int, flags: dict) -> PipelineInput:
    """Re-run: prefer current wiki revision as input, fall back to parent's output file."""
    parent = db.get_transformation(parent_txn_id)
    if not parent:
        raise RuntimeError(f"Parent transformation {parent_txn_id} not found")
    capture_id = parent['raw_capture_id']
    flags = {**flags, 'generate_ai_summary': False}

    published_page = db.get_published_page_for_capture(capture_id)
    if published_page:
        wiki_content, revid = fetch.fetch_wiki_page(published_page)
        if wiki_content is not None:
            with tempfile.NamedTemporaryFile(
                suffix='.wiki', mode='w', encoding='utf-8', delete=False
            ) as f:
                f.write(wiki_content)
                tmp_path = Path(f.name)
            log.info(f"[run] using wiki revid={revid} ({len(wiki_content)} bytes)")
            return PipelineInput(
                capture_id=capture_id,
                raw_path=tmp_path,
                content=wiki_content,
                flags=flags,
                source_wiki_page=published_page,
                source_wiki_revid=revid,
                _tmp_path=tmp_path,
            )
        log.info(f"[run] wiki page {published_page} not found, falling back to parent output")

    if not parent['output_path'] or not Path(parent['output_path']).exists():
        raise RuntimeError("No wiki content and no parent output file available")
    raw_path = Path(parent['output_path'])
    return PipelineInput(
        capture_id=capture_id,
        raw_path=raw_path,
        content=raw_path.read_text('utf-8'),
        flags=flags,
    )


def _resolve_from_file(meeting_date: str, raw_path: Path, flags: dict) -> PipelineInput:
    """Use a local file as input; archive it if this date hasn't been captured yet."""
    content = raw_path.read_text('utf-8')
    capture = db.get_capture_by_date(meeting_date)
    if capture is None:
        _, capture_id = fetch.archive_raw(meeting_date, content, str(raw_path))
    else:
        capture_id = capture['id']
    return PipelineInput(capture_id=capture_id, raw_path=raw_path, content=content, flags=flags)


def _resolve_from_pad(meeting_date: str, flags: dict) -> PipelineInput:
    """Fetch from the Riseup Pad (or load from a previous capture if available)."""
    capture = db.get_capture_by_date(meeting_date)
    if capture is not None:
        raw_path = Path(capture['file_path'])
        return PipelineInput(
            capture_id=capture['id'],
            raw_path=raw_path,
            content=raw_path.read_text('utf-8'),
            flags=flags,
        )
    content, capture_id = fetch.fetch_and_archive(meeting_date)
    raw_path = config.RAW_DIR / f'raw_{meeting_date}.txt'
    return PipelineInput(capture_id=capture_id, raw_path=raw_path, content=content, flags=flags)


def resolve_input(
    meeting_date: str,
    raw_path: Path | None,
    parent_txn_id: int | None,
    flags: dict,
) -> PipelineInput:
    """Return a PipelineInput for this run, archiving raw content in the DB if needed."""
    if parent_txn_id is not None:
        return _resolve_from_parent(parent_txn_id, flags)
    if raw_path is not None:
        return _resolve_from_file(meeting_date, raw_path, flags)
    return _resolve_from_pad(meeting_date, flags)


# ── Pipeline steps ─────────────────────────────────────────────────────────────

def start_transformation(inp: PipelineInput, parent_txn_id: int | None) -> int:
    """Insert a transformation row and return its id."""
    input_sha256 = hashlib.sha256(inp.content.encode('utf-8')).hexdigest()
    txn_id = db.insert_transformation(
        raw_capture_id=inp.capture_id,
        parent_id=parent_txn_id,
        run_at=datetime.now(timezone.utc).isoformat(),
        pipeline_version=_git_version(),
        pipeline_script=str(config.PROCESSOR_SCRIPT.name),
        model_name=None,
        flags=inp.flags,
        input_sha256=input_sha256,
    )
    log.info(f"[run] transformation id={txn_id}  capture={inp.capture_id}  parent={parent_txn_id}")
    return txn_id


def save_output(
    txn_id: int,
    meeting_date: str,
    result: adapter.TransformationResult,
    dry_run: bool = False,
    out_dir: Path | None = None,
) -> Path | None:
    """Write the processed output to disk and update the DB. Returns the output path."""
    if dry_run:
        out_path = out_sha = None
    else:
        out_dir = out_dir or config.RAW_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'Meeting_Notes_{meeting_date}_pass{txn_id}.wiki'
        out_path.write_text(result.content, encoding='utf-8')
        out_sha = hashlib.sha256(result.content.encode('utf-8')).hexdigest()

        if result.trace:
            trace_path = out_dir / f'Meeting_Notes_{meeting_date}_pass{txn_id}_trace.md'
            trace_md = format_trace_markdown(
                steps=result.trace,
                meeting_date=meeting_date,
                txn_id=txn_id,
                pipeline_version=_git_version(),
                duration_seconds=result.duration_seconds,
            )
            trace_path.write_text(trace_md, encoding='utf-8')
            log.info(f"[run] trace  → {trace_path}")

    if result.generated_summary:
        db.record_generated_summary(txn_id, result.generated_summary)
        log.info(f"[run] summary stored for review ({len(result.generated_summary)} chars)")

    db.update_transformation_output(
        txn_id=txn_id,
        output_path=out_path,
        output_sha256=out_sha,
        duration_seconds=result.duration_seconds,
        token_usage=result.token_usage,
        artifact_lines_removed=result.artifact_lines_removed,
        sections_found=result.sections_found,
    )
    log.info(f"[run] done  duration={result.duration_seconds:.1f}s  output={out_path}")
    return out_path


def record_provenance(
    txn_id: int,
    result: adapter.TransformationResult,
    source_wiki_page: str | None = None,
    source_wiki_revid: int | None = None,
) -> None:
    """Record model name, processor version, wiki source, and template version atomically."""
    with db.conn() as c:
        c.execute(
            'UPDATE transformations SET model_name=?, processor_version=? WHERE id=?',
            (result.model_name, result.processor_version, txn_id),
        )
        if source_wiki_page:
            c.execute(
                'UPDATE transformations SET source_wiki_page=?, source_wiki_revid=? WHERE id=?',
                (source_wiki_page, source_wiki_revid, txn_id),
            )
        row = c.execute(
            'SELECT revid FROM template_snapshots ORDER BY id DESC LIMIT 1'
        ).fetchone()
        if row:
            c.execute(
                'UPDATE transformations SET template_revid=? WHERE id=?',
                (row['revid'], txn_id),
            )


# ── Orchestrator ───────────────────────────────────────────────────────────────

def run_pipeline(
    meeting_date: str,
    raw_path: Path | None = None,
    parent_txn_id: int | None = None,
    flags: dict | None = None,
    publish: bool = False,
    dry_run: bool = False,
    processor: adapter.Processor | None = None,
    out_dir: Path | None = None,
) -> int:
    """
    Run one full pipeline pass. Returns the transformation id.

    processor — injectable callable for tests (default: subprocess or passthrough)
    out_dir   — where to write output files (default: config.RAW_DIR)
    """
    flags = flags or {'generate_ai_summary': True}
    inp = resolve_input(meeting_date, raw_path, parent_txn_id, flags)
    txn_id = start_transformation(inp, parent_txn_id)
    try:
        result = adapter.run(inp.raw_path, meeting_date, inp.flags, processor=processor)
    finally:
        if inp._tmp_path:
            inp._tmp_path.unlink(missing_ok=True)
    save_output(txn_id, meeting_date, result, dry_run, out_dir)
    record_provenance(txn_id, result, inp.source_wiki_page, inp.source_wiki_revid)
    if publish and not dry_run:
        _publish(txn_id, meeting_date, result.content)
    return txn_id


# ── Fetch-only (no processing) ─────────────────────────────────────────────────

def fetch_only(meeting_date: str) -> int:
    """
    Archive raw pad content without processing. Checks for a pre-existing wiki
    page and records an import transformation if found.
    Returns the capture id.
    """
    capture = db.get_capture_by_date(meeting_date)
    if capture:
        log.info(f"[fetch] already captured {meeting_date} (id={capture['id']})")
        capture_id = capture['id']
    else:
        content, capture_id = fetch.fetch_and_archive(meeting_date)
        log.info(f"[fetch] archived {meeting_date} ({len(content)} bytes, capture id={capture_id})")

    existing_txn = db.get_latest_transformation(capture_id)
    if existing_txn:
        log.info(f"[fetch] transformation already recorded for {meeting_date}, skipping wiki check")
        return capture_id

    page_title = f'Meeting_Notes_{meeting_date}'
    log.info(f"[fetch] checking wiki for existing {page_title} ...")
    wiki_content, revid = fetch.fetch_wiki_page(page_title)

    if wiki_content is not None:
        log.info(f"[fetch] found existing wiki page at revid={revid}, recording import")
        sha = hashlib.sha256(wiki_content.encode('utf-8')).hexdigest()
        txn_id = db.insert_transformation(
            raw_capture_id=capture_id,
            parent_id=None,
            run_at=datetime.now(timezone.utc).isoformat(),
            pipeline_version='external',
            pipeline_script='wiki-import',
            model_name=None,
            flags={'source': 'pre-existing-wiki-publication'},
            input_sha256=sha,
        )
        db.record_wiki_source(txn_id, page_title, revid)
        db.record_publish(txn_id, page_title, datetime.now(timezone.utc).isoformat())
        log.info(f"[fetch] import transformation id={txn_id}")
        # Auto-lock: this meeting's wiki page has existing content
        try:
            rev_count = fetch.fetch_wiki_revision_count(page_title)
            db.set_capture_locked(capture_id, locked=True, wiki_revisions=rev_count)
            log.info(f"[fetch] capture locked (wiki page has {rev_count} revisions)")
        except Exception as e:
            log.warning(f"[fetch] could not get revision count: {e}")
    else:
        log.info(f"[fetch] no existing wiki page found for {page_title}")

    # Check the meeting notes template version — alerts the UI if it has changed
    # since the pipeline's artifact-removal rules were last updated.
    try:
        tmpl_content, tmpl_revid = fetch.fetch_wiki_page(config.WIKI_TEMPLATE_TITLE)
        if tmpl_content is not None and tmpl_revid is not None:
            tmpl_sha = hashlib.sha256(tmpl_content.encode('utf-8')).hexdigest()
            is_new = db.record_template_snapshot(
                revid=tmpl_revid,
                sha256=tmpl_sha,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            if is_new:
                log.warning(f"[fetch] template version changed: revid={tmpl_revid}")
    except Exception as e:
        log.warning(f"[fetch] could not check template version: {e}")

    return capture_id


# ── Summary generation ─────────────────────────────────────────────────────────

def generate_summary_for_txn(txn_id: int) -> None:
    """
    Run AI summary generation on an existing transformation's processed output.
    Stores the result in generated_summary without creating a new transformation.
    """
    txn = db.get_transformation(txn_id)
    if not txn or not txn['output_path']:
        raise RuntimeError(f"Transformation {txn_id} has no output file")
    capture = db.get_capture_by_id(txn['raw_capture_id'])
    result = adapter.run(
        Path(txn['output_path']),
        capture['meeting_date'],
        flags={'generate_ai_summary': True, 'summary_only': True},
    )
    if result.generated_summary:
        db.record_generated_summary(txn_id, result.generated_summary)
        log.info(f"[run] summary generated and stored for txn={txn_id}")
    else:
        log.info(f"[run] summary generation produced no output for txn={txn_id}")


# ── Summary insertion ──────────────────────────────────────────────────────────

def _apply_summary(content: str, summary_text: str) -> str:
    """Insert summary_text into the == Meeting Summary == section."""
    lines = content.split('\n')
    header_idx = None
    content_end = len(lines)
    for i, line in enumerate(lines):
        if re.match(r'^==\s*Meeting Summary\s*==$', line.strip()):
            header_idx = i
        elif header_idx is not None and i > header_idx:
            if re.match(r'^=+\s*\w', line.strip()):
                content_end = i
                break
    if header_idx is None:
        return content
    new_lines = (
        lines[:header_idx + 1]
        + ['']
        + summary_text.strip().split('\n')
        + ['']
        + lines[content_end:]
    )
    return '\n'.join(new_lines)


def insert_summary_pass(
    parent_txn_id: int,
    summary_text: str,
    out_dir: Path | None = None,
) -> int:
    """
    Create a new transformation that inserts the (possibly edited) summary
    into the parent transformation's output. Returns the new txn_id.
    """
    parent = db.get_transformation(parent_txn_id)
    if not parent or not parent['output_path']:
        raise RuntimeError(f"Parent transformation {parent_txn_id} has no output")
    capture = db.get_capture_by_id(parent['raw_capture_id'])

    original = Path(parent['output_path']).read_text('utf-8')
    new_content = _apply_summary(original, summary_text)

    flags = {'generate_ai_summary': False, 'summary_source': 'user_reviewed'}
    inp = PipelineInput(
        capture_id=capture['id'],
        raw_path=Path(parent['output_path']),
        content=original,
        flags=flags,
    )
    txn_id = start_transformation(inp, parent_txn_id=parent_txn_id)
    result = adapter.TransformationResult(content=new_content, flags=flags)
    save_output(txn_id, capture['meeting_date'], result, out_dir=out_dir)
    return txn_id


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    db.init()
    parser = argparse.ArgumentParser(description='Run the meeting notes pipeline')
    parser.add_argument('--date', required=True, help='YYYY_MM_DD')
    parser.add_argument('--input', '-i', help='Use local raw file instead of fetching')
    parser.add_argument('--rerun', type=int, metavar='TXN_ID',
                        help='Re-run as a child of this transformation id')
    parser.add_argument('--no-summary', action='store_true',
                        help='Skip AI summary generation')
    parser.add_argument('--publish', action='store_true',
                        help='Publish to NB wiki after processing')
    parser.add_argument('--dry-run', action='store_true',
                        help='Process but do not write output or publish')
    parser.add_argument('--fetch-only', action='store_true',
                        help='Archive raw pad content only, skip processing')
    args = parser.parse_args()

    if args.fetch_only:
        capture_id = fetch_only(args.date)
        log.info(f"[fetch-only] capture id={capture_id}")
        sys.exit(0)

    flags = {'generate_ai_summary': not args.no_summary}
    txn_id = run_pipeline(
        meeting_date=args.date,
        raw_path=Path(args.input) if args.input else None,
        parent_txn_id=args.rerun,
        flags=flags,
        publish=args.publish,
        dry_run=args.dry_run,
    )
    log.info(f"[run] transformation id={txn_id}")
