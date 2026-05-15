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

## I-005 — Metrics table content and verdict cell not enforced by validator

- **Date observed:** 2026-05-05 (run `Li_Auto_2026-05-05_dd577c81/research/Li_Auto_Research_CN.html`, lines 610–617)
- **Phase:** `P5_html`; also implicates `P5_html_gate`.
- **What happened:** Section II metrics table rendered seven rows of absolute P&L amounts (`营业收入 / 毛利润 / 营业利润 / 净利润 / 稀释EPS / 经营现金流 / 自由现金流`) instead of the nine ratio rows mandated by `skills_repo/er/references/financial_metrics.md` §"Metrics table YoY movement verdict" (`毛利率 / 营业利润率 / 净利率 / ROE / ROA / 资产负债率 / 利息保障倍数 / 每股收益（EPS）/ 自由现金流利润率`). The 4th-column verdict cell was also unconstrained — emitter wrote `显著恶化` on every row without any check against the controlled vocabulary. `validate_report_html.py` exit 0; `report_validation.txt` `pass`.
- **Root cause:** `tools/research/validate_report_html.py` has zero assertions about the metrics table — no row-name whitelist, no `<td>`-count, no 4th-cell vocab. Same family as I-004 (validator silent on a slot's content shape), different slot.
- **Rule (load-bearing):**
  - The metrics table MUST contain **exactly nine `<tr>`** whose first `<td>` plain text matches the controlled ratio names per `financial_metrics.md` (CN: `毛利率`, `营业利润率`, `净利率`, `ROE`, `ROA`, `资产负债率`, `利息保障倍数`, `每股收益（EPS）` (alias `稀释EPS` accepted), `自由现金流利润率`; EN equivalents in the same file).
  - Each row MUST have **exactly four `<td>`** (指标 / 当年值 / 上年值 / 同比变动).
  - The 4th `<td>` plain text MUST match the controlled vocabulary — CN: `显著改善 | 改善 | 基本持平 | 恶化 | 显著恶化 | 权益缺口收窄 | 权益缺口扩大 | 期末股东权益为负 | 不适用`; EN: `Significantly improved | Improved | Stable | Deteriorated | Significantly deteriorated | Equity deficit narrowed | Equity deficit widened | Ending equity negative | N/A`.
- **Detection:** `tools/research/validate_report_html.py` parses `<table class="metrics-table"> <tbody>` and fails-closed if row count ≠ 9, any first-`<td>` is not in the ratio whitelist, any row has ≠ 4 `<td>`, or the 4th-`<td>` plain text is not in the controlled vocab. `P5_html_gate` rejects on failure.
- **Related contract:** `skills_repo/er/references/financial_metrics.md` §"Metrics table YoY movement verdict"; `skills_repo/er/agents/report_writer_cn.md` §`{{METRICS_ROWS}}`; `skills_repo/er/agents/report_writer_en.md` mirror; `tools/research/validate_report_html.py`.

---

## I-006 — Logo transparency contract misread as white-background requirement

- **Date observed:** 2026-05-07
- **Phase:** `P7_logo` / `P11_render`
- **What happened:** In run `Spirit_Aviation_Holdings_2026-05-07_9a1b9cdb`, the logo asset at `cards/logo/spirit_wordmark.png` was changed to an opaque white-background PNG after user feedback, and Card 1 / Card 5 were re-rendered with that white logo backing. This contradicted the EP logo-production contract, which requires a clean transparent logo asset and no white logo container.
- **Root cause:** The operator acted on ambiguous visual feedback without first re-reading `skills_repo/ep/agents/logo-production-agent.md` and `skills_repo/ep/SKILL.md` logo rules. The workflow conflated “logo visibility/contrast problem” with “add a white background,” even though the renderer is designed to paste transparent logo assets directly onto the card background.
- **Rule (load-bearing):** Before changing any logo background treatment, re-read the EP logo-production instructions. Final `logo_asset_path` must point to a clean transparent PNG/WEBP regenerated from an official logo reference unless the brand’s own mark intrinsically includes a filled shape. Do not add an opaque white canvas or white logo container to satisfy contrast concerns; solve contrast by using the correct transparent logo variant or by regenerating the mark cleanly.
- **Detection:** Add a logo audit that opens `card_slots.logo_asset_path`, verifies alpha transparency exists for non-filled canvas regions, and flags opaque white-canvas logo assets unless explicitly justified as part of the official mark. For rendered cards, sample Card 1 and Card 5 logo boxes to ensure the card background remains visible around the logo rather than a pasted white rectangle.
- **Related contract:** `skills_repo/ep/SKILL.md` §Logo convention / §2.5 Logo Production; `skills_repo/ep/agents/logo-production-agent.md` §Rules and §Quality Check; `skills_repo/ep/references/design-spec.md` §Logo Rules; `skills_repo/ep/scripts/generate_social_cards.py` `paste_logo()`, `card_1()`, `card_5()`.

---

## I-007 — Sector/theme research bypassed locked report and EP card format

- **Date observed:** 2026-05-09
- **Phase:** `P0_intent` / `P5_html` / `P6_pkg` / `P11_render`
- **What happened:** In run `Stablecoin_Cross_Border_Payments_2026-05-09_78540d26`, the user asked for an industry analysis. The run invented a custom `sector_pack` path, emitted a short non-locked HTML report, and generated custom cards outside the EP renderer. The initial report failed `validate_report_html.py` with missing locked-template markers, missing required sections, missing metrics table, and line count below 500. The initial cards also had visible large blank regions.
- **Root cause:** The orchestrator treated a sector/theme prompt as permission to bypass the formal Anamnesis report/card format instead of representing the industry as the analysis object inside the locked template. It also confused "sector topic" with "template not applicable."
- **Rule (load-bearing):** Sector or industry research must still use the locked report skeleton and official EP card renderer unless the user explicitly requests a non-Anamnesis custom artifact. If issuer-level financials do not exist, fill the required financial, prediction, Sankey, and card fields with clearly labelled industry proxy metrics; do not invent packaging profiles such as `sector_pack`; do not claim analogous incident checks as pass.
- **Detection:** `tools/research/validate_report_html.py` and `tools/research/packaging_check.py` must pass before card work. `tools/photo/validate_cards.py` must pass before render. Reject `structure_conformance.json -> profile` values outside the whitelisted strict profiles.
- **Related contract:** `SKILL.md` Hard floor; `agents/orchestrator.md` P5/P6/P11; `tools/research/validate_report_html.py`; `tools/research/packaging_check.py`; `tools/photo/validate_cards.py`; `INCIDENTS.md` I-002 and I-005.

---

## I-008 — Waterfall / Sankey schema and Porter QC-prefix mode not enforced by validator

- **Date observed:** 2026-05-15 (regression observed on runs `China_General_Nuclear_Power_2026-05-13_3fc946f7` and `NextEra_Energy_2026-05-13_2f081932`; minor latent flaws also visible in `Waste_Management_2026-05-14_e20146cf` and `ADM_2026-05-13_7e0175b5`)
- **Phase:** `P5_html` (writer — `skills_repo/er/agents/report_writer_cn.md`, mirror EN); detection sites `P5_html_gate`, `report_validator`.
- **What happened:** Three independent flaws in the rendered HTML, all undetected by the post-render validator:
    1. **Porter prefix mode-mismatch.** Both runs produced `qc_audit_trail.json` (full QC ran). The contract in `skills_repo/er/agents/qc_resolution_merge.md` §134 and `skills_repo/er/agents/report_writer_cn.md` table row for `{{PORTER_COMPANY_TEXT}}` says: when QC ran the `<li>` opening MUST be `"经QC合议，..."` (zh) / `"Dual-QC deliberation..."` (en); the `"基于初稿评分，..."` / `"Per draft scoring..."` opening is reserved exclusively for fast-runs with no `qc_audit_trail.json`. Both reports nonetheless used the no-QC prefix for all 15 `<li>`s (5 forces × 3 perspectives). User-visible result: every Porter bullet read "基于初稿评分，X 议价能力为 N 分。" — readers correctly perceived this as draft/template residue.
    2. **`waterfallData` schema mismatch.** The D3 renderer (`drawWaterfall()` in the locked template) expects `[{label, type, value, start, end}, …]` with `type ∈ {baseline, positive, negative, result}`. Both runs emitted `{label, type, value}` only (no `start`, no `end`) and used a fabricated `type` vocabulary `{start, delta, end}`. The renderer computes `Math.max(...waterfallData.flatMap(d => [d.start, d.end]))` → `NaN`; the y-scale collapses; **no bars render**. The labels (-4.1% / -1.3% / +4.0% / -1.4%) still appear because they're plotted via separate `<text>` elements, so the chart looks "halfway there" rather than blank.
    3. **Sankey conservation violation.** Both runs declared nodes that were never wired into any link (`费用`, `税前利润`, `税费` on CGN). For interior nodes that did receive flow, inflow ≠ outflow by > 1% (CGN: `毛利润` in 242 vs out 155 — 87 RMB-B silently dropped; NextEra: `毛利润` outflow > inflow by 2.7 USD-B — phantom money). `d3-sankey` either drops the orphans silently or renders disproportionate ribbons; downstream readers cannot reconcile the income statement.
- **Root cause:** `tools/research/validate_report_html.py` only verified the *presence* of the JS data variables (`waterfallData`, `sankeyActualData`, `sankeyForecastData`) and that the Porter `<li>` count was 5 — it did **not** validate their *schema*. Worse, the Porter prefix whitelist accepted **both** `"经QC合议..."` and `"基于初稿评分..."` openings unconditionally, regardless of whether `qc_audit_trail.json` existed on disk. The writer prompt was correct (it pairs each opening with the mode that justifies it), but the safety net let either through. This is the same family as I-004 / I-005: writer contract correct, validator silent on a slot's content shape.
- **Rule (load-bearing):**
    - **Porter prefix is mode-gated.** The validator MUST inspect for a sibling `qc_audit_trail.json` next to the HTML. If present (QC ran), every Porter `<li>` MUST open with `"经QC合议，..."` (zh) or `"Dual-QC deliberation..."` (en); the no-QC openings are forbidden. If absent (no-QC fast-run), every `<li>` MUST open with `"基于初稿评分，..."` / `"Per draft scoring..."`; inventing `"经QC合议..."` wording without a real trail is forbidden.
    - **`waterfallData` schema.** Each bar MUST be `{label: str, type: "baseline"|"positive"|"negative"|"result", value: number, start: number, end: number}`. Missing `start`/`end` or any unknown `type` is fail-closed.
    - **Sankey conservation.** Every declared node MUST appear in at least one link (orphans demoted to warnings — they render blank but don't break the chart). For every node that has both inflow and outflow, `|in − out| / max(in, out)` MUST be ≤ 1%. Larger imbalances indicate phantom or dropped flow and are fail-closed.
    - These checks are **not bypassable for "fast-run"**: schema + conservation are independent of QC presence.
- **Detection:** `tools/research/validate_report_html.py`:
    - `_validate_porter_texts(soup, *, qc_ran)` (new signature) — mode-gated via `validate_html_report(..., qc_audit_trail_path=None)`, which auto-detects `html_path.parent / "qc_audit_trail.json"` when not passed.
    - `_validate_waterfall_data(script_text)` — parses the `const waterfallData = [...]` literal via `_extract_js_literal` and enforces per-bar required fields + canonical `type` vocab.
    - `_validate_sankey_conservation(script_text, var_name)` — parses both `sankeyActualData` and `sankeyForecastData`, flags orphans as warnings, fails on > 1% flow imbalance.
    - Test coverage: `tests/test_validate_report_html.py::test_i007_porter_no_qc_prefix_when_qc_ran_fails`, `…_porter_qc_prefix_when_no_qc_trail_fails`, `…_waterfall_missing_start_end_fails`, `…_waterfall_good_passes`, `…_sankey_orphan_warns`, `…_sankey_conservation_violation_fails`.
- **Related contract:** `skills_repo/er/agents/report_writer_cn.md` §`{{PORTER_COMPANY_TEXT}}` / `{{WATERFALL_JS_DATA}}` / `{{SANKEY_ACTUAL_JS_DATA}}`; `skills_repo/er/agents/qc_resolution_merge.md` §134; `skills_repo/er/references/porter_framework.md` §QC vs no-QC openings; `skills_repo/er/references/report_style_guide_cn.md` §波特五力; `INCIDENTS.md` I-004 (this entry tightens I-004's detection from "must start with whitelisted sentence" to "must start with the *correct-mode* sentence").

---

## How this file is used

1. **Pre-run** (`P_INCIDENT_PRECHECK`, fires before `P0_intent`): the orchestrator reads this file end-to-end. For each incident, it ensures the corresponding rule is wired into the current plan. If a rule is unclear or the incident is novel-looking for the current target, the orchestrator notes it in `meta/run.jsonl` as `incident_precheck.acknowledged`.
2. **Post-run** (`P_INCIDENT_POSTCHECK`, fires after `P12_final_audit` and before `P_DB_INDEX`): the orchestrator re-reads this file and confirms each incident's detection signal is green for this run. Output: `validation/incident_postcheck.json` with one entry per incident (`status: pass | flagged`, plus evidence path).
3. **On new failure**: the user runs `/log-incident <one-line description>`. Claude pulls the latest `meta/run.jsonl`, the user's description, and any phase outputs; drafts a candidate entry; the user confirms; the entry is appended here as `I-NNN`.
