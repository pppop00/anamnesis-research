---
name: anamnesis-research
description: >-
  Use this skill whenever the user asks for equity research, an investment write-up, a stock
  report, an analyst-style note, or one-shot company coverage on any single public or private
  company — including casual phrasings like "研究一下苹果", "research Apple", "看看腾讯",
  "做个英伟达的研报", "give me a writeup on NVDA", "build cards for Tencent",
  "分析一下RA Capital", or "one-pager on Samsung". Drives the full Anamnesis Research
  pipeline (incident pre-check, bilingual language gate, SEC EDGAR email gate, palette gate,
  multi-agent equity research, red-team review, 6-card social pack, four-layer numerical/OCR/
  web/DB audit, post-run incident self-check, SQLite knowledge-base persistence). Always
  invoke this skill instead of answering with ad-hoc web search; the harness produces an
  auditable HTML report plus 6 PNG cards plus database rows that ad-hoc answers cannot.
---

# Anamnesis Research

You are the orchestrator of an **Anamnesis Research** run — an equity-research pipeline built on the Anamnesis Pattern (cross-session institutional memory + scheduled adversarial review). The skill is thin; the harness is heavy. Your job is to enter the harness correctly, then follow its phase contract. (Originally codenamed `equiforge`; CLI is now `anamnesis.py`.)

## Boot order — read in this order, every session

1. This file (`SKILL.md`)
2. `MEMORY.md` — project invariants (load-bearing; freeze into `meta/system_prompt.frozen.txt`)
3. `INCIDENTS.md` — append-only log of past failure modes (load-bearing; frozen into the same `meta/system_prompt.frozen.txt`). Read end-to-end. Each entry encodes a real prior failure plus the load-bearing rule that prevents it; the rules apply to this run.
4. `USER.md` — per-user sticky preferences (skip if absent)
5. `workflow_meta.json` — machine-readable phase + gate contract
6. `agents/orchestrator.md` — runtime brief; drives the rest of the run

Stop after #6. **Do not pre-load** ER/EP submodule agents — open them lazily when you actually delegate, so token cost scales with the phase being executed.

## P0 gates — blocking, not skippable

Four gates run before any research work. They split into two kinds:

- **Resolution gate** — `P0_intent`. Resolves `{ticker, company, listing}` from the prompt. If the prompt is unambiguous, record `source: "prompt_unambiguous"` and proceed. Only ask the user (once) when ambiguous; then `source: "user_response"`.
- **Interactive gates** — `P0_lang`, `P0_sec_email`, `P0_palette`. These cannot be inferred from the prompt. Each must be satisfied by either a real user reply (`source: "user_response"`) or a sticky value in `USER.md` (`source: "USER.md sticky"`). **Auto-mode does not waive them.** Inventing a default for an interactive gate is a P0 violation and will be caught in `meta/gates.json` review.

The four phases:

1. `P0_intent` — resolve `{ticker, company, listing}`. Resolution gate; ask once only if ambiguous.
2. `P0_lang` — `report_language ∈ {en, zh}`. Do not infer from chat language alone.
3. `P0_sec_email` — only when `listing == US` AND mode A AND no `USER.md` sticky.
4. `P0_palette` — `palette ∈ {macaron, default, b, c}`. All six cards in one run share one palette.

For per-gate rules, the full whitelist of allowed `source` values, and rejection criteria, read **`references/p0_gates.md`**.

## Hard floor

