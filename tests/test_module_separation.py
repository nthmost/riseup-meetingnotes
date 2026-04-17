"""
Architectural test: enforce the LLM / non-LLM module boundary.

Rule: `anthropic` may only be imported by noisebridge_pipeline/ai.py.
Any other file importing it directly is a violation of the separation.

This test catches accidental boundary violations at CI time so they
don't silently creep back in.
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ALLOWED_TO_IMPORT_ANTHROPIC = {'noisebridge_pipeline/ai.py'}


def _imports_anthropic(path: Path) -> bool:
    """Return True if the file contains a direct `import anthropic` or `from anthropic`."""
    try:
        tree = ast.parse(path.read_text('utf-8'))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == 'anthropic' or alias.name.startswith('anthropic.')
                   for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            if node.module and (node.module == 'anthropic'
                                or node.module.startswith('anthropic.')):
                return True
    return False


def test_only_ai_module_imports_anthropic():
    """No Python file outside noisebridge_pipeline/ai.py may import anthropic."""
    violations = []
    for py_file in REPO_ROOT.rglob('*.py'):
        # Skip venv and __pycache__
        parts = py_file.relative_to(REPO_ROOT).parts
        if any(p in ('venv', '.venv', '__pycache__') for p in parts):
            continue
        rel = str(py_file.relative_to(REPO_ROOT))
        if rel in ALLOWED_TO_IMPORT_ANTHROPIC:
            continue
        if _imports_anthropic(py_file):
            violations.append(rel)

    assert violations == [], (
        f"The following files import 'anthropic' but are not in the allowed set "
        f"({ALLOWED_TO_IMPORT_ANTHROPIC}):\n"
        + '\n'.join(f'  {v}' for v in violations)
        + "\n\nAll AI/LLM code must live in noisebridge_pipeline/ai.py."
    )


def test_transforms_has_no_network_imports():
    """transforms.py must remain pure — no network I/O, no AI."""
    transforms = REPO_ROOT / 'noisebridge_pipeline' / 'transforms.py'
    tree = ast.parse(transforms.read_text('utf-8'))
    forbidden = {'urllib', 'requests', 'httpx', 'anthropic', 'openai', 'aiohttp'}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top in forbidden:
                    found.append(alias.name)
        if isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split('.')[0]
                if top in forbidden:
                    found.append(node.module)
    assert found == [], (
        f"transforms.py must not import network or AI libraries, found: {found}"
    )
