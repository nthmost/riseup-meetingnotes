"""
pipeline/trace.py — step-level trace for pipeline runs.

Each pipeline step records a step-report dict:
    {name: str, lines_in: int, lines_out: int, note: str}

format_trace_markdown() renders a list of these into a human-readable
.md report saved alongside the .wiki output for every non-dry-run pass.
"""


def format_trace_markdown(
    steps: list[dict],
    meeting_date: str,
    txn_id: int,
    pipeline_version: str,
    duration_seconds: float,
) -> str:
    """Render pipeline step reports as a Markdown document."""
    out = [
        f'# Pipeline Trace — {meeting_date}',
        '',
        f'**Transaction:** {txn_id}  ',
        f'**Pipeline version:** {pipeline_version}  ',
        f'**Duration:** {duration_seconds:.1f}s',
        '',
        '## Steps',
        '',
        '| Step | Lines in | Lines out | Δ | Note |',
        '|------|----------|-----------|---|------|',
    ]
    for s in steps:
        delta = s['lines_out'] - s['lines_in']
        delta_str = f'+{delta}' if delta > 0 else str(delta)
        out.append(
            f"| `{s['name']}` | {s['lines_in']} | {s['lines_out']}"
            f" | {delta_str} | {s.get('note', '')} |"
        )
    if not steps:
        out.append('| *(no steps recorded)* | — | — | — | — |')
    return '\n'.join(out) + '\n'
