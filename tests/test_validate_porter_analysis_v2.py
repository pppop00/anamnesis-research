"""Tests for the v2 Porter analysis validator (plan v3)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.research.validate_porter_analysis import (
    V1_MIGRATION_MESSAGE,
    main,
    validate_porter_analysis,
)


FIXTURES = Path(__file__).parent / "fixtures"
VALID_FIXTURE = FIXTURES / "porter_analysis_v2_valid.json"
MISSING_MECHANISM_FIXTURE = FIXTURES / "porter_analysis_v2_missing_mechanism.json"
V1_FIXTURE = FIXTURES / "porter_analysis_v1.json"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_valid_v2_fixture_passes() -> None:
    """Positive: realistic 5-force v2 fixture must pass cleanly."""
    data = _load(VALID_FIXTURE)
    result = validate_porter_analysis(data)
    assert result["status"] == "pass", result
    assert result["errors"] == [], result["errors"]


def test_missing_mechanism_fails_with_specific_message() -> None:
    """Negative: a mechanism shorter than the floor must be flagged."""
    data = _load(MISSING_MECHANISM_FIXTURE)
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any(
        "forces[0].mechanism is too short" in e for e in result["errors"]
    ), result["errors"]


def test_v1_schema_rejected_with_migration_hint() -> None:
    """v1 three-perspective shape gets a single clear migration error."""
    data = _load(V1_FIXTURE)
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any(V1_MIGRATION_MESSAGE in e for e in result["errors"]), result["errors"]


def test_schema_version_must_be_2() -> None:
    data = _load(VALID_FIXTURE)
    data["schema_version"] = 1
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any("schema_version must be exactly 2" in e for e in result["errors"])


def test_forces_out_of_canonical_order_fails() -> None:
    data = _load(VALID_FIXTURE)
    # swap supplier_power and buyer_power
    data["forces"][0], data["forces"][1] = data["forces"][1], data["forces"][0]
    # also swap scores so we don't double-fire the scores-mismatch error
    data["scores"][0], data["scores"][1] = data["scores"][1], data["scores"][0]
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any(
        "canonical order expects 'supplier_power' at position 0" in e
        for e in result["errors"]
    )


def test_score_out_of_range_fails() -> None:
    data = _load(VALID_FIXTURE)
    data["forces"][0]["score"] = 7
    data["forces"][0]["score_after"] = 7
    data["scores"][0] = 7
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any("forces[0].score=7" in e for e in result["errors"])


def test_qc_statement_wrong_score_fails() -> None:
    data = _load(VALID_FIXTURE)
    # force a sentence-score / actual-score mismatch
    data["forces"][0]["qc_statement"] = (
        "经QC合议，维持供应商议价能力为2分。先进设备、EDA和关键材料仍受外部约束，短期难以替代。"
    )
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any(
        "forces[0].qc_statement" in e and "does not match" in e
        for e in result["errors"]
    )


def test_no_qc_mode_requires_no_qc_prefix() -> None:
    """When qc_audit_trail_present=False, qc_statement must use 基于初稿评分."""
    data = _load(VALID_FIXTURE)
    data["qc_audit_trail_present"] = False
    # strip the audit-trail-only fields so they're not required
    for f in data["forces"]:
        for k in ("score_changed", "score_before", "score_after"):
            f.pop(k, None)
    # rewrite all five qc_statements to the no-QC opener with matching score
    base = data["forces"]
    no_qc_openers = {
        "supplier_power": ("供应商议价能力", 4),
        "buyer_power": ("买方议价能力", 3),
        "new_entrants": ("新进入者威胁", 2),
        "substitutes": ("替代品威胁", 3),
        "rivalry": ("行业内竞争", 5),
    }
    for f in base:
        name, score = no_qc_openers[f["key"]]
        f["qc_statement"] = (
            f"基于初稿评分，{name}为{score}分。"
            "维持原有评分判断，结构性因素未发生方向性变化，足以支撑当前评分。"
        )
    result = validate_porter_analysis(data)
    assert result["status"] == "pass", result["errors"]

    # Now break one force back to the QC-mode prefix → must fail
    bad = copy.deepcopy(data)
    bad["forces"][0]["qc_statement"] = (
        "经QC合议，维持供应商议价能力为4分。先进设备、EDA和关键材料仍受外部约束，短期难以替代。"
    )
    bad_result = validate_porter_analysis(bad)
    assert bad_result["status"] == "critical"
    assert any(
        "no-QC mode requires" in e for e in bad_result["errors"]
    ), bad_result["errors"]


def test_falsifier_without_observable_hint_fails() -> None:
    data = _load(VALID_FIXTURE)
    data["forces"][0]["falsifier"] = (
        "如果未来出现某些结构性的行业格局变化或者宏观环境出现重大转折时，"
        "我们将考虑重新评估当前评分。"
    )
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any(
        "falsifier lacks an observable-event hint" in e for e in result["errors"]
    )


def test_data_anchor_value_must_contain_number() -> None:
    data = _load(VALID_FIXTURE)
    data["forces"][0]["data_anchor"]["value"] = "很高"
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any(
        "data_anchor.value must contain a number" in e for e in result["errors"]
    )


def test_scores_must_match_forces_inline_scores() -> None:
    data = _load(VALID_FIXTURE)
    data["scores"][2] = 5  # disagree with forces[2].score (2)
    result = validate_porter_analysis(data)
    assert result["status"] == "critical"
    assert any("must equal forces[].score" in e for e in result["errors"])


def test_cli_with_run_dir_exits_zero_on_pass(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    research = run_dir / "research"
    research.mkdir(parents=True)
    (research / "porter_analysis.json").write_text(
        VALID_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rc = main(["--run-dir", str(run_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    # On pass, no ERROR: lines should be emitted to stdout.
    assert "ERROR:" not in out
    # The full JSON result is pretty-printed across multiple lines, so parse
    # the whole stdout as JSON.
    payload = json.loads(out)
    assert payload["status"] == "pass"
    assert payload["errors"] == []


def test_cli_with_run_dir_exits_nonzero_on_v1(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    research = run_dir / "research"
    research.mkdir(parents=True)
    (research / "porter_analysis.json").write_text(
        V1_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rc = main(["--run-dir", str(run_dir)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "ERROR:" in captured.out
    assert V1_MIGRATION_MESSAGE in captured.out


def test_cli_with_no_args_returns_invocation_error(capsys) -> None:
    rc = main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "provide --run-dir or --json" in err


def test_cli_help_runs_cleanly() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
