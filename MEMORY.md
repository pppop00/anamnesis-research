---
schema_version: 1
description: Project-level invariants frozen into the system prompt at session start. Do not violate without an explicit user instruction in the same turn.
---

# Anamnesis Research — Project Memory

These rules are **load-bearing** and apply to every run. They are read once at session start and frozen into `meta/system_prompt.frozen.txt`. `INCIDENTS.md` is loaded alongside this file at the same moment and into the same frozen prompt — it carries the project's institutional memory of past failure modes (one entry per incident, with the load-bearing rule that prevents recurrence). Read both. The contracts compose: anything in `INCIDENTS.md` overrides nothing here, and nothing here waives anything in `INCIDENTS.md`.

## P0 gates — ordered, blocking, not skippable

1. **`P0_intent`** — resolve the user's prompt to a concrete `{ticker, company, listing}` triple. If ambiguous, ask **once**.
2. **`P0_lang`** — `report_language ∈ {en, zh}`. If not derivable from explicit phrases per `skills_repo/er/SKILL.md` §0A.1, ask the bilingual gate question and **stop until answered**. Do not infer from chat language alone.
3. **`P0_sec_email`** — only when `listing == US` AND `Mode A` (no PDFs uploaded) AND `USER.md` has no sticky decision. Ask for a real email or accept explicit decline. Reject obvious placeholders (`example.com`, `test@test`, `user@localhost`) with one re-ask. Persist `sec_user_agent` for SEC hosts and `public_user_agent` for all non-SEC HTTP; the latter must contain no email.
4. **`P0_palette`** — `palette ∈ {macaron, default, b, c}`. Ask before any EP work.

`USER.md` may pre-fill any of P0_lang / P0_sec_email / P0_palette as sticky preferences.

## Hard rules

- **Locked HTML template.** `skills_repo/er/agents/report_writer_{cn,en}.md` is SHA256-pinned. Phase P5 must extract the skeleton via `tools/research/extract_template.py` and substitute `{{PLACEHOLDER}}` only; never edit structure.
- **Logo save order.** P7 must (a) create the per-run output folder first, (b) save `logo_official.png` directly into it, (c) set `logo_asset_path` to the absolute path inside that folder, (d) only then proceed.
- **Palette consistency.** All six cards in one run must use the same `--palette`. The palette is **not** stored in `card_slots.json`; mismatched single-card re-renders cause silent header colour drift.
- **No fallback copy generation in EP.** `card_slots.json` must be complete before render; missing keys abort at load time.
- **Numerical reconciliation tolerance** (P12 layer 1):
  - margins / ratios / percentage points: ±0.5pp
  - currency amounts: ±0.5% relative
  - growth rates: ±0.5pp
  - prices, share counts, or any value tagged `"exact": true`: 0 tolerance

## QC scoring math (P3.6) — plan v3 single perspective

Porter is **one perspective × 5 forces × 6 mandatory segments per force** (schema v2). The 3-perspective layout (`company` / `industry` / `forward`) was removed by plan v3. Anything still emitting v1-shape `porter_analysis.json` is a defect — `tools/research/validate_porter_analysis.py` rejects v1 with a migration hint.

For each of the 5 forces: `weighted = 0.34·draft + 0.33·peer_a + 0.33·peer_b`.
- `delta = |weighted − draft|`
- If `delta > 1.00` → change score to `round(weighted)`, clamped to 1–5.
- If `delta ≤ 1.00` → keep draft, mark as "maintain X" (never fabricate "from X to Y").

Reasoning-only QC items must say "maintain X". Only QC items with an actual score change in the audit trail may say "from X to Y".

Per force, the QC peers and merge agent additionally audit all 6 segments:
`qc_statement` / `data_anchor` / `mechanism` / `falsifier` / `primary_signal` / `look_ahead`.
Missing or under-length segments block resolution; `P5_6_porter_depth_gate` then blocks `P5_7_RED_TEAM`.

## Porter score orientation

Threat / pressure scale (not attractiveness):
- 1–2 = low threat / green
- 3 = mixed / amber
- 4–5 = high threat / red

Intense rivalry → high red; minimal competition → low green. Reverse this and Validator and reviewers will catch it.

## Cards 1–5 voice governed by analyst-content gate (plan v3)

