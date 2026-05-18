# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> The workspace-level `../CLAUDE.md` routes between three sibling repos. This file covers what you need when working **inside** `anamnesis-research/` itself. The parent doc takes precedence on cross-repo questions (e.g. why you must not edit `skills_repo/{er,ep}/` copies — those are submodule-managed).

## Two contracts, one repo

This repo is a **harness-backed skill**:

- **Skill side** (LLM-facing, auto-triggered): `SKILL.md` is the thin entry. It tells the model *what to do* and dictates a strict **boot order** — read on every research-style invocation. See `SKILL.md` § "Boot order — read in this order, every session".
- **Harness side** (architecture/CLI/tests/DB/audit): `HARNESS.md` tells maintainers *how it runs*. The runtime brief that actually drives a research session is `agents/orchestrator.md`.

The machine-readable phase + gate contract is `workflow_meta.json` (35 phases). It is authoritative — anything in prose docs that contradicts it is a bug in the prose.

## Boot order for an Anamnesis Research run

When the user asks for company research (any of the trigger phrases in `SKILL.md`), do **not** answer with ad-hoc web search. The harness produces an auditable HTML report + 6 PNG cards + DB rows that ad-hoc answers cannot. Open in order:

1. `SKILL.md`
2. `MEMORY.md` — project invariants (load-bearing, frozen into `meta/system_prompt.frozen.txt`)
3. `INCIDENTS.md` — append-only failure log (load-bearing, frozen into the same file). Read end-to-end. Each entry encodes a real prior failure plus the rule that prevents it.
4. `USER.md` if present (gitignored, sticky preferences — skip if absent)
5. `workflow_meta.json`
6. `agents/orchestrator.md`

Stop after #6. Do **not** pre-load ER/EP submodule agents — open them lazily when you actually delegate, so token cost scales with the phase being executed.

## Commands

All Python commands assume the project root (`/Users/pppop/Desktop/Projects/Skills/anamnesis-research`) as cwd. `tests/conftest.py` injects the repo root into `sys.path` so `from tools.db import ...` works.

```bash
# First-time setup
git submodule update --init --recursive    # ER + EP submodules are SHA-pinned (.gitmodules)
pip install -r requirements.txt            # Pillow, BeautifulSoup, pytesseract, requests, pytest
brew install tesseract                     # macOS: required by P12 layer 2 (PNG OCR via pytesseract)
python anamnesis.py init                   # builds db/equity_kb.sqlite from db/schema/
cp USER.md.template USER.md                # optional sticky preferences

# Pre-flight (must be green before any production run)
pytest -q
python tools/research/validate_workflow_meta.py            # validates root contract
python tools/research/validate_workflow_meta.py --target er  # also validate ER submodule's contract

# Per-phase gates the orchestrator runs (or you run manually when debugging)
python tools/research/validate_porter_analysis.py --run-dir <path>      # before P3.5; reruns at P5 entry
python tools/research/validate_report_html.py --run-dir <path> --lang <cn|en>  # before P6/P7

# Top-level CLI (anamnesis.py — drives the deterministic phases; LLM phases run in the host)
python anamnesis.py bootstrap --company Apple --date 2026-04-30
python anamnesis.py precheck  --run-dir <path> --ticker AAPL [--sector ... --geography US --period FY2026Q2]
python anamnesis.py audit     --run-dir <path> [--lang cn|en] [--top-n 3] [--continue-on-fail]
python anamnesis.py index     --run-dir <path>     # only after P12 passes + post-check is clean
python anamnesis.py sector-report --type porter_heatmap --sector "Information Technology" --period FY2026Q2
python anamnesis.py status

# Single test files (examples)
pytest tests/test_db_pii.py -v             # PII regression — no email may persist to any TEXT column
pytest tests/test_user_agent_pii.py -v     # PII regression — SEC EDGAR email must not leak via UA strings
pytest tests/test_aggregate_p12.py -v      # P12 layer aggregation
pytest tests/test_reconcile_numbers.py -v  # numerical tolerance enforcement
pytest tests/test_db_migrations.py -v      # cold + existing DB
pytest tests/test_queries_cold_start.py    # DB precheck cold-start contract
pytest tests/test_incident_loop.py -v      # P_INCIDENT_PRE/POSTCHECK contract
pytest tests/test_skill_mount_parity.py -v # SKILL.md ≡ .claude/skills/.../SKILL.md
```

