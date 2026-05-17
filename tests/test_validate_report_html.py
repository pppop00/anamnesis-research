from __future__ import annotations

import json
from pathlib import Path

from tools.research.validate_report_html import validate_html_report


# Plan v3 Phase A: Section V is now a single bar chart + 5 force-blocks.
# The force-block order is fixed: 供应商→买方→新进入者→替代品→行业内竞争.
PORTER_FORCES_ZH = (
    "供应商议价能力",
    "买方议价能力",
    "新进入者威胁",
    "替代品威胁",
    "行业内竞争",
)


def _porter_force_block(force: str, *, qc_ran: bool = False, rating: int = 3, missing_class: str | None = None) -> str:
    """Build a single <div class="porter-force-block"> per plan v3 Phase A.

    Each block contains:
      <h3>力名 — N/5</h3>
      <p class="porter-rating-statement">QC opening sentence</p>
      <p class="porter-anchor">…</p>
      <p class="porter-mechanism">…</p>
      <p class="porter-falsifier">…</p>
      <p class="porter-signal">…</p>
      <p class="porter-lookahead">…</p>
    """
    if qc_ran:
        opener = f"经QC合议，维持{force}为{rating}分。基于公开披露的数据。"
    else:
        opener = f"基于初稿评分，{force}为{rating}分。基于公开披露的数据。"

    paras = [
        ("porter-rating-statement", opener),
        ("porter-anchor", "Data anchor：某项具体指标 — 30 字以上补充说明。"),
        ("porter-mechanism", "评级机制：为什么是此分而非别的分 — 至少 60 字。"),
        ("porter-falsifier", "Falsifier：若 X 在 Y 时间内发生则评级会上调或下调。"),
        ("porter-signal", "Primary signal：CFO 直引「……」（来源/日期）。"),
        ("porter-lookahead", "Look-ahead：下半年关注的具体可观测数据点。"),
    ]
    p_html = "\n".join(
        f'<p class="{cls}">{text}</p>'
        for cls, text in paras
        if cls != missing_class
    )
    return (
        f'<div class="porter-force-block">'
        f"<h3>{force} — {rating}/5</h3>"
        f"{p_html}"
        f"</div>"
    )


def _porter_blocks(*, qc_ran: bool = False, count: int = 5, missing_class: str | None = None) -> str:
    return "\n".join(
        _porter_force_block(force, qc_ran=qc_ran, missing_class=missing_class)
        for force in PORTER_FORCES_ZH[:count]
    )


def _metrics_table(valid: bool = True) -> str:
    if not valid:
        rows = "\n".join(
            f'<tr><td>{name}</td><td>1</td><td>2</td><td class="metric-down">显著恶化</td></tr>'
            for name in (
                "营业收入（百万人民币）",
                "毛利润（百万人民币）",
                "营业利润（百万人民币）",
                "净利润（百万人民币）",
                "稀释EPS（人民币）",
                "经营现金流（百万人民币）",
                "自由现金流（百万人民币）",
            )
        )
        return f'<table class="metrics-table"><tbody>{rows}</tbody></table>'

    canonical = (
        ("毛利率", "改善"),
        ("营业利润率", "基本持平"),
        ("净利率", "改善"),
        ("ROE", "显著改善"),
        ("ROA", "改善"),
        ("资产负债率", "恶化"),
        ("利息保障倍数", "显著改善"),
        ("每股收益（EPS）", "改善"),
        ("自由现金流利润率", "基本持平"),
    )
    rows = "\n".join(
        f'<tr><td>{name}</td><td>1.0</td><td>1.0</td><td class="metric-up">{verdict}</td></tr>'
        for name, verdict in canonical
    )
    return f'<table class="metrics-table"><tbody>{rows}</tbody></table>'