Card 1–5 prose (cover / industry-vs-consensus / one-number+comp / catalyst+falsifier / PM-soundbite) is governed by `validate_card1_5_analytical_content()` in `skills_repo/ep/scripts/generate_social_cards.py`, called at `P10_6_voice_gate`. The writer must emit two parallel JSONs:

- `<Company>_Research_<lang>.card_slots.json` (rendered prose, existing schema)
- `<Company>_Research_<lang>.card_slots_worker_notes.json` (hidden analyst fields per Card 1-5 slot: `data_anchor` with number + comp, `variant_view` ≥15 chars, plus ≥1 of `falsifier` / `primary_quote` / `catalyst_with_date`; authority slots `brand_statement` / `judgement_paragraph` require `primary_quote`)

Backstop banned phrases on Cards 1-5 only (Card 6 keeps 贴吧 tone): `说白了`, `X 不是 Y 而是 Z` template, `已不是核心叙事 / 已不重要 / 体现了 / 总而言之 / 综上 / 简单来说`, "关注 X 每天学一个公司" subscription-bait CTA.

`P10_6_voice_gate` blocks `P10_7_RED_TEAM`, `P11_render`, and `P_DB_INDEX` on failure. Red-team narrative §6.a attacks the substance of what passes the deterministic gate.

## Database write rules

- `P_DB_INDEX` runs only after `P12_final_audit` passes and `P_INCIDENT_POSTCHECK` reports `flagged: []`. Failed audits or flagged incident post-checks do not write to DB.
- All writes for one run are inside a single transaction; failure → rollback + `runs.run_status='failed'` + `db_export/index_error.json`.
- Append-only tables (`intelligence_signals`, `disclosure_quirks`) survive partial-run admission with an analyst note.
- Cross-validation queries (`db/queries.py`) filter on `runs.run_status='complete'` by default; partial rows exist for audit only.

## Privacy invariants

- SEC EDGAR email is **never** persisted. It lives only as a runtime arg to `tools/research/sec_edgar_fetch.py`.
- Before inserting any TEXT column, run `re.sub(r'\([^)]*@[^)]*\)', '()', value)` on `data_source` strings to strip embedded emails (User-Agent leak guard).
- `tests/test_db_pii.py` is a regression: any TEXT column matching `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` after a fixture run = test fails = release blocked.

## Failure caps

- Single ER subagent failure → 2 retries with same prompt, then halt.
- `P10.5_validator2` ↔ `P10_validator1` loop cap = 3.
- `P5.5` → `P5` (data validation fail → rewrite) cap = 2.
- Subagent timeouts: research 600s / photo 300s / QC 180s; first timeout retries at ×1.5; second timeout = phase failure.
- `P12` has no auto-retry — failures surface to the user with paths and a "which upstream phase to re-run" question.

## Incident loop (load-bearing)

- `P_INCIDENT_PRECHECK` runs **before** `P0_intent`. The orchestrator reads `INCIDENTS.md` end-to-end and writes one `incident_precheck.acknowledged` event to `meta/run.jsonl` per entry.
- `P5_7_RED_TEAM` and `P10_7_RED_TEAM` run two adversarial agents in parallel (`agents/attackers/red_team_numeric.md`, `red_team_narrative.md`). They are **not** QC peers — QC peers vote, attackers try to falsify. Critical findings loop the writer once (cap = 1 per phase); a second critical halts the run.
- `P_INCIDENT_POSTCHECK` runs **after** `P12_final_audit` and **before** `P_DB_INDEX`. The orchestrator re-reads `INCIDENTS.md` and confirms each entry's detection signal is green for this run. A flagged post-check blocks DB write — a relapse on a known incident is a release-blocking event.
- New failure modes are captured by the user via the `/log-incident` slash command (spec at `.claude/commands/log-incident.md`, backend at `tools/io/log_incident.py`). The model drafts an `INCIDENTS.md` entry; the user confirms; only then is it appended. Append-only — never delete or rewrite past entries; supersede with a new entry if needed.

## What this project does NOT do

- No skill self-improvement / DSPy / GEPA optimizer. Auditability beats agility.
- No code-execution sandbox. Everything is a registered tool; LLM cannot exec arbitrary Python.
- No multi-tenant routing. Single-user, local SQLite, single process.
- No streaming UI. CLI in, files out.
