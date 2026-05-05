---
schema_version: 1
description: Principles inherited from Anthropic's "Harness design for long-running apps" and Skills design guidance, mapped to Anamnesis Research's concrete files. These are the foundation the Anamnesis Pattern (references/anamnesis_pattern.md) builds on top of. Read this when extending the harness; cross-check that any change still respects these inherited contracts.
---

# Inherited principles

This file collects the principles Anamnesis Research inherits from Anthropic's harness and skill design guidance. These are not Anamnesis Research's own contribution — they are the foundation the **Anamnesis Pattern** (`references/anamnesis_pattern.md`) builds on. The pattern is the project's distinctive contribution; these principles are the prerequisites.

If you are extending the harness, read both files: this one to know what existing contract you must not break, and `anamnesis_pattern.md` to know what active feedback loop you must preserve.

## 1 — Thin skill, heavy harness

A skill body should fit in the model's working memory at every turn. The harness can be arbitrarily large; the model only pulls it lazily.

| Concrete | Where it lives |
|---|---|
| Skill body (auto-loaded, ~500 lines max) | `SKILL.md` (root) and `.claude/skills/anamnesis-research/SKILL.md` (project mount) |
| Frozen invariants (read once, frozen) | `MEMORY.md`, `INCIDENTS.md` |
| Phase contract (machine-readable) | `workflow_meta.json` |
| Per-phase prose | `references/phase_contract.md` |
| Per-agent brief (lazy-loaded) | `agents/*.md`, `skills_repo/{er,ep}/agents/*.md` |
| Maintenance / architecture (humans) | `HARNESS.md`, this file |

The model never reads `HARNESS.md` or `references/maintenance.md` during a normal run — those are for the maintainer.

## 2 — Auditability beats agility

Every artifact a paying customer sees must be traceable backward to (1) a specific upstream JSON, (2) a frozen system prompt, (3) a pinned submodule SHA, (4) a pinned tool version. No self-improving loops, no DSPy, no GEPA.

| Concrete | Where |
|---|---|
| Frozen system prompt | `meta/system_prompt.frozen.txt` |
| Pinned submodule SHAs | `meta/submodule_shas.json` |
| Phase event log (append-only) | `meta/run.jsonl` |
| Pinned HTML template | SHA256 in `skills_repo/er/tests/test_extract_report_template.py` |
| Numerical reconciliation | `tools/audit/reconcile_numbers.py` against tolerances in `MEMORY.md` |

If a number on a card cannot be traced through this chain in under five clicks, the chain is broken.

## 3 — Tools registered, not freely executed

The model never `exec`s arbitrary Python. Every executable surface is a registered CLI under `tools/` with a documented argument set. Toolset whitelisting per agent (frontmatter `allowed_toolsets`) means a content-production agent literally cannot reach a DB write tool, even if it tries.

| Toolset | Owners |
|---|---|
| `research` | report writers, validators, edge_insight |
| `photo` | logo / content / layout / validator agents in EP |
| `audit` | reconcile_numbers, ocr_cards, web_third_check, db_cross_validate |
| `db` | queries (read), index_run (write — orchestrator only) |
| `web` | search-only; never raw fetch on private endpoints |
| `io` | run_dir bootstrap, log_incident digest |

See `references/subagent_toolsets.md` for the per-agent matrix.

## 4 — Resume is a property of the event log, not the model

Long-running runs (P0…P_DB_INDEX can take 30+ minutes) must be resumable after Ctrl-C, machine sleep, or a subagent timeout. Resume is implemented as: `meta/run.jsonl` is append-only; on start, walk to the last `phase_exit`; pick up from the next phase. Inputs already on disk are reused.

The model is **not** trusted to "remember where it was." The event log is the truth. The model reads the event log on resume and obeys it.

(The Anamnesis Pattern adds one wrinkle: `P_INCIDENT_PRECHECK` events do not count as "complete on resume". Re-fire them every fresh session so newly-curated incidents are picked up.)

## 5 — Memory tier split: invariants vs. failures vs. behaviour

Easy to confuse — the split:

| | Where | Read when | Editable by |
|---|---|---|---|
| Permanent invariants (tolerance numbers, schema rules, P0 gate definitions) | `MEMORY.md` | session start (frozen) | maintainer |
| Failure-derived rules (each entry traces to a real prior incident) | `INCIDENTS.md` | session start (frozen) | user via `/log-incident` only |
| Per-phase how-to | `agents/<role>.md` | when the phase fires (lazy) | maintainer |
| Per-tool contract | `tools/<area>/<name>.py` docstring + tests | when the tool is invoked | maintainer |