def _locked_like_html(
    porter_valid: bool = True,
    metrics_valid: bool = True,
    *,
    qc_ran: bool = False,
    waterfall_js: str | None = None,
    sankey_actual_js: str | None = None,
    porter_block_count: int = 5,
    porter_missing_class: str | None = None,
    extra_body: str = "",
) -> str:
    """Build a plan-v3 locked-like HTML fixture.

    Notable changes from pre-v3:
      - Section IV: single SVG (#chart-sankey-actual), NO #chart-sankey-forecast.
      - Section V: single SVG (#chart-porter-bars) + 5 .porter-force-block divs,
        NO #porter-panel-*, NO .porter-text, NO #chart-radar-*.
      - JS: only sankeyActualData (sankeyForecastData removed).
    """
    summary = "\n".join('<p class="summary-para">x</p>' for _ in range(4))
    kpis = "\n".join('<div class="kpi-card"></div>' for _ in range(4))
    trends = "\n".join('<div class="trend-card"></div>' for _ in range(5))
    metrics = _metrics_table(valid=metrics_valid)
    if porter_valid:
        porter_blocks = _porter_blocks(
            qc_ran=qc_ran,
            count=porter_block_count,
            missing_class=porter_missing_class,
        )
    else:
        # Free-form Porter text — none of the required force-block divs.
        porter_blocks = "<p>品牌心智强、SKU聚焦；但成本波动仍影响扩张节奏。</p>"
    filler = "\n".join("<!-- locked filler -->" for _ in range(520))
    wf = waterfall_js if waterfall_js is not None else "[]"
    sa = sankey_actual_js if sankey_actual_js is not None else "{}"
    return f"""<!doctype html>
<html lang="zh-CN">
<head><style>CANONICAL CSS</style></head>
<body>
<div class="section" id="section-summary">{summary}</div>
<div class="section" id="section-financials">{kpis}{trends}{metrics}</div>
<div class="section" id="section-prediction"></div>
<div class="section" id="section-sankey"><svg id="chart-sankey-actual"></svg></div>
<div class="section" id="section-porter">
<svg id="chart-porter-bars"></svg>
<div class="porter-analysis-blocks">{porter_blocks}</div>
</div>
<div class="section" id="section-appendix"></div>
{extra_body}
<script>
LOCKED JAVASCRIPT
DATA VARIABLES
const waterfallData = {wf};
const sankeyActualData = {sa};
const porterScores = [3,3,3,3,3];
function drawWaterfall() {{}}
function drawSankey() {{}}
function drawPorterBars() {{}}
</script>
{filler}
</body>
</html>"""


def _locked_template_html(**kwargs) -> str:
    """Build a RAW template fixture: porter section carries the unreplaced
    `{{PORTER_ANALYSIS_BLOCKS}}` placeholder instead of 5 force-blocks.

    Used to exercise the auto-detect template-mode branch.
    """
    # Build the porter blocks string directly and route it through the
    # `extra_body` slot so it stays out of #section-porter, then rewrite
    # #section-porter to carry only the placeholder. Cleaner than regex on
    # the rendered string.
    base = _locked_like_html(porter_valid=True, **kwargs)
    placeholder_section = (
        '<div class="section" id="section-porter">'
        '<svg id="chart-porter-bars"></svg>'
        '<div class="porter-analysis-blocks">{{PORTER_ANALYSIS_BLOCKS}}</div>'
        "</div>"
    )
    # Splice: find <div class="section" id="section-porter"> ... </div> and
    # replace. We rely on the fixture's known shape (one occurrence with no
    # nested <div class="section"> inside).
    start_marker = '<div class="section" id="section-porter">'
    end_marker = '<div class="section" id="section-appendix">'
    i = base.index(start_marker)
    j = base.index(end_marker)
    return base[:i] + placeholder_section + "\n" + base[j:]


# ---------- canonical sample data ----------

_GOOD_WATERFALL = json.dumps([
    {"label": "基准增长", "type": "baseline", "value": 4.2, "start": 0,   "end": 4.2},
    {"label": "宏观因子", "type": "positive", "value": 1.5, "start": 4.2, "end": 5.7},
    {"label": "公司特定", "type": "positive", "value": 1.0, "start": 5.7, "end": 6.7},
    {"label": "预测结果", "type": "result",   "value": 6.7, "start": 0,   "end": 6.7},
])

_GOOD_SANKEY = json.dumps({
    "nodes": [
        {"name": "营业收入"},
        {"name": "营业成本"},
        {"name": "毛利润"},
        {"name": "营业利润"},
        {"name": "净利润"},
    ],
    "links": [
        {"source": 0, "target": 1, "value": 60.0},
        {"source": 0, "target": 2, "value": 40.0},
        {"source": 2, "target": 3, "value": 40.0},
        {"source": 3, "target": 4, "value": 40.0},
    ],
})


# ---------- baseline tests ----------

def test_validate_report_html_rejects_simplified_page(tmp_path: Path) -> None:
    html = tmp_path / "Simple_Research_CN.html"
    html.write_text("<html><body><h1>简化版</h1></body></html>", encoding="utf-8")

    result = validate_html_report(html)

    assert result["status"] == "critical"
    assert any("missing locked-template marker" in e for e in result["errors"])
    assert any("line count is too low" in e for e in result["errors"])


