from __future__ import annotations

import json
from pathlib import Path

from tools.research.validate_report_html import validate_html_report


PORTER_FORCES_ZH = (
    "供应商议价能力",
    "买方议价能力",
    "新进入者威胁",
    "替代品威胁",
    "行业竞争强度",
)


def _porter_text(valid: bool = True, *, qc_ran: bool = False) -> str:
    """Build the Porter <ul> with mode-aware prefixes.

    qc_ran=True  → "经QC合议，..." opening (only valid when qc_audit_trail.json exists)
    qc_ran=False → "基于初稿评分，..." opening (no-QC fast-run convention)
    """
    if not valid:
        return '<div class="porter-text">品牌心智强、SKU聚焦；但成本波动仍影响扩张节奏。</div>'

    def opener(force: str) -> str:
        if qc_ran:
            return f"经QC合议，维持{force}为3分。"
        return f"基于初稿评分，{force}为3分。"

    lis = "\n".join(
        f"<li>{opener(force)}这里保留每个维度的公司事实、行业证据和评分解释。</li>"
        for force in PORTER_FORCES_ZH
    )
    return f'<div class="porter-text"><ul style="margin:0;padding-left:1.25em;">{lis}</ul></div>'


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
    sankey_forecast_js: str | None = None,
) -> str:
    sections = "\n".join(
        f'<div class="section" id="{sid}"></div>'
        for sid in (
            "section-summary",
            "section-financials",
            "section-prediction",
            "section-sankey",
            "section-porter",
            "section-appendix",
        )
    )
    summary = "\n".join('<p class="summary-para">x</p>' for _ in range(4))
    kpis = "\n".join('<div class="kpi-card"></div>' for _ in range(4))
    trends = "\n".join('<div class="trend-card"></div>' for _ in range(5))
    metrics = _metrics_table(valid=metrics_valid)
    porters = "\n".join(
        f'<div id="porter-panel-{i}">{_porter_text(valid=porter_valid, qc_ran=qc_ran)}</div>'
        for i in ("company", "industry", "forward")
    )
    radar = "\n".join(f'<canvas id="chart-radar-{i}"></canvas>' for i in ("company", "industry", "forward"))
    filler = "\n".join("<!-- locked filler -->" for _ in range(520))
    wf = waterfall_js if waterfall_js is not None else "[]"
    sa = sankey_actual_js if sankey_actual_js is not None else "{}"
    sf = sankey_forecast_js if sankey_forecast_js is not None else "{}"
    return f"""<!doctype html>
<html>
<head><style>CANONICAL CSS</style></head>
<body>
{sections}
<div id="section-summary">{summary}</div>
<div id="section-financials">{kpis}{trends}{metrics}</div>
<div id="section-sankey"><svg id="chart-sankey-actual"></svg><svg id="chart-sankey-forecast"></svg></div>
<div id="section-porter">{porters}{radar}</div>
<script>
LOCKED JAVASCRIPT
DATA VARIABLES
const waterfallData = {wf};
const sankeyActualData = {sa};
const sankeyForecastData = {sf};
const porterScores = {{}};
function drawWaterfall() {{}}
function drawSankey() {{}}
function drawRadar() {{}}
</script>
{filler}
</body>
</html>"""


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


# ---------- existing tests ----------

def test_validate_report_html_rejects_simplified_page(tmp_path: Path) -> None:
    html = tmp_path / "Simple_Research_CN.html"
    html.write_text("<html><body><h1>简化版</h1></body></html>", encoding="utf-8")

    result = validate_html_report(html)

    assert result["status"] == "critical"
    assert any("missing locked-template marker" in e for e in result["errors"])
    assert any("line count is too low" in e for e in result["errors"])


def test_validate_report_html_accepts_locked_like_page(tmp_path: Path) -> None:
    """Locked skeleton with empty placeholders — should pass with only warnings."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    payload = _locked_like_html()  # qc_ran=False → 基于初稿评分 prefix, no qc trail
    skeleton.write_text(payload, encoding="utf-8")
    html.write_text(payload.replace("locked filler", "filled filler"), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] in {"pass", "warn"}, result
    assert result["errors"] == [], result["errors"]


def test_validate_report_html_rejects_freeform_porter_text(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(porter_valid=False), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any(".porter-text" in e for e in result["errors"])


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


# ---------- I-007 tests ----------

def test_i007_porter_no_qc_prefix_when_qc_ran_fails(tmp_path: Path) -> None:
    """I-007: writer emits 基于初稿评分 but qc_audit_trail.json exists → critical."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    (tmp_path / "qc_audit_trail.json").write_text("{}", encoding="utf-8")  # QC ran
    skeleton.write_text(_locked_like_html(qc_ran=True), encoding="utf-8")
    # Force the no-QC prefix into the porter texts (writer bug).
    html.write_text(_locked_like_html(qc_ran=False), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any("QC mode sentence" in e and "I-007" in e for e in result["errors"])


def test_i007_porter_qc_prefix_when_no_qc_trail_fails(tmp_path: Path) -> None:
    """I-007: writer emits 经QC合议 without a qc trail → critical (inventing QC wording)."""
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    # No qc_audit_trail.json sibling
    skeleton.write_text(_locked_like_html(), encoding="utf-8")
    html.write_text(_locked_like_html(qc_ran=True), encoding="utf-8")

    result = validate_html_report(html, skeleton)

    assert result["status"] == "critical"
    assert any("no-QC mode sentence" in e and "I-007" in e for e in result["errors"])


def test_i007_waterfall_missing_start_end_fails(tmp_path: Path) -> None:
    """I-007: waterfallData bars missing start/end render the chart empty (CGN/NextEra class)."""
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


def test_i007_waterfall_good_passes(tmp_path: Path) -> None:
    skeleton = tmp_path / "_locked_cn_skeleton.html"
    html = tmp_path / "Company_Research_CN.html"
    skeleton.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                          sankey_actual_js=_GOOD_SANKEY,
                                          sankey_forecast_js=_GOOD_SANKEY),
                        encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                      sankey_actual_js=_GOOD_SANKEY,
                                      sankey_forecast_js=_GOOD_SANKEY),
                    encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] in {"pass", "warn"}, result
    assert result["errors"] == [], result["errors"]


def test_i007_sankey_orphan_warns(tmp_path: Path) -> None:
    """I-007: orphan Sankey node is a warning, not error (renders blank but doesn't break)."""
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
                                          sankey_actual_js=with_orphan,
                                          sankey_forecast_js=_GOOD_SANKEY),
                        encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                      sankey_actual_js=with_orphan,
                                      sankey_forecast_js=_GOOD_SANKEY),
                    encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] == "warn"
    assert result["errors"] == []
    assert any("orphan" in w and "无用节点" in w for w in result["warnings"])


def test_i007_sankey_conservation_violation_fails(tmp_path: Path) -> None:
    """I-007: interior node where outflow >> inflow (NextEra-class phantom money)."""
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
                                          sankey_actual_js=unbalanced,
                                          sankey_forecast_js=_GOOD_SANKEY),
                        encoding="utf-8")
    html.write_text(_locked_like_html(waterfall_js=_GOOD_WATERFALL,
                                      sankey_actual_js=unbalanced,
                                      sankey_forecast_js=_GOOD_SANKEY),
                    encoding="utf-8")

    result = validate_html_report(html, skeleton)
    assert result["status"] == "critical"
    assert any("violates flow conservation" in e and "毛利润" in e for e in result["errors"])
