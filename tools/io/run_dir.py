"""Bootstrap a per-run output directory under output/{Company}_{Date}_{RunID}/.

Creates the standard subfolder layout (meta/, research/, cards/, validation/, db_export/, logs/),
seeds meta/run.jsonl with a `bootstrap_started` event, and prints the absolute run-dir path.

Usage:
    python tools/io/run_dir.py --company Apple --date 2026-04-28
    python tools/io/run_dir.py --company Apple --date 2026-04-28 --run-id a1b2c3d4
    python tools/io/run_dir.py --company Apple --date 2026-04-28 --output-root /tmp/runs
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output"

SUBFOLDERS = (
    "meta",
    "research",
    "cards",
    "cards/logo",
    "validation",
    "validation/ocr_dump",
    "db_export",
    "logs",
)


def slugify(name: str) -> str:
    """ASCII slug for folder names. Falls back to underscores; never empty."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return s or "Company"


def init_run_dir(
    company: str,
    date: str,
    run_id: str | None = None,
    output_root: Path | None = None,
    orchestrator_model: str | None = None,
) -> Path:
    if run_id is None:
        run_id = secrets.token_hex(4)
    if output_root is None:
        output_root = DEFAULT_OUTPUT_ROOT

    slug = slugify(company)
    run_dir = output_root / f"{slug}_{date}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)

    for sub in SUBFOLDERS:
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    run_jsonl = run_dir / "meta" / "run.jsonl"
    event = {
        "ts": _now_iso(),
        "phase": "bootstrap",
        "event": "started",
        "payload": {
            "company": company,
            "slug": slug,
            "date": date,
            "run_id": run_id,
            "run_dir": str(run_dir.resolve()),
            "output_root": str(output_root.resolve()),
            "orchestrator_model": orchestrator_model,
        },
    }
    with run_jsonl.open("w", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    run_json_payload = {
        "run_id": run_id,
        "company": company,
        "slug": slug,
        "date": date,
        "started_at": event["ts"],
        "schema_version": 1,
    }
    if orchestrator_model is not None:
        run_json_payload["orchestrator_model"] = orchestrator_model

    run_json = run_dir / "meta" / "run.json"
    run_json.write_text(
        json.dumps(run_json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return run_dir


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None) -> int:
    # Allow `python tools/io/run_dir.py ...` to find sibling modules.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tools.io.model_gate import classify

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--company", required=True, help="Company display name (will be slugified)")
    p.add_argument("--date", required=True, help="Run date in YYYY-MM-DD form")
    p.add_argument("--run-id", default=None, help="Override generated 8-hex run id")
    p.add_argument("--output-root", default=None, help="Output directory root (default: ./output)")
    p.add_argument(
        "--orchestrator-model",
        required=True,
        help=(
            "Model id of the LLM driving the run (e.g. claude-opus-4-7, "
            "claude-sonnet-4-6). Refused for Haiku/Instant families — see "
            "tools/io/model_gate.py."
        ),
    )
    args = p.parse_args(argv)

    gate = classify(args.orchestrator_model)
    if not gate.allowed:
        print(gate.message, file=sys.stderr)
        return 2
    if gate.message:
        print(gate.message, file=sys.stderr)

    output_root = Path(args.output_root).resolve() if args.output_root else None
    try:
        run_dir = init_run_dir(
            args.company, args.date, args.run_id, output_root,
            orchestrator_model=args.orchestrator_model,
        )
    except FileExistsError:
        print(f"error: run dir already exists for {args.company} {args.date} {args.run_id}", file=sys.stderr)
        return 2
    print(str(run_dir.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
