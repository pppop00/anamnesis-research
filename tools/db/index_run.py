"""Walk a completed run directory and write rows into db/equity_kb.sqlite.

Triggered as the final P_DB_INDEX phase by the orchestrator. Single transaction;
on failure, rollback and emit db_export/index_error.json. Append-only tables
(intelligence_signals, disclosure_quirks) are admitted on a second pass even
if the main transaction failed.

Usage:
    python tools/db/index_run.py --run-dir <path>
    python tools/db/index_run.py --run-dir <path> --db /tmp/test.sqlite
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "db" / "equity_kb.sqlite"

EMAIL_IN_PARENS_RE = re.compile(r"\([^)]*@[^)]*\)")
PORTER_FORCES = ("supplier", "buyer", "entrant", "substitute", "rivalry")
MACRO_SLOTS = ("rate", "gdp", "inflation", "fx", "oil", "consumer_confidence")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"warn: invalid JSON in {path}: {e}", file=sys.stderr)
        return None


def _scrub_email(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return EMAIL_IN_PARENS_RE.sub("()", s)


def _g(d: Any, *keys, default=None) -> Any:
    """Safe nested .get. Last positional arg is treated as default if it's not a string."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _num(x: Any) -> Optional[float]:
    """Coerce to float; return None if missing or not numeric."""
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _safe_pct(numer: Any, denom: Any) -> Optional[float]:
    """numer/denom * 100, with None on missing or zero denominator."""
    n, d = _num(numer), _num(denom)
    if n is None or d is None or d == 0:
        return None
    return n / d * 100.0


def _eps_growth(curr: Any, prior: Any) -> Optional[float]:
    """EPS growth in pct. Returns None if prior is non-positive (sign change is not a percentage)."""
    n, d = _num(curr), _num(prior)
    if n is None or d is None or d <= 0:
        return None
    return (n - d) / d * 100.0


def _first(*candidates: Any) -> Any:
    """Return the first non-None argument; preserves zero/falsy numeric values."""
    for c in candidates:
        if c is not None:
            return c
    return None


# ─────────────────────────────────────────────────────────────────────
# Slot inference
# ─────────────────────────────────────────────────────────────────────

MACRO_SLOT_BY_NAME = {
    # Chinese / English heuristics for the 6 fixed slots
    "policy_rate": "rate", "rate": "rate", "interest_rate": "rate",
    "fed_funds": "rate", "lpr": "rate", "policy rate": "rate",
    "利率": "rate", "政策利率": "rate", "贴现率": "rate",
    "gdp": "gdp", "real_gdp": "gdp", "gdp_growth": "gdp", "国内生产总值": "gdp",
    "cpi": "inflation", "inflation": "inflation", "ppi": "inflation", "pce": "inflation",
    "通胀": "inflation", "通货膨胀": "inflation",
    "fx": "fx", "exchange_rate": "fx", "汇率": "fx", "usd_cny": "fx", "usd_jpy": "fx",
    "dxy": "fx", "usd_index": "fx", "usd index": "fx", "美元指数": "fx",
    "oil": "oil", "brent": "oil", "wti": "oil", "crude_oil": "oil", "原油": "oil", "石油": "oil",
    "consumer_confidence": "consumer_confidence", "consumer_sentiment": "consumer_confidence",
    "消费者信心": "consumer_confidence", "消费信心": "consumer_confidence",
}


def infer_macro_slot(factor_name: str) -> Optional[str]:
    if not factor_name:
        return None
    name = factor_name.strip().lower().replace(" ", "_")
    if name in MACRO_SLOT_BY_NAME:
        return MACRO_SLOT_BY_NAME[name]
    for key, slot in MACRO_SLOT_BY_NAME.items():
        if key in name or key in factor_name:
            return slot
    return None


# ─────────────────────────────────────────────────────────────────────
# Indexer
# ─────────────────────────────────────────────────────────────────────

