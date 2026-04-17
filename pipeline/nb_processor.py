#!/usr/bin/env python3
"""
pipeline/nb_processor.py — adapter between nbmeetingnotes and the pipeline modules.

Accepts --date / --input, writes processed wiki text to stdout, emits
METRICS: and TRACE: JSON lines to stderr so the pipeline runner can record
provenance and step-level observability.

Set NB_PIPELINE_DIR to override where process.py / ai.py are found.
Default: org_pipeline/ in this repo (sibling of pipeline/).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── locate org_pipeline ────────────────────────────────────────────────

def _find_pipeline_dir() -> Path:
    env = os.environ.get('NB_PIPELINE_DIR')
    if env:
        return Path(env)
    candidates = [
        Path(__file__).parent.parent / 'org_pipeline',  # same repo (default)
        Path('/opt/nbmeetingnotes/org_pipeline'),        # production deploy path
    ]
    for c in candidates:
        if (c / 'process.py').exists():
            return c
    raise FileNotFoundError(
        "Cannot find org_pipeline/process.py. "
        "Set NB_PIPELINE_DIR env var to its directory."
    )


pipeline_dir = _find_pipeline_dir()
sys.path.insert(0, str(pipeline_dir))


def _get_processor_git(directory: Path) -> str:
    """Return the short git hash of the repo containing the processor script."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=directory,
        )
        return result.stdout.strip() if result.returncode == 0 else 'unknown'
    except Exception:
        return 'unknown'


try:
    import process as pipeline_process
    import ai as pipeline_ai
except ImportError as e:
    print(f"ERROR: Could not import pipeline modules from {pipeline_dir}: {e}",
          file=sys.stderr)
    sys.exit(1)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Meeting notes processor adapter'
    )
    parser.add_argument('--date', required=True, help='YYYY_MM_DD')
    parser.add_argument('--input', '-i', required=True, help='Path to raw .txt file')
    parser.add_argument('--no-summary', action='store_true',
                        help='Skip AI summary generation')
    parser.add_argument('--summary-only', action='store_true',
                        help='Generate AI summary for already-processed content; skip all other transforms')
    args = parser.parse_args()

    raw = Path(args.input).read_text('utf-8')

    if args.summary_only:
        # Input is already-processed wiki text — just generate the summary
        t0 = time.monotonic()
        summary, ai_metrics = pipeline_ai.generate_summary(raw)
        duration = time.monotonic() - t0
        sys.stdout.write(raw)  # content unchanged
        metrics = {
            'model_name': (ai_metrics or {}).get('model_name'),
            'token_usage': (ai_metrics or {}).get('token_usage'),
            'generated_summary': summary,
            'processor_version': _get_processor_git(pipeline_dir),
        }
        print(f'METRICS: {json.dumps(metrics)}', file=sys.stderr)
        return

    lines_before = raw.count('\n')

    t0 = time.monotonic()
    result, result_metrics = pipeline_process.process(
        raw,
        date_str=args.date,
        generate_ai_summary=not args.no_summary,
    )
    duration = time.monotonic() - t0

    lines_after = result.count('\n')
    artifact_lines_removed = max(0, lines_before - lines_after)
    sections_found = sum(
        1 for line in result.splitlines()
        if line.startswith('== ') and line.rstrip().endswith(' ==')
    )

    sys.stdout.write(result)

    metrics = {
        'model_name': result_metrics.get('model_name'),
        'artifact_lines_removed': artifact_lines_removed,
        'sections_found': sections_found,
        'token_usage': result_metrics.get('token_usage'),
        'processor_version': _get_processor_git(pipeline_dir),
        'generated_summary': result_metrics.get('generated_summary'),
    }
    print(f'METRICS: {json.dumps(metrics)}', file=sys.stderr)
    print(f'TRACE: {json.dumps(result_metrics.get("steps", []))}', file=sys.stderr)


if __name__ == '__main__':
    main()
