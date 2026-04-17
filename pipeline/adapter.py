"""
pipeline/adapter.py — pluggable transformation adapter.

The default processor invokes NB_PROCESSOR_SCRIPT as a subprocess.
The script must:
  - Accept:  --date YYYY_MM_DD  --input <path-to-raw-file>
  - Write processed wiki text to stdout
  - Optionally emit a single JSON line to stderr prefixed "METRICS: "
    e.g.  METRICS: {"artifact_lines_removed": 42, "sections_found": 8,
                    "model_name": "claude-haiku-4-5", "token_usage": {...}}

Pass a custom `processor` callable to `run()` to bypass the subprocess —
useful for tests, local integrations, or alternative implementations.

    Processor = Callable[[Path, str, dict], TransformationResult]
"""
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import config


@dataclass
class TransformationResult:
    content: str
    model_name: Optional[str] = None
    flags: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    token_usage: Optional[dict] = None
    artifact_lines_removed: Optional[int] = None
    sections_found: Optional[int] = None
    processor_version: Optional[str] = None  # git hash of the processor script's repo
    trace: Optional[list] = None             # step-report dicts; see pipeline/trace.py
    generated_summary: Optional[str] = None  # AI summary text, pending user review


# Type alias — a processor is any callable with this signature
Processor = Callable[[Path, str, dict], TransformationResult]


def passthrough_processor(raw_path: Path, date_str: str, flags: dict) -> TransformationResult:
    """Return raw content unchanged. Useful for testing provenance plumbing."""
    return TransformationResult(content=raw_path.read_text('utf-8'), flags=flags)


def subprocess_processor(raw_path: Path, date_str: str, flags: dict) -> TransformationResult:
    """Invoke NB_PROCESSOR_SCRIPT as a subprocess and parse its output."""
    script = config.PROCESSOR_SCRIPT
    cmd = [sys.executable, str(script), '--date', date_str, '--input', str(raw_path)]

    if flags.get('generate_ai_summary') is False:
        cmd.append('--no-summary')
    if flags.get('summary_only'):
        cmd.append('--summary-only')

    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    duration = time.monotonic() - t0

    if result.returncode != 0:
        raise RuntimeError(
            f"Processor exited {result.returncode}:\n{result.stderr[-2000:]}"
        )

    metrics = {}
    trace = None
    for line in result.stderr.splitlines():
        if line.startswith('METRICS:'):
            try:
                metrics = json.loads(line[len('METRICS:'):].strip())
            except json.JSONDecodeError:
                pass
        elif line.startswith('TRACE:'):
            try:
                trace = json.loads(line[len('TRACE:'):].strip())
            except json.JSONDecodeError:
                pass

    return TransformationResult(
        content=result.stdout,
        model_name=metrics.get('model_name'),
        flags=flags,
        duration_seconds=duration,
        token_usage=metrics.get('token_usage'),
        artifact_lines_removed=metrics.get('artifact_lines_removed'),
        sections_found=metrics.get('sections_found'),
        processor_version=metrics.get('processor_version'),
        trace=trace,
        generated_summary=metrics.get('generated_summary'),
    )


def _default_processor() -> Processor:
    """Pick the right processor based on config."""
    return subprocess_processor if Path(config.PROCESSOR_SCRIPT).exists() \
        else passthrough_processor


def run(raw_path: Path, date_str: str, flags: dict,
        processor: Optional[Processor] = None) -> TransformationResult:
    """
    Run the processor and return a TransformationResult.

    processor — any callable matching Processor = (Path, str, dict) -> TransformationResult.
                Defaults to subprocess_processor when NB_PROCESSOR_SCRIPT exists,
                otherwise passthrough_processor.
    """
    if processor is None:
        processor = _default_processor()
    return processor(raw_path, date_str, flags)