class IndexResult:
    def __init__(self) -> None:
        self.tables_touched: dict[str, int] = {}
        self.errors: list[str] = []
        self.run_id: Optional[str] = None
        self.ticker: Optional[str] = None

    def bump(self, table: str, n: int = 1) -> None:
        self.tables_touched[table] = self.tables_touched.get(table, 0) + n

    def to_dict(self, started: str, finished: str) -> dict:
        return {
            "run_id": self.run_id,
            "ticker": self.ticker,
            "started_at": started,
            "finished_at": finished,
            "tables_touched": self.tables_touched,
            "errors": self.errors,
        }


def index_run(run_dir: Path, db_path: Path) -> IndexResult:
    started = _now_iso()
    result = IndexResult()
    run_dir = run_dir.resolve()

    meta = _read_json(run_dir / "meta" / "run.json") or {}
    gates = _read_json(run_dir / "meta" / "gates.json") or {}

    research = run_dir / "research"
    fd = _read_json(research / "financial_data.json") or {}
    fa = _read_json(research / "financial_analysis.json") or {}
    mf = _read_json(research / "macro_factors.json") or {}
    ni = _read_json(research / "news_intel.json") or {}
    ei = _read_json(research / "edge_insights.json") or {}
    pw = _read_json(research / "prediction_waterfall.json") or {}
    pa = _read_json(research / "porter_analysis.json") or {}
    qc = _read_json(research / "qc_audit_trail.json") or {}
    fv = _read_json(research / "final_report_data_validation.json") or {}
    sc = _read_json(research / "structure_conformance.json") or {}

    cards_dir = run_dir / "cards"
    card_slots_files = list(cards_dir.glob("*.card_slots.json"))
    cs = _read_json(card_slots_files[0]) if card_slots_files else None

    # Identity
    ticker = meta.get("ticker") or _g(fd, "ticker") or _g(meta, "slug")
    if not ticker:
        result.errors.append("cannot determine ticker")
        return result
    result.ticker = ticker
    run_id = meta.get("run_id") or uuid.uuid4().hex[:8]
    result.run_id = run_id

    fiscal_period = (
        _g(fd, "fiscal_period") or _g(fd, "fiscal_year") or _g(meta, "fiscal_period") or "UNKNOWN"
    )
    period_type = _g(fd, "period_type") or "annual"
    period_end_date = _g(fd, "fiscal_year_end") or _g(fd, "period_end_date")

    sector = _g(fd, "sector") or _g(meta, "sector")
    sub_industry = _g(fd, "sub_industry")
    geography = _g(mf, "primary_operating_geography") or _g(meta, "primary_geography")
    macro_period = _g(mf, "macro_period") or fiscal_period

    name_en = _g(fd, "company") or _g(fd, "name_en") or _g(meta, "company")
    name_cn = _g(fd, "name_cn") or _g(meta, "company_cn")
    exchange = _g(meta, "exchange")

    language = (
        _g(meta, "report_language")
        or _g(gates, "P0_lang", "value")
        or "en"
    )
    palette = _g(gates, "P0_palette", "value")
    sec_used = bool(_g(gates, "P0_sec_email", "value")) and _g(gates, "P0_sec_email", "value") != "declined"
    qc_full = bool(qc and _g(qc, "macro") is not None)

    packaging_profile = _g(sc, "profile")
    output_folder = str(run_dir)

    # Open DB and apply migrations defensively (idempotent)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        with conn:
            # companies upsert
            conn.execute(
                """INSERT INTO companies (ticker, exchange, name_en, name_cn, sector, sub_industry, primary_geography, first_seen_date, last_run_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT first_seen_date FROM companies WHERE ticker = ?), ?), ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                       exchange = COALESCE(excluded.exchange, companies.exchange),
                       name_en = COALESCE(excluded.name_en, companies.name_en),
                       name_cn = COALESCE(excluded.name_cn, companies.name_cn),
                       sector = COALESCE(excluded.sector, companies.sector),
                       sub_industry = COALESCE(excluded.sub_industry, companies.sub_industry),
                       primary_geography = COALESCE(excluded.primary_geography, companies.primary_geography),
                       last_run_date = excluded.last_run_date""",
                (ticker, exchange, name_en, name_cn, sector, sub_industry, geography,
                 ticker, meta.get("date"), meta.get("date")),
            )
            result.bump("companies")

            # runs row
            conn.execute(
                """INSERT INTO runs (run_id, ticker, run_date, language, mode, packaging_profile,
                                     output_folder, run_status, schema_version, started_at, finished_at,
                                     qc_full, sec_api_used)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       run_status = excluded.run_status,
                       finished_at = excluded.finished_at,
                       packaging_profile = excluded.packaging_profile""",
                (run_id, ticker, meta.get("date"), language, _g(meta, "mode"), packaging_profile,
                 output_folder, "complete", 1, meta.get("started_at"), _now_iso(),
                 1 if qc_full else 0, 1 if sec_used else 0),
            )
            result.bump("runs")

            # financials_period
            cy = _g(fd, "income_statement", "current_year") or {}
            py_is = _g(fd, "income_statement", "prior_year") or {}
            bs = _g(fd, "balance_sheet") or {}
            cf = _g(fd, "cash_flow") or {}
            # ER's financial_analysis.json uses nested sections (profitability/growth/
            # cash_flow/leverage/valuation). Field names also vary by writer (NVDA uses
            # roic_current_pct + diluted_eps_growth_yoy_pct; Macy uses ev_to_ebitda etc.).
            # Read all known variants. metrics_map kept as legacy fallback.
            fa_prof = _g(fa, "profitability") or {}
            fa_growth = _g(fa, "growth") or {}
            fa_cf = _g(fa, "cash_flow") or {}
            fa_lev = _g(fa, "leverage") or {}
            fa_val = _g(fa, "valuation") or {}
            metrics_map = {m.get("name"): m.get("value") for m in (_g(fa, "metrics", default=[]) or []) if isinstance(m, dict)}

            data_source = _scrub_email(_g(fd, "data_source"))

            # Derived fields. Order: ER analysis section → financial_data current_year
            # (Macy's writer puts margins here with `_pct` suffix) → raw derivation →
            # legacy metrics_map. yoy uses prior_year subtraction as last resort.
            revenue_cy = cy.get("revenue") or cy.get("total_revenue")
            py_revenue = py_is.get("revenue") or py_is.get("total_revenue")
            py_ni = py_is.get("net_income")
            cy_ni = cy.get("net_income")
            cy_eps = cy.get("diluted_eps") or cy.get("eps")
            py_eps = py_is.get("diluted_eps") or py_is.get("eps")

            gross_margin_v = _first(fa_prof.get("gross_margin_current"), fa_prof.get("gross_margin_pct"),
                                    cy.get("gross_margin"), cy.get("gross_margin_pct"),
                                    _safe_pct(cy.get("gross_profit"), revenue_cy))
            operating_margin_v = _first(fa_prof.get("operating_margin_current"), fa_prof.get("operating_margin_pct"),
                                        cy.get("operating_margin"), cy.get("operating_margin_pct"),
                                        _safe_pct(cy.get("operating_income"), revenue_cy))
            net_margin_v = _first(fa_prof.get("net_margin_current"), fa_prof.get("net_margin_pct"),
                                  cy.get("net_margin"), cy.get("net_margin_pct"),
                                  _safe_pct(cy_ni, revenue_cy))
            yoy_revenue_v = _first(fa_growth.get("revenue_growth_yoy_pct"),
                                   _g(fd, "income_statement", "yoy_revenue_pct"), cy.get("yoy_revenue_pct"),
                                   _safe_pct(_num(revenue_cy) - _num(py_revenue)
                                             if revenue_cy is not None and py_revenue is not None else None,
                                             py_revenue))
            yoy_ni_v = _first(fa_growth.get("net_income_growth_yoy_pct"),
                              _g(fd, "income_statement", "yoy_net_income_pct"), cy.get("yoy_net_income_pct"),
                              _safe_pct(_num(cy_ni) - _num(py_ni)
                                        if cy_ni is not None and py_ni is not None else None,
                                        py_ni))
            roic_v = _first(fa_prof.get("roic_current_pct"), fa_prof.get("roic_pct"), fa_prof.get("roic_current"),
                            metrics_map.get("ROIC"), metrics_map.get("roic_pct"))
            fcf_margin_v = _first(fa_cf.get("fcf_margin_pct"), fa_cf.get("fcf_margin_current"),
                                  metrics_map.get("FCF margin"), metrics_map.get("fcf_margin_pct"),
                                  _safe_pct(cf.get("free_cash_flow"), revenue_cy))
            debt_ebitda_v = _first(fa_lev.get("net_debt_to_ebitda"), fa_lev.get("net_debt_ebitda"),
                                   fa_lev.get("debt_to_ebitda"),
                                   metrics_map.get("Debt/EBITDA"), metrics_map.get("debt_to_ebitda"))
            ev_ebitda_v = _first(fa_val.get("ev_to_ebitda"), fa_val.get("ev_ebitda"),
                                 metrics_map.get("EV/EBITDA"), metrics_map.get("ev_ebitda"))
            eps_growth_v = _first(fa_growth.get("eps_growth_yoy_pct"), fa_growth.get("diluted_eps_growth_yoy_pct"),
                                  metrics_map.get("EPS growth"), _g(fa, "growth", "eps_growth_pct"),
                                  _eps_growth(cy_eps, py_eps))

            conn.execute(
                """INSERT OR REPLACE INTO financials_period (
                       ticker, fiscal_period, period_type, period_end_date,
                       revenue, cogs, gross_profit, rd_expense, sm_expense, ga_expense, total_opex,
                       operating_income, net_income, diluted_eps, diluted_shares,
                       gross_margin, operating_margin, net_margin, yoy_revenue_pct, yoy_net_income_pct,
                       cash_and_equivalents, total_assets, total_debt, total_equity, shares_outstanding,
                       operating_cash_flow, capex, free_cash_flow,
                       roic_pct, fcf_margin_pct, debt_to_ebitda, ev_to_ebitda, eps_growth_pct,
                       currency, unit, data_source, data_confidence, source_filing_url, source_run_id, superseded_by_run_id
                   ) VALUES (?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?,
                       ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?,
                       ?, ?, ?,
                       ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    ticker, fiscal_period, period_type, period_end_date,
                    cy.get("revenue"), cy.get("cogs"), cy.get("gross_profit"),
                    cy.get("rd_expense") or cy.get("research_and_development"),
                    cy.get("sm_expense") or cy.get("selling_and_marketing"),
                    cy.get("ga_expense") or cy.get("general_and_admin"),
                    cy.get("total_opex"),
                    cy.get("operating_income"), cy.get("net_income"),
                    cy.get("diluted_eps") or cy.get("eps"), cy.get("diluted_shares"),
                    gross_margin_v, operating_margin_v, net_margin_v,
                    yoy_revenue_v, yoy_ni_v,
                    bs.get("cash_and_equivalents"), bs.get("total_assets"),
                    bs.get("total_debt"), bs.get("total_equity"), bs.get("shares_outstanding"),
                    cf.get("operating_cash_flow"), cf.get("capex"), cf.get("free_cash_flow"),
                    roic_v,
                    fcf_margin_v,
                    debt_ebitda_v,
                    ev_ebitda_v,
                    eps_growth_v,
                    _g(fd, "currency"), _g(fd, "unit"),
                    data_source, _g(fd, "data_confidence"), _g(fd, "source_filing_url"),
                    run_id,
                ),
            )
            result.bump("financials_period")

            # segments
            for seg in (_g(fd, "segment_data", default=[]) or []):
                if not isinstance(seg, dict):
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO segments_period (ticker, fiscal_period, segment_name, revenue, pct_of_total, source_run_id)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ticker, fiscal_period, seg.get("segment_name") or seg.get("name"),
                     seg.get("revenue"), seg.get("pct_of_total"), run_id),
                )
                result.bump("segments_period")

            # macro_factors_period — keyed on (geography, period, factor_slot)
            if geography:
                for factor in (_g(mf, "factors", default=[]) or []):
                    if not isinstance(factor, dict):
                        continue
                    name_raw = factor.get("name") or factor.get("factor_name") or ""
                    slot = factor.get("factor_slot") or infer_macro_slot(name_raw)
                    if not slot:
                        continue
                    conn.execute(
                        """INSERT OR REPLACE INTO macro_factors_period (
                               geography, period, factor_slot, factor_name_raw,
                               current_value, forecast_value, factor_change_pct, beta, phi, adjustment_pct,
                               unit, source, collected_at, source_run_id
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (geography, macro_period, slot, name_raw,
                         factor.get("current_value"), factor.get("forecast_value"),
                         factor.get("factor_change_pct"), factor.get("beta"),
                         factor.get("phi") or _g(mf, "phi"),
                         factor.get("adjustment_pct"),
                         factor.get("unit"), _scrub_email(factor.get("source")),
                         _now_iso(), run_id),
                    )
                    result.bump("macro_factors_period")

            # porter_scores_period — supports two schemas:
            #   v1 (legacy):  three perspectives × 5 forces, keyed by `company_perspective` /
            #                 `industry_perspective` / `forward_perspective`.
            #   v2 (current): single perspective × 5 forces, top-level `forces[]` array in
            #                 canonical order (supplier_power, buyer_power, new_entrants,
            #                 substitutes, rivalry). MEMORY.md "QC scoring math (P3.6) —
            #                 plan v3 single perspective" is the authoritative spec.
            #
            # v2 rows are written with perspective='company' because:
            #   - porter_scores_period.perspective has a CHECK constraint accepting only
            #     ('company','industry','forward'); adding 'single' would require a schema
            #     migration with downstream churn (queries.py, sector_report.py).
            #   - downstream readers (get_peer_porter_matrix, porter_heatmap) default to
            #     perspective='company', so v2 rows surface automatically without callers
            #     having to learn a new value.
            #   - v2 collapsed the three v1 perspectives into one company-focused view —
            #     'company' is semantically the closest match.
            # v1 historical runs continue to populate all three perspective values; v2
            # runs populate only 'company'. Heatmap queries see one row per ticker either way.
            qc_porter_items = _g(qc, "porter", "items", default=[]) or []
            qc_changed: dict[tuple[str, str], dict] = {}
            for it in qc_porter_items:
                if not isinstance(it, dict):
                    continue
                key = (it.get("perspective") or "company", it.get("force"))
                if it.get("force"):
                    qc_changed[key] = it

            schema_v = pa.get("schema_version") if isinstance(pa, dict) else None
            top_forces = pa.get("forces") if isinstance(pa, dict) else None
            top_scores = pa.get("scores") if isinstance(pa, dict) else None

            if schema_v == 2 or isinstance(top_forces, list):
                # ----- v2: single perspective, top-level forces[] -----
                forces_arr = top_forces if isinstance(top_forces, list) else []
                short = "company"
                for i, force in enumerate(PORTER_FORCES):
                    f_obj = forces_arr[i] if i < len(forces_arr) else None
                    # Prefer the per-force `score`; fall back to top-level scores[i].
                    score = None
                    if isinstance(f_obj, dict):
                        score = f_obj.get("score")
                    if score is None and isinstance(top_scores, list) and i < len(top_scores):
                        score = top_scores[i]
                    if not isinstance(score, (int, float)):
                        continue
                    rationale = ""
                    if isinstance(f_obj, dict):
                        rationale = (f_obj.get("qc_statement")
                                     or f_obj.get("mechanism")
                                     or "")
                    qc_item = qc_changed.get((short, force))
                    conn.execute(
                        """INSERT OR REPLACE INTO porter_scores_period (
                               ticker, fiscal_period, perspective, force, score,
                               rationale_excerpt, qc_score_changed, score_before, score_after, source_run_id
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ticker, fiscal_period, short, force, int(round(score)),
                         rationale[:240],
                         1 if (qc_item and qc_item.get("score_changed")) else (
                             1 if (isinstance(f_obj, dict) and f_obj.get("score_changed")) else 0
                         ),
                         qc_item.get("score_before") if qc_item else (
                             f_obj.get("score_before") if isinstance(f_obj, dict) else None
                         ),
                         qc_item.get("score_after") if qc_item else (
                             f_obj.get("score_after") if isinstance(f_obj, dict) else None
                         ),
                         run_id),
                    )
                    result.bump("porter_scores_period")
            else:
                # ----- v1: legacy three-perspective shape -----
                perspective_aliases = (
                    ("company",   ("company_perspective", "company_level")),
                    ("industry",  ("industry_perspective", "industry_level")),
                    ("forward",   ("forward_perspective", "forward_looking")),
                )
                for short, candidate_keys in perspective_aliases:
                    persp_data = None
                    for k in candidate_keys:
                        persp_data = _g(pa, k)
                        if persp_data:
                            break
                    if not persp_data:
                        continue
                    scores = persp_data.get("scores")
                    if not isinstance(scores, list):
                        continue
                    for i, force in enumerate(PORTER_FORCES):
                        if i >= len(scores):
                            break
                        score = scores[i]
                        if not isinstance(score, (int, float)):
                            continue
                        qc_item = qc_changed.get((short, force))
                        conn.execute(
                            """INSERT OR REPLACE INTO porter_scores_period (
                                   ticker, fiscal_period, perspective, force, score,
                                   rationale_excerpt, qc_score_changed, score_before, score_after, source_run_id
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ticker, fiscal_period, short, force, int(round(score)),
                             ((persp_data.get("narrative") or persp_data.get("text") or ""))[:240],
                             1 if (qc_item and qc_item.get("score_changed")) else 0,
                             qc_item.get("score_before") if qc_item else None,
                             qc_item.get("score_after") if qc_item else None,
                             run_id),
                        )
                        result.bump("porter_scores_period")

            # prediction_waterfall_period
            if pw:
                conn.execute(
                    """INSERT OR REPLACE INTO prediction_waterfall_period (
                           ticker, fiscal_period, baseline_growth_pct, macro_adjustment_total_pct,
                           company_specific_adjustment_pct, predicted_revenue_growth_pct, predicted_revenue,
                           phi, confidence, formula_note, macro_adjustments_json, company_events_detail_json,
                           qc_deliberation_json, source_run_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, fiscal_period,
                     pw.get("baseline_growth_pct"),
                     sum((adj.get("adjustment_pct") or 0) for adj in (pw.get("macro_adjustments") or []) if isinstance(adj, dict)) or pw.get("macro_adjustment_total_pct"),
                     pw.get("company_specific_adjustment_pct"),
                     pw.get("predicted_revenue_growth_pct"),
                     pw.get("predicted_revenue"),
                     pw.get("phi"), pw.get("confidence"), pw.get("formula_note"),
                     json.dumps(pw.get("macro_adjustments"), ensure_ascii=False) if pw.get("macro_adjustments") else None,
                     json.dumps(pw.get("company_events_detail"), ensure_ascii=False) if pw.get("company_events_detail") else None,
                     json.dumps(pw.get("qc_deliberation"), ensure_ascii=False) if pw.get("qc_deliberation") else None,
                     run_id),
                )
                result.bump("prediction_waterfall_period")

            # qc_events
            for src, items in (("macro", _g(qc, "macro", "items", default=[]) or []),
                               ("porter", _g(qc, "porter", "items", default=[]) or [])):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    item_id = it.get("id") or f"{src.upper()}-{uuid.uuid4().hex[:6]}"
                    conn.execute(
                        """INSERT OR REPLACE INTO qc_events (
                               run_id, item_id, phase, perspective, force, verdict,
                               score_before, score_after, weighted_score, delta_vs_draft,
                               rationale, fields_changed_json
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, item_id, src, it.get("perspective"), it.get("force"),
                         it.get("verdict"), it.get("score_before"), it.get("score_after"),
                         it.get("weighted_score"), it.get("delta_vs_draft"),
                         it.get("rationale"),
                         json.dumps(it.get("fields_changed"), ensure_ascii=False) if it.get("fields_changed") else None),
                    )
                    result.bump("qc_events")

            # validation_findings
            for sev_key in ("CRITICAL", "WARNING", "INFO"):
                items = _g(fv, sev_key.lower() + "_items", default=None) or _g(fv, sev_key, default=None)
                if not items:
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    fid = it.get("id") or f"{sev_key}-{uuid.uuid4().hex[:8]}"
                    conn.execute(
                        """INSERT OR REPLACE INTO validation_findings (
                               finding_id, run_id, severity, category, description, root_cause, recomputed_value
                           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (fid, run_id, sev_key, it.get("category"), it.get("description") or it.get("issue"),
                         it.get("root_cause"), str(it.get("recomputed_value")) if it.get("recomputed_value") is not None else None),
                    )
                    result.bump("validation_findings")

            # edge_insights
            insight = _g(ei, "chosen_insight") or {}
            if insight.get("headline"):
                insight_id = f"{run_id}:edge"
                conn.execute(
                    """INSERT OR REPLACE INTO edge_insights (
                           insight_id, ticker, run_id, headline, insight_type, surface_read,
                           hidden_rule, investment_implication, confidence, evidence_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (insight_id, ticker, run_id, insight.get("headline"),
                     insight.get("insight_type"), insight.get("surface_read"),
                     insight.get("hidden_rule"), insight.get("investment_implication"),
                     insight.get("confidence"),
                     json.dumps(insight.get("evidence"), ensure_ascii=False) if insight.get("evidence") else None),
                )
                result.bump("edge_insights")

            # card_slots
            if cs:
                paths_by_n = {}
                for n in range(1, 7):
                    candidates = list(cards_dir.glob(f"{n:02d}_*.png"))
                    paths_by_n[n] = str(candidates[0]) if candidates else None
                conn.execute(
                    """INSERT OR REPLACE INTO card_slots (
                           ticker, run_id, card_slots_json, cover_focus, brand_statement, social_post,
                           card1_png_path, card2_png_path, card3_png_path,
                           card4_png_path, card5_png_path, card6_png_path
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, run_id, json.dumps(cs, ensure_ascii=False),
                     cs.get("company_focus_paragraph"), cs.get("brand_statement"),
                     "\n".join(cs.get("post_content_lines") or []),
                     paths_by_n[1], paths_by_n[2], paths_by_n[3],
                     paths_by_n[4], paths_by_n[5], paths_by_n[6]),
                )
                result.bump("card_slots")

            # FTS narratives
            def add_fts(doc_id: str, section: str, content) -> None:
                if not content:
                    return
                if isinstance(content, list):
                    content = "\n".join(str(x) for x in content if x)
                elif isinstance(content, dict):
                    content = json.dumps(content, ensure_ascii=False)
                elif not isinstance(content, str):
                    content = str(content)
                if not content.strip():
                    return
                conn.execute(
                    "INSERT INTO fts_narratives (doc_id, ticker, sector, section, content) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, ticker, sector, section, content),
                )
                result.bump("fts_narratives")

            add_fts(f"{run_id}:thesis", "thesis",
                    _g(fa, "investment_thesis_short") or _g(fa, "investment_thesis") or _g(fa, "executive_summary"))
            add_fts(f"{run_id}:edge", "edge", insight.get("headline"))
            add_fts(f"{run_id}:news_summary", "news_summary",
                    _g(ni, "narrative_summary") or _g(ni, "summary"))
            add_fts(f"{run_id}:macro_commentary", "macro_commentary",
                    _g(mf, "macro_factor_commentary") or _g(mf, "notes"))
            for short, candidate_keys in (
                ("company",  ("company_perspective", "company_level")),
                ("industry", ("industry_perspective", "industry_level")),
                ("forward",  ("forward_perspective", "forward_looking")),
            ):
                pdata = None
                for k in candidate_keys:
                    pdata = _g(pa, k)
                    if pdata:
                        break
                if pdata:
                    add_fts(f"{run_id}:porter:{short}", "porter",
                            pdata.get("narrative") or pdata.get("text"))

        # ─────────────────────────────────────────────────────────────
        # Append-only tables — best-effort, even if main txn failed
        # ─────────────────────────────────────────────────────────────

    except sqlite3.Error as e:
        result.errors.append(f"main transaction failed: {e}")

    # Append-only pass: signals + quirks (independent of main transaction)
    try:
        signals = _g(ni, "intelligence_signals", default=[]) or []
        with conn:
            for s in signals:
                if not isinstance(s, dict):
                    continue
                sid = f"{run_id}:{s.get('id') or s.get('sig_id') or uuid.uuid4().hex[:8]}"
                fact = s.get("fact") or s.get("description")
                conn.execute(
                    """INSERT OR IGNORE INTO intelligence_signals (
                           sig_id, ticker, sector, signal_type, fact, affected_metric, watch_metric,
                           thesis_implication, observation_date, source_run_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sid, s.get("ticker") or ticker, sector, s.get("type") or s.get("signal_type"),
                     fact, s.get("affected_metric"), s.get("watch_metric"),
                     s.get("thesis_implication"),
                     s.get("source_date") or meta.get("date"), run_id),
                )
                result.bump("intelligence_signals")
                if fact:
                    conn.execute(
                        "INSERT INTO fts_narratives (doc_id, ticker, sector, section, content) VALUES (?, ?, ?, ?, ?)",
                        (f"signal:{sid}", ticker, sector, "signal", fact),
                    )
                    result.bump("fts_narratives")

            for q in (_g(fd, "disclosure_quirks", default=[]) or []):
                if not isinstance(q, dict):
                    continue
                qid = f"{run_id}:{q.get('id') or uuid.uuid4().hex[:8]}"
                conn.execute(
                    """INSERT OR IGNORE INTO disclosure_quirks (
                           quirk_id, ticker, sector, fiscal_period, description, basis_change, run_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (qid, ticker, sector, fiscal_period,
                     q.get("description"), q.get("basis_change"), run_id),
                )
                result.bump("disclosure_quirks")
    except sqlite3.Error as e:
        result.errors.append(f"append-only pass failed: {e}")

    conn.close()

    finished = _now_iso()
    summary = result.to_dict(started, finished)

    db_export = run_dir / "db_export"
    db_export.mkdir(parents=True, exist_ok=True)
    (db_export / "db_index_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (db_export / "rows_written.json").write_text(
        json.dumps({"run_id": run_id, "ticker": ticker, "tables_touched": result.tables_touched},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if result.errors:
        (db_export / "index_error.json").write_text(
            json.dumps({"errors": result.errors, "run_id": run_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"error: {run_dir} does not exist", file=sys.stderr)
        return 2

    res = index_run(run_dir, Path(args.db))
    print(json.dumps({
        "run_id": res.run_id,
        "ticker": res.ticker,
        "tables_touched": res.tables_touched,
        "errors": res.errors,
    }, ensure_ascii=False, indent=2))
    return 0 if not res.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
