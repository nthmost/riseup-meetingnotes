# Contributing

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
```

## Running tests

```bash
pytest tests/ -v
```

59 tests, ~0.4 s. No network, no AI, no subprocess. See `AGENTS.md` for the full development standards this project follows.

## Architecture

Read `AGENTS.md` before writing code. The key rules:

- **Only `org_pipeline/ai.py` imports `anthropic`** — enforced by `tests/test_module_separation.py`
- **Only `org_pipeline/transforms.py` does text transforms** — pure functions, no I/O
- **No in-function imports** — all imports at the top of the module
- **No mocks in tests** — use `db.override_db(':memory:')` and `passthrough_processor` instead

## Adapting for your organisation

The `org_pipeline/` directory contains all organisation-specific logic:

- `transforms.py` — artifact removal rules for your meeting notes template
- `process.py` — pipeline orchestration (add new transforms here via `_record()`)
- `ai.py` — AI summary prompt and membership context fetch

Everything else (`pipeline/`, `web/`, `db.py`, `wiki.py`) is generic infrastructure.

## Reporting security issues

See [SECURITY.md](SECURITY.md).

## Code of conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