## Architecture — the big picture

### Two-loop closed feedback (the Anamnesis Pattern)

The repo's distinguishing methodology is **CFRV** — Curate → Freeze → Read → Verify — wrapped around every run:

- **Outer loop, across runs.** New failure modes are captured only via `/log-incident` (slash command spec at `.claude/commands/log-incident.md`, backend at `tools/io/log_incident.py`). The model drafts an `I-NNN` entry from the latest run digest; the user confirms before append. `INCIDENTS.md` is append-only — never edit by hand, never auto-append.
- **Inner loop, within one run.** `P_INCIDENT_PRECHECK` reads `INCIDENTS.md` end-to-end and emits `incident_precheck.acknowledged` events to `meta/run.jsonl` (one per entry; `incident_precheck.skipped` carries a `superseded_by` pointer for `Status: superseded` entries). `P_INCIDENT_POSTCHECK` re-checks every entry's detection signal after `P12_final_audit`. **A flagged post-check blocks `P_DB_INDEX`** — relapse on a known incident is release-blocking. The two loops connect at exactly two files: `INCIDENTS.md` (institutional log) and `meta/system_prompt.frozen.txt` (per-run snapshot).

### P0 gates — blocking, not skippable

Four gates run before any research work:

- **`P0_intent`** is a *resolution gate*. If the prompt is unambiguous, record `source: "prompt_unambiguous"` and proceed. Ask the user only when ambiguous (once), then `source: "user_response"`.
- **`P0_lang`**, **`P0_sec_email`** (only when `listing == US` AND mode A AND no `USER.md` sticky), and **`P0_palette`** are *interactive gates*. They must be satisfied by either a real user reply (`source: "user_response"`) or a sticky in `USER.md` (`source: "USER.md sticky"`). **Auto-mode does not waive them.** Inventing a default for an interactive gate is a P0 violation and will be caught in `meta/gates.json` review. See `references/p0_gates.md` for the full whitelist of allowed `source` values.

### Red-team attackers ≠ QC peers

Distinct jobs that must not be conflated:

| | QC peers (P2.6, P3.5, P10.5) | Red-team attackers (P5.7, P10.7) |
|---|---|---|
| Function | vote on agreement, weighted-average, surface deltas > tolerance | try to falsify, succeed on finding defects |
| Loop budget | high (cap = 3) | low (cap = 1 per phase) |
| Clean output is | suspicious | acceptable |

Critical findings from either attacker loop the writer once; a second critical halts the run. Briefs at `agents/attackers/red_team_numeric.md` and `red_team_narrative.md`.

### Composition by SHA-pinned submodule (not copy, not symlink)

- `skills_repo/er/` (Equity Research Skill, P1..P6) and `skills_repo/ep/` (Equity Photo Skill, P7..P11) are pinned by SHA in `.gitmodules`. Bumps are deliberate — never auto-update.
- The root `agents/` directory contains **only** Anamnesis-Research-owned briefs (`orchestrator`, the four gate agents, `intent_resolver`, `cross_validator`, `post_card_auditor`, and `attackers/`). It does **not** symlink or alias upstream agents — `workflow_meta.json` references them by real path (`skills_repo/er/agents/...`, `skills_repo/ep/agents/...`). The path is the audit surface.
- Each run records the resolved SHAs to `meta/submodule_shas.json`. After bumping a submodule, run `pytest -q` before committing.

### Run state and resume

Every run lives under `output/{Company}_{Date}_{RunID}/` (gitignored). Two files are load-bearing for resume:

- `meta/run.jsonl` — append-only event log. The orchestrator walks it to the last `phase_exit` and restarts from the next phase if the dir already exists.
- `meta/system_prompt.frozen.txt` — frozen `MEMORY.md` + `INCIDENTS.md` snapshot for audit replay.

Schema-valid outputs already on disk are reused — no double-billing the LLM.

### Three hook/skill surfaces under `.claude/` (with `.codex/` and `.cursor/` parallels)

