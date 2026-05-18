"""Photo-validator wrappers must write the JSON reports their workflow_meta.json
phases declare as `produces`. Without these files, the phase-advance watchdog
(tools/io/advance.py) blocks runs that actually passed validation — and there is
no machine-readable verdict for downstream consumers (Codex P1#3).

We don't depend on the EP submodule's real validator here; instead we mock the
subprocess.run call inside the wrapper, then assert the wrapper writes the
declared JSON file with the right shape and status.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools.io import run_dir as run_dir_mod


def _seed_run(tmp_path: Path) -> Path:
    rd = run_dir_mod.init_run_dir("Apple", "2026-04-28", "wrapper1",
                                  output_root=tmp_path,
                                  orchestrator_model="claude-opus-4-7")
    (rd / "meta" / "run.json").write_text(json.dumps({
        "run_id": "wrapper1", "ticker": "AAPL", "fiscal_period": "FY2026",
    }), encoding="utf-8")
    return rd


def _seed_slots(rd: Path, with_worker_notes: bool) -> Path:
    slots = rd / "cards" / "Apple_Research_CN.card_slots.json"
    slots.parent.mkdir(parents=True, exist_ok=True)
    slots.write_text(json.dumps({"cover_company_name_cn": "苹果"}), encoding="utf-8")
    if with_worker_notes:
        sidecar = rd / "cards" / "Apple_Research_CN.card_slots_worker_notes.json"
        sidecar.write_text(json.dumps({"brand_statement": {"data_anchor": {}}}),
                           encoding="utf-8")
    return slots


def _fake_ep_run(returncode: int, stderr: str = "") -> Any:
    """A CompletedProcess-shaped stub for subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout="", stderr=stderr)


# ---- validator1 wrapper -----------------------------------------------------


def test_validator1_writes_pass_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.photo import validate_cards as wrapper
    rd = _seed_run(tmp_path)
    slots = _seed_slots(rd, with_worker_notes=False)
    html = rd / "research" / "Apple_Research_CN.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<html></html>")

    monkeypatch.setattr(wrapper, "find_skill_root", lambda *_: tmp_path)
    monkeypatch.setattr(wrapper, "script_path", lambda *a: tmp_path / "fake_ep.py")
    monkeypatch.setattr(wrapper, "python_exec", lambda: "python3")
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *a, **k: _fake_ep_run(0))

    rc = wrapper.main([
        "--input", str(html), "--slots", str(slots), "--palette", "macaron",
    ])
    assert rc == 0

    report_path = slots.parent / "validator1_report.json"
    assert report_path.exists(), "validator1_report.json must be written by wrapper"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["status"] == "pass"
    assert report["exit_code"] == 0
    assert report["palette"] == "macaron"
    assert report["run_id"] == "wrapper1"
    assert report["ticker"] == "AAPL"


def test_validator1_writes_fail_report_with_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.photo import validate_cards as wrapper
    rd = _seed_run(tmp_path)
    slots = _seed_slots(rd, with_worker_notes=False)
    html = rd / "research" / "Apple_Research_CN.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<html></html>")

    monkeypatch.setattr(wrapper, "find_skill_root", lambda *_: tmp_path)
    monkeypatch.setattr(wrapper, "script_path", lambda *a: tmp_path / "fake_ep.py")
    monkeypatch.setattr(wrapper, "python_exec", lambda: "python3")
    monkeypatch.setattr(
        wrapper.subprocess, "run",
        lambda *a, **k: _fake_ep_run(1, stderr="logo: missing\npalette: drift\n")
    )

    rc = wrapper.main([
        "--input", str(html), "--slots", str(slots), "--palette", "macaron",
    ])
    assert rc == 1

    report = json.loads((slots.parent / "validator1_report.json").read_text())
    assert report["status"] == "fail"
    assert report["exit_code"] == 1
    assert "logo: missing" in report["issues"]
    assert "palette: drift" in report["issues"]


# ---- voice_gate wrapper -----------------------------------------------------


def test_voice_gate_fails_when_worker_notes_absent(tmp_path: Path) -> None:
    """No sidecar = fail-closed; the gate cannot certify what it cannot see."""
    from tools.photo import voice_gate as wrapper
    rd = _seed_run(tmp_path)
    slots = _seed_slots(rd, with_worker_notes=False)  # NO sidecar
    html = rd / "research" / "Apple_Research_CN.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<html></html>")

    rc = wrapper.main([
        "--input", str(html), "--slots", str(slots), "--palette", "macaron",
    ])
    assert rc == 1

    report_path = rd / "validation" / "voice_gate.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["status"] == "fail"
    assert "missing required sidecar" in report["issues"][0]["message"]


def test_voice_gate_pass_when_ep_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.photo import voice_gate as wrapper
    rd = _seed_run(tmp_path)
    slots = _seed_slots(rd, with_worker_notes=True)  # sidecar present
    html = rd / "research" / "Apple_Research_CN.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<html></html>")

    monkeypatch.setattr(wrapper, "find_skill_root", lambda *_: tmp_path)
    monkeypatch.setattr(wrapper, "script_path", lambda *a: tmp_path / "fake_ep.py")
    monkeypatch.setattr(wrapper, "python_exec", lambda: "python3")
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *a, **k: _fake_ep_run(0))

    rc = wrapper.main([
        "--input", str(html), "--slots", str(slots), "--palette", "macaron",
    ])
    assert rc == 0

    report = json.loads((rd / "validation" / "voice_gate.json").read_text())
    assert report["status"] == "pass"
    assert report["worker_notes_present"] is True
    assert report["issues"] == []


def test_voice_gate_classifies_worker_note_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lines starting with `worker_notes.X.Y:` become structured (slot, field, message)."""
    from tools.photo import voice_gate as wrapper
    rd = _seed_run(tmp_path)
    slots = _seed_slots(rd, with_worker_notes=True)
    html = rd / "research" / "Apple_Research_CN.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("<html></html>")

    stderr = (
        "worker_notes.brand_statement.data_anchor: no parseable number\n"
        "worker_notes.judgement_paragraph: missing primary_quote\n"
        "card_slots.cover_intro: contains banned phrase '说白了'\n"
        "logo: missing\n"  # structural — should NOT appear in voice_gate.json
    )
    monkeypatch.setattr(wrapper, "find_skill_root", lambda *_: tmp_path)
    monkeypatch.setattr(wrapper, "script_path", lambda *a: tmp_path / "fake_ep.py")
    monkeypatch.setattr(wrapper, "python_exec", lambda: "python3")
    monkeypatch.setattr(wrapper.subprocess, "run",
                        lambda *a, **k: _fake_ep_run(1, stderr=stderr))

    wrapper.main([
        "--input", str(html), "--slots", str(slots), "--palette", "macaron",
    ])

    report = json.loads((rd / "validation" / "voice_gate.json").read_text())
    assert report["status"] == "fail"
    issues = report["issues"]
    assert len(issues) == 3, f"expected 3 voice-related issues, got {issues}"

    slots_seen = {i["slot"] for i in issues if i["slot"]}
    assert "brand_statement" in slots_seen
    assert "judgement_paragraph" in slots_seen
    assert "cover_intro" in slots_seen

    fields_seen = {i["field"] for i in issues if i["field"]}
    assert "data_anchor" in fields_seen

    # structural `logo: missing` line must NOT appear in voice_gate.json
    assert not any("logo" in i["raw"] for i in issues)
