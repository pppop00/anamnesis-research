"""anamnesis.py — top-level CLI subcommand smoke tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "anamnesis.py"


def _run(*args, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True,
        cwd=str(cwd) if cwd else str(PROJECT_ROOT),
    )


def test_help() -> None:
    res = _run("--help")
    assert res.returncode == 0
    assert "init" in res.stdout
    assert "bootstrap" in res.stdout
    assert "audit" in res.stdout
    assert "sector-report" in res.stdout


def test_status_runs() -> None:
    res = _run("status")
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert "submodules" in payload
    assert "db" in payload


def test_bootstrap_creates_run_dir(tmp_path: Path) -> None:
    res = _run("bootstrap", "--company", "Apple", "--date", "2026-04-28",
                "--run-id", "clitest1", "--output-root", str(tmp_path),
                "--orchestrator-model", "claude-opus-4-7")
    assert res.returncode == 0, res.stderr
    expected = tmp_path / "Apple_2026-04-28_clitest1"
    assert expected.exists()
    assert (expected / "meta" / "run.json").exists()
    assert (expected / "research").exists()
    assert (expected / "cards").exists()
    assert (expected / "validation").exists()
    assert "Next steps" in res.stdout


def test_bootstrap_refuses_haiku(tmp_path: Path) -> None:
    res = _run("bootstrap", "--company", "Apple", "--date", "2026-04-28",
                "--run-id", "clitest_haiku", "--output-root", str(tmp_path),
                "--orchestrator-model", "claude-haiku-4-5")
    assert res.returncode == 2, res.stderr
    assert "haiku" in res.stderr.lower()
    assert not (tmp_path / "Apple_2026-04-28_clitest_haiku").exists()
