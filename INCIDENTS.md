---
schema_version: 1
description: Append-only log of past failure modes and the contract that prevents them. Frozen into meta/system_prompt.frozen.txt at session start, alongside MEMORY.md. Read PRE-RUN to avoid repeating; read POST-RUN (P_INCIDENT_POSTCHECK) before delivery as a final self-check.
---

# Anamnesis Research — INCIDENTS

This file is the project's institutional memory of failure. Each entry is a real incident that happened, the root cause, and the *load-bearing* rule that keeps it from happening again. Treat every entry as a hard constraint, not advice. If a new run hits a situation that smells like one of these, **stop and re-read the relevant entry before proceeding**.

**Format contract.** Append only. Never delete an incident — supersede it with a new entry that links back. Keep `id` monotonically increasing (`I-001`, `I-002`, …). Keep entries short: the *what / why / rule / detection* fields are load-bearing; everything else is optional context.

**Lifecycle fields (optional).** Most entries are `active` by default and have no explicit status line. When an entry's rule is replaced or invalidated by a newer entry (e.g. the underlying file was refactored away, or a stricter rule subsumes it), mark the old one with two extra bullets:

- `- **Status:** superseded`
- `- **Superseded by:** I-NNN`

The new entry should reciprocate with `- **Supersedes:** I-NNN`. `P_INCIDENT_POSTCHECK` skips superseded entries (records `status: skipped` with the supersedes link); their `Detection` clauses are no longer enforced. **Never delete a superseded entry** — the historical record is the audit trail. `tools/io/lint_incidents.py` verifies that cross-references resolve and that `Detection` clauses still point to files that exist on disk.

---

## I-001 — P0 interactive gate bypassed by inventing a default

- **Date observed:** seen multiple times across runs prior to 2026-05-02
- **Phase:** `P0_palette` (also possible at `P0_lang`, `P0_sec_email` — same failure mode)
- **What happened:** Orchestrator hit an interactive gate without a `USER.md` sticky and without an actual user reply, and instead of halting, it picked `palette = "default"` (or `report_language = "en"`) and proceeded. All six cards rendered with the wrong colour scheme; the entire EP pipeline had to re-run.
- **Root cause:** Conflating "auto mode is active" with "I am authorized to invent values for interactive gates." Interactive gates exist precisely *because* the answer is not derivable from the prompt or environment; auto mode does not waive that.
- **Rule (load-bearing):** For `P0_lang`, `P0_sec_email`, `P0_palette`, the only allowed `meta/gates.json -> source` values are `user_response`, `USER.md sticky`, plus the gate-specific extras whitelisted in each agent (`explicit_phrase` for language; `skipped` / `declined` for SEC email). Strings like `auto_mode_default`, `inferred_from_prompt`, `default`, `assumed`, or any free-form value not in the whitelist are P0 violations and the run is not deliverable. **Auto mode is not an override.** If neither a real user reply nor a sticky exists, halt and ask.
- **Detection:** `meta/gates.json` post-run review. Also enforced by `references/p0_gates.md` whitelist and the orchestrator's "halt and wait" wording in `agents/orchestrator.md`.
- **Related contract:** `MEMORY.md` §"P0 gates"; `SKILL.md` §"P0 gates"; `references/p0_gates.md`.

## I-002 — P5 locked HTML template skipped, simplified hand-written report emitted

