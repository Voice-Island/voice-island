"""Tests without the stack: no LiveKit, no LLM, no microphone. Settings isolated in a temporary file.

  cd web/agent && uv run pytest
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Each test starts from the defaults, without reading or writing the user's settings."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({}))
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    return path
