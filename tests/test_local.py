"""Runs a real local model; only with `pytest -m local`, Ollama answering on this computer and
the model installed.

`bench` uses the fake gateway only, so this touches no Telegram data. The model is
`TGMCP_BENCH_MODEL` (default `gemma4:12b`).
"""

import json
import os
import sys
import urllib.request

import pytest

from tgmcp import cli

pytestmark = pytest.mark.local

TAGS = "http://127.0.0.1:11434/api/tags"


def _installed() -> list[str] | None:
    """The model names Ollama lists, or None when it does not answer."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(TAGS, timeout=3) as response:
            listed = json.loads(response.read())
    except (OSError, ValueError):
        return None
    models = listed.get("models") if isinstance(listed, dict) else None
    return [str(m.get("name")) for m in models or [] if isinstance(m, dict)]


def test_bench_smoke_against_ollama(capsys, tmp_path, monkeypatch):
    model = os.environ.get("TGMCP_BENCH_MODEL", "gemma4:12b")
    names = _installed()
    if names is None:
        pytest.skip(f"Ollama does not answer at {TAGS}")
    if model not in names and f"{model}:latest" not in names:
        pytest.skip(f"model {model} is not installed in Ollama")
    monkeypatch.setenv("TGMCP_HOME", str(tmp_path))
    assert cli.main(["bench", "--set", "smoke", "--model", model]) == 0
    out = capsys.readouterr().out
    with capsys.disabled():
        sys.stdout.write(f"\n{out}")
    assert out.splitlines()[0].startswith("model=")
    assert any(line.startswith("total") for line in out.splitlines())