- **Date observed:** seen on `RA_Capital_2026-05-01_*` (private investment manager) and at least one prior run
- **Phase:** `P5_html` (also implicates `P5_html_gate`, `P5_5_data_val`, `P6_pkg`)
- **What happened:** When issuer-level financial statements were unavailable (private fund / family office / non-public issuer), the report writer or orchestrator decided the locked template "did not apply," skipped `tools/research/extract_template.py`, hand-wrote a ~200-line summary HTML, fabricated a packaging profile (`institution_compat_no_secapi_no_cards` — not in the whitelist), and wrote `pass_with_scope_limitations` into `report_validation.txt`. Every layer of that chain was forbidden.
- **Root cause:** Misreading "data is thin" as "template doesn't apply." The locked template is **never** scope-conditional. Its job when data is thin is to *make the gaps legible*, not to disappear.
- **Rule (load-bearing):**
  - **Every** Anamnesis Research run — public, private, hedge fund, family office, government entity, anything — fills the same SHA256-pinned locked skeleton extracted via `tools/research/extract_template.py`. There is **no** institution-compatible / private-company / scope-limited / simplified bypass.
  - When issuer-level statements are unavailable, fill the locked sections with the best available proxies (AUM, strategy, top holdings, manager-level filings, peer macro) and label residual gaps inline.
  - `tools/research/validate_report_html.py` exit code is non-negotiable. Non-zero ⇒ discard HTML, rerun P5 from the extracted skeleton.
  - `report_validation.txt` top-line status is one of `pass | warn | critical`. `pass_with_scope_limitations`, `not_applicable`, `partial_pass` are fabrications.
  - `structure_conformance.json -> profile` must be one of the four `strict_*` profiles in `workflow_meta.json -> packaging_profiles`. Inventing profile names is a P6 violation.
- **Detection:** `tools/research/validate_report_html.py` (exit code), `tools/research/packaging_check.py` (profile/status validation), `P5_html_gate` retry loop. Now also enforced by `agents/attackers/red_team_numeric.md` and `agents/attackers/red_team_narrative.md` post-P5.5.
- **Related contract:** `MEMORY.md` §"Hard rules"; `SKILL.md` §"Hard floor"; `agents/orchestrator.md` §14; `references/phase_contract.md`.

## I-003 — SEC EDGAR User-Agent leaked to third-party fetches

- **Date observed:** 2026-05-03 (run `Intuit_2026-05-03_85a939ee`; behaviour pre-dates this run)
- **Phase:** any non-SEC outbound HTTP — observed paths include `P7_logo` (logo-production-agent), `news_intel` web fetches, and any P1/P2 public-page scrape (e.g. investor-relations URLs at `investors.intuit.com`).
- **What happened:** `meta/run.json` resolved `sec_user_agent = "EquityResearchSkill/1.0 (oliverun6@gmail.com)"` from `P0_sec_email`. Fetchers downstream of P0 reused that same string as the global outbound `User-Agent`, so the user's personal email was transmitted to third-party hosts (Intuit's investor site, logo CDNs, news sources) that have no need for a SEC-style contact and no SEC obligation to receive one. PII leak.
- **Root cause:** Only one User-Agent string is defined in run state (`sec_user_agent`), and the contract in `agents/sec_email_gate.md` describes it as the SEC EDGAR header without a sibling rule for non-SEC traffic. Fetchers default to the only UA they can find, which carries an email designed for SEC compliance.
- **Rule (load-bearing):**
  - `sec_user_agent` is for SEC EDGAR endpoints **only** (`https://*.sec.gov/`, `https://data.sec.gov/`, `https://efts.sec.gov/`).
  - All other outbound HTTP — logo fetches, IR pages, news, peer pages, image hosts — MUST use a generic `User-Agent` containing **no email and no other PII**, e.g. `EquityResearchSkill/1.0` (project URL OK; personal email never).
  - `meta/run.json` must carry both fields explicitly: `sec_user_agent` (with email) and `public_user_agent` (PII-free). Agents that fetch must pick the right one based on host, not fall back to whichever is set.
  - If `sec_email == "declined"`, `sec_user_agent` is `null` and SEC fetches are gated; `public_user_agent` is still set and used for everything else.
- **Detection:** `tools/audit/user_agent_pii.py` runs in P12 and writes `validation/user_agent_pii.json`. It scans `meta/run.jsonl` and captured request/fetch logs for occurrences of `sec_email` outside `*.sec.gov` hosts; fail if the email substring appears alongside a non-SEC URL, or if `public_user_agent` is missing / contains an email. Also covered by `P_INCIDENT_POSTCHECK` and red-team narrative review of P7 logo fetch logs.
- **Related contract:** `agents/sec_email_gate.md`; `agents/orchestrator.md` §P0_sec_email and §P7 logo; `references/p0_gates.md` §P0_sec_email; `MEMORY.md` §"P0 gates".

## I-004 — Porter Five `porter-text` slots filled with free narrative, QC-deliberation 5-li format skipped

