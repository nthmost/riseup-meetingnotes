# AGENTS.md — nbarchive development standards

## Imports

All imports go at the top of the module. No in-function imports.

```python
# wrong
def publish(...):
    from wiki import WikiAPI   # ← no
    ...

# right
from wiki import WikiAPI

def publish(...):
    ...
```

## Error handling

Exceptions should be specific, not general. Catch the narrowest exception type
that makes sense; never use a bare `except:` or `except Exception:` to silence
unknown failures.

```python
# wrong
try:
    result = fetch_url(url)
except Exception:
    return None

# right
try:
    result = fetch_url(url)
except urllib.error.HTTPError as e:
    log.warning('fetch failed: HTTP %s', e.code)
    return None
except urllib.error.URLError as e:
    log.warning('fetch failed: %s', e.reason)
    return None
```

Avoid large try-except blocks that wrap multiple unrelated operations. Each
block should protect exactly one operation. If you find yourself writing a
multi-step try block, that is a signal to split into smaller functions.

## Separation of concerns / testability

Functions should do one thing. The test for this: can you test it in isolation
with real inputs, a real (in-memory) DB, and no mocking framework?

- Pure transforms (text in → text out) live in `noisebridge_pipeline/transforms.py`.
  They import only `re`, `sys`, and `datetime`. Tests run in milliseconds.
- DB access lives in `db.py`. Use `db.override_db(':memory:')` in tests.
- Network I/O is isolated in `pipeline/fetch.py` and `wiki.py`.
- Orchestration (`pipeline/run.py`) calls the above; each step is its own
  named function (`resolve_input`, `start_transformation`, `save_output`,
  `record_provenance`).

Injectable dependencies are preferred over global state. If a function always
hits the network, accept an optional callable that callers (and tests) can
swap out — see `process.run(processor=...)` and `run_pipeline(processor=...)`.

## Pipeline observability

Every pipeline run must produce a step-level trace so that the effect of each
transform is visible without re-running anything or reading code.

**Step report format** — a plain dict emitted by each named step in `process()`:

```python
{'name': 'strip_artifacts', 'lines_in': 350, 'lines_out': 312, 'note': 'date=2026_04_08'}
```

`note` adds context the line delta does not already show (e.g. model name for AI
steps, attribution count for formatting steps). It is optional; leave it empty
when the delta is self-explanatory.

**How it flows**:

1. `process_meeting_notes.py::process()` collects step dicts in `metrics['steps']`.
2. `pipeline/nb_processor.py` emits `TRACE: <json>` to stderr (one line, same
   protocol as `METRICS:`).
3. `pipeline/process.py::subprocess_processor()` parses it into
   `TransformationResult.trace`.
4. `pipeline/run.py::save_output()` writes `Meeting_Notes_YYYY_MM_DD_passN_trace.md`
   alongside every non-dry-run `.wiki` output.
5. `pipeline/trace.py::format_trace_markdown()` owns the Markdown rendering.

**Standard for new steps**: every named transform in `process()` must call
`_record(name, before, after, note=...)`. The `before`/`after` strings are the
full text before and after the step; `_record` computes the line delta. New
transform functions do not need to change their signatures — all instrumentation
happens in `process()`.

If a step is conditional (network call, optional flag), it must still appear in
the trace: skipped steps record `lines_in == lines_out` and an explanatory note.

## Explainable processes first

Prefer deterministic, auditable steps over probabilistic ones. When processing
meeting notes:

1. **Regex / rule-based transforms** run first (`strip_artifacts`,
   `fix_ordered_lists`, `format_speaker_attributions`, etc.).
2. **LLM summarisation** runs last and only touches the Meeting Summary
   section — it never rewrites content the notetakers wrote.

The same principle applies generally: if a regex or lookup table can solve the
problem, use it. Reach for an LLM only when the task is genuinely
language-level (summarisation, classification) and the output is clearly
scoped and reviewable.

Reversibility matters. Every pipeline pass is recorded with its input SHA,
output path, model used, and token counts. A bad AI output can be audited,
rated, and replaced with a fresh pass.

## Testing

```
pytest tests/          # all tests, ~0.1 s
```

Tests use:
- `db.override_db(':memory:')` for DB isolation — no temp files, no env vars
- `pipeline.process.passthrough_processor` for pipeline runs — no subprocess,
  no AI call
- Real files via pytest's `tmp_path` fixture

No `unittest.mock`, no `MagicMock`, no `@patch`. If a function can only be
tested with mocks, that is a signal to refactor it.
