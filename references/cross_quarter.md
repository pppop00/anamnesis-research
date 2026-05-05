---
schema_version: 1
description: How Anamnesis Research reuses prior runs across quarters and across peers in the same sector. Read this when entering P0_DB_PRECHECK or interpreting peer/macro short-circuits.
---

# Cross-quarter / cross-company reuse

Anamnesis Research's database is cumulative. A run for Apple in 2026Q3 should not re-collect Apple's 2026Q1 financials from scratch, and it should be cross-checkable against Samsung if Samsung is in the DB.

## P0_DB_PRECHECK — what runs before P1

Before any research subagent fires, the orchestrator calls `tools/db/queries.py` for three lookups:

| Query | Purpose | Effect on downstream |
|---|---|---|
| `get_prior_financials(ticker, n=4)` | last 4 quarters of this ticker | ER `financial_data_collector` is told "we already have FY2025–FY2026Q1; only fetch the new period" |
| `get_macro_snapshot(geography, period, max_age_days=14)` | fresh 6-factor macro vector for this geography/period | If hit, ER `macro_scanner` short-circuits and reuses instead of re-collecting |
| `get_peer_companies(ticker, sector, geography)` | peers in the same sector + geography | If ≥2 peers: P3.7_X_VALIDATE runs peer Porter divergence; P12 layer 4 runs DB cross-validation |

Outputs:
- `db_export/prior_financials_used.json`
- `db_export/peer_context.json`
- A `meta/run.jsonl` event noting whether macro will short-circuit

## Cold start (no priors)

Every read function returns empty list / `None`. The orchestrator skips dependent checks with `status: "no_priors"` in the affected report. Cold start is **not** an error.

## What gets reused vs always re-fetched

| Always reuse if available | Always re-fetch |
|---|---|
| Prior-quarter financials for the same ticker | Current-quarter financials (the whole point of the run) |
| Macro snapshot for `(geography, period)` <14d old | News/intelligence (volatile, never cached) |
| Peer Porter scores (read-only, for divergence checks) | Focal Porter analysis (always recomputed; peer rows are reference) |
| Disclosure quirks for the same ticker | Predictions (never cached — every run is a fresh forecast) |

## DB write rules (recap from MEMORY.md)

- `P_DB_INDEX` runs **after** `P12_final_audit` passes. Failed audits do not write to DB.
- All writes for one run are inside a single transaction; failure → rollback + `runs.run_status='failed'` + `db_export/index_error.json`.
- Append-only tables (`intelligence_signals`, `disclosure_quirks`) survive partial-run admission with an analyst note.
- Cross-validation queries filter on `runs.run_status='complete'` by default; partial rows exist for audit only.

## Sector reports

`tools/db/sector_report.py` regenerates cross-company analytical reports on demand (`db/sector_reports/` is gitignored — rebuilds are cheap). Example:

```bash
python tools/db/sector_report.py \
  --type porter_heatmap \
  --sector "Information Technology" \
  --period 2026Q2
```
