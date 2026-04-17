"""
Tests for pipeline/run.py — real DB (in-memory), real files, real processor.
No mocking, no patching.
"""
from pathlib import Path
from datetime import datetime, timezone

import db
from pipeline import adapter
from pipeline.adapter import TransformationResult, passthrough_processor
from pipeline.run import (
    PipelineInput,
    _resolve_from_file,
    record_provenance,
    run_pipeline,
    save_output,
    start_transformation,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _insert_capture(tmp_path, meeting_date='2026_01_06', content='raw notes'):
    raw = tmp_path / f'raw_{meeting_date}.txt'
    raw.write_text(content)
    return db.insert_capture(
        meeting_date=meeting_date,
        captured_at=datetime.now(timezone.utc).isoformat(),
        source_url='https://example.com/pad',
        file_path=str(raw),
        sha256='abc123',
        size_bytes=len(content),
    ), raw, content


def _insert_txn(capture_id):
    return db.insert_transformation(
        raw_capture_id=capture_id,
        parent_id=None,
        run_at=datetime.now(timezone.utc).isoformat(),
        pipeline_version='test',
        pipeline_script='test_script',
        model_name=None,
        flags={},
        input_sha256='inputsha',
    )


# ── start_transformation ──────────────────────────────────────────────────────

def test_start_transformation_creates_row(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, raw, content = _insert_capture(tmp_path)
        inp = PipelineInput(
            capture_id=capture_id, raw_path=raw,
            content=content, flags={'generate_ai_summary': False},
        )
        txn_id = start_transformation(inp, parent_txn_id=None)
        txn = db.get_transformation(txn_id)
        assert txn['raw_capture_id'] == capture_id
        assert txn['parent_id'] is None
        assert txn['pipeline_script']  # non-empty; actual value is config-dependent


def test_start_transformation_records_input_hash(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, raw, content = _insert_capture(tmp_path)
        inp = PipelineInput(
            capture_id=capture_id, raw_path=raw,
            content=content, flags={},
        )
        txn_id = start_transformation(inp, parent_txn_id=None)
        txn = db.get_transformation(txn_id)
        import hashlib
        assert txn['input_sha256'] == hashlib.sha256(content.encode()).hexdigest()


# ── save_output ───────────────────────────────────────────────────────────────

def test_save_output_writes_file(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, _, _ = _insert_capture(tmp_path)
        txn_id = _insert_txn(capture_id)
        result = TransformationResult(content='processed output', flags={})
        out_path = save_output(txn_id, '2026_01_06', result, dry_run=False, out_dir=tmp_path)
        assert out_path is not None
        assert out_path.read_text() == 'processed output'


def test_save_output_updates_db(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, _, _ = _insert_capture(tmp_path)
        txn_id = _insert_txn(capture_id)
        result = TransformationResult(content='output', duration_seconds=1.5, flags={})
        out_path = save_output(txn_id, '2026_01_06', result, out_dir=tmp_path)
        txn = db.get_transformation(txn_id)
        assert txn['output_path'] == str(out_path)
        assert txn['duration_seconds'] == 1.5


def test_save_output_dry_run_writes_nothing(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, _, _ = _insert_capture(tmp_path)
        txn_id = _insert_txn(capture_id)
        result = TransformationResult(content='output', flags={})
        out_path = save_output(txn_id, '2026_01_06', result, dry_run=True, out_dir=tmp_path)
        assert out_path is None
        # Only the stub .txt file should exist, no .wiki output
        wiki_files = list(tmp_path.glob('*.wiki'))
        assert wiki_files == []


# ── record_provenance ─────────────────────────────────────────────────────────

def test_record_provenance_updates_model_and_version(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, _, _ = _insert_capture(tmp_path)
        txn_id = _insert_txn(capture_id)
        result = TransformationResult(
            content='x', model_name='claude-haiku', processor_version='abc123', flags={}
        )
        record_provenance(txn_id, result)
        txn = db.get_transformation(txn_id)
        assert txn['model_name'] == 'claude-haiku'
        assert txn['processor_version'] == 'abc123'


def test_record_provenance_no_wiki_source(tmp_path):
    with db.override_db(':memory:'):
        db.init()
        capture_id, _, _ = _insert_capture(tmp_path)
        txn_id = _insert_txn(capture_id)
        result = TransformationResult(content='x', flags={})
        record_provenance(txn_id, result)          # should not raise
        txn = db.get_transformation(txn_id)
        assert txn['source_wiki_page'] is None


# ── _resolve_from_file ────────────────────────────────────────────────────────

def test_resolve_from_file_uses_existing_capture(tmp_path):
    raw = tmp_path / 'raw.txt'
    raw.write_text('meeting notes')
    with db.override_db(':memory:'):
        db.init()
        capture_id = db.insert_capture(
            '2026_01_06', datetime.now(timezone.utc).isoformat(),
            'http://example.com', str(raw), 'sha', 12,
        )
        inp = _resolve_from_file('2026_01_06', raw, {'generate_ai_summary': False})
        assert inp.capture_id == capture_id
        assert inp.content == 'meeting notes'
        assert inp.flags == {'generate_ai_summary': False}


# ── run_pipeline (integration) ────────────────────────────────────────────────

def test_run_pipeline_end_to_end(tmp_path):
    raw = tmp_path / 'raw.txt'
    raw.write_text('== Agenda ==\nItem 1\n')
    with db.override_db(':memory:'):
        db.init()
        db.insert_capture(
            '2026_01_06', datetime.now(timezone.utc).isoformat(),
            'http://example.com', str(raw), 'sha', raw.stat().st_size,
        )
        txn_id = run_pipeline(
            meeting_date='2026_01_06',
            raw_path=raw,
            flags={'generate_ai_summary': False},
            processor=passthrough_processor,
            out_dir=tmp_path,
        )
        txn = db.get_transformation(txn_id)
        assert txn['output_path'] is not None
        assert Path(txn['output_path']).read_text() == '== Agenda ==\nItem 1\n'


def test_run_pipeline_dry_run(tmp_path):
    raw = tmp_path / 'raw.txt'
    raw.write_text('notes')
    with db.override_db(':memory:'):
        db.init()
        db.insert_capture(
            '2026_01_06', datetime.now(timezone.utc).isoformat(),
            'http://example.com', str(raw), 'sha', 5,
        )
        txn_id = run_pipeline(
            meeting_date='2026_01_06',
            raw_path=raw,
            flags={'generate_ai_summary': False},
            processor=passthrough_processor,
            dry_run=True,
            out_dir=tmp_path,
        )
        txn = db.get_transformation(txn_id)
        assert txn['output_path'] is None
        assert list(tmp_path.glob('*.wiki')) == []


def test_run_pipeline_rerun_uses_parent_output(tmp_path):
    raw = tmp_path / 'raw.txt'
    raw.write_text('original notes')
    parent_out = tmp_path / 'parent_output.wiki'
    parent_out.write_text('parent processed output')

    with db.override_db(':memory:'):
        db.init()
        capture_id = db.insert_capture(
            '2026_01_06', datetime.now(timezone.utc).isoformat(),
            'http://example.com', str(raw), 'sha', raw.stat().st_size,
        )
        parent_txn_id = db.insert_transformation(
            raw_capture_id=capture_id, parent_id=None,
            run_at=datetime.now(timezone.utc).isoformat(),
            pipeline_version='test', pipeline_script='test',
            model_name=None, flags={}, input_sha256='sha',
        )
        db.update_transformation_output(
            txn_id=parent_txn_id, output_path=parent_out,
            output_sha256='outsha', duration_seconds=0.1,
        )
        txn_id = run_pipeline(
            meeting_date='2026_01_06',
            parent_txn_id=parent_txn_id,
            flags={'generate_ai_summary': True},  # should be forced False for reruns
            processor=passthrough_processor,
            out_dir=tmp_path,
        )
        txn = db.get_transformation(txn_id)
        # Content comes from parent output, not the raw file
        assert Path(txn['output_path']).read_text() == 'parent processed output'
        # generate_ai_summary must be False for reruns (wiki content already has summary)
        import json
        assert json.loads(txn['flags'])['generate_ai_summary'] is False
