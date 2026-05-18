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


def _seed_meta_run_json_keys(run_dir: Path, **keys) -> None:
    """Add keys to meta/run.json (preserving existing content)."""
    run_json = run_dir / "meta" / "run.json"
    if run_json.exists():
        data = json.loads(run_json.read_text(encoding="utf-8"))
    else:
        data = {}
    data.update(keys)
    run_json.write_text(json.dumps(data), encoding="utf-8")


def _seed_incident_precheck(run_dir: Path) -> None:
    """Simulate the P_INCIDENT_PRECHECK phase completing — its produces[] requires
    at least one `incident_precheck.acknowledged` event in meta/run.jsonl."""
    _append_jsonl(run_dir / "meta" / "run.jsonl",
                  phase="P_INCIDENT_PRECHECK",
                  event="incident_precheck.acknowledged",
                  incident_id="I-001")
    _append_jsonl(run_dir / "meta" / "run.jsonl",
                  phase="P_INCIDENT_PRECHECK", event="phase_exit")


def _seed_p0_intent(run_dir: Path) -> None:
    _seed_meta_run_json_keys(run_dir, ticker="AAPL", company="Apple Inc.", listing="US")
    _append_jsonl(run_dir / "meta" / "run.jsonl", phase="P0_intent", event="phase_exit")


def _seed_p0_lang(run_dir: Path, language: str = "en") -> None:
    _seed_meta_run_json_keys(run_dir, report_language=language)
    _append_jsonl(run_dir / "meta" / "run.jsonl", phase="P0_lang", event="phase_exit")


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

    # Seed the first phase's produces[] so the next-phase check is on solid ground.
    _seed_incident_precheck(rd)

    result = compute_next_phase(rd, meta)
    assert result.ok is True
    # First phase is P_INCIDENT_PRECHECK; next should be P0_intent.
    assert result.next_phase_id == meta["phases"][1]["id"]


def test_advance_refuses_invented_gate_source(tmp_path: Path) -> None:
    """If P0_lang.source is `auto_mode_default` it should block (INCIDENTS I-001)."""
    rd = init_run_dir("Apple", "2026-04-28", "adv3", tmp_path,
                      orchestrator_model="claude-opus-4-7")
    meta = _load_workflow_meta()

    # Walk past intent and lang phases to force the gate check.
    _seed_incident_precheck(rd)
    _seed_p0_intent(rd)
    _seed_p0_lang(rd, language="en")

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

    _seed_incident_precheck(rd)
    _seed_p0_intent(rd)
    _seed_p0_lang(rd, language="en")

    _write_gates(rd,
                 P0_intent={"value": "AAPL", "source": "prompt_unambiguous"},
                 P0_lang={"value": "en", "source": "user_response"})

    result = compute_next_phase(rd, meta)
    assert result.ok is True


def test_cli_exit_code_blocked_returns_1(tmp_path: Path) -> None:
    rd = init_run_dir("Apple", "2026-04-28", "adv5", tmp_path,
                      orchestrator_model="claude-opus-4-7")

    _seed_incident_precheck(rd)
    _seed_p0_intent(rd)
    _seed_p0_lang(rd, language="en")
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


# ---- produces[] parser & verifier ----------------------------------------
# Direct unit coverage for the four kinds of produce specs (Codex P2#2).

from tools.io.advance import _parse_produces, _verify_produces


def test_parse_exact_path() -> None:
    p = _parse_produces("research/edge_insights.json")
    assert p.kind == "exact"
    assert p.path == "research/edge_insights.json"
    assert p.json_key is None


def test_parse_glob_with_template() -> None:
    p = _parse_produces("cards/{stem}.card_slots.json")
    assert p.kind == "glob"
    assert p.path == "cards/*.card_slots.json"


def test_parse_glob_with_numeric_range() -> None:
    p = _parse_produces("validation/ocr_dump/card_{1..6}.txt")
    assert p.kind == "glob"
    assert p.path == "validation/ocr_dump/card_*.txt"


def test_parse_strips_annotation_suffix() -> None:
    p = _parse_produces("cards/{stem}.card_slots.json (audited)")
    assert p.kind == "glob"
    assert p.path == "cards/*.card_slots.json"
    p2 = _parse_produces("cards/{stem}.card_slots.json (compressed)")
    assert p2.path == "cards/*.card_slots.json"


def test_parse_json_key_exact_path() -> None:
    p = _parse_produces("meta/run.json:ticker")
    assert p.kind == "json_key"
    assert p.path == "meta/run.json"
    assert p.json_key == "ticker"


def test_parse_json_key_with_glob_path() -> None:
    p = _parse_produces("cards/{stem}.card_slots.json:logo_asset_path")
    assert p.kind == "json_key"
    assert p.path == "cards/*.card_slots.json"
    assert p.json_key == "logo_asset_path"


