---
schema_version: 1
description: What an Anamnesis Research run produces on disk. Read this when you need to know which subfolder gets which artifact, or when validating a finished run dir.
---

# Run-dir layout

Every run lands in `output/{Company}_{Date}_{RunID}/` with this fixed structure. The orchestrator must produce all of it; downstream tooling (`tools/db/index_run.py`, `tools/audit/*`, the eval viewer) assumes these paths verbatim.

The run-dir root is not a deliverable folder. It must contain only the standard subfolders below (plus harmless OS metadata such as `.DS_Store`). The customer-facing deliverables are the HTML report in `research/` and the six PNG cards in `cards/`; everything else is an audit contract or runtime trace.

| Subfolder | Contents |
|---|---|
| `meta/` | `run.jsonl` event log, `system_prompt.frozen.txt`, `gates.json`, `submodule_shas.json`, `workflow_meta.snapshot.json`, `run.json` (resolved `{ticker, company, listing, report_language, sec_email, palette}`) |
| `research/` | All ER JSON artifacts + the locked-template HTML report (`{Company}_Research_{CN\|EN}.html`) + `cross_validation.json` + `report_validation.txt` + `structure_conformance.json` |
| `cards/` | `logo/{slug}_wordmark.png` + `{stem}.card_slots.json` + 6 PNGs (`01_cover.png` … `06_post_copy.png`) + `validator1_report.json` + `validator2_report.json` |
| `validation/` | P12 audit: `post_card_audit.json`, `QA_REPORT.md`, `reconciliation.csv`, `ocr_dump/card_{1..6}.txt`, `web_third_check.json`, `db_cross.json`, `user_agent_pii.json` |
| `db_export/` | `rows_written.json`, `peer_context.json`, `prior_financials_used.json`, `db_index_summary.json` (and `index_error.json` on failure) |
| `logs/` | `tools.jsonl` per-tool telemetry |

Before handoff, run:

```bash
python tools/io/validate_run_artifacts.py --run-dir <run_dir>
```

If known artifacts were accidentally written at the root, normalize them:

```bash
python tools/io/validate_run_artifacts.py --run-dir <run_dir> --fix
python tools/io/validate_run_artifacts.py --run-dir <run_dir>
```

Unknown root-level artifacts are delivery blockers until intentionally moved into one of the subfolders or removed. Do not hand off a run by listing every intermediate JSON; list the report HTML, six card PNGs, QA warning count, and DB write summary.

After `P_DB_INDEX` succeeds, `db/equity_kb.sqlite` has new rows for this `(ticker, period)` that future runs (same company, sibling peers in the same sector) will reuse as priors / peer context.

## Why these paths are fixed

- `tools/audit/reconcile_numbers.py` reads `cards/*.card_slots.json` and `research/*.json` by relative path — moving either side breaks reconciliation.
- `tools/db/index_run.py` walks the run dir as input; renaming any subfolder will silently drop rows.
- The eval viewer and `tests/test_aggregate_p12.py` assume `validation/QA_REPORT.md` exists.

If you need a new artifact, add a subfolder — never rename an existing one.
