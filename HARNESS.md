# Anamnesis Research — harness architecture

**Anamnesis Research** is delivered as a skill (entry: `SKILL.md`, slug: `anamnesis-research`) and maintained as a production harness (this file). It was originally codenamed `equiforge` during early development; that codename is now retained only as a historical `"codename"` field in `workflow_meta.json` and as the namespace prefix for the SQLite database (`db/equity_kb.sqlite`). Everything user-facing — CLI, docs, agent descriptions — uses the public name, **Anamnesis Research**.

The skill side tells the model *what to do*; the harness side defines *how the run is executed, persisted, audited, and resumed*. If you are running the pipeline, read `SKILL.md`. If you are extending the harness — adding a tool, changing the DB schema, wiring a new audit layer — start here.

## Two contracts, one repo

| Surface | Audience | Source of truth |
|---|---|---|
| Skill (auto-triggered) | LLM / Codex / Claude | `SKILL.md` (root, canonical) + `.claude/skills/anamnesis-research/SKILL.md` (project mount for auto-discovery) |
| Workflow contract | Both | `workflow_meta.json` (machine-readable phases) |
| Project invariants | LLM | `MEMORY.md` (frozen at session start) |
| Institutional failure log | LLM | `INCIDENTS.md` (frozen at session start, append-only) |
| User preferences | LLM | `USER.md` (gitignored, sticky) |
| Runtime brief | Orchestrator subagent | `agents/orchestrator.md` |
| Adversarial reviewers | Orchestrator subagent | `agents/attackers/red_team_{numeric,narrative}.md` |
| Reference docs | Lazy-loaded by the model | `references/*.md` |
| The Anamnesis Pattern (the methodology, generalised) | Maintainer + adopters of the pattern | `references/anamnesis_pattern.md` |
| Inherited principles (Anthropic harness/skill foundations) | Maintainer | `references/inherited_principles.md` |
| Pre-prompt safety net | Claude Code runtime | `.claude/settings.json` + `.claude/hooks/inject_incidents.py` |
| Slash commands (user-triggered) | Claude Code runtime | `.claude/commands/*.md` |
| Maintainer architecture | Humans (you, future you) | `HARNESS.md` (this file) |

The split exists because the skill anatomy spec only allows `name` + `description` in frontmatter, and SKILL bodies should stay under ~500 lines for progressive disclosure. Anything heavier (phase contract, subagent toolset whitelist, run-dir layout, maintenance notes) lives in `references/` and is read on demand.

## Repository layout

```
anamnesis-research/
├── SKILL.md                # ★ canonical skill entry — thin, strict, boot order
├── HARNESS.md              # this file — harness/architecture/CLI/tests
├── MEMORY.md               # project invariants, frozen at session start
├── INCIDENTS.md            # institutional failure log, frozen at session start, append-only
├── USER.md.template        # copy → USER.md (gitignored), sticky preferences
├── workflow_meta.json      # machine-readable phase + gate contract
├── anamnesis.py            # CLI entry (init, status, etc.)
│
├── .claude/                # Claude Code project-scoped configuration
│   ├── skills/anamnesis-research/SKILL.md   # project skill mount (auto-discovery)
│   ├── settings.json               # hooks block (UserPromptSubmit reminder)
│   ├── hooks/inject_incidents.py   # injects INCIDENTS reminder on research-style prompts
│   └── commands/log-incident.md    # /log-incident slash command spec
│
├── agents/                 # Anamnesis-Research-owned briefs ONLY (no symlinks to upstream)
│   ├── orchestrator.md
│   ├── intent_resolver.md
│   ├── language_gate.md / sec_email_gate.md / palette_gate.md
│   ├── post_card_auditor.md
│   ├── cross_validator.md
│   └── attackers/
│       ├── red_team_numeric.md     # numeric/source-chain falsifier
│       └── red_team_narrative.md   # narrative/Porter-direction falsifier
│   # ER/EP subagents live at skills_repo/{er,ep}/agents/*.md and are referenced
│   # by their real paths in workflow_meta.json — never aliased into agents/.
│
├── references/             # lazy-loaded skill docs
│   ├── phase_contract.md
│   ├── p0_gates.md
│   ├── subagent_toolsets.md
│   ├── run_artifacts.md
│   ├── cross_quarter.md
│   ├── maintenance.md
│   ├── anamnesis_pattern.md        # ★ the methodology, generalised
│   └── inherited_principles.md     # principles inherited from Anthropic harness/skill design
│
├── tools/                  # Python CLIs (one tool per concern)
│   ├── research/   ├── photo/   ├── audit/
│   └── db/         ├── web/     └── io/
│
├── skills_repo/            # git submodules (pinned by SHA)
│   ├── er/                 # Equity Research Skill (P1..P6)
│   └── ep/                 # Equity Photo Skill (P7..P11)
│
├── db/
│   ├── equity_kb.sqlite    # gitignored runtime
│   ├── schema/00X_*.sql    # numbered, additive migrations
│   ├── seed/               # optional fixture data
│   └── sector_reports/     # gitignored, regenerated on demand
│
├── tests/                  # pytest suite (CLI, migrations, PII, reconcile, etc.)
└── output/                 # gitignored runtime — one folder per run
    └── {Company}_{Date}_{RunID}/   # see references/run_artifacts.md
```

