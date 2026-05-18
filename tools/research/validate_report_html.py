"""Validate that P5 produced a locked-template ER HTML report, not a simplified page.

This is a deterministic P5/P6 gate. It does not judge prose quality; it catches
the high-cost failure mode where the host model writes a short custom HTML page
instead of extracting the locked report skeleton and replacing placeholders.

Usage:
    python tools/research/validate_report_html.py --run-dir <path> --lang cn
    python tools/research/validate_report_html.py --html <report.html> --skeleton <_locked_cn_skeleton.html>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


REQUIRED_SECTION_IDS = (
    "section-summary",
    "section-financials",
    "section-prediction",
    "section-sankey",
    "section-porter",
    "section-appendix",
)

REQUIRED_MARKERS = (
    "CANONICAL CSS",
    "LOCKED JAVASCRIPT",
    "DATA VARIABLES",
    "drawWaterfall",
    "drawSankey",
    "drawPorterBars",
    "sankeyActualData",
    "porterScores",
    "waterfallData",
)

# Plan v3 Phase A removed the forecast Sankey (single-panel actual only) and
# the 3-tab Porter radar (single bar chart + 5 force-block analysis). The
# substrings below previously appeared in the locked template and were
# positive markers in earlier validator versions; their presence now indicates
# the writer copied stale skeleton content or used a pre-v3 template.
FORBIDDEN_MARKERS = (
    # Section IV — forecast sankey is gone
    'id="chart-sankey-forecast"',
    "sankeyForecastData",
    "{{SANKEY_FORECAST_JS_DATA}}",
    "{{SANKEY_YEAR_FORECAST}}",
    'id="sankey-tabs"',
    # Section V — radar tabs are gone
    'id="chart-radar-company"',
    'id="chart-radar-industry"',
    'id="chart-radar-forward"',
    "{{PORTER_COMPANY_TEXT}}",
    "{{PORTER_INDUSTRY_TEXT}}",
    "{{PORTER_FORWARD_TEXT}}",
    'id="porter-tabs"',
    'class="porter-tabs"',
    'class="porter-radar"',
)

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")

# Plan v3 Phase A: Porter is now a single bar chart + 5 force blocks (no tabs).
PORTER_FORCES = (
    ("供应商议价能力", "supplier power"),
    ("买方议价能力", "buyer power"),
    ("新进入者威胁", "threat of new entrants"),
    ("替代品威胁", "threat of substitutes"),
    ("行业内竞争", "competitive rivalry"),
)
PORTER_FORCE_ZH_ALIASES = {
    "供应商议价能力": ("供应商议价能力", "供应商议价"),
    "买方议价能力": ("买方议价能力", "买方议价", "买家议价能力"),
    "新进入者威胁": ("新进入者威胁",),
    "替代品威胁": ("替代品威胁",),
    "行业内竞争": ("行业内竞争", "行业竞争强度", "行业竞争"),
}

# Each <div class="porter-force-block"> must contain a <p> with every one of
# these classes (in any order).
PORTER_FORCE_BLOCK_REQUIRED_CLASSES = (
    "porter-rating-statement",
    "porter-anchor",
    "porter-mechanism",
    "porter-falsifier",
    "porter-signal",
    "porter-lookahead",
)
# `<h3>...— N/5</h3>` (em dash or hyphen, EN/CN both supported)
_PORTER_H3_RATING_RE = re.compile(r"[—\-]\s*([1-5])\s*/\s*5\s*$")

# I-005: metrics table — nine controlled ratio categories.
METRIC_ROW_ALIASES: dict[str, tuple[str, ...]] = {
    "gross_margin": ("毛利率", "Gross Margin", "Gross margin"),
    "operating_margin": ("营业利润率", "Operating Margin", "Operating margin"),
    "net_margin": ("净利率", "Net Margin", "Net margin"),
    "roe": ("ROE",),
    "roa": ("ROA",),
    "debt_to_asset": (
        "资产负债率",
        "Debt-to-asset ratio",
        "Debt-to-Asset Ratio",
        "Asset-liability ratio",
        "Asset-Liability Ratio",
    ),
    "interest_coverage": (
        "利息保障倍数",
        "Interest Coverage",
        "Interest coverage",
        "Interest Coverage Ratio",
    ),
    "eps": (
        "每股收益（EPS）",
        "每股收益(EPS)",
        "稀释EPS",
        "EPS",
        "Diluted EPS",
        "Earnings per share",
        "Earnings per Share",
    ),
    "fcf_margin": (
        "自由现金流利润率",
        "FCF Margin",
        "FCF margin",
        "Free Cash Flow Margin",
        "Free cash flow margin",
    ),
}

_METRIC_NAME_TO_KEY: dict[str, str] = {
    name: key for key, names in METRIC_ROW_ALIASES.items() for name in names
}

METRIC_VERDICT_VOCAB: frozenset[str] = frozenset({
    "显著改善", "改善", "基本持平", "恶化", "显著恶化",
    "权益缺口收窄", "权益缺口扩大", "期末股东权益为负", "不适用",
    "Significantly improved", "Improved", "Stable", "Deteriorated",
    "Significantly deteriorated", "Equity deficit narrowed",
    "Equity deficit widened", "Ending equity negative", "N/A",
})


def _find_single_report(research_dir: Path) -> Path | None:
    candidates = sorted(
        p for p in research_dir.glob("*_Research_*.html")
        if not p.name.startswith("_locked_")
    )
    if len(candidates) != 1:
        return None
    return candidates[0]


def _find_skeleton(research_dir: Path, lang: str) -> Path | None:
    candidates = [
        research_dir / f"_locked_{lang}_skeleton.html",
        research_dir / "_locked_skeleton.html",
        research_dir / "_locked_cn_skeleton.html" if lang == "zh" else research_dir / "_locked_en_skeleton.html",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _count(soup: BeautifulSoup, selector: str) -> int:
    return len(soup.select(selector))


def _porter_li_start_ok(text: str, zh_force: str, en_force: str, *, qc_ran: bool) -> bool:
    """Per I-004 / I-007 (anamnesis-research): the allowed opening for each
    Porter <li> depends on whether QC actually ran (i.e. whether
    `qc_audit_trail.json` exists alongside the report).

      qc_ran=True  → only "经QC合议..." / "Dual-QC deliberation..." accepted.
                     "基于初稿评分..." / "Per draft scoring..." is forbidden
                     here because the writer must rewrite to QC wording in
                     `qc_resolution_merge.md` at Phase 3.6.
      qc_ran=False → only "基于初稿评分..." / "Per draft scoring..." accepted.
                     Inventing "经QC合议..." wording without a real QC trail
                     is forbidden.
    """
    text = " ".join(text.split())
    if qc_ran:
        zh_patterns = (
            rf"^经QC合议，维持{re.escape(zh_force)}为[1-5]分。",
            rf"^经QC合议，决定将{re.escape(zh_force)}评分维持[1-5]分不变。",
            rf"^经QC合议，决定将{re.escape(zh_force)}评分从[1-5]分调整为[1-5]分。",
        )
    else:
        zh_patterns = (
            rf"^基于初稿评分，{re.escape(zh_force)}为[1-5]分。",
        )
    if any(re.search(pattern, text) for pattern in zh_patterns):
        return True

    lower = text.lower()
    force = re.escape(en_force.lower())
    if qc_ran:
        en_patterns = (
            rf"^dual-qc deliberation maintained (the )?{force} at [1-5]/5\.",
            rf"^after dual-qc deliberation, (the )?{force} remains [1-5]/5\.",
            rf"^dual-qc deliberation adjusted (the )?{force} score from [1-5] to [1-5]/5\.",
        )
    else:
        en_patterns = (
            rf"^per draft scoring, (the )?{force} stands at [1-5]/5\.",
        )
    return any(re.search(pattern, lower) for pattern in en_patterns)


def _validate_porter_force_blocks(soup: BeautifulSoup, *, qc_ran: bool) -> tuple[list[str], list[str]]:
    """Plan v3: Section V is now a single bar chart + 5 `<div class="porter-force-block">` elements.

    Each block must contain:
      - one `<h3>` ending with `— N/5` (rating 1..5)
      - six `<p>` tags carrying the classes in PORTER_FORCE_BLOCK_REQUIRED_CLASSES
        (order is conventional but unenforced here — every class must appear once)

    Mode handling:
      - If the raw `{{PORTER_ANALYSIS_BLOCKS}}` placeholder is present, treat
        the document as a raw template and skip the structural check (the
        unreplaced-placeholder gate elsewhere already reports the unfilled
        state). The QC-prefix check is still applied to whatever blocks DO
        appear (in case the writer leaked both).
      - Otherwise (rendered mode): require exactly 5 force-block divs.

    The QC-prefix rule from I-004 / I-007 still applies to the first <p>
    (`porter-rating-statement`).
    """
    errors: list[str] = []
    warnings: list[str] = []
    section = soup.select_one("#section-porter")
    if section is None:
        # caller will already report the missing section; nothing more here.
        return errors, warnings

    has_placeholder = "{{PORTER_ANALYSIS_BLOCKS}}" in section.decode_contents()
    blocks = section.select(".porter-force-block")

    if has_placeholder and not blocks:
        # raw template — defer to the unreplaced-placeholder gate.
        return errors, warnings

    if len(blocks) != 5:
        errors.append(
            f"#section-porter must contain exactly 5 <div class=\"porter-force-block\"> "
            f"(plan v3 Phase A); got {len(blocks)}"
        )
        # still try to validate whatever's present so we surface multiple
        # issues per run instead of one-at-a-time.

    mode_label = "QC mode" if qc_ran else "no-QC mode"
    expected_zh = "经QC合议..." if qc_ran else "基于初稿评分..."

    for idx, block in enumerate(blocks, start=1):
        h3 = block.find("h3")
        if h3 is None:
            errors.append(f"porter-force-block[{idx}] is missing its <h3> (plan v3)")
        else:
            h3_text = " ".join(h3.get_text(" ", strip=True).split())
            if not _PORTER_H3_RATING_RE.search(h3_text):
                errors.append(
                    f"porter-force-block[{idx}] <h3> '{h3_text}' must end with "
                    f"'— N/5' where N is 1-5 (plan v3)"
                )

        paras = block.find_all("p", recursive=False)
        # Some writers nest <strong> inside <p>; recursive=False is enough
        # since the spec requires direct <p> children. If a writer puts <p>s
        # under another wrapper, the class lookup below will still surface
        # them — but we lose strict ordering. Accept either.
        if not paras:
            paras = block.find_all("p")

        class_to_para: dict[str, list] = {}
        for p in paras:
            for cls in p.get("class", []) or []:
                class_to_para.setdefault(cls, []).append(p)

        missing = [c for c in PORTER_FORCE_BLOCK_REQUIRED_CLASSES if c not in class_to_para]
        if missing:
            errors.append(
                f"porter-force-block[{idx}] missing required <p> classes: {missing} "
                "(plan v3 — each block needs porter-rating-statement / -anchor / "
                "-mechanism / -falsifier / -signal / -lookahead)"
            )

        # QC-prefix rule on the rating statement (best-effort; per-force-name
        # matching is loose because the <h3> already encodes the force).
        rating_ps = class_to_para.get("porter-rating-statement", [])
        if rating_ps:
            rating_text = " ".join(rating_ps[0].get_text(" ", strip=True).split())
            if rating_text and not _porter_rating_statement_ok(rating_text, qc_ran=qc_ran):
                errors.append(
                    f"porter-force-block[{idx}] .porter-rating-statement must "
                    f"start with the {mode_label} sentence (expected zh-prefix "
                    f"'{expected_zh}', cf. I-004 / I-007); got: "
                    f"{rating_text[:80]!r}"
                )

    return errors, warnings


def _porter_rating_statement_ok(text: str, *, qc_ran: bool) -> bool:
    """Loose variant of `_porter_li_start_ok` that does not pin the force
    name (the <h3> already carries it). Per I-004 / I-007:
      qc_ran=True  → "经QC合议..." / "Dual-QC deliberation..."
      qc_ran=False → "基于初稿评分..." / "Per draft scoring..."
    """
    text = " ".join(text.split())
    if qc_ran:
        zh_patterns = (
            r"^经QC合议[，,]",
        )
        en_patterns = (
            r"^dual-qc deliberation\b",
            r"^after dual-qc deliberation",
        )
    else:
        zh_patterns = (
            r"^基于初稿评分[，,]",
        )
        en_patterns = (
            r"^per draft scoring",
        )
    if any(re.search(p, text) for p in zh_patterns):
        return True
    lower = text.lower()
    return any(re.search(p, lower) for p in en_patterns)


def _validate_metrics_table(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    """I-005: Section II metrics table content and verdict cell vocabulary."""
    errors: list[str] = []
    warnings: list[str] = []

    table = soup.select_one("table.metrics-table")
    if table is None:
        errors.append("missing <table class='metrics-table'> (I-005)")
        return errors, warnings

    tbody = table.find("tbody")
    rows = tbody.find_all("tr", recursive=False) if tbody else []
    if not rows:
        errors.append("metrics-table tbody contains no <tr> (I-005)")
        return errors, warnings

    if len(rows) != 9:
        errors.append(
            f"metrics-table must contain exactly 9 <tr>; got {len(rows)} (I-005)"
        )

    seen_keys: set[str] = set()
    for idx, tr in enumerate(rows, start=1):
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 4:
            errors.append(
                f"metrics-table row[{idx}] must have exactly 4 <td>; got {len(tds)} (I-005)"
            )
            continue

        name = tds[0].get_text(" ", strip=True)
        key = _METRIC_NAME_TO_KEY.get(name)
        if key is None:
            errors.append(
                f"metrics-table row[{idx}] first <td> '{name}' is not in the controlled ratio whitelist; "
                "expected one of 毛利率/营业利润率/净利率/ROE/ROA/资产负债率/利息保障倍数/每股收益（EPS）/自由现金流利润率 "
                "or English equivalents (I-005)"
            )
        elif key in seen_keys:
            errors.append(
                f"metrics-table row[{idx}] '{name}' duplicates ratio category '{key}' (I-005)"
            )
        else:
            seen_keys.add(key)

        verdict = tds[3].get_text(" ", strip=True)
        if verdict not in METRIC_VERDICT_VOCAB:
            errors.append(
                f"metrics-table row[{idx}] 4th <td> '{verdict}' is not in the controlled verdict vocabulary "
                "(I-005). Expected: 显著改善/改善/基本持平/恶化/显著恶化/权益缺口收窄/权益缺口扩大/期末股东权益为负/不适用 "
                "or English equivalents."
            )

    if len(rows) == 9 and len(seen_keys) < 9:
        missing = sorted(set(METRIC_ROW_ALIASES.keys()) - seen_keys)
        errors.append(
            f"metrics-table missing required ratio categories: {missing} (I-005)"
        )

    return errors, warnings


WATERFALL_VALID_TYPES = frozenset({"baseline", "positive", "negative", "result"})


def _extract_js_literal(script_text: str, var_name: str) -> Any | None:
    """Extract `const <var_name> = <literal>;` and return parsed JSON.

    The locked template emits JS object/array literals that happen to be
    valid JSON (quoted keys). Anything else (function calls, computed
    values, references) returns None and the per-shape validator emits a
    targeted error.
    """
    pattern = rf"\bconst\s+{re.escape(var_name)}\s*=\s*([\[\{{].*?)\s*;\s*$"
    matches = re.findall(pattern, script_text, flags=re.DOTALL | re.MULTILINE)
    if not matches:
        return None
    # Take the LAST occurrence — if the locked template ships a placeholder
    # empty literal and the writer replaces it, both appear in concatenated
    # script_text from soup.find_all("script").
    raw = matches[-1].strip()
    # Trim trailing characters that confuse a strict JSON parser.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to balance the literal by finding the matching bracket.
        depth = 0
        for i, ch in enumerate(raw):
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[: i + 1])
                    except json.JSONDecodeError:
                        return None
        return None


def _validate_waterfall_data(script_text: str) -> tuple[list[str], list[str]]:
    """I-007: every bar in `waterfallData` must have `{label, type, value, start, end}`
    with `type` in WATERFALL_VALID_TYPES. Bars without start/end render at
    NaN coordinates (the y-axis scale collapses) and the entire chart
    appears empty — observed in CGN/NextEra 2026-05-13."""
    errors: list[str] = []
    warnings: list[str] = []
    data = _extract_js_literal(script_text, "waterfallData")
    if data is None:
        warnings.append(
            "waterfallData: could not parse as JSON literal — locked template may "
            "ship an empty placeholder; skipping schema check (I-007)"
        )
        return errors, warnings
    if not isinstance(data, list):
        errors.append(f"waterfallData must be an array; got {type(data).__name__} (I-007)")
        return errors, warnings
    if not data:
        warnings.append("waterfallData is empty (I-007)")
        return errors, warnings
    required_fields = ("label", "type", "value", "start", "end")
    numeric_fields = ("value", "start", "end")
    for idx, bar in enumerate(data, start=1):
        if not isinstance(bar, dict):
            errors.append(f"waterfallData[{idx}] is not an object (I-007)")
            continue
        missing = [f for f in required_fields if f not in bar]
        if missing:
            errors.append(
                f"waterfallData[{idx}] (label={bar.get('label')!r}) missing required fields: "
                f"{missing}. Each bar must have label+type+value+start+end (I-007)"
            )
        t = bar.get("type")
        if t is not None and t not in WATERFALL_VALID_TYPES:
            errors.append(
                f"waterfallData[{idx}] type={t!r} not in "
                f"{sorted(WATERFALL_VALID_TYPES)} (I-007)"
            )
        for nf in numeric_fields:
            if not isinstance(bar.get(nf), (int, float)) or isinstance(bar.get(nf), bool):
                errors.append(
                    f"waterfallData[{idx}].{nf}={bar.get(nf)!r} must be a number (I-007)"
                )
    return errors, warnings


def _validate_sankey_conservation(script_text: str, var_name: str) -> tuple[list[str], list[str]]:
    """I-007: per-node conservation in Sankey income/forecast diagrams.

    Catches the CGN-class bug where the writer declares nodes (e.g. 费用,
    税前利润, 税费) but never wires links to/from them — d3-sankey renders
    a broken diagram and observers can't reconcile the flows.

    Rules:
      - Every declared node MUST appear as the source or target of at
        least one link. Orphans are P5 failures.
      - For each interior node (has BOTH inflow and outflow), inflow must
        equal outflow within a 1% tolerance. This catches the NextEra-class
        bug where 毛利润 had outflow > inflow (phantom money).
    """
    errors: list[str] = []
    warnings: list[str] = []
    data = _extract_js_literal(script_text, var_name)
    if data is None:
        warnings.append(f"{var_name}: could not parse as JSON literal; skipping conservation check (I-007)")
        return errors, warnings
    if not isinstance(data, dict):
        errors.append(f"{var_name} must be an object with `nodes` + `links`; got {type(data).__name__} (I-007)")
        return errors, warnings
    # The locked template ships `{}` as an unfilled placeholder; an empty
    # object is the writer's "I haven't populated this yet" state. Treat it
    # as a warning so the locked-skeleton lineage check still passes, but
    # surface it visibly. A populated literal without `nodes`/`links` is a
    # different bug class.
    if not data:
        warnings.append(f"{var_name} is empty `{{}}` — writer must populate nodes/links (I-007)")
        return errors, warnings
    nodes = data.get("nodes")
    links = data.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        errors.append(f"{var_name} must have list-typed `nodes` and `links` (I-007)")
        return errors, warnings
    if not nodes:
        warnings.append(f"{var_name}.nodes is empty (I-007)")
        return errors, warnings

    flows = {i: {"in": 0.0, "out": 0.0} for i in range(len(nodes))}
    for li, link in enumerate(links):
        if not isinstance(link, dict):
            errors.append(f"{var_name}.links[{li}] is not an object (I-007)")
            continue
        s, t, v = link.get("source"), link.get("target"), link.get("value")
        if not isinstance(s, int) or not isinstance(t, int):
            errors.append(f"{var_name}.links[{li}] source/target must be integer indices (I-007)")
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errors.append(f"{var_name}.links[{li}] value must be a number (I-007)")
            continue
        if s < 0 or s >= len(nodes) or t < 0 or t >= len(nodes):
            errors.append(f"{var_name}.links[{li}] source/target out of range (I-007)")
            continue
        flows[s]["out"] += v
        flows[t]["in"] += v

    for i, node in enumerate(nodes):
        name = (node or {}).get("name", f"#{i}") if isinstance(node, dict) else f"#{i}"
        f = flows[i]
        if f["in"] == 0 and f["out"] == 0:
            # Orphan = cosmetic flaw (d3-sankey just ignores it). Warn only.
            warnings.append(
                f"{var_name}.nodes[{i}] {name!r} is orphan (no incoming or outgoing link). "
                "Drop the node or wire its flow (I-007)"
            )
            continue
        # Interior node: conservation required.
        if f["in"] > 0 and f["out"] > 0:
            denom = max(f["in"], f["out"])
            delta = abs(f["in"] - f["out"]) / denom if denom else 0
            if delta > 0.01:
                errors.append(
                    f"{var_name}.nodes[{i}] {name!r} violates flow conservation: "
                    f"in={f['in']:.4f} vs out={f['out']:.4f} (delta {delta*100:.2f}% > 1%) (I-007)"
                )
    return errors, warnings


def validate_html_report(
    html_path: Path,
    skeleton_path: Path | None = None,
    *,
    qc_audit_trail_path: Path | None = None,
) -> dict[str, Any]:
    html_path = html_path.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if not html_path.exists():
        return {"status": "critical", "errors": [f"html file not found: {html_path}"], "warnings": []}

    html = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    skeleton_bytes = None
    if skeleton_path:
        skeleton_path = skeleton_path.resolve()
        if not skeleton_path.exists():
            errors.append(f"locked skeleton not found: {skeleton_path}")
        else:
            skeleton_bytes = len(skeleton_path.read_bytes())
            # The locked CN skeleton is ~38KB. A complete filled report may change
            # byte count, but a bespoke simplified page is usually far smaller.
            min_bytes = int(skeleton_bytes * 0.70)
            actual_bytes = len(html.encode("utf-8"))
            if actual_bytes < min_bytes:
                errors.append(
                    f"html is too small for locked template lineage: {actual_bytes} bytes < {min_bytes} bytes"
                )

    # Plan v3 Phase A: in a raw template the {{PORTER_ANALYSIS_BLOCKS}}
    # placeholder is expected. In a rendered report all placeholders must be
    # gone. Mode is auto-detected: if the placeholder is present and 5
    # force-blocks are NOT, treat as raw template (skip the
    # unreplaced-placeholder error so a skeleton can be validated for
    # structure too).
    in_template_mode = (
        "{{PORTER_ANALYSIS_BLOCKS}}" in html
        and not soup.select(".porter-force-block")
    )

    placeholders = sorted(set(PLACEHOLDER_RE.findall(html)))
    if placeholders and not in_template_mode:
        errors.append(f"unreplaced locked-template placeholders remain: {', '.join(placeholders[:12])}")

    for marker in REQUIRED_MARKERS:
        if marker not in html:
            errors.append(f"missing locked-template marker: {marker}")

    # Plan v3 Phase A: forecast Sankey + 3-tab radar Porter were removed.
    # Their substrings appearing in a fresh report mean the writer copied
    # stale skeleton content or used a pre-v3 template.
    for forbidden in FORBIDDEN_MARKERS:
        if forbidden in html:
            errors.append(
                f"pre-v3 marker still present (plan v3 Phase A removed it): {forbidden}"
            )

    for section_id in REQUIRED_SECTION_IDS:
        if not soup.select_one(f"#{section_id}"):
            errors.append(f"missing required report section: #{section_id}")

    structural_counts = {
        "summary_para": _count(soup, "#section-summary .summary-para"),
        "kpi_card": _count(soup, "#section-financials .kpi-card"),
        "trend_card": _count(soup, "#section-financials .trend-card"),
        "sankey_actual_svg": _count(soup, "#chart-sankey-actual"),
        "porter_bars_svg": _count(soup, "#chart-porter-bars"),
        "porter_force_block": _count(soup, ".porter-force-block"),
    }
    expected_min = {
        "summary_para": 4,
        "kpi_card": 4,
        "trend_card": 5,
        "sankey_actual_svg": 1,
        "porter_bars_svg": 1,
    }
    for key, need in expected_min.items():
        got = structural_counts[key]
        if got < need:
            errors.append(f"locked report structure incomplete: {key} count {got} < {need}")

    # Rendered reports must contain exactly 5 force-blocks. Raw templates
    # (placeholder present) skip this check — the Porter validator does the
    # detailed gate.
    if not in_template_mode and structural_counts["porter_force_block"] != 5:
        errors.append(
            "#section-porter must contain exactly 5 <div class=\"porter-force-block\"> "
            f"(plan v3 Phase A); got {structural_counts['porter_force_block']}"
        )

    # Auto-detect QC trail if caller didn't pass one explicitly. The trail's
    # mere existence flips the Porter validator into "QC mode" (per I-004 /
    # I-007); its absence flips it into "no-QC mode".
    if qc_audit_trail_path is None:
        qc_audit_trail_path = html_path.parent / "qc_audit_trail.json"
    qc_ran = qc_audit_trail_path.exists()

    porter_errors, porter_warnings = _validate_porter_force_blocks(soup, qc_ran=qc_ran)
    errors.extend(porter_errors)
    warnings.extend(porter_warnings)

    metrics_errors, metrics_warnings = _validate_metrics_table(soup)
    errors.extend(metrics_errors)
    warnings.extend(metrics_warnings)

    script_text = "\n".join(node.get_text("\n") for node in soup.find_all("script"))
    # Plan v3 Phase A: sankeyForecastData is gone; only sankeyActualData remains.
    for var_name in ("waterfallData", "sankeyActualData", "porterScores"):
        if not re.search(rf"\bconst\s+{re.escape(var_name)}\s*=", script_text):
            errors.append(f"missing JS data variable: {var_name}")

    waterfall_errors, waterfall_warnings = _validate_waterfall_data(script_text)
    errors.extend(waterfall_errors)
    warnings.extend(waterfall_warnings)

    # Plan v3 Phase A: only the actual sankey survives.
    s_errors, s_warnings = _validate_sankey_conservation(script_text, "sankeyActualData")
    errors.extend(s_errors)
    warnings.extend(s_warnings)

    line_count = len(html.splitlines())
    # Plan v3 Phase A shrank the template by ~21 (CN) / ~61 (EN) lines.
    # New raw template is ~1071/1045 lines; older skeletons were ~1058/1014.
    # Keep the lower bound at 400 so future iterations have headroom without
    # producing trivial false-positives. A simplified bespoke page is still
    # caught (it's typically under 100 lines).
    if line_count < 400:
        errors.append(f"html line count is too low for locked report template: {line_count} < 400")

    return {
        "status": "critical" if errors else ("warn" if warnings else "pass"),
        "html_file": str(html_path),
        "skeleton_file": str(skeleton_path) if skeleton_path else None,
        "line_count": line_count,
        "byte_count": len(html.encode("utf-8")),
        "skeleton_byte_count": skeleton_bytes,
        "structural_counts": structural_counts,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--run-dir", default=None)
    p.add_argument("--lang", default="cn", choices=["cn", "en", "zh"])
    p.add_argument("--html", default=None)
    p.add_argument("--skeleton", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    if args.run_dir:
        research_dir = Path(args.run_dir).resolve() / "research"
        html_path = _find_single_report(research_dir)
        if html_path is None:
            result = {
                "status": "critical",
                "errors": [f"expected exactly one non-locked *_Research_*.html under {research_dir}"],
                "warnings": [],
            }
        else:
            skeleton = Path(args.skeleton).resolve() if args.skeleton else _find_skeleton(research_dir, args.lang)
            result = validate_html_report(html_path, skeleton)
    else:
        if not args.html:
            print("error: provide --run-dir or --html", file=sys.stderr)
            return 2
        result = validate_html_report(
            Path(args.html),
            Path(args.skeleton).resolve() if args.skeleton else None,
        )

    out_path = Path(args.out).resolve() if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "critical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
