"""Validate and optionally normalize a per-run artifact directory.

The run-dir root is an index, not a dumping ground. Customer-facing deliverables
are the HTML report under research/ and the six PNG cards under cards/. All JSON
contracts, gates, logs, and DB summaries must live in their phase subfolders.

Usage:
    python tools/io/validate_run_artifacts.py --run-dir output/Apple_2026-05-17_abcd1234
    python tools/io/validate_run_artifacts.py --run-dir output/Apple_2026-05-17_abcd1234 --fix
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ALLOWED_ROOT_DIRS = {"meta", "research", "cards", "validation", "db_export", "logs"}
ALLOWED_ROOT_FILES = {".DS_Store"}

RESEARCH_EXACT = {
    "financial_data.json",
    "macro_factors.json",
    "news_intel.json",
    "edge_insights.json",
    "financial_analysis.json",
    "prediction_waterfall.json",
    "porter_analysis.json",
    "qc_macro_peer_a.json",
    "qc_macro_peer_b.json",
    "qc_porter_peer_a.json",
    "qc_porter_peer_b.json",
    "qc_audit_trail.json",
    "cross_validation.json",
    "final_report_data_validation.json",
    "report_validation.txt",
    "structure_conformance.json",
    "sec_edgar_bundle.json",
}

VALIDATION_EXACT = {
    "post_card_audit.json",
    "QA_REPORT.md",
    "reconciliation.csv",
    "ocr_summary.json",
    "web_third_check.json",
    "db_cross.json",
    "user_agent_pii.json",
    "incident_postcheck.json",
    "porter_depth_gate.json",
    "voice_gate.json",
}

DB_EXPORT_EXACT = {
    "rows_written.json",
    "peer_context.json",
    "prior_financials_used.json",
    "db_index_summary.json",
    "index_error.json",
}

META_EXACT = {
    "run.json",
    "run.jsonl",
    "gates.json",
    "submodule_shas.json",
    "workflow_meta.snapshot.json",
    "system_prompt.frozen.txt",
}


def destination_for(name: str) -> str | None:
    if name in RESEARCH_EXACT or name.startswith("_locked_") and name.endswith("_skeleton.html"):
        return "research"
    if name.endswith("_Research_CN.html") or name.endswith("_Research_EN.html"):
        return "research"
    if name in VALIDATION_EXACT or name.startswith("red_team_"):
        return "validation"
    if name in DB_EXPORT_EXACT:
        return "db_export"
    if name in META_EXACT:
        return "meta"
    if name.endswith(".card_slots.json") or name.endswith(".card_slots_worker_notes.json"):
        return "cards"
    if name.startswith("validator") and name.endswith("_report.json"):
        return "cards"
    if name.endswith(".png") and name[:2].isdigit():
        return "cards"
    if name.endswith(".log") or name.endswith(".jsonl"):
        return "logs"
    return None


def move_into(run_dir: Path, item: Path, dest_dir: str) -> str:
    dest_root = run_dir / dest_dir
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / item.name
    if dest.exists():
        raise FileExistsError(f"destination already exists: {dest}")
    shutil.move(str(item), str(dest))
    return f"{item.name} -> {dest_dir}/{item.name}"


def validate(run_dir: Path, *, fix: bool = False) -> dict:
    errors: list[str] = []
    moved: list[str] = []

    if not run_dir.is_dir():
        return {"status": "critical", "errors": [f"run dir not found: {run_dir}"], "moved": []}

    for dirname in sorted(ALLOWED_ROOT_DIRS):
        if not (run_dir / dirname).is_dir():
            errors.append(f"missing required subfolder: {dirname}/")

    for item in sorted(run_dir.iterdir(), key=lambda p: p.name):
        if item.is_dir():
            if item.name not in ALLOWED_ROOT_DIRS:
                errors.append(f"unexpected root directory: {item.name}/")
            continue
        if item.name in ALLOWED_ROOT_FILES:
            continue
        dest_dir = destination_for(item.name)
        if dest_dir and fix:
            try:
                moved.append(move_into(run_dir, item, dest_dir))
            except FileExistsError as exc:
                errors.append(str(exc))
        elif dest_dir:
            errors.append(f"misplaced root artifact: {item.name} (expected {dest_dir}/)")
        else:
            errors.append(f"unknown root artifact: {item.name}")

    return {"status": "critical" if errors else "pass", "errors": errors, "moved": moved}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--fix", action="store_true", help="Move known misplaced root artifacts into subfolders.")
    args = p.parse_args(argv)

    result = validate(Path(args.run_dir).resolve(), fix=args.fix)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