def test_parse_jsonl_event() -> None:
    p = _parse_produces("meta/run.jsonl:incident_precheck.acknowledged")
    assert p.kind == "jsonl_event"
    assert p.path == "meta/run.jsonl"
    assert p.json_key == "incident_precheck.acknowledged"


def test_verify_glob_finds_real_file(tmp_path: Path) -> None:
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards" / "Apple_Research_CN.card_slots.json").write_text("{}")
    ok, reason = _verify_produces(tmp_path, "cards/{stem}.card_slots.json")
    assert ok, reason


def test_verify_glob_blocks_when_no_match(tmp_path: Path) -> None:
    (tmp_path / "cards").mkdir()
    ok, reason = _verify_produces(tmp_path, "cards/{stem}.card_slots.json")
    assert not ok
    assert "no file" in reason or "matched no" in reason


def test_verify_json_key_pass(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "run.json").write_text(json.dumps({"ticker": "AAPL"}))
    ok, _ = _verify_produces(tmp_path, "meta/run.json:ticker")
    assert ok


def test_verify_json_key_blocks_when_key_missing(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "run.json").write_text(json.dumps({"other": "x"}))
    ok, reason = _verify_produces(tmp_path, "meta/run.json:ticker")
    assert not ok
    assert "ticker" in reason


def test_verify_json_key_with_glob_path(tmp_path: Path) -> None:
    """`cards/{stem}.card_slots.json:logo_asset_path` should glob + key-check."""
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards" / "Apple_Research_CN.card_slots.json").write_text(
        json.dumps({"logo_asset_path": "/abs/logo.png"})
    )
    ok, _ = _verify_produces(tmp_path, "cards/{stem}.card_slots.json:logo_asset_path")
    assert ok

    # If the file exists but lacks the key:
    (tmp_path / "cards" / "Apple_Research_CN.card_slots.json").write_text(
        json.dumps({"other": "x"})
    )
    ok2, reason = _verify_produces(tmp_path, "cards/{stem}.card_slots.json:logo_asset_path")
    assert not ok2
    assert "logo_asset_path" in reason


def test_verify_jsonl_event_pass(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "run.jsonl").write_text(
        json.dumps({"event": "incident_precheck.acknowledged", "incident_id": "I-001"}) + "\n"
    )
    ok, _ = _verify_produces(tmp_path, "meta/run.jsonl:incident_precheck.acknowledged")
    assert ok


def test_verify_jsonl_event_blocks_when_event_absent(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "run.jsonl").write_text(
        json.dumps({"event": "phase_exit"}) + "\n"
    )
    ok, reason = _verify_produces(tmp_path, "meta/run.jsonl:incident_precheck.acknowledged")
    assert not ok
    assert "incident_precheck.acknowledged" in reason


def test_advance_blocks_when_p10_skipped_without_validator_report(tmp_path: Path) -> None:
    """Regression for Codex P1#3 + P2#2 combined: my old advance filter
    silently let the run through when P10's `produces` (cards/validator1_report.json)
    was missing. The new parser must catch it."""
    rd = init_run_dir("Apple", "2026-04-28", "advp10", tmp_path,
                      orchestrator_model="claude-opus-4-7")
    meta = _load_workflow_meta()

    # Fast-forward all phases up to (but not including) P10_validator1.
    p10_idx = next(i for i, p in enumerate(meta["phases"]) if p["id"] == "P10_validator1")
    _seed_incident_precheck(rd)
    _seed_p0_intent(rd)
    _seed_p0_lang(rd, language="en")
    _seed_meta_run_json_keys(rd, sec_email="declined", sec_user_agent=None,
                             public_user_agent="EquityResearchSkill/1.0",
                             palette="macaron")
    _write_gates(rd,
                 P0_intent={"value": "AAPL", "source": "prompt_unambiguous"},
                 P0_lang={"value": "en", "source": "user_response"},
                 P0_sec_email={"value": "declined", "source": "declined"},
                 P0_palette={"value": "macaron", "source": "user_response"})

    # Falsely log every prior phase as exited, but write none of their produces beyond
    # the four we already seeded. The watchdog must catch the very next missing
    # predecessor and block — we don't need to walk all the way to P10 to make the
    # point.
    for p in meta["phases"][:p10_idx + 1]:
        _append_jsonl(rd / "meta" / "run.jsonl", phase=p["id"], event="phase_exit")

    result = compute_next_phase(rd, meta)
    assert result.ok is False
    # Reason should name a missing produces — pre-fix this returned ok=True silently.
    assert "produces" in result.reason or "missing" in result.reason