## CLI

```bash
# First-time setup
git clone <repo> anamnesis-research
cd anamnesis-research
git submodule update --init --recursive
pip install -r requirements.txt
python anamnesis.py init                # builds db/equity_kb.sqlite from db/schema/
cp USER.md.template USER.md             # then edit defaults

# Pre-flight
pytest -q                               # must be green before any production run
python tools/research/validate_workflow_meta.py

# Bootstrap a run dir (the orchestrator does this; CLI for debugging)
python tools/io/run_dir.py --company apple --date 2026-04-30 --run-id $(python -c 'import secrets;print(secrets.token_hex(4))')

# Index a finished run (P_DB_INDEX; orchestrator runs this after P12 passes)
python tools/db/index_run.py --run-dir output/Apple_2026-04-30_<RunID>/

# Sector reports (regenerate on demand)
python tools/db/sector_report.py --type porter_heatmap --sector "Information Technology" --period 2026Q2
```

## Run state

Every run has a stable directory under `output/{Company}_{Date}_{RunID}/`. The full subfolder layout is in `references/run_artifacts.md`. Two files are load-bearing for resume:

- `meta/run.jsonl` — append-only event log. `phase_enter` / `phase_exit` / `phase_failed` / `phase_skipped`.
- `meta/system_prompt.frozen.txt` — the resolved `MEMORY.md` + `USER.md` snapshot. Frozen at session start so audits can replay.

If `meta/run.jsonl` exists at start, the orchestrator is in resume context: it walks the log to the last `phase_exit` and restarts from the next phase. Schema-valid outputs already on disk are reused — no double-billing the LLM.

## Database

SQLite single-file at `db/equity_kb.sqlite`. Schema is migration-driven:

- Each change is a new `db/schema/00X_*.sql` with a `PRAGMA user_version` bump.
- Additive only — never drop a column or table. Rename via `ADD COLUMN` + dual-write window.
- `tests/test_db_migrations.py` verifies migrations apply cleanly to both cold and existing DBs.
- `tests/test_db_pii.py` regression-tests that no TEXT column persists an email after a fixture run (SEC EDGAR User-Agent leak guard).

Cross-quarter / cross-company reuse details are in `references/cross_quarter.md`.

## Audit

The four-layer P12 audit (numerical reconciliation, PNG OCR, web third-check, DB cross-validate) is the paying-customer gate. The orchestrator must not write to the DB if P12 fails.

- Numerical tolerances live in two places that must stay in sync: `MEMORY.md` (contract) and `tools/audit/reconcile_numbers.py` (enforcer). See `references/maintenance.md`.
- Web third-check uses an independent search path from Validator 2 — they must not share a session.
- DB cross-validate is fail-soft on cold start (`cold_start_ok: true` in `workflow_meta.json`).

## Tests

```bash
pytest -q                                 # full suite
pytest tests/test_db_pii.py -v            # PII regression
pytest tests/test_aggregate_p12.py -v     # P12 layer aggregation
pytest tests/test_reconcile_numbers.py -v # tolerance enforcement
pytest tests/test_db_migrations.py -v     # cold + existing DB
pytest tests/test_queries_cold_start.py   # DB precheck cold-start contract
```

