"""Runs a real local model; only with `pytest -m local` and Ollama answering on this computer.

`bench` uses the fake gateway only, so this touches no Telegram data. The model is
`TGMCP_BENCH_MODEL` (default `qwen3.5:9b`).
"""

import os
import sys
import urllib.request

import pytest

from tgmcp import cli

pytestmark = pytest.mark.local

TAGS = "http://127.0.0.1:11434/api/tags"


def _ollama_answers() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(TAGS, timeout=3) as response:
            return bool(response.status == 200)
    except OSError:
        return False


def test_bench_smoke_against_ollama(capsys, tmp_path, monkeypatch):
    if not _ollama_answers():
        pytest.skip(f"Ollama does not answer at {TAGS}")
    monkeypatch.setenv("TGMCP_HOME", str(tmp_path))
    model = os.environ.get("TGMCP_BENCH_MODEL", "qwen3.5:9b")
    assert cli.main(["bench", "--set", "smoke", "--model", model]) == 0
    out = capsys.readouterr().out
    with capsys.disabled():
        sys.stdout.write(f"\n{out}")
    assert out.splitlines()[0].startswith(f"model={model}")
    assert any(line.startswith("total") for line in out.splitlines())