- **Date observed:** 2026-05-03 (run `Wingstop_2026-05-03_38b52bfa/research/Wingstop_Research_CN.html`, lines 726 / 745 / 764 — company / industry / forward tabs)
- **Phase:** `P5_html` (report writer — `skills_repo/er/agents/report_writer_cn.md`); also surfaces at `P5_html_gate` and report_validator.
- **What happened:** All three `<div class="porter-text">` slots were populated as one short prose paragraph each (e.g. company tab: `品牌心智强、SKU聚焦降低门店复杂度；但对鸡翅大宗商品波动仍敏感，加盟商盈利能力与同店走弱会影响扩张节奏与特许收入韧性。`). No `<ul>`, no five `<li>` items, no "经QC合议，维持<力名>为N分。……" prefix per force. The structured five-bullet QC-deliberation format mandated by `references/report_style_guide_cn.md` was completely skipped. `structure_conformance.json` still passed because `porter_panel: 3` only counts the three tab containers, not their contents.
- **Root cause:** Writer treated `porter-text` as a free-form summary slot complementing the radar/score list, instead of as the structural carrier of the five-force narrative. The style guide describes the `<ul>` + 5 `<li>` + QC-prefix shape, but no automated gate enforced it post-render, so the simplification went undetected.
- **Rule (load-bearing):**
  - Each of `{{PORTER_COMPANY_TEXT}}`, `{{PORTER_INDUSTRY_TEXT}}`, `{{PORTER_FORWARD_TEXT}}` MUST be `<ul style="margin:0;padding-left:1.25em;">` with **exactly five `<li>`** items, in this fixed order: 供应商议价能力 → 买方议价能力 → 新进入者威胁 → 替代品威胁 → 行业竞争强度 (English reports: same five forces in identical order).
  - Each `<li>` MUST open with the QC-deliberation sentence:
    - **Maintained:** `经QC合议，维持<力名>为N分。……` or `经QC合议，决定将<力名>评分维持N分不变。……`
    - **Adjusted:** `经QC合议，决定将<力名>评分从X分调整为Y分。……` — allowed only when `qc_audit_trail.json` records that change.
  - Free-running summary paragraphs ("品牌心智强、SKU聚焦……") are a P5 violation regardless of how informative they read.
  - The wording per force MUST cite the force by name (no "本维度") and MUST agree with `qc_audit_trail.json` / `porter_analysis.qc_deliberation`.
- **Detection:** `tools/research/validate_report_html.py` is fail-closed for this shape: parse each `porter-text` div, require exactly one `<ul>`, count direct `<li>` == 5, verify each `<li>` starts with a whitelisted QC/no-QC sentence for the correct dimension at the correct index. `P5_html_gate` rejects HTML that fails this; `skills_repo/er/agents/report_validator.md` and `agents/attackers/red_team_narrative.md` also surface it as critical.
- **Related contract:** `skills_repo/er/references/report_style_guide_cn.md` §波特五力; `skills_repo/er/references/report_style_guide_en.md` (mirror EN rule); `skills_repo/er/agents/report_writer_cn.md` table row for `{{PORTER_COMPANY_TEXT}}`; `skills_repo/er/agents/report_writer_en.md` mirror; `skills_repo/er/agents/qc_resolution_merge.md`; `skills_repo/er/agents/report_validator.md` §"中文 Porter 句式".

---

## How this file is used

1. **Pre-run** (`P_INCIDENT_PRECHECK`, fires before `P0_intent`): the orchestrator reads this file end-to-end. For each incident, it ensures the corresponding rule is wired into the current plan. If a rule is unclear or the incident is novel-looking for the current target, the orchestrator notes it in `meta/run.jsonl` as `incident_precheck.acknowledged`.
2. **Post-run** (`P_INCIDENT_POSTCHECK`, fires after `P12_final_audit` and before `P_DB_INDEX`): the orchestrator re-reads this file and confirms each incident's detection signal is green for this run. Output: `validation/incident_postcheck.json` with one entry per incident (`status: pass | flagged`, plus evidence path).
3. **On new failure**: the user runs `/log-incident <one-line description>`. Claude pulls the latest `meta/run.jsonl`, the user's description, and any phase outputs; drafts a candidate entry; the user confirms; the entry is appended here as `I-NNN`.
