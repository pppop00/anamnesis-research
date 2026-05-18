---
schema_version: 1
description: Prose explanation of the Anamnesis Research pipeline — what each phase does, what blocks, what runs in parallel, where outputs land. Read this for the phase narrative; read workflow_meta.json for the machine-readable contract. The contract is bracketed by an incident pre-check before P0_intent and an incident post-check before P_DB_INDEX; report and card pipelines are each followed by a parallel red-team review.
---

# Phase contract

`workflow_meta.json` is the source of truth for phase IDs, tools, agents, parallelism, retry policies, and produced artifacts. **If anything in this prose disagrees with `workflow_meta.json`, the JSON wins.** This file is the prose companion: it explains *why* each phase exists and how it connects to the next.

For runtime procedure (the orchestrator's step-by-step), see `agents/orchestrator.md`. This file is for understanding the pipeline shape.

## Phase id index

The prose below uses dotted shorthand (P1.5, P3.6, P5.7) that maps to canonical phase ids in `workflow_meta.json`. `tools/research/validate_workflow_meta.py` cross-checks that every id below appears literally somewhere in this file; keep both columns in sync when adding or renaming a phase.

| Section narrative | Canonical id |
|---|---|
| Incident pre-check | `P_INCIDENT_PRECHECK` |
| P0 — intent | `P0_intent` |
| P0 — language | `P0_lang` |
| P0 — SEC email | `P0_sec_email` |
| P0 — palette | `P0_palette` |
| P0 — meta validation | `P0M_meta` |
| P0 — DB precheck | `P0_DB_PRECHECK` |
| P1 — parallel research | `P1_parallel_research` |
| P1.5 — edge insight | `P1_5_edge` |
| P2 — financial analysis + waterfall + Sankey (consolidated) | `P2_analysis` |
| P2.6 — macro QC peers | `P2_6_qc_macro` |
| P3 — Porter analysis | `P3_porter` |
| P3.5 — Porter QC peers | `P3_5_qc_porter` |
| P3.6 — QC resolution merge | `P3_6_qc_merge` |
| P3.7 — cross-validation | `P3_7_X_VALIDATE` |
| P5 — HTML report writer | `P5_html` |
| P5_gate — HTML structural gate | `P5_html_gate` |
| P5.5 — final report data validator | `P5_5_data_val` |
| P5.6 — Porter five-forces depth gate (plan v3) | `P5_6_porter_depth_gate` |
| P5.7 — red team report | `P5_7_RED_TEAM` |
| P6 — packaging + report validator | `P6_pkg` |
| P7 — logo production | `P7_logo` |
| P8 — card content production | `P8_content` |
| P8.5 — hardcode/logic audit | `P8_5_hardcode` |
| P9 — layout fill | `P9_layout` |
| P10 — Validator 1 | `P10_validator1` |
| P10.5 — Validator 2 | `P10_5_validator2` |
| P10.6 — Cards 1-5 analyst-content gate (plan v3) | `P10_6_voice_gate` |
| P10.7 — red team cards | `P10_7_RED_TEAM` |
| P11 — render six PNGs | `P11_render` |
| P12 — final audit (paying-customer gate) | `P12_final_audit` |
| Incident post-check | `P_INCIDENT_POSTCHECK` |
| DB index | `P_DB_INDEX` |

## The 33 phases at a glance

```
P_INCIDENT_PRECHECK ★
  → P0_intent → P0_lang → P0_sec_email → P0_palette → P0M_meta → P0_DB_PRECHECK
  → P1 parallel research (financial / macro / news, 3 subagents)
  → P1.5 edge insight
  → P2_analysis: financial analysis + prediction waterfall + Sankey payload (consolidated)
  → P2.6 macro QC peer A/B (parallel)
  → P3 Porter analysis
  → P3.5 Porter QC peer A/B (parallel)
  → P3.6 QC resolution merge
  → P3.7 cross-validation (history / peer / macro drift)
  → P5 HTML report writer (locked SHA256-pinned template)
  → P5_gate validate_report_html.py (line/section/JS/marker hard gate)
  → P5.5 final data validator (CFA-level)
  → P5.6 Porter depth gate (5 forces × 6 mandatory segments)
  → P5.7 RED TEAM ★ (numeric + narrative attackers, parallel; cap=1 loop)
  → P6 report validator + packaging profile
  → P7 logo production (≥840px wide; saved to output dir first)
  → P8 card content production
  → P8.5 hardcode/logic audit
  → P9 layout fill (char/pixel budgets)
  → P10 Validator 1 (tools/photo/validate_cards.py)
  → P10.5 Validator 2 (web fact-check; loops back to P10 ≤3×)
  → P10.6 voice gate (Cards 1-5 analyst substrate + worker notes)
  → P10.7 RED TEAM ★ (numeric + narrative attackers, parallel; pre-render; cap=1 loop)
  → P11 render 6 PNGs (2160×2700)
  → P12 final audit: reconcile + OCR + web third + DB cross  ★ paying-customer gate
  → P_INCIDENT_POSTCHECK ★ (re-read INCIDENTS.md; flagged blocks DB write)
  → P_DB_INDEX writes everything into db/equity_kb.sqlite
```

★ = non-skippable phases added on top of the original research+card pipeline. They are the harness's institutional-memory loop (`P_INCIDENT_PRECHECK` / `P_INCIDENT_POSTCHECK`) and its adversarial-review fire (`P5_7_RED_TEAM` / `P10_7_RED_TEAM`).

## P_INCIDENT_PRECHECK — institutional memory, read first

Runs **before** `P0_intent`. The orchestrator reads `INCIDENTS.md` end-to-end and writes one event to `meta/run.jsonl` per entry:

```json
{"phase": "P_INCIDENT_PRECHECK", "event": "incident_precheck.acknowledged", "incident_id": "I-001", "ack": "<one-line acknowledgement>"}
```

If any incident's `Phase` field matches a phase the run will execute, the orchestrator raises the bar on that surface (e.g. private-fund target → I-002 applies → expect harder scrutiny on locked-template adherence). When the matching phase fires, log a `phase_enter.incident_aware` event with the incident id.

This phase is short, cheap, and non-skippable. A run that did not pre-check is not deliverable.

## P0 block — gates and bootstrap

| Phase | Purpose |
|---|---|
| `P0_intent` | Resolve `{ticker, company, listing}`. See `references/p0_gates.md`. |
| `P0_lang` | `report_language ∈ {en, zh}`. Blocking. |
| `P0_sec_email` | SEC EDGAR `User-Agent` email if US-listed mode A. Blocking. |
| `P0_palette` | One of `{macaron, default, b, c}`. Blocking. |
| `P0M_meta` | Validate `workflow_meta.json` schema (`tools/research/validate_workflow_meta.py`). |
| `P0_DB_PRECHECK` | Lookups for prior financials, peer companies, fresh macro snapshot. Never blocks. See `references/cross_quarter.md`. |

**Hard floor for P0_lang / P0_sec_email / P0_palette:** the only allowed `meta/gates.json -> source` values are `user_response`, `USER.md sticky`, plus per-gate whitelist extras. Auto mode does not waive these — see `INCIDENTS.md` I-001.

## P1–P3.7 — research pipeline (ER)

Delegated to subagents under `skills_repo/er/agents/`. The orchestrator's job is to dispatch with the right inputs (e.g., pass `prior_financials_used.json` to `financial_data_collector` so it knows which periods are already covered).

- **P1** is parallel: `financial_data_collector` ‖ `macro_scanner` ‖ `news_researcher` (concurrency 3).
- **P2.6** and **P3.5** are parallel pairs of QC peer agents.
- **P3.6** merges QC verdicts. Apply the scoring math from `MEMORY.md` exactly: `weighted = 0.34·draft + 0.33·a + 0.33·b`; only change scores when `|weighted − draft| > 1.00`.
- **P3 Porter schema is plan v3**: one perspective, five forces, six mandatory segments per force (`qc_statement`, `data_anchor`, `mechanism`, `falsifier`, `primary_signal`, `look_ahead`). The old three-perspective shape is invalid and is blocked by `tools/research/validate_porter_analysis.py`.
- **P3.7** is `agents/cross_validator.md` + `tools/audit/db_cross_validate.py`. CRITICAL findings (self-history YoY mismatch >5pp; sector_macro_identity in mode A) block the next phase.

## P4–P5.7 — report writing + adversarial review (ER)

- **P4**: inject Sankey payload into `financial_analysis.json`.
- **P5**: extract the locked HTML skeleton via `tools/research/extract_template.py --lang <cn|en> --run-dir <run_dir> --sha256`. Delegate to `report_writer_{cn,en}.md`. **Never edit structure** — substitute `{{PLACEHOLDER}}` only. **Never** hand-write a simplified report regardless of target type — see `INCIDENTS.md` I-002.
- **P5_gate**: run `tools/research/validate_report_html.py --run-dir <run_dir> --lang <cn|en>`. It blocks simplified hand-written HTML by checking locked-template markers, six required sections, chart JS variables, minimum size/line count, and unresolved placeholders. Failure → discard the HTML and rerun P5 from the extracted skeleton.
- **P5.5**: `final_report_data_validator.md`. CRITICAL → loop back to P5 (cap 2). 0 CRITICAL → proceed.
- **P5.6**: `tools/research/validate_porter_analysis.py` writes `validation/porter_depth_gate.json`; failure loops back to P3 once and blocks P5.7.
- **P5.7 RED TEAM** ★: in parallel, delegate to `agents/attackers/red_team_numeric.md` and `agents/attackers/red_team_narrative.md`. Inputs at `meta/red_team/P5_7_RED_TEAM.input.json` cover the locked-template HTML, all upstream `research/*.json`, `cross_validation.json`, and the P5.5 validator output. Outputs at `validation/red_team_numeric_P5_7_RED_TEAM.json` and `validation/red_team_narrative_P5_7_RED_TEAM.json`. **Loop rule**: `summary.critical > 0` from either attacker → loop back to `P5_html` once with both attackers' challenge lists combined into a single revision request. Red-team retry cap = 1 (separate from the P5.5 retry cap of 2). A second critical from the red team after the loop → halt and surface to user. `warn` findings flow into `validation/QA_REPORT.md` at P12 but do not block. Distinct from QC peer agents: peers vote on agreement, attackers try to falsify.
- **P6**: `tools/research/packaging_check.py` + `report_validator.md`. `packaging_check.py` repeats the P5 HTML gate and stores `html_template_gate` in `structure_conformance.json`. Selects packaging profile from `strict_18_full_qc_secapi`, `strict_17_full_qc_no_secapi`, `strict_13_fast_no_qc_secapi`, `strict_12_fast_no_qc_no_secapi`. **No fabricated profile names**, **no fabricated statuses** (`pass | warn | critical` only) — see `INCIDENTS.md` I-002.

## P7–P10.7 — card pipeline + adversarial review (EP)

- **P7 logo**: hard rule — create the per-run `cards/` directory **first**, save `logo_official.png` into it, set `logo_asset_path` to that absolute path, only then proceed. Order matters; see `MEMORY.md`.
- **P8 content**: produces `cards/{stem}.card_slots.json` with all 17 top-level keys.
- **P8.5 hardcode audit**: every sentence has a company-specific anchor; no boilerplate.
- **P9 layout**: compress to char/pixel budgets — do not invent facts.
- **P10 Validator 1**: `python tools/photo/validate_cards.py`. Exit 0 required.
- **P10.5 Validator 2**: web fact-check. Any change to `card_slots.json` → rerun P10. Loop cap 3.
- **P10.6 voice gate**: deterministic Cards 1-5 analyst-content gate. Requires `cards/{stem}.card_slots_worker_notes.json` beside `card_slots.json`; failure blocks P10.7, P11, and DB index.
- **P10.7 RED TEAM** ★: **fires before P11 render — cards do not yet exist as PNGs.** Inputs at `meta/red_team/P10_7_RED_TEAM.input.json` cover the six `card_slots.json` files, the source `research/*.json`, `cards/validator{1,2}_report.json`, and the upstream P5.7 red-team outputs. The attacker contracts at this phase are **pre-render only**:
  - **Numeric attacker** — checks source-chain integrity, basis/units, tolerance compliance against source JSONs, and *render-budget realizability* (will the value as written fit the card's pixel/char budget without truncation; will rounding shift change reader meaning). It does **not** OCR anything — there is nothing rendered to OCR yet.
  - **Narrative attacker** — checks Porter-score directionality, hidden assumptions, missing counter-evidence, cross-card narrative coherence, and locked-template integrity carry-over.

  Outputs at `validation/red_team_numeric_P10_7_RED_TEAM.json` and `validation/red_team_narrative_P10_7_RED_TEAM.json`. **Loop rule**: `summary.critical > 0` from either attacker → loop back once to `P9_layout` (or `P8_content` if the defect is content-level rather than layout-level). Red-team retry cap = 1. A second critical = halt. Actual PNG OCR remains P12 layer 2.
- **P11 render**: 6 PNGs at 2160×2700, palette = `P0_palette`.

## P12 — paying-customer audit ★

Layers in order, via `agents/post_card_auditor.md`:

| Layer | Tool | Fail blocks? |
|---|---|---|
| 1. Numerical reconciliation | `tools/audit/reconcile_numbers.py` | yes |
| 2. OCR over the 6 PNGs | `tools/audit/ocr_cards.py` | yes |
| 3. Web third-check (top-3 numbers) | `tools/audit/web_third_check.py` | yes |
| 4. DB cross-validate | `tools/audit/db_cross_validate.py` | no (cold-start OK) |
| 5. User-Agent PII guard | `tools/audit/user_agent_pii.py` | yes |

Output: `validation/post_card_audit.json`, `validation/user_agent_pii.json`, and human-readable `validation/QA_REPORT.md` (the QA report aggregates P12 findings *and* the `warn`-level findings from P5.7 / P10.7). Never skip P12 unless the user types "skip audit / 跳过审计" in the same turn — and even then, log a `phase_skipped` event.

## P_INCIDENT_POSTCHECK — relapse detector

Runs **after** `P12_final_audit` and **before** `P_DB_INDEX`. The orchestrator re-reads `INCIDENTS.md` and confirms each entry's detection signal is green for this run. Output: `validation/incident_postcheck.json`:

```json
{
  "schema_version": 1,
  "incidents": [
    {"id": "I-001", "status": "pass", "evidence": "meta/gates.json"},
    {"id": "I-002", "status": "pass", "evidence": "research/structure_conformance.json"}
  ],
  "flagged": []
}
```

Any `flagged` entry **blocks** `P_DB_INDEX`. A relapse on a known incident is a release-blocking event; surface to the user with the exact incident id, the file path that contradicts it, and the rule that was violated. Do not write to DB.

## P_DB_INDEX — persistence

Runs **only** after BOTH:
- `P12_final_audit` reports `status: pass` (or warn-only), AND
- `P_INCIDENT_POSTCHECK` reports `flagged: []`.

`workflow_meta.json` declares `runs_after: P_INCIDENT_POSTCHECK` plus `requires: [P12_final_audit, P_INCIDENT_POSTCHECK]` so any runner that follows the contract enforces both gates, not just P12.

`python tools/db/index_run.py --run-dir <run_dir>` — single transaction, rollback on failure. Append-only `intelligence_signals` and `disclosure_quirks` survive partial-run admission with an analyst note.

## Failure caps (from MEMORY.md)

| Loop | Cap |
|---|---|
| ER subagent retry (same prompt) | 2 |
| `P10.5_validator2` ↔ `P10_validator1` | 3 |
| `P5.5` → `P5` (data validation fail → rewrite) | 2 |
| `P5_7_RED_TEAM` → `P5_html` (red-team critical) | 1 |
| `P10_7_RED_TEAM` → `P9_layout` / `P8_content` (red-team critical) | 1 |
| Subagent timeout retry | 1 (×1.5 multiplier); second timeout = phase failure |
| `P12` auto-retry | 0 (surface to user with run-dir path) |
| `P_INCIDENT_POSTCHECK` flagged retry | 0 (surface to user; relapse is release-blocking) |

## Resume semantics

If `meta/run.jsonl` already exists at start, you are in a resume context. Find the last `phase_exit` event; restart from the next phase. Inputs that already exist on disk and are schema-valid are reused — do not re-call subagents whose outputs are already present.

`P_INCIDENT_PRECHECK` events are **not** considered "complete on resume" by virtue of having fired in a prior session — re-fire them at every fresh session start, since `INCIDENTS.md` may have been updated between sessions.
