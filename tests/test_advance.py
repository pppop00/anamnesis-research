"""Phase-advance watchdog — refuses to proceed past unsatisfied gates."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.io.advance import compute_next_phase
from tools.io.run_dir import init_run_dir

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADVANCE_CLI = PROJECT_ROOT / "tools" / "io" / "advance.py"
ANAMNESIS_CLI = PROJECT_ROOT / "anamnesis.py"


def _load_workflow_meta() -> dict:
    return json.loads((PROJECT_ROOT / "workflow_meta.json").read_text(encoding="utf-8"))


def _append_jsonl(path: Path, **payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_gates(run_dir: Path, **gates) -> None:
    (run_dir / "meta" / "gates.json").write_text(
        json.dumps(gates, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_first_advance_returns_first_phase(tmp_path: Path) -> None:
    rd = init_run_dir("Apple", "2026-04-28", "adv1", tmp_path,
                      orchestrator_model="claude-opus-4-7")
    meta = _load_workflow_meta()
    result = compute_next_phase(rd, meta)
    assert result.ok is True
    assert result.next_phase_id == meta["phases"][0]["id"]


def test_advance_walks_past_exited_phases(tmp_path: Path) -> None:
    rd = init_run_dir("Apple", "2026-04-28", "adv2", tmp_path,
                      orchestrator_model="claude-opus-4-7")
    meta = _load_workflow_meta()

    first = meta["phases"][0]["id"]
    _append_jsonl(rd / "meta" / "run.jsonl",
                  phase=first, event="phase_exit")

    result = compute_next_phase(rd, meta)
    assert result.ok is True
    assert result.next_phase_id == meta["phases"][1]["id"]


def test_advance_refuses_invented_gate_source(tmp_path: Path) -> None:
    """If P0_lang.source is `auto_mode_default` it should block (INCIDENTS I-001)."""
    rd = init_run_dir("Apple", "2026-04-28", "adv3", tmp_path,
                      orchestrator_model="claude-opus-4-7")
    meta = _load_workflow_meta()

    # Walk past intent and lang phases to force the gate check.
    for pid in ("P_INCIDENT_PRECHECK", "P0_intent", "P0_lang"):
        _append_jsonl(rd / "meta" / "run.jsonl", phase=pid, event="phase_exit")

    _write_gates(rd,
                 P0_intent={"value": "AAPL", "source": "prompt_unambiguous"},
                 P0_lang={"value": "en", "source": "auto_mode_default"})

    result = compute_next_phase(rd, meta)
    assert result.ok is False
    assert "P0_lang" in result.reason
    assert "auto_mode_default" in result.reason


def test_advance_accepts_whitelisted_gate_source(tmp_path: Path) -> None:
    rd = init_run_dir("Apple", "2026-04-28", "adv4", tmp_path,
                      orchestrator_model="claude-opus-4-7")
    meta = _load_workflow_meta()

    for pid in ("P_INCIDENT_PRECHECK", "P0_intent", "P0_lang"):
        _append_jsonl(rd / "meta" / "run.jsonl", phase=pid, event="phase_exit")

    _write_gates(rd,
                 P0_intent={"value": "AAPL", "source": "prompt_unambiguous"},
                 P0_lang={"value": "en", "source": "user_response"})

    result = compute_next_phase(rd, meta)
    assert result.ok is True


def test_cli_exit_code_blocked_returns_1(tmp_path: Path) -> None:
    rd = init_run_dir("Apple", "2026-04-28", "adv5", tmp_path,
                      orchestrator_model="claude-opus-4-7")

    for pid in ("P_INCIDENT_PRECHECK", "P0_intent", "P0_lang"):
        _append_jsonl(rd / "meta" / "run.jsonl", phase=pid, event="phase_exit")
    _write_gates(rd,
                 P0_intent={"value": "AAPL", "source": "prompt_unambiguous"},
                 P0_lang={"value": "en", "source": "inferred_from_locale"})

    res = subprocess.run(
        [sys.executable, str(ANAMNESIS_CLI), "advance",
         "--run-dir", str(rd), "--format", "json"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert res.returncode == 1, res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert "P0_lang" in payload["reason"]


def test_cli_exit_code_ok_returns_0(tmp_path: Path) -> None:
    rd = init_run_dir("Apple", "2026-04-28", "adv6", tmp_path,
                      orchestrator_model="claude-opus-4-7")

    res = subprocess.run(
        [sys.executable, str(ANAMNESIS_CLI), "advance",
         "--run-dir", str(rd), "--format", "json"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["next_phase_id"] is not None