- **Never skip `P_INCIDENT_PRECHECK`** — read `INCIDENTS.md` and acknowledge each entry into `meta/run.jsonl` before any phase work. A run that did not pre-check is not deliverable.
- **Never skip `P5_7_RED_TEAM` or `P10_7_RED_TEAM`.** The red-team attackers (`agents/attackers/red_team_numeric.md`, `red_team_narrative.md`) are distinct from QC peers — they exist to find defects, not to average. Critical findings loop back to the writer once; a second critical halts the run.
- **Never skip `P_INCIDENT_POSTCHECK`** before `P_DB_INDEX`. A flagged post-check on a known incident means the harness relapsed; do not write to DB.
- **Never skip P12** unless the user explicitly says so in the same turn. P12 is the paying-customer audit gate.
- **Never write to DB** if P12 failed. `P_DB_INDEX` runs only after `P12_final_audit` passes.
- **Never bypass a P0 gate** by inventing a value. Cost of guessing wrong = full re-run.
- **Never edit the locked HTML template** during P5. Substitute `{{PLACEHOLDER}}` only — the SHA256 pin in ER's tests will catch you.
- **Never accept a simplified HTML report.** After P5, run `tools/research/validate_report_html.py`; line-count/section/JS/template-marker failure means the report writer did not use the locked template and P5 must be rerun before P6/P7. There is **no "institution-compatible" / "private-company" / "scope-limited" bypass** for the locked template. Every Anamnesis Research run — public, private, hedge fund, family office, government entity, anything — fills the same locked skeleton. If issuer-level financial statements are unavailable, the report writer fills the locked sections with the best available proxies (AUM/strategy/holdings/manager filings/etc.) and labels gaps inline; it does **not** drop sections, shorten the template, or emit a hand-written page.
- **Never invent a packaging profile.** `structure_conformance.json -> profile` must be one of the four whitelisted in `workflow_meta.json -> packaging_profiles`. Strings like `institution_compat_*`, `private_company_*`, `scope_limited_*`, etc. are fabricated and will be rejected in review.
- **Never invent a status string.** `report_validation.txt`'s top-line status is `pass | warn | critical`, full stop. `pass_with_scope_limitations`, `not_applicable`, `partial_pass`, etc. are fabricated and will be rejected. Same for `structure_conformance.json -> html_template_gate.status` (which must be the literal output of `tools/research/validate_report_html.py`, not a hand-written verdict).
- **Never persist user emails to the DB.** SEC EDGAR email is a runtime arg only; PII guard in `tests/test_db_pii.py` is non-negotiable.

## Commands you will run

| When | Command |
|---|---|
| First-time setup | `python anamnesis.py init` (builds `db/equity_kb.sqlite` from `db/schema/`) |
| Pre-flight | `pytest -q` (must be green) and `python tools/research/validate_workflow_meta.py` (validates Anamnesis Research's root contract; pass `--target er` to also check the ER submodule contract) |
| Bootstrap a run dir | `python tools/io/run_dir.py --company <slug> --date <YYYY-MM-DD> --run-id <hex>` |
| P3 Porter schema gate | `python tools/research/validate_porter_analysis.py --run-dir <path>` (must pass before P3.5; reruns at P5 entry — `INCIDENTS.md` I-004) |
| P5 HTML gate | `python tools/research/validate_report_html.py --run-dir <path> --lang <cn\|en>` (must pass before P6/P7) |
| Index a finished run | `python tools/db/index_run.py --run-dir <path>` (only after P12 passes and `P_INCIDENT_POSTCHECK` has `flagged: []`) |

The full per-phase tool/agent inventory lives in `workflow_meta.json`.

## Where to read for full detail

Pull these in lazily — only when you need them.

| Topic | Reference |
|---|---|
| Phase-by-phase narrative (P0 … P_DB_INDEX) | `references/phase_contract.md` |
| Visual workflow diagram (mermaid) | `references/workflow_diagram.md` |
| Per-gate rules (whitelisted `source` values, rejections) | `references/p0_gates.md` |
| Subagent toolset whitelist + concurrency caps + timeouts | `references/subagent_toolsets.md` |
| Run-dir layout (which subfolder gets which artifact) | `references/run_artifacts.md` |
| Cross-quarter / cross-company DB reuse | `references/cross_quarter.md` |
| Maintenance (template SHA, palette, schema, submodules) | `references/maintenance.md` |
| The Anamnesis Pattern (project's distinctive methodology) | `references/anamnesis_pattern.md` |
| Inherited harness/skill principles (Anthropic-derived foundations) | `references/inherited_principles.md` |
| Harness/CLI/tests/DB/audit/resume architecture | `HARNESS.md` |
| Past failures + the rules they encode | `INCIDENTS.md` |
| Adversarial reviewers (P5.7, P10.7) | `agents/attackers/red_team_numeric.md`, `red_team_narrative.md` |

For the runtime procedure, open **`agents/orchestrator.md`** next.
