"""Shared test plumbing.

Tests must never read a hotel's own `config/hotel.yaml` / `config/agent.yaml`
- those are the hotel's, and an edit there must never turn `make test` red.
`isolated_settings` points `AGENT_CONFIG_DIR` (checked first by
`core.config.config_path`) at a temp copy of the shipped `.example.yaml`
files instead, so every test is hermetic regardless of what `make setup` has
done in this working copy.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Factory fixture: `isolated_settings(provider="mock", mode="shadow")`."""

    def _make(**kwargs):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        for name in ("hotel", "agent"):
            example = REPO_ROOT / "config" / f"{name}.example.yaml"
            (cfg_dir / f"{name}.yaml").write_text(example.read_text(encoding="utf-8"),
                                                   encoding="utf-8")
        monkeypatch.setenv("AGENT_CONFIG_DIR", str(cfg_dir))
        # Sandbox the repo root too: the run loop imports data/imports/*.csv
        # from repo_root(), and a hotel's own CSVs must never reach a test.
        sandbox = tmp_path / "repo"
        if not sandbox.exists():
            sandbox.mkdir()
            for name in ("prompts", "knowledge", "fixtures"):
                src = REPO_ROOT / name
                if src.exists():
                    shutil.copytree(src, sandbox / name)
            (sandbox / "data" / "imports").mkdir(parents=True)
        monkeypatch.setenv("AGENT_REPO_ROOT", str(sandbox))
        from core.config import load_settings
        kwargs.setdefault("provider", "mock")
        kwargs.setdefault("mode", "shadow")
        return load_settings(**kwargs)

    return _make


@pytest.fixture
def store_at(tmp_path):
    """Factory fixture: `store_at(settings)` - a fresh SQLite file per test."""

    def _make(settings):
        from core.store import Store
        import store_ext
        store = Store(settings, path=tmp_path / "test.db")
        store_ext.migrate(store)
        return store

    return _make


@pytest.fixture
def fixtures_dir():
    return REPO_ROOT / "fixtures" / "inbound"
