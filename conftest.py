"""
pytest configuration: add project root and org_pipeline/ to sys.path
so test modules can import without manual path manipulation.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'org_pipeline'))
