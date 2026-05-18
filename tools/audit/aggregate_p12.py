"""P12 aggregator — combine audit layer reports into one verdict + a human QA_REPORT.md.

Layer inputs (all written by their respective tools earlier in the phase):

    <run>/validation/reconciliation.csv          (layer 1, with summary fields embedded)
    <run>/validation/ocr_summary.json            (layer 2)
    <run>/validation/web_third_check.json        (layer 3)
    <run>/validation/db_cross.json               (layer 4)
    <run>/validation/user_agent_pii.json         (privacy guard; written by this aggregator)

Outputs:

    <run>/validation/post_card_audit.json        machine-readable
    <run>/validation/QA_REPORT.md                human-readable, in report_language

Exit code: 0 if status in {pass, warn}, 1 if status == fail. Layers 1-3 and
the User-Agent PII guard are fail-blocking; layer 4 cold-start is OK.

Usage:
    python tools/audit/aggregate_p12.py --run-dir <path>
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

# When invoked as `python tools/audit/aggregate_p12.py ...` the repo root is
# not on sys.path, so the absolute import below fails. Insert the root so
# direct script invocation works the same as `python -m`.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.audit.user_agent_pii import audit_run as audit_user_agent_pii


def _load_json(p: Path) -> Optional[dict]:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _summarise_reconcile(csv_path: Path) -> dict:
    if not csv_path.exists():
        return {"status": "missing", "rows_checked": 0, "fails": 0, "warns": 0}
    fails: list[dict] = []
    warns: list[dict] = []
    total = 0
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if row["status"] == "fail":
                fails.append(row)
            elif row["status"] == "warn":
                warns.append(row)
    status = "fail" if fails else ("warn" if warns else "pass")
    return {
        "status": status,
        "rows_checked": total,
        "fails": len(fails),
        "warns": len(warns),
        "top_fails": fails[:5],
        "top_warns": warns[:3],
    }


def aggregate(run_dir: Path) -> dict:
    val = run_dir / "validation"
    layer1 = _summarise_reconcile(val / "reconciliation.csv")
    layer2 = _load_json(val / "ocr_summary.json") or {"status": "missing"}
    layer3 = _load_json(val / "web_third_check.json") or {"status": "missing"}
    layer4 = _load_json(val / "db_cross.json") or {"status": "missing"}
    user_agent_pii = audit_user_agent_pii(run_dir)
    val.mkdir(parents=True, exist_ok=True)
    (val / "user_agent_pii.json").write_text(
        json.dumps(user_agent_pii, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    layers = {
        "reconcile": layer1,
        "ocr": layer2,
        "web": layer3,
        "db_cross": layer4,
        "user_agent_pii": user_agent_pii,
    }

    statuses_blocking = [layer1.get("status"), layer2.get("status"), layer3.get("status"), user_agent_pii.get("status")]
    if any(s == "fail" for s in statuses_blocking):
        overall = "fail"
    elif (
        any(s in {"warn", "miss"} for s in statuses_blocking)
        or layer4.get("status") == "warn"
        or layer1.get("warns", 0) > 0
        or (layer3.get("status") == "pending" and layer3.get("targets"))
    ):
        overall = "warn"
    else:
        overall = "pass"

    meta = _load_json(run_dir / "meta" / "run.json") or {}

    out = {
        "schema_version": 1,
        "run_id": meta.get("run_id"),
        "ticker": meta.get("ticker"),
        "fiscal_period": meta.get("fiscal_period"),
        "language": meta.get("report_language", "en"),
        "status": overall,
        "layers": layers,
    }

    (val / "post_card_audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (val / "QA_REPORT.md").write_text(_render_qa_report(out), encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────────────────
# QA report rendering (CN + EN)
# ─────────────────────────────────────────────────────────────────────

ICONS = {"pass": "✅", "warn": "⚠️", "fail": "❌", "missing": "—"}

L10N = {
    "en": {
        "title": "P12 Final Audit — QA Report",
        "verdict": "Verdict",
        "layer1": "Layer 1 — Numerical reconciliation",
        "layer2": "Layer 2 — PNG OCR",
        "layer3": "Layer 3 — Independent web check",
        "layer4": "Layer 4 — DB cross-validation",
        "privacy": "Privacy guard — User-Agent PII",
        "rows_checked": "Rows checked",
        "fails": "Fails",
        "warns": "Warns",
        "top_mismatches": "Top mismatches",
        "engine": "Engine",
        "key_misses": "Key misses",
        "decorative_misses": "Decorative misses",
        "top_n": "Top-N targets",
        "verifications_pending": "Verifications pending host LLM",
        "checks": "Checks",
        "operator_actions": "Operator actions",
        "violations": "Violations",
        "no_pii": "No SEC email leakage found in captured request logs.",
        "no_fails": "No mismatches.",
        "no_misses": "No key numerics missing in OCR.",
        "no_targets": "No headline numerics flagged for re-verify.",
        "cold_start": "Cold start — no priors in DB.",
        "ship_ok": "Ship-ready. No paying-customer-blocking issues found.",
        "warn_action": "Review the warnings before publishing; warnings do not block release.",
        "fail_action": "Do not publish. Re-run the upstream phase that produced the offending value.",
        "ticker": "Ticker", "period": "Period", "run_id": "Run ID",
    },
    "zh": {
        "title": "P12 最终审计 —— 交付前 QA 报告",
        "verdict": "结论",
        "layer1": "Layer 1 —— 渲染数字核对",
        "layer2": "Layer 2 —— PNG OCR 复读",
        "layer3": "Layer 3 —— Web 第三轮事实复查",
        "layer4": "Layer 4 —— DB 历史 / 同行交叉",
        "privacy": "隐私守卫 —— User-Agent PII",
        "rows_checked": "核对条数",
        "fails": "不通过",
        "warns": "警告",
        "top_mismatches": "Top 差异",
        "engine": "OCR 引擎",
        "key_misses": "关键数字缺失",
        "decorative_misses": "装饰数字缺失",
        "top_n": "Top-N 复查目标",
        "verifications_pending": "等待宿主 LLM 完成 Web 核查",
        "checks": "检查项",
        "operator_actions": "操作员需关注",
        "violations": "违规项",
        "no_pii": "捕获的请求日志中未发现 SEC 邮箱泄露。",
        "no_fails": "未发现不一致。",
        "no_misses": "关键数字均在 OCR 中命中。",
        "no_targets": "未抽到需要复查的头部数字。",
        "cold_start": "冷启动 —— 数据库尚无可比历史。",
        "ship_ok": "可交付。未发现阻塞付费客户的问题。",
        "warn_action": "发布前请人工复核警告项；警告不阻塞发布。",
        "fail_action": "不要发布。回到产生该数值的上游 phase 重跑。",
        "ticker": "股票代码", "period": "财报期", "run_id": "Run ID",
    },
}


def _render_qa_report(out: dict) -> str:
    lang = "zh" if out.get("language") == "zh" else "en"
    t = L10N[lang]
    status = out["status"]
    icon = ICONS.get(status, "•")
    lines: list[str] = []
    lines.append(f"# {t['title']}")
    lines.append("")
    lines.append(f"**{t['verdict']}: {icon} `{status.upper()}`**")
    lines.append("")
    lines.append(
        f"- {t['ticker']}: `{out.get('ticker') or '-'}` · "
        f"{t['period']}: `{out.get('fiscal_period') or '-'}` · "
        f"{t['run_id']}: `{out.get('run_id') or '-'}`"
    )
    lines.append("")

    # Layer 1
    l1 = out["layers"]["reconcile"]
    lines.append(f"## {t['layer1']}  {ICONS.get(l1.get('status'), '—')}")
    lines.append("")
    lines.append(
        f"- {t['rows_checked']}: {l1.get('rows_checked', 0)}  ·  "
        f"{t['fails']}: {l1.get('fails', 0)}  ·  {t['warns']}: {l1.get('warns', 0)}"
    )
    if l1.get("top_fails"):
        lines.append("")
        lines.append(f"### {t['top_mismatches']}")
        lines.append("")
        for r in l1["top_fails"]:
            lines.append(
                f"- `{r.get('slot_path', '')}` = {r.get('slot_value', '')} {r.get('slot_unit', '')} "
                f"vs research `{r.get('match_path', '')}` = {r.get('match_value', '')} {r.get('match_unit', '')}"
            )
    elif status != "fail":
        lines.append("")
        lines.append(f"_{t['no_fails']}_")
    lines.append("")

    # Layer 2
    l2 = out["layers"]["ocr"]
    lines.append(f"## {t['layer2']}  {ICONS.get(l2.get('status'), '—')}")
    lines.append("")
    lines.append(f"- {t['engine']}: `{l2.get('engine', '-')}`")
    key_misses = l2.get("key_misses") or []
    deco_misses = l2.get("decorative_misses") or []
    if key_misses:
        lines.append("")
        lines.append(f"### {t['key_misses']}")
        for m in key_misses:
            lines.append(
                f"- card_{m.get('card')}: `{m.get('slot')}` value={m.get('value')} ({m.get('context','')})"
            )
    elif l2.get("status") in {"pass", "warn"}:
        lines.append("")
        lines.append(f"_{t['no_misses']}_")
    if deco_misses:
        lines.append("")
        lines.append(f"### {t['decorative_misses']}")
        for m in deco_misses[:5]:
            lines.append(
                f"- card_{m.get('card')}: value={m.get('value')} ({m.get('context','')})"
            )
    if l2.get("note"):
        lines.append("")
        lines.append(f"> {l2['note']}")
    lines.append("")

    # Layer 3
    l3 = out["layers"]["web"]
    lines.append(f"## {t['layer3']}  {ICONS.get(l3.get('status'), '—')}")
    lines.append("")
    targets = l3.get("targets") or []
    if not targets:
        lines.append(f"_{t['no_targets']}_")
    else:
        pending = [tg for tg in targets if tg.get("verification") == "pending"]
        if pending:
            lines.append(f"_{t['verifications_pending']}: {len(pending)}/{len(targets)}_")
            lines.append("")
        lines.append(f"### {t['top_n']}")
        for tg in targets:
            lines.append(
                f"- `{tg.get('slot_path')}` = {tg.get('value')} {tg.get('unit') or ''} → "
                f"{tg.get('verification', 'pending')}"
                + (f" (source: {tg['source_url']})" if tg.get("source_url") else "")
            )
    lines.append("")

    # Layer 4
    l4 = out["layers"]["db_cross"]
    l4_status = l4.get("status", "missing")
    lines.append(f"## {t['layer4']}  {ICONS.get(l4_status if l4_status != 'no_priors' else 'missing', '—')}")
    lines.append("")
    if l4_status == "no_priors":
        lines.append(f"_{t['cold_start']}_")
    else:
        lines.append(f"### {t['checks']}")
        for check in (l4.get("checks") or []):
            sev_icon = {"warn": "⚠️", "fail": "❌", "info": "·"}.get(check.get("severity"), "·")
            lines.append(f"- {sev_icon} `{check.get('id')}` — {check.get('result')}")
            for fl in (check.get("flags") or []):
                lines.append(f"    - {json.dumps(fl, ensure_ascii=False)}")
    lines.append("")

    # Privacy guard
    l5 = out["layers"]["user_agent_pii"]
    lines.append(f"## {t['privacy']}  {ICONS.get(l5.get('status'), '—')}")
    lines.append("")
    lines.append(f"- `public_user_agent`: `{l5.get('public_user_agent') or '-'}`")
    violations = l5.get("violations") or []
    if violations:
        lines.append("")
        lines.append(f"### {t['violations']}")
        for item in violations[:5]:
            lines.append(
                f"- `{item.get('file')}`:{item.get('line')} — "
                f"{', '.join(item.get('non_sec_urls') or [])}"
            )
    elif l5.get("status") in {"pass", "warn"}:
        lines.append("")
        lines.append(f"_{t['no_pii']}_")
    lines.append("")

    # Operator actions
    lines.append(f"## {t['operator_actions']}")
    lines.append("")
    if status == "pass":
        lines.append(f"_{t['ship_ok']}_")
    elif status == "warn":
        lines.append(f"_{t['warn_action']}_")
    else:
        lines.append(f"_{t['fail_action']}_")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--run-dir", required=True)
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"error: {run_dir} does not exist", file=sys.stderr)
        return 2

    summary = aggregate(run_dir)
    print(json.dumps({"status": summary["status"], "run_id": summary["run_id"],
                       "ticker": summary["ticker"]},
                      ensure_ascii=False, indent=2))
    return 0 if summary["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
