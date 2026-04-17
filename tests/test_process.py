"""Tests for pipeline/process.py — no subprocess, no AI, no real DB."""
import tempfile
from pathlib import Path

from pipeline.adapter import TransformationResult, passthrough_processor, run


def _write_tmp(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    f.write(content)
    f.close()
    return Path(f.name)


def test_injectable_processor_is_called():
    called_with = {}

    def mock_processor(raw_path, date_str, flags):
        called_with['raw_path'] = raw_path
        called_with['date_str'] = date_str
        called_with['flags'] = flags
        return TransformationResult(content='mocked output', flags=flags)

    raw = _write_tmp('raw notes')
    result = run(raw, '2026_01_06', {'generate_ai_summary': False},
                 processor=mock_processor)

    assert result.content == 'mocked output'
    assert called_with['date_str'] == '2026_01_06'
    assert called_with['flags'] == {'generate_ai_summary': False}


def test_passthrough_processor_returns_raw_content():
    raw = _write_tmp('== Agenda ==\nItem 1\n')
    result = passthrough_processor(raw, '2026_01_06', {})
    assert result.content == '== Agenda ==\nItem 1\n'
    assert result.model_name is None
    assert result.token_usage is None
