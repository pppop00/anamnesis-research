"""Orchestrator model-id gate — refuses Haiku/Instant, allows Opus/Sonnet.

This gate fires at `anamnesis.py bootstrap` and `tools/io/run_dir.py main`.
A run started under a refused model never reaches phase work, so the gate
must be cheap, deterministic, and substring-tolerant of future suffixes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.io.model_gate import classify

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR_CLI = PROJECT_ROOT / "tools" / "io" / "run_dir.py"


def test_opus_is_allowed() -> None:
    r = classify("claude-opus-4-7")
    assert r.allowed is True
    assert r.family == "opus"
    assert r.message == ""


def test_sonnet_is_allowed() -> None:
    r = classify("claude-sonnet-4-6")
    assert r.allowed is True
    assert r.family == "sonnet"


def test_haiku_is_refused() -> None:
    r = classify("claude-haiku-4-5-20251001")
    assert r.allowed is False
    assert r.family == "haiku"
    assert "not allowed" in r.message


def test_instant_is_refused() -> None:
    r = classify("claude-instant-1.2")
    assert r.allowed is False
    assert r.family == "instant"


def test_empty_is_refused() -> None:
    r = classify("")
    assert r.allowed is False
    assert r.family == "unknown"


def test_unknown_family_warns_but_allows() -> None:
    # Forward-compat: a future Claude family or a third-party model.
    r = classify("claude-some-future-family-9")
    assert r.allowed is True
    assert r.family == "unknown"
    assert "Proceeding" in r.message


def test_substring_match_catches_suffixed_haiku() -> None:
    # We match the family name as a substring so version suffixes
    # do not bypass the gate.
    r = classify("CLAUDE-HAIKU-5-experimental")
    assert r.allowed is False
    assert r.family == "haiku"


def test_run_dir_cli_refuses_haiku(tmp_path: Path) -> None:
    """`tools/io/run_dir.py --orchestrator-model claude-haiku-...` exits 2."""
    out_root = tmp_path / "out"
    res = subprocess.run(
        [sys.executable, str(RUN_DIR_CLI),
         "--company", "Apple", "--date", "2026-04-28",
         "--run-id", "gate1",
         "--output-root", str(out_root),
         "--orchestrator-model", "claude-haiku-4-5"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert res.returncode == 2, f"expected exit 2, got {res.returncode}: {res.stderr}"
    assert "haiku" in res.stderr.lower()
    assert not (out_root / "Apple_2026-04-28_gate1").exists()


def test_run_dir_cli_writes_orchestrator_model(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    res = subprocess.run(
        [sys.executable, str(RUN_DIR_CLI),
         "--company", "Apple", "--date", "2026-04-28",
         "--run-id", "gate2",
         "--output-root", str(out_root),
         "--orchestrator-model", "claude-opus-4-7"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert res.returncode == 0, res.stderr
    run_dir = out_root / "Apple_2026-04-28_gate2"
    assert run_dir.exists()

    run_json = json.loads((run_dir / "meta" / "run.json").read_text())
    assert run_json["orchestrator_model"] == "claude-opus-4-7"

    log_line = (run_dir / "meta" / "run.jsonl").read_text().strip()
    rec = json.loads(log_line)
    assert rec["payload"]["orchestrator_model"] == "claude-opus-4-7"


def test_run_dir_cli_requires_orchestrator_model(tmp_path: Path) -> None:
    """Argparse should refuse the call without --orchestrator-model."""
    res = subprocess.run(
        [sys.executable, str(RUN_DIR_CLI),
         "--company", "Apple", "--date", "2026-04-28",
         "--run-id", "gate3",
         "--output-root", str(tmp_path)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert res.returncode != 0
    assert "orchestrator-model" in res.stderr