`tests/conftest.py` sets up an in-memory SQLite for migration tests and a fixture run-dir for aggregation tests.

## Submodule policy

`skills_repo/er` and `skills_repo/ep` are pinned by SHA in `.gitmodules`. Bumps are deliberate, not automatic. Each run records the resolved SHAs to `meta/submodule_shas.json`. To bump:

```bash
cd skills_repo/er && git fetch && git checkout <sha> && cd ../..
git add skills_repo/er
pytest -q                                  # must be green
git commit -m "bump er submodule to <short-sha>"
```

If the upstream ER skill changes the locked HTML template, the upstream maintainer updates the SHA256 in `skills_repo/er/tests/test_extract_report_template.py`. Anamnesis Research picks it up on the next bump.

### Why root `agents/` is Anamnesis-Research-only (no symlinks to upstream)

`agents/` contains **only** Anamnesis Research's own orchestration briefs (`orchestrator`, `intent_resolver`, the four gate agents, `cross_validator`, `post_card_auditor`). It does **not** symlink or alias the upstream `skills_repo/er/agents/*` and `skills_repo/ep/agents/*` files. Why:

- **One canonical path per agent.** `meta/run.jsonl` and audit artifacts always log the real path. No "did this come from the symlink or the submodule?" ambiguity.
- **Stale-symlink risk on submodule bumps.** Renames upstream silently break aliases while `ls agents/` still looks healthy. Since nothing reads the aliases, the breakage stays invisible until a live phase fails.
- **Semantic separation.** `agents/` is for things Anamnesis Research owns and can change. `skills_repo/{er,ep}/agents/` is upstream territory we pin and consume.
- **Future-proof for `agents/openai.yaml`.** When we add UI/runtime metadata at `agents/openai.yaml`, the directory should not also contain dozens of upstream-owned briefs — the two concerns don't belong together.

`workflow_meta.json` and `agents/orchestrator.md` reference upstream agents by their **real submodule path** (`skills_repo/er/agents/financial_data_collector.md`, `skills_repo/ep/agents/logo-production-agent.md`, etc.). The path is the audit surface — keep it honest.

Symlinking back into `agents/` would only be justified if some harness or environment could only read from a single flat directory. Anamnesis Research has no such constraint, so we don't pay the cost.

## Incident loop

`INCIDENTS.md` is the project's append-only failure log. It is loaded into the system prompt at session start (frozen alongside `MEMORY.md` into `meta/system_prompt.frozen.txt`) and bracketed by two non-skippable phases:

- **`P_INCIDENT_PRECHECK`** runs before `P0_intent`. The orchestrator walks each `I-NNN` entry and writes one `incident_precheck.acknowledged` event to `meta/run.jsonl`. If any incident's `Phase` field matches a phase the run will execute, the orchestrator raises the bar on that surface (e.g. private-fund target → expect harder scrutiny on locked-template adherence per I-002).
- **`P_INCIDENT_POSTCHECK`** runs after `P12_final_audit` and before `P_DB_INDEX`. The orchestrator re-reads `INCIDENTS.md` and confirms each entry's detection signal is green. Output: `validation/incident_postcheck.json`. Any `flagged` entry blocks DB write — a relapse on a known incident is a release-blocking event.

New incidents are captured by the user via `/log-incident <one-line description>`. The slash command spec is at `.claude/commands/log-incident.md`; the backend is `tools/io/log_incident.py --collect`. The flow is:

1. User runs `/log-incident <description>`.
2. Backend collects digest of latest run (`run.jsonl` tail, `gates.json`, `run.json`, `structure_conformance.json`, `post_card_audit.json`, red-team outputs).
3. Model drafts a candidate `I-NNN` entry matching existing format.
4. User confirms before append. Append-only — no rewrites.

See `references/anamnesis_pattern.md` for the full pattern definition (the CFRV cycle, outer/inner loops, adversarial axis, applicability beyond equity research, and anti-patterns).

## Red-team attackers

