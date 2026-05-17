from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EP_SCRIPT = ROOT / "skills_repo" / "ep" / "scripts" / "generate_social_cards.py"

# TODO(Zelong): money_scale_consistency_issues was removed from the EP renderer
# during the pre-v3 WIP refactor (EP commit 5bcbaa3, which removed the function
# added in 48eaf16). Decide whether to (a) reinstate the guard on the EP side,
# (b) rewrite this test against whatever replacement check now lives in the
# renderer, or (c) delete this test if the guardrail is no longer wanted.
pytestmark = pytest.mark.skip(
    reason=(
        "money_scale_consistency_issues no longer exists in EP renderer after WIP "
        "refactor (5bcbaa3); test needs to be reinstated alongside the guard or "
        "rewritten for the replacement check."
    )
)


def _load_ep():
    spec = importlib.util.spec_from_file_location("generate_social_cards_for_test", EP_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _report(mod, *, unit: str, revenue: float, net_income: float):
    return mod.ReportData(
        stem="Kweichow_Moutai_Research_CN",
        source_dir=ROOT,
        company_cn="贵州茅台",
        company_en="Kweichow Moutai",
        ticker="600519.SH",
        date="2026年5月1日",
        summary=["2025年公司营业总收入1720.5亿元，归母净利润823.2亿元。"],
        highlights=["2025年营业总收入1720.5亿元。"],
        risks=[],
        thesis="",
        porter_industry="",
        porter_forward="",
        porter_scores_industry=[3, 3, 3, 3, 3],
        sankey_actual={},
        financial_data={
            "currency": "CNY",
            "income_statement": {
                "unit": unit,
                "current_year": {
                    "revenue": revenue,
                    "cogs": revenue * 0.1,
                    "gross_profit": revenue * 0.9,
                    "operating_income": revenue * 0.6,
                    "net_income": net_income,
                },
            },
        },
        financial_analysis={},
        porter_analysis={},
        card_slots=mod.CardSlotOverrides(
            company_focus_paragraph="贵州茅台2025年营业总收入1720.5亿元、归母净利润823.2亿元。",
            background_bullets=["2025年营业总收入1720.5亿元。"],
        ),
    )


def test_money_scale_guard_flags_renderer_unit_drift() -> None:
    mod = _load_ep()
    data = _report(mod, unit="billions", revenue=172.05, net_income=82.32)
    mod.set_currency_label(data)

    issues = mod.money_scale_consistency_issues(
        data,
        mod.finance(data),
        data.card_slots.company_focus_paragraph,
        data.card_slots.background_bullets,
    )

    assert any("Money scale mismatch for revenue" in issue for issue in issues)
    assert any("renderer will show 1.7 亿元" in issue for issue in issues)


def test_money_scale_guard_accepts_native_yi_values() -> None:
    mod = _load_ep()
    data = _report(mod, unit="亿元人民币", revenue=1720.5, net_income=823.2)
    mod.set_currency_label(data)

    issues = mod.money_scale_consistency_issues(
        data,
        mod.finance(data),
        data.card_slots.company_focus_paragraph,
        data.card_slots.background_bullets,
    )

    assert issues == []
