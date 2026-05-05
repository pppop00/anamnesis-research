"""anamnesis — top-level CLI for Anamnesis Research.

Drives the deterministic phases (bootstrap, DB precheck, P12 audit, DB index,
sector reports). The LLM-driven phases (P1 research, P5 report writing, P10.5
validator 2) happen in the host LLM environment by reading agents/orchestrator.md.

Usage:

    anamnesis.py init
        Apply DB migrations to db/equity_kb.sqlite.

    anamnesis.py bootstrap --company Apple --date 2026-04-28
        Create output/{slug}_{date}_{run_id}/ with the standard subfolders.
        Echoes the run dir path and the next step (read SKILL.md in the host).

    anamnesis.py precheck --run-dir <path> --ticker AAPL [--sector ...] [--geography US] [--period FY2026Q2]
        Run P0_DB_PRECHECK and write db_export/peer_context.json + db_export/prior_financials_used.json.

    anamnesis.py audit --run-dir <path>
        Run all four P12 layers in order, then aggregate to validation/post_card_audit.json + QA_REPORT.md.
        Exits 0 on pass/warn, 1 on fail.

    anamnesis.py index --run-dir <path>
        Run P_DB_INDEX (write the run's research artifacts into db/equity_kb.sqlite).

    anamnesis.py sector-report --type porter_heatmap --sector "Information Technology" --period FY2026Q2
        Generate a sector-level analytical artifact (HTML + JSON) in db/sector_reports/.

    anamnesis.py status
        Show the schema version, total runs in DB, and submodule SHAs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLS = PROJECT_ROOT / "tools"


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    return res.returncode


# ─────────────────────────────────────────────────────────────────────
# init
# ─────────────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    return _run([sys.executable, str(TOOLS / "db" / "migrate.py")])


# ─────────────────────────────────────────────────────────────────────
# bootstrap
# ─────────────────────────────────────────────────────────────────────

def cmd_bootstrap(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(TOOLS / "io" / "run_dir.py"),
           "--company", args.company, "--date", args.date]
    if args.run_id:
        cmd += ["--run-id", args.run_id]
    if args.output_root:
        cmd += ["--output-root", args.output_root]
    res = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    if res.returncode != 0:
        return res.returncode
    run_dir = res.stdout.strip().splitlines()[-1]
    print()
    print("Next steps (host LLM):")
    print("  1. Read SKILL.md, MEMORY.md, agents/orchestrator.md")
    print(f"  2. Drive phases P0_lang → P12 against run dir: {run_dir}")
    print("  3. After P11 render, run:")
    print(f"     python anamnesis.py audit --run-dir '{run_dir}'")
    print(f"     python anamnesis.py index --run-dir '{run_dir}'")
    return 0


# ─────────────────────────────────────────────────────────────────────
# precheck
# ─────────────────────────────────────────────────────────────────────

def cmd_precheck(args: argparse.Namespace) -> int:
    out_path = Path(args.run_dir) / "db_export" / "peer_context.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(TOOLS / "db" / "queries.py"),
           "--ticker", args.ticker, "--out", str(out_path)]
    if args.sector:
        cmd += ["--sector", args.sector]
    if args.geography:
        cmd += ["--geography", args.geography]
    if args.period:
        cmd += ["--period", args.period]
    return _run(cmd)


# ─────────────────────────────────────────────────────────────────────
# audit (P12 four layers + aggregate)
# ─────────────────────────────────────────────────────────────────────

def cmd_audit(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"error: {run_dir} does not exist", file=sys.stderr)
        return 2

    layers = [
        ("layer 1 — reconcile",  [sys.executable, str(TOOLS / "audit" / "reconcile_numbers.py"), "--run-dir", str(run_dir)]),
        ("layer 2 — OCR",        [sys.executable, str(TOOLS / "audit" / "ocr_cards.py"), "--run-dir", str(run_dir), "--lang", args.lang]),
        ("layer 3 — web envelope", [sys.executable, str(TOOLS / "audit" / "web_third_check.py"), "--run-dir", str(run_dir), "--top-n", str(args.top_n)]),
        ("layer 4 — DB cross",   [sys.executable, str(TOOLS / "audit" / "db_cross_validate.py"), "--run-dir", str(run_dir)]),
        ("privacy — User-Agent PII", [sys.executable, str(TOOLS / "audit" / "user_agent_pii.py"), "--run-dir", str(run_dir)]),
    ]
    for name, cmd in layers:
        print(f"\n>>> {name}")
        rc = _run(cmd)
        if rc != 0 and not args.continue_on_fail:
            print(f"\nlayer failed: {name} (exit {rc}). Aggregating partial results.", file=sys.stderr)
            break

    print("\n>>> aggregate")
    return _run([sys.executable, str(TOOLS / "audit" / "aggregate_p12.py"), "--run-dir", str(run_dir)])


# ─────────────────────────────────────────────────────────────────────
# index (P_DB_INDEX)
# ─────────────────────────────────────────────────────────────────────

def cmd_index(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(TOOLS / "db" / "index_run.py"), "--run-dir", args.run_dir]
    if args.db:
        cmd += ["--db", args.db]
    return _run(cmd)


# ─────────────────────────────────────────────────────────────────────
# sector-report
# ─────────────────────────────────────────────────────────────────────

def cmd_sector_report(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(TOOLS / "db" / "sector_report.py"),
           "--type", args.type, "--sector", args.sector]
    if args.period:
        cmd += ["--period", args.period]
    if args.out:
        cmd += ["--out", args.out]
    return _run(cmd)


# ─────────────────────────────────────────────────────────────────────
# status
# ─────────────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> int:
    import sqlite3
    db = PROJECT_ROOT / "db" / "equity_kb.sqlite"
    info: dict = {"db": str(db), "exists": db.exists()}
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            info["schema_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
            for table in ("companies", "runs", "financials_period", "porter_scores_period",
                          "macro_factors_period", "intelligence_signals", "edge_insights"):
                info[f"{table}_count"] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
    submods = {}
    for name in ("er", "ep"):
        sub = PROJECT_ROOT / "skills_repo" / name
        if (sub / ".git").exists() or (sub / "HEAD").exists():
            try:
                sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=sub,
                                     capture_output=True, text=True, check=True).stdout.strip()
                submods[name] = sha
            except (subprocess.CalledProcessError, FileNotFoundError):
                submods[name] = "unknown"
        else:
            submods[name] = "not_initialised"
    info["submodules"] = submods
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Apply DB migrations")
    p_init.set_defaults(func=cmd_init)

    p_boot = sub.add_parser("bootstrap", help="Create a per-run output directory")
    p_boot.add_argument("--company", required=True)
    p_boot.add_argument("--date", required=True)
    p_boot.add_argument("--run-id", default=None)
    p_boot.add_argument("--output-root", default=None)
    p_boot.set_defaults(func=cmd_bootstrap)

    p_pre = sub.add_parser("precheck", help="P0_DB_PRECHECK — peer/prior/macro context")
    p_pre.add_argument("--run-dir", required=True)
    p_pre.add_argument("--ticker", required=True)
    p_pre.add_argument("--sector", default=None)
    p_pre.add_argument("--geography", default=None)
    p_pre.add_argument("--period", default=None)
    p_pre.set_defaults(func=cmd_precheck)

    p_aud = sub.add_parser("audit", help="P12 — four layers + aggregator")
    p_aud.add_argument("--run-dir", required=True)
    p_aud.add_argument("--lang", default="cn", choices=["cn", "en"])
    p_aud.add_argument("--top-n", type=int, default=3)
    p_aud.add_argument("--continue-on-fail", action="store_true",
                       help="Run all four layers even if an earlier one returns non-zero")
    p_aud.set_defaults(func=cmd_audit)

    p_idx = sub.add_parser("index", help="P_DB_INDEX — persist run artifacts to DB")
    p_idx.add_argument("--run-dir", required=True)
    p_idx.add_argument("--db", default=None)
    p_idx.set_defaults(func=cmd_index)

    p_sr = sub.add_parser("sector-report", help="Cross-company analytical reports from DB")
    p_sr.add_argument("--type", required=True,
                       choices=["porter_heatmap", "macro_consistency",
                                "peer_growth_attribution", "signal_taxonomy"])
    p_sr.add_argument("--sector", required=True)
    p_sr.add_argument("--period", default=None)
    p_sr.add_argument("--out", default=None)
    p_sr.set_defaults(func=cmd_sector_report)

    p_st = sub.add_parser("status", help="Show DB + submodule state")
    p_st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
