"""tools/audit/aggregate_p12.py — combines four layer outputs into one verdict + QA_REPORT.md."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from tools.audit import aggregate_p12
from tools.io import run_dir as run_dir_mod

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGGREGATE_CLI = PROJECT_ROOT / "tools" / "audit" / "aggregate_p12.py"


def _seed(tmp_path: Path,
          recon_rows: list[dict] | None = None,
          ocr_summary: dict | None = None,
          web_envelope: dict | None = None,
          db_cross: dict | None = None,
          language: str = "zh") -> Path:
    rd = run_dir_mod.init_run_dir("Apple", "2026-04-28", run_id="agg1", output_root=tmp_path)
    (rd / "meta" / "run.json").write_text(json.dumps({
        "run_id": "agg1", "ticker": "AAPL", "fiscal_period": "FY2026Q2",
        "report_language": language, "started_at": "2026-04-28T00:00:00Z",
    }), encoding="utf-8")
    val = rd / "validation"

    if recon_rows is not None:
        recon = val / "reconciliation.csv"
        with recon.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["slot_path", "slot_value", "slot_unit", "slot_context",
                        "match_path", "match_value", "match_unit", "match_context",
                        "status", "details"])
            for r in recon_rows:
                w.writerow([r.get("slot_path", ""), r.get("slot_value", ""),
                            r.get("slot_unit", ""), r.get("slot_context", ""),
                            r.get("match_path", ""), r.get("match_value", ""),
                            r.get("match_unit", ""), r.get("match_context", ""),
                            r.get("status", "pass"), r.get("details", "{}")])

    if ocr_summary is not None:
        (val / "ocr_summary.json").write_text(json.dumps(ocr_summary), encoding="utf-8")
    if web_envelope is not None:
        (val / "web_third_check.json").write_text(json.dumps(web_envelope), encoding="utf-8")
    if db_cross is not None:
        (val / "db_cross.json").write_text(json.dumps(db_cross), encoding="utf-8")

    return rd


def test_pass_when_all_clean(tmp_path: Path) -> None:
    rd = _seed(
        tmp_path,
        recon_rows=[{"slot_path": "a", "slot_value": "1", "status": "pass"}],
        ocr_summary={"engine": "tesseract", "status": "pass", "key_misses": [], "decorative_misses": []},
        web_envelope={"status": "pass", "targets": [{"value": 5, "verification": "verified"}]},
        db_cross={"status": "no_priors", "checks": []},
    )
    out = aggregate_p12.aggregate(rd)
    assert out["status"] == "pass"
    assert (rd / "validation" / "post_card_audit.json").exists()
    qa = (rd / "validation" / "QA_REPORT.md").read_text(encoding="utf-8")
    assert "P12" in qa
    assert "PASS" in qa


def test_fail_when_reconcile_fails(tmp_path: Path) -> None:
    rd = _seed(
        tmp_path,
        recon_rows=[
            {"slot_path": "card_slots.intro", "slot_value": "99.9", "match_value": "56.3",
             "status": "fail"},
        ],
        ocr_summary={"engine": "tesseract", "status": "pass", "key_misses": [], "decorative_misses": []},
        web_envelope={"status": "pass", "targets": []},
        db_cross={"status": "no_priors", "checks": []},
    )
    out = aggregate_p12.aggregate(rd)
    assert out["status"] == "fail"
    assert out["layers"]["reconcile"]["fails"] == 1
    qa = (rd / "validation" / "QA_REPORT.md").read_text(encoding="utf-8")
    assert "FAIL" in qa
    assert "card_slots.intro" in qa


def test_fail_when_ocr_misses_key(tmp_path: Path) -> None:
    rd = _seed(
        tmp_path,
        recon_rows=[],
        ocr_summary={"engine": "paddleocr", "status": "fail",
                       "key_misses": [{"card": 3, "slot": "intro_sentence", "value": 56.3, "context": "毛利率"}],
                       "decorative_misses": []},
        web_envelope={"status": "pass", "targets": []},
        db_cross={"status": "pass", "checks": []},
    )
    out = aggregate_p12.aggregate(rd)
    assert out["status"] == "fail"
    qa = (rd / "validation" / "QA_REPORT.md").read_text(encoding="utf-8")
    assert "intro_sentence" in qa


def test_warn_when_only_db_layer_warns(tmp_path: Path) -> None:
    rd = _seed(
        tmp_path,
        recon_rows=[{"slot_path": "x", "slot_value": "1", "status": "pass"}],
        ocr_summary={"engine": "tesseract", "status": "pass", "key_misses": [], "decorative_misses": []},
        web_envelope={"status": "pass", "targets": []},
        db_cross={
            "status": "warn",
            "checks": [{"id": "peer_porter_divergence", "severity": "warn", "result": "divergence",
                        "flags": [{"force": "rivalry", "focal": 3, "peer_median": 5}]}],
        },
    )
    out = aggregate_p12.aggregate(rd)
    assert out["status"] == "warn"
    qa = (rd / "validation" / "QA_REPORT.md").read_text(encoding="utf-8")
    assert "WARN" in qa


def test_fail_when_user_agent_pii_guard_fails(tmp_path: Path) -> None:
    rd = _seed(
        tmp_path,
        recon_rows=[{"slot_path": "x", "slot_value": "1", "status": "pass"}],
        ocr_summary={"engine": "tesseract", "status": "pass", "key_misses": [], "decorative_misses": []},
        web_envelope={"status": "pass", "targets": []},
        db_cross={"status": "no_priors", "checks": []},
    )
    meta = json.loads((rd / "meta" / "run.json").read_text(encoding="utf-8"))
    meta.update({
        "sec_email": "user@example-real.com",
        "sec_user_agent": "EquityResearchSkill/1.0 (user@example-real.com)",
    })
    (rd / "meta" / "run.json").write_text(json.dumps(meta), encoding="utf-8")

    out = aggregate_p12.aggregate(rd)

    assert out["status"] == "fail"
    assert out["layers"]["user_agent_pii"]["status"] == "fail"
    assert (rd / "validation" / "user_agent_pii.json").exists()


def test_zh_qa_report(tmp_path: Path) -> None:
    rd = _seed(
        tmp_path,
        recon_rows=[{"slot_path": "x", "slot_value": "1", "status": "pass"}],
        ocr_summary={"engine": "tesseract", "status": "pass", "key_misses": [], "decorative_misses": []},
        web_envelope={"status": "pass", "targets": []},
        db_cross={"status": "no_priors", "checks": []},
        language="zh",
    )
    aggregate_p12.aggregate(rd)
    qa = (rd / "validation" / "QA_REPORT.md").read_text(encoding="utf-8")
    assert "渲染数字核对" in qa or "数字核对" in qa
    assert "结论" in qa


def test_aggregate_p12_runs_as_script(tmp_path: Path) -> None:
    """Regression for Codex P1#1: running the aggregator as a script must not ModuleNotFoundError."""
    rd = _seed(
        tmp_path,
        recon_rows=[{"slot_path": "a", "slot_value": "1", "status": "pass"}],
        ocr_summary={"engine": "tesseract", "status": "pass", "key_misses": [], "decorative_misses": []},
        web_envelope={"status": "no_targets", "targets": []},
        db_cross={"status": "no_priors", "checks": []},
    )
    res = subprocess.run(
        [sys.executable, str(AGGREGATE_CLI), "--run-dir", str(rd)],
        capture_output=True, text=True, cwd="/tmp",
    )
    assert "ModuleNotFoundError" not in res.stderr, res.stderr
    assert res.returncode == 0, f"exit={res.returncode}\nstderr={res.stderr}"
    assert (rd / "validation" / "post_card_audit.json").exists()
