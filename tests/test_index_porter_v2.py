"""DB indexer must handle Porter v2 (single-perspective forces[]) schema.

Pre-fix (Codex P1#2): index_run.py only looked for v1 keys
(`company_perspective` / `industry_perspective` / `forward_perspective`),
so any run that produced the v2 contract — which MEMORY.md mandates as the
authoritative shape — wrote zero porter rows. This left the knowledge base
without Porter scores for new runs, weakening DB cross-validation and peer
heatmaps.

Post-fix: v2 input writes 5 rows with `perspective='company'` (the only
CHECK-allowed value that downstream readers default to). v1 input still
writes 15 rows across three perspectives for backwards-compatibility with
historical runs.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.db import index_run, migrate
from tools.io import run_dir as run_dir_mod

FIXTURE_V2 = Path(__file__).parent / "fixtures" / "porter_analysis_v2_valid.json"


def _seed_run_dir(tmp_path: Path, porter_payload: dict) -> Path:
    rd = run_dir_mod.init_run_dir("Apple", "2026-04-28", run_id="porterv2",
                                  output_root=tmp_path)
    (rd / "meta" / "run.json").write_text(json.dumps({
        "run_id": "porterv2",
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "date": "2026-04-28",
        "started_at": "2026-04-28T00:00:00Z",
        "report_language": "en",
        "fiscal_period": "FY2026",
        "primary_geography": "US",
    }), encoding="utf-8")
    (rd / "research" / "financial_data.json").write_text(json.dumps({
        "ticker": "AAPL", "company": "Apple Inc.", "fiscal_period": "FY2026",
        "fiscal_year_end": "2026-09-30", "currency": "USD", "unit": "billion",
        "sector": "Information Technology", "primary_operating_geography": "US",
        "data_source": "SEC EDGAR", "data_confidence": "high",
        "income_statement": {
            "current_year": {"revenue": 400.0, "net_income": 100.0},
            "prior_year":   {"revenue": 380.0, "net_income": 95.0},
        },
    }), encoding="utf-8")
    (rd / "research" / "porter_analysis.json").write_text(
        json.dumps(porter_payload), encoding="utf-8"
    )
    return rd


def test_v2_porter_writes_five_company_rows(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_V2.read_text(encoding="utf-8"))
    rd = _seed_run_dir(tmp_path, payload)
    db_path = tmp_path / "kb.sqlite"
    migrate.apply_migrations(db_path)

    index_run.index_run(rd, db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT perspective, force, score
               FROM porter_scores_period
               WHERE ticker = 'AAPL' AND fiscal_period = 'FY2026'
               ORDER BY force"""
        ).fetchall()

    assert len(rows) == 5, f"v2 should write 5 rows (one per force), got {len(rows)}"
    perspectives = {r[0] for r in rows}
    assert perspectives == {"company"}, (
        f"v2 must use perspective='company' (only DB-allowed value for new schema); got {perspectives}"
    )
    score_by_force = {r[1]: r[2] for r in rows}
    # v2 fixture top-level scores: [4, 3, 2, 3, 5] in canonical PORTER_FORCES order.
    assert score_by_force["supplier"] == 4
    assert score_by_force["buyer"] == 3
    assert score_by_force["entrant"] == 2
    assert score_by_force["substitute"] == 3
    assert score_by_force["rivalry"] == 5


def test_v2_porter_uses_per_force_score_when_present(tmp_path: Path) -> None:
    """If forces[i].score disagrees with top-level scores[i], the per-force
    value wins. (Per-force objects are the merge_resolution output and have
    been QC-adjusted; the top-level scores[] is a cached summary.)"""
    payload = {
        "schema_version": 2,
        "scores": [1, 1, 1, 1, 1],  # stale summary
        "forces": [
            {"key": "supplier_power",  "score": 5, "qc_statement": "经QC..."},
            {"key": "buyer_power",     "score": 4, "qc_statement": "经QC..."},
            {"key": "new_entrants",    "score": 3, "qc_statement": "经QC..."},
            {"key": "substitutes",     "score": 2, "qc_statement": "经QC..."},
            {"key": "rivalry",         "score": 1, "qc_statement": "经QC..."},
        ],
    }
    rd = _seed_run_dir(tmp_path, payload)
    db_path = tmp_path / "kb.sqlite"
    migrate.apply_migrations(db_path)

    index_run.index_run(rd, db_path)

    with sqlite3.connect(db_path) as conn:
        score_by_force = dict(conn.execute(
            "SELECT force, score FROM porter_scores_period WHERE ticker='AAPL'"
        ).fetchall())

    assert score_by_force["supplier"] == 5
    assert score_by_force["rivalry"] == 1


def test_v2_writes_qc_statement_into_rationale_excerpt(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_V2.read_text(encoding="utf-8"))
    rd = _seed_run_dir(tmp_path, payload)
    db_path = tmp_path / "kb.sqlite"
    migrate.apply_migrations(db_path)

    index_run.index_run(rd, db_path)

    with sqlite3.connect(db_path) as conn:
        rationale = conn.execute(
            "SELECT rationale_excerpt FROM porter_scores_period "
            "WHERE ticker='AAPL' AND force='supplier'"
        ).fetchone()[0]

    # Expect the qc_statement to be carried, truncated to 240 chars.
    assert rationale.startswith("经QC合议"), rationale[:50]


def test_v1_legacy_still_writes_fifteen_rows(tmp_path: Path) -> None:
    """Historical three-perspective payloads must continue to index."""
    payload = {
        "company_perspective":  {"scores": [3, 3, 2, 3, 4], "narrative": "moat"},
        "industry_perspective": {"scores": [3, 3, 3, 4, 4], "narrative": "rivalry"},
        "forward_perspective":  {"scores": [4, 4, 4, 4, 5], "narrative": "AI"},
    }
    rd = _seed_run_dir(tmp_path, payload)
    db_path = tmp_path / "kb.sqlite"
    migrate.apply_migrations(db_path)

    index_run.index_run(rd, db_path)

    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM porter_scores_period WHERE ticker='AAPL'"
        ).fetchone()[0]
    assert n == 15, f"v1 legacy must write 15 rows (3 perspectives × 5 forces), got {n}"