def test_validate_report_html_accepts_locked_like_page(tmp_path: Path) -> None:
    """Locked v3 report with valid Porter force-blocks — should pass."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    payload = _locked_like_html()  # qc_ran=False → 基于初稿评分 prefix, no qc trail
    skeleton.write_text(payload, encoding="utf-8")
    html.write_text(payload.replace("locked filler", "filled filler"), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] in {"pass", "warn"}, result
    assert result["errors"] == [], result["errors"]


def test_validate_report_html_rejects_freeform_porter_text(tmp_path: Path) -> None:
    """Section V missing the 5 force-block divs → critical."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(porter_valid=False), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any("porter-force-block" in e for e in result["errors"])


def test_validate_report_html_rejects_metrics_table_with_pl_amounts(tmp_path: Path) -> None:
    """I-005: metrics table emitting absolute P&L amounts must fail validation."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(metrics_valid=False), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any("metrics-table" in e and "I-005" in e for e in result["errors"])
    assert any("9 <tr>" in e for e in result["errors"])
    assert any("not in the controlled ratio whitelist" in e for e in result["errors"])


# ---------- I-004 / I-007 Porter QC prefix tests ----------

def test_porter_no_qc_prefix_when_qc_ran_fails(tmp_path: Path) -> None:
    """Writer emits 基于初稿评分 but qc_audit_trail.json exists → critical."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    (tmp_path / "qc_audit_trail.json").write_text("{}", encoding="utf-8")  # QC ran
    skeleton.write_text(_locked_like_html(qc_ran=True), encoding="utf-8")
    # Force the no-QC prefix into the porter blocks (writer bug).
    html.write_text(_locked_like_html(qc_ran=False), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any("QC mode sentence" in e for e in result["errors"])


def test_porter_qc_prefix_when_no_qc_trail_fails(tmp_path: Path) -> None:
    """Writer emits 经QC合议 without a qc trail → critical (inventing QC wording)."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    # No qc_audit_trail.json sibling
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(qc_ran=True), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any("no-QC mode sentence" in e for e in result["errors"])


# ---------- I-007 waterfall / sankey tests ----------

def test_waterfall_missing_start_end_fails(tmp_path: Path) -> None:
    """I-007: waterfallData bars missing start/end render the chart empty."""
    bad = json.dumps([
        {"label": "2025收入增速", "type": "start", "value": -4.1},
        {"label": "宏观/电价",   "type": "delta", "value": -1.3},
        {"label": "2026E收入增速", "type": "end",   "value": -1.4},
    ])
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=bad), encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("waterfallData" in e and "I-007" in e for e in result["errors"])
    assert any("missing required fields" in e for e in result["errors"])
    assert any("type='delta'" in e or "type='start'" in e or "not in" in e for e in result["errors"])


def test_waterfall_good_passes(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                          sankey_actual_js=_GOOD_SANKEY),
                        encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                      sankey_actual_js=_GOOD_SANKEY),
                    encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] in {"pass", "warn"}, result
    assert result["errors"] == [], result["errors"]


def test_sankey_orphan_warns(tmp_path: Path) -> None:
    """I-007: orphan Sankey node is a warning, not error."""
    with_orphan = json.dumps({
        "nodes": [
            {"name": "营业收入"},
            {"name": "营业成本"},
            {"name": "毛利润"},
            {"name": "营业利润"},
            {"name": "净利润"},
            {"name": "无用节点"},  # orphan
        ],
        "links": [
            {"source": 0, "target": 1, "value": 60.0},
            {"source": 0, "target": 2, "value": 40.0},
            {"source": 2, "target": 3, "value": 40.0},
            {"source": 3, "target": 4, "value": 40.0},
        ],
    })
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                          sankey_actual_js=with_orphan),
                        encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                      sankey_actual_js=with_orphan),
                    encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] == "warn"
    assert result["errors"] == []
    assert any("orphan" in w and "无用节点" in w for w in result["warnings"])


def test_sankey_conservation_violation_fails(tmp_path: Path) -> None:
    """I-007: interior node where outflow >> inflow."""
    unbalanced = json.dumps({
        "nodes": [
            {"name": "营业收入"},
            {"name": "营业成本"},
            {"name": "毛利润"},
            {"name": "营业利润"},
            {"name": "净利润"},
        ],
        "links": [
            {"source": 0, "target": 1, "value": 60.0},
            {"source": 0, "target": 2, "value": 40.0},
            # 毛利润 outflow 60 > inflow 40
            {"source": 2, "target": 3, "value": 60.0},
            {"source": 3, "target": 4, "value": 60.0},
        ],
    })
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                          sankey_actual_js=unbalanced),
                        encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                      sankey_actual_js=unbalanced),
                    encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("violates flow conservation" in e and "毛利润" in e for e in result["errors"])


# ---------- Plan v3 Phase A negative marker tests ----------

def _inject(html: str, snippet: str) -> str:
    """Insert snippet just before </body> to simulate stale pre-v3 fragments."""
    return html.replace("</body>", snippet + "\n</body>")


def test_v3_rejects_chart_sankey_forecast(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, '<svg id="chart-sankey-forecast"></svg>'), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any('id="chart-sankey-forecast"' in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_sankey_forecast_data(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(
        _inject(base, "<script>const sankeyForecastData = {};</script>"),
        encoding="utf-8",
    )
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("sankeyForecastData" in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_sankey_forecast_js_placeholder(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, "{{SANKEY_FORECAST_JS_DATA}}"), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("SANKEY_FORECAST_JS_DATA" in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_sankey_year_forecast_placeholder(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, "{{SANKEY_YEAR_FORECAST}}"), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("SANKEY_YEAR_FORECAST" in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_chart_radar_company(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, '<canvas id="chart-radar-company"></canvas>'), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any('id="chart-radar-company"' in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_chart_radar_industry_and_forward(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(
        _inject(
            base,
            '<canvas id="chart-radar-industry"></canvas>'
            '<canvas id="chart-radar-forward"></canvas>',
        ),
        encoding="utf-8",
    )
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any('id="chart-radar-industry"' in e for e in result["errors"])
    assert any('id="chart-radar-forward"' in e for e in result["errors"])


def test_v3_rejects_porter_text_placeholders(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(
        _inject(
            base,
            "{{PORTER_COMPANY_TEXT}}{{PORTER_INDUSTRY_TEXT}}{{PORTER_FORWARD_TEXT}}",
        ),
        encoding="utf-8",
    )
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("PORTER_COMPANY_TEXT" in e and "pre-v3 marker" in e for e in result["errors"])
    assert any("PORTER_INDUSTRY_TEXT" in e for e in result["errors"])
    assert any("PORTER_FORWARD_TEXT" in e for e in result["errors"])


def test_v3_rejects_porter_tabs(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, '<div id="porter-tabs"></div>'), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any('id="porter-tabs"' in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_porter_radar_container(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, '<div class="porter-radar"></div>'), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any('class="porter-radar"' in e and "pre-v3 marker" in e for e in result["errors"])


def test_v3_rejects_sankey_tabs(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    base = _locked_like_html()
    skeleton.write_text(base, encoding="utf-8")
    html.write_text(_inject(base, '<div id="sankey-tabs"></div>'), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any('id="sankey-tabs"' in e and "pre-v3 marker" in e for e in result["errors"])


# ---------- Plan v3 force-block structural tests ----------

def test_v3_wrong_force_block_count_fails(tmp_path: Path) -> None:
    """Rendered report with only 4 force-blocks (writer dropped one) → critical."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(porter_block_count=4), encoding="utf-8")
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("exactly 5" in e and "porter-force-block" in e for e in result["errors"])


def test_v3_force_block_missing_required_class_fails(tmp_path: Path) -> None:
    """Each force-block must contain 6 named <p> classes. Drop one → critical."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(
        _locked_like_html(porter_missing_class="porter-falsifier"),
        encoding="utf-8",
    )
    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any(
        "porter-falsifier" in e and "missing required <p> classes" in e
        for e in result["errors"]
    )


def test_v3_template_mode_skips_force_block_count(tmp_path: Path) -> None:
    """When {{PORTER_ANALYSIS_BLOCKS}} is unreplaced and no force-blocks are
    present, treat as raw template — skip the force-block count check and
    don't flag the placeholder as critical."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    payload = _locked_template_html()
    skeleton.write_text(payload, encoding="utf-8")
    html.write_text(payload, encoding="utf-8")
    result = validate_html_report(html, skeleton)
    # Template mode: no force-block count error; placeholders are tolerated.
    assert not any(
        "porter-force-block" in e and "exactly 5" in e for e in result["errors"]
    ), result["errors"]
    assert not any("unreplaced locked-template placeholders" in e for e in result["errors"]), result["errors"]