`agents/attackers/red_team_numeric.md` and `red_team_narrative.md` are **adversarial** reviewers. They run in parallel at `P5_7_RED_TEAM` (after the data validator clears the report) and at `P10_7_RED_TEAM` (after Validator 2 clears the cards). They are distinct from QC peer agents:

| | QC peers | Red-team attackers |
|---|---|---|
| Function | Vote on agreement; weight-average; surface deltas > tolerance | Try to falsify; succeed on finding defects |
| Output | Score deltas → `qc_audit_trail.json` | Challenge list with severity → `validation/red_team_*.json` |
| Loop budget | High (P10↔P10.5 cap = 3) | Low (red-team retry cap = 1 per phase) |
| Where | P2.6, P3.5, P10.5 | P5.7, P10.7 |

Critical findings from either attacker loop the writer once with a combined revision request. A second critical halts the run. Warnings are surfaced in `QA_REPORT.md` but do not block.

## Hooks and slash commands

Project-scoped under `.claude/`. Three independent surfaces:

- **`.claude/skills/anamnesis-research/SKILL.md`** — auto-discovered skill mount. Lets Claude Code find the skill from any folder under this project. Canonical content is at the repo root `SKILL.md`; this file is a thin pointer with the same frontmatter so description-based auto-trigger works. Edit both when you change the description.
- **`.claude/settings.json` + `.claude/hooks/inject_incidents.py`** — UserPromptSubmit hook. On every prompt, the hook checks for research-style trigger phrases (EN/ZH); if matched, it injects an `INCIDENTS.md` reminder into the model's context as a safety net. No-op for non-research prompts.
- **`.claude/commands/log-incident.md`** — slash command. User types `/log-incident <description>` to draft a new INCIDENTS entry from the latest run.

The hook is a safety net, not a substitute. The orchestrator must still read `INCIDENTS.md` in `P_INCIDENT_PRECHECK` regardless of whether the hook fired.

## What this harness deliberately does not do

- **No skill self-improvement / DSPy / GEPA optimizer.** Auditability beats agility. Every numeric in a card is traceable to a source JSON to a frozen system prompt to a submodule SHA.
- **No code-execution sandbox.** Everything is a registered tool under `tools/`. The LLM cannot exec arbitrary Python.
- **No multi-tenant routing.** Single user, local SQLite, single process. Run two Anamnesis Research instances in two terminals if you want concurrency.
- **No streaming UI.** CLI in, files out. The output folder is the deliverable.

## When to update what

| You are changing… | Update |
|---|---|
| What the skill triggers on | `SKILL.md` description **and** `.claude/skills/anamnesis-research/SKILL.md` description (keep in sync) |
| Boot order / commands the model must run | `SKILL.md` body |
| What a phase produces / its tool | `workflow_meta.json` + `references/phase_contract.md` |
| Per-gate rules (whitelist, sticky source) | `references/p0_gates.md` |
| Subagent toolset scope | `references/subagent_toolsets.md` |
| Numerical tolerances | `MEMORY.md` AND `tools/audit/reconcile_numbers.py` (both, every time) |
| Adding a new failure rule that traces to a real incident | `INCIDENTS.md` via `/log-incident` (never edit by hand) |
| Adding a new permanent invariant unrelated to an incident | `MEMORY.md` + a test under `tests/` that enforces it |
| Adding a new red-team attack surface | `agents/attackers/red_team_*.md` (extend the existing two; don't create a third unless the surface is genuinely orthogonal) |
| Hook trigger phrases (when the UserPromptSubmit reminder fires) | `.claude/hooks/inject_incidents.py` `TRIGGER_PATTERNS` |
| Slash commands | `.claude/commands/<name>.md` (spec) + `tools/io/<name>.py` (backend, if needed) |
| DB schema | new `db/schema/00X_*.sql` + bump `PRAGMA user_version` + run migration tests |
| Locked HTML template | upstream `skills_repo/er` only — Anamnesis Research bumps the submodule |
| Architecture / CLI / dev workflow | this file (`HARNESS.md`) |
| The Anamnesis Pattern (methodology, generalised) | `references/anamnesis_pattern.md` |
| Inherited principles (Anthropic harness/skill foundations) | `references/inherited_principles.md` |
