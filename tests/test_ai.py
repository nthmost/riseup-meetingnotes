"""
Tests for org_pipeline/ai.py.

No API key required — generate_summary returns (None, None) gracefully
when ANTHROPIC_API_KEY is not set.
"""
import os
import sys
from pathlib import Path

# Ensure org_pipeline is on the path (conftest does this globally,
# but be explicit for clarity)
sys.path.insert(0, str(Path(__file__).parent.parent / 'org_pipeline'))

from ai import generate_summary, fetch_membership_levels, _ANTHROPIC_AVAILABLE


def test_generate_summary_skips_without_api_key(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    summary, metrics = generate_summary("Some meeting content here.")
    assert summary is None
    assert metrics is None


def test_generate_summary_skips_when_anthropic_unavailable(monkeypatch):
    """If anthropic package is not importable, returns (None, None) gracefully."""
    import ai
    original = ai._ANTHROPIC_AVAILABLE
    ai._ANTHROPIC_AVAILABLE = False
    try:
        summary, metrics = generate_summary("content")
        assert summary is None
        assert metrics is None
    finally:
        ai._ANTHROPIC_AVAILABLE = original


def test_fetch_membership_levels_returns_fallback_on_network_error(monkeypatch):
    """fetch_membership_levels falls back to hardcoded string on any network failure."""
    import urllib.request

    def broken_urlopen(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(urllib.request, 'urlopen', broken_urlopen)
    result = fetch_membership_levels()
    assert isinstance(result, str)
    assert len(result) > 0  # fallback returns something regardless
    


def test_fetch_membership_levels_returns_string():
    """fetch_membership_levels always returns a non-empty string."""
    # May hit the network or fall back — either way must return a string
    result = fetch_membership_levels()
    assert isinstance(result, str)
    assert len(result) > 0
