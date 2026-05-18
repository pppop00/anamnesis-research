"""P10.6 voice-gate wrapper — Cards 1-5 analyst-content gate (plan v3).

The voice gate is the `validate_card1_5_analytical_content()` function inside
EP's `generate_social_cards.py`; it's invoked by running EP's
`skills_repo/ep/scripts/validate_cards.py` with both the rendered slots and
the mandatory `<stem>.card_slots_worker_notes.json` sidecar present in the
same directory. EP doesn't expose a separate CLI flag for the voice gate —
when both files are present the validator runs both checks (structural +
analytical), and the analytical-content failures appear in its stderr.

This wrapper exists for two reasons:

1. workflow_meta.json declares P10.6's `produces` as `validation/voice_gate.json`,
   but neither EP's validator nor the existing `tools/photo/validate_cards.py`
   wrapper writes it. Without this file, the phase-advance watchdog
   (tools/io/advance.py) cannot verify the gate ran (Codex P1#3).

2. Even though P10 and P10.6 use the *same* EP script, they semantically
   differ: P10 cares about structural / palette / logo issues; P10.6 cares
   about analyst-content issues from worker_notes. Running them as two
   separate wrappers — even if both invoke the same EP CLI — lets each
   produce its own purpose-built report and gives the orchestrator two
   distinct retry targets when one fails.

Usage:
    python tools/photo/voice_gate.py \
        --input <run_dir>/research/Apple_Research_CN.html \
        --slots <run_dir>/cards/Apple_Research_CN.card_slots.json \
        --brand "金融豹" \
        --palette <confirmed_palette>

Requires the sidecar `<stem>.card_slots_worker_notes.json` to exist next to
`--slots`; absent sidecar = fail status (the gate cannot certify analytical
content it cannot see). The report lands at
`<run_dir>/validation/voice_gate.json` by default — pass `--report-out` to
override.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
from _common import find_skill_root, python_exec, script_path  # noqa: E402


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _find_run_dir(slots_path: Path) -> Path | None:
    """Walk up from slots_path looking for a meta/run.json — that's the run root."""
    parent = slots_path if slots_path.is_dir() else slots_path.parent
    for _ in range(4):
        if (parent / "meta" / "run.json").exists():
            return parent
        if parent.parent == parent:
            break
        parent = parent.parent
    return None


def _worker_notes_sidecar(slots_path: Path) -> Path:
    """`Foo_Research_CN.card_slots.json` -> `Foo_Research_CN.card_slots_worker_notes.json`."""
    if slots_path.is_dir():
        # Pick the first card_slots.json under the dir.
        candidates = list(slots_path.glob("*.card_slots.json"))
        if not candidates:
            return slots_path / "card_slots_worker_notes.json"
        slots_path = candidates[0]
    stem = slots_path.name.replace(".card_slots.json", "")
    return slots_path.parent / f"{stem}.card_slots_worker_notes.json"


def _classify_issue(line: str) -> dict:
    """Turn EP's free-form issue string into a structured dict.

    EP emits lines like `worker_notes.brand_statement.data_anchor: no parseable number`.
    We tokenise by the first ':' so callers can pivot by (slot, field).
    """
    if ":" in line:
        head, _, rest = line.partition(":")
        parts = head.split(".")
        return {
            "slot": parts[1] if len(parts) > 1 else None,
            "field": parts[2] if len(parts) > 2 else None,
            "message": rest.strip(),
            "raw": line,
        }
    return {"slot": None, "field": None, "message": line, "raw": line}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--input", required=True, help="absolute path to *_Research_{CN,EN}.html")
    p.add_argument("--slots", required=True, help="absolute path to card_slots.json (file or parent dir)")
    p.add_argument("--brand", default="金融豹")
    p.add_argument("--palette", required=True, choices=["macaron", "default", "b", "c"])
    p.add_argument("--allow-no-logo", action="store_true")
    p.add_argument("--report-out", default=None,
                   help="Where to write voice_gate.json. Defaults to "
                        "<run_dir>/validation/voice_gate.json when a run dir can be inferred.")
    args = p.parse_args(argv)

    slots_path = Path(args.slots).resolve()
    sidecar = _worker_notes_sidecar(slots_path)
    run_dir = _find_run_dir(slots_path)

    report_path = (
        Path(args.report_out).resolve()
        if args.report_out
        else (run_dir / "validation" / "voice_gate.json" if run_dir else
              slots_path.parent / "voice_gate.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    run_meta = {}
    if run_dir is not None:
        try:
            run_meta = json.loads((run_dir / "meta" / "run.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            run_meta = {}

    # Sidecar absence is a fail-closed condition. The gate certifies analytical
    # content; without worker_notes there is nothing to certify.
    if not sidecar.exists():
        report = {
            "schema_version": 1,
            "ts": _now_iso(),
            "run_id": run_meta.get("run_id"),
            "ticker": run_meta.get("ticker"),
            "tool": "tools/photo/voice_gate.py",
            "status": "fail",
            "reason": "worker_notes sidecar not found",
            "sidecar_path_checked": str(sidecar),
            "issues": [{"slot": None, "field": None,
                        "message": f"missing required sidecar: {sidecar.name}",
                        "raw": ""}],
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"voice_gate.json -> {report_path}", file=sys.stderr)
        print(f"error: missing worker_notes sidecar at {sidecar}", file=sys.stderr)
        return 1

    ep_root = find_skill_root("ep")
    validator = script_path("ep", "scripts", "validate_cards.py")
    cmd = [
        python_exec(),
        str(validator),
        "--input", args.input,
        "--slots", args.slots,
        "--brand", args.brand,
        "--palette", args.palette,
    ]
    if args.allow_no_logo:
        cmd.append("--allow-no-logo")

    result = subprocess.run(cmd, cwd=str(ep_root), capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)

    # Voice-gate issues live under `worker_notes.*` and `card_slots.*` (banned-phrase
    # backstops). Filter EP's stderr to those buckets so the report is voice-focused;
    # purely structural issues belong to validator1_report.json.
    voice_issues = []
    for line in result.stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("worker_notes.", "card_slots.")):
            voice_issues.append(_classify_issue(line))

    status = "pass" if result.returncode == 0 and not voice_issues else "fail"
    report = {
        "schema_version": 1,
        "ts": _now_iso(),
        "run_id": run_meta.get("run_id"),
        "ticker": run_meta.get("ticker"),
        "tool": "tools/photo/voice_gate.py (skills_repo/ep/scripts/validate_cards.py)",
        "status": status,
        "exit_code": result.returncode,
        "worker_notes_present": True,
        "sidecar_path": str(sidecar),
        "slots_path": str(slots_path),
        "input_html": args.input,
        "palette": args.palette,
        "brand": args.brand,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "issues": voice_issues,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"voice_gate.json -> {report_path}", file=sys.stderr)

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