1. `.claude/skills/anamnesis-research/SKILL.md` — project skill mount (auto-discovery). Must stay in description-sync with the root `SKILL.md` (test: `tests/test_skill_mount_parity.py`).
2. `.claude/settings.json` + `.claude/hooks/inject_incidents.py` — `UserPromptSubmit` hook. Injects an `INCIDENTS.md` reminder on research-style prompts (EN/ZH). The hook is a safety net, not a substitute — `P_INCIDENT_PRECHECK` must still run.
3. `.claude/commands/log-incident.md` — the `/log-incident` slash command (the Curate beat).

Parallel surfaces for other agents (the Curate beat must work from any of them, since users may /log-incident from whichever harness ran the failing session):

- `.codex/hooks.json` + `.codex/hooks/inject_incidents.py` — Codex prompt hook.
- `.cursor/commands/log-incident.md` — Cursor slash command.

Hook shell commands must resolve their script path independent of cwd (including inside submodules) — see `references/maintenance.md` § "Hook cwd-invariance".

## Hard floor (do not violate without explicit user override in the same turn)

The substantive hard-floor lists live in `MEMORY.md` — it's frozen verbatim into `meta/system_prompt.frozen.txt` at session start, so the model already has these rules in context. Pointers:

- `MEMORY.md` §"Never-skip phases" (`P_INCIDENT_PRECHECK`, `P5_7_RED_TEAM` / `P10_7_RED_TEAM`, `P12`, `P_INCIDENT_POSTCHECK`, the four P0 gates).
- `MEMORY.md` §"Locked template invariants" (locked HTML skeleton, no simplified bypass, packaging-profile whitelist, status-string whitelist — all from `INCIDENTS.md` I-002).
- `MEMORY.md` §"Hard rules" (logo, palette, EP no-fallback, DB PII / `tests/test_db_pii.py`, submodule policy, P12 tolerances).
- `MEMORY.md` §"Orchestrator model gate" (Haiku/Instant refused at `anamnesis.py bootstrap`).

Single-source-of-truth is intentional. Same rule in five files = drift risk; one canonical place + pointers = grep-once, change-once. To modify a hard-floor rule, edit `MEMORY.md` (and the relevant enforcer — e.g. `tools/audit/reconcile_numbers.py` for tolerances) in the same commit; everything else just points back here.

Maintainer-facing additions specific to this file:

- **Phase-advance watchdog discipline.** Runtime phase advancement is externalised via `python anamnesis.py advance --run-dir <X>` (`tools/io/advance.py`). If you change `workflow_meta.json` phase order, retry targets, or `blocking` flags, also re-run `pytest -q` and verify `GATE_SOURCE_WHITELIST` in `tools/io/advance.py` still matches `references/p0_gates.md`.
- **Hook substance lives in `tools/io/incident_trigger.py`.** Both `.claude/hooks/inject_incidents.py` and `.codex/hooks/inject_incidents.py` are thin adapters over this module. Edit the shared module, not the per-host shells.

## Numerical tolerances (P12 layer 1) — change in two places

If you change tolerance numbers, update **both** files in the same commit:

- `MEMORY.md` (the human-readable contract)
- `tools/audit/reconcile_numbers.py` (the enforcer)

Current tolerances: margins/ratios/pp: ±0.5pp; currency: ±0.5% relative; growth rates: ±0.5pp; prices/share counts/anything tagged `"exact": true`: 0.

## Where to read for full detail (lazy-load only when needed)

| Topic | File |
|---|---|
| Phase-by-phase narrative (35 phases) | `references/phase_contract.md` |
| Visual workflow diagram | `references/workflow_diagram.md` |
| Per-gate rules (whitelisted `source` values) | `references/p0_gates.md` |
| Subagent toolset whitelist, concurrency caps, timeouts | `references/subagent_toolsets.md` |
| Run-dir layout (`output/{Company}_{Date}_{RunID}/`) | `references/run_artifacts.md` |
| Cross-quarter / cross-company DB reuse | `references/cross_quarter.md` |
| Maintainer notes (template SHA, palette, schema, submodules, hook cwd) | `references/maintenance.md` |
| The Anamnesis Pattern (methodology, generalised) | `references/anamnesis_pattern.md` |
| Anthropic-derived foundations | `references/inherited_principles.md` |
| Harness/CLI/tests/DB architecture | `HARNESS.md` |
| Past failures + load-bearing rules they encode | `INCIDENTS.md` |
| Adversarial reviewers | `agents/attackers/red_team_{numeric,narrative}.md` |
