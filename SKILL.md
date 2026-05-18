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

`MEMORY.md` is the single source of truth for hard rules and is frozen verbatim into `meta/system_prompt.frozen.txt` at session start. Read it once at boot; do not re-paraphrase it inline. The substantive lists live there:

- `MEMORY.md` §"Never-skip phases" — `P_INCIDENT_PRECHECK`, `P5_7_RED_TEAM` / `P10_7_RED_TEAM`, `P12_final_audit`, `P_INCIDENT_POSTCHECK`, the four P0 gates.
- `MEMORY.md` §"Locked template invariants" — locked HTML skeleton, no simplified bypass, packaging-profile whitelist, status-string whitelist.
- `MEMORY.md` §"Hard rules" — logo, palette, EP fallback prohibition, DB PII, submodule policy, P12 numerical tolerances.
- `MEMORY.md` §"Orchestrator model gate" — Haiku/Instant refused at `anamnesis.py bootstrap` time.

Operational rules unique to your run-time behaviour (not duplicated in MEMORY.md):

- **Halt and ask** at the interactive P0 gates (`P0_lang` / `P0_sec_email` / `P0_palette`) if no user reply and no `USER.md` sticky exists. The `anamnesis.py advance` watchdog will refuse to move past these gates on a non-whitelisted source — do not work around it.
- **Never write to DB on failure** — `P_DB_INDEX` runs only when `P12_final_audit` is `pass`/`warn` AND `P_INCIDENT_POSTCHECK` reports `flagged: []`. The advance watchdog will not advance you past `P_INCIDENT_POSTCHECK` if either is failing.

## Commands you will run

| When | Command |
|---|---|
| First-time setup | `python anamnesis.py init` (builds `db/equity_kb.sqlite` from `db/schema/`) |
| Pre-flight | `pytest -q` (must be green) and `python tools/research/validate_workflow_meta.py` (validates Anamnesis Research's root contract; pass `--target er` to also check the ER submodule contract) |
| Bootstrap a run dir | `python anamnesis.py bootstrap --company <name> --date <YYYY-MM-DD> --orchestrator-model <your model id>` — **you must declare your own model id** (e.g. `claude-opus-4-7`, `claude-sonnet-4-6`). The CLI refuses Haiku/Instant families because they have repeatedly skipped P0 gates and red-team phases (see `INCIDENTS.md` I-001, I-002). Subagents you delegate to may still use Haiku — the gate only applies to the orchestrator. |
| Before every phase | `python anamnesis.py advance --run-dir <path>` — externalised watchdog. Tells you which phase to run next, and **refuses (exit 1)** if a predecessor's `produces[]` artifact is missing or an interactive P0 gate has a non-whitelisted `source`. Call this between phases instead of advancing from memory; the CLI is the floor against silent step-skipping. |
| P3 Porter schema gate | `python tools/research/validate_porter_analysis.py --run-dir <path>` (must pass before P3.5; reruns at P5 entry — `INCIDENTS.md` I-004) |
| P5 HTML gate | `python tools/research/validate_report_html.py --run-dir <path> --lang <cn\|en>` (must pass before P6/P7) |
| Delivery tree check | `python tools/io/validate_run_artifacts.py --run-dir <path>` (root must contain only standard subfolders; HTML lives in `research/`, cards in `cards/`) |
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