If you find yourself writing a rule into an agent brief that should apply to *every* phase, lift it to `MEMORY.md`. If you find yourself writing a `MEMORY.md` rule that traces back to a specific past failure, move it to `INCIDENTS.md` (and link from `MEMORY.md`).

## 6 — Compliance is enforcement, not policy

The harness has compliance properties (no PII to DB, locked template, SHA-pinned submodules, schema migrations). These are **enforced by tests**, not by policy:

- `tests/test_db_pii.py` — fails the suite if any TEXT column matches an email regex after a fixture run.
- `tests/test_extract_report_template.py` (in ER submodule) — fails if the locked HTML template's SHA256 doesn't match.
- `tests/test_db_migrations.py` — fails if a migration breaks cold-start or existing DB.
- `tools/research/validate_workflow_meta.py` — fails CI if `workflow_meta.json` violates the contract schema.

A rule that is not enforceable in code is **not a rule**, it is a wish. Don't add wishes to `MEMORY.md`.

## 7 — Observability is the artifact tree, not a separate system

Every phase produces files. The artifact tree under `output/{Company}_{Date}_{RunID}/` is the entire observability surface — there is no separate metrics service, no log shipper, no dashboard. If a question cannot be answered by reading files in the run dir, the harness has a gap and the answer is to add an artifact, not a service.

| Question | File |
|---|---|
| What phase are we in? | last entry in `meta/run.jsonl` |
| What did the user actually answer at the gates? | `meta/gates.json` |
| What was the system prompt at session start? | `meta/system_prompt.frozen.txt` |
| Did the audit pass? | `validation/post_card_audit.json` + `validation/QA_REPORT.md` |
| Did the red team find anything? | `validation/red_team_*.json` |
| Did we relapse on a known incident? | `validation/incident_postcheck.json` |
| What did we write to DB? | `db_export/rows_written.json` |

If you are tempted to add a metrics service, instead add a file to the run dir.

## 8 — Pre-prompt injection is a safety net, not a substitute

`.claude/hooks/inject_incidents.py` (UserPromptSubmit hook) injects an `INCIDENTS.md` reminder for research-style prompts. This is **not** a replacement for the model reading the file in `P_INCIDENT_PRECHECK`. It is a second-layer reminder for runs where the SKILL.md auto-trigger may have drifted.

Skill auto-trigger (description match) + project-mount discovery (`.claude/skills/anamnesis-research/SKILL.md`) + UserPromptSubmit hook = three independent paths to "the model reads the right files at the right time." Defence in depth.

---

## How this composes with the Anamnesis Pattern

| Inherited principle | Anamnesis-pattern dependency on it |
|---|---|
| 1 thin skill / heavy harness | INCIDENTS.md is heavy; only its boot pointer goes in SKILL.md |
| 2 auditability | the post-check verdict is itself an audit artifact (`validation/incident_postcheck.json`) |
| 3 registered tools | `/log-incident` is implemented as a registered tool (`tools/io/log_incident.py`), not a model-side LLM trick |
| 4 event-log resume | pre-check acks are events in the same log |
| 5 memory tier split | INCIDENTS is the file the pattern adds to the tier; without the split, where to put it would be unclear |
| 6 compliance by enforcement | the post-check is the enforcement mechanism for the failure-derived tier |
| 7 observability by artifact | every pattern phase produces a file under the run dir |
| 8 defence in depth on trigger | the hook injects INCIDENTS reminder; the pattern guarantees the read happens regardless |

You cannot implement the Anamnesis Pattern correctly while violating these foundations. They are not additive — they are the bedrock.

## When extending the harness, ask

1. Does the change need to be visible in *every* run, regardless of target? → likely belongs in `MEMORY.md` or a new phase in `workflow_meta.json`.
2. Does the change come from a real past incident? → `INCIDENTS.md` via `/log-incident`.
3. Is the change a per-phase how-to? → an agent brief under `agents/`.
4. Is the change a tool contract? → a CLI under `tools/`.
5. Is the change about *the methodology itself*? → `references/anamnesis_pattern.md`.
6. Is the change about *the inherited foundations*? → this file.
