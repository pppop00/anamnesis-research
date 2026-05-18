"""Phase advancement watchdog — externalises the orchestrator state machine.

The fast-model-skips-steps failure mode (INCIDENTS I-001, I-002) happens
because phase advancement currently lives in prose: the orchestrator
*should* halt and ask, *should* run the validator, *should* not move on
until red-team is clean. A model that compresses the contract too
aggressively just writes `phase_exit P5_html` and proceeds.

`advance` flips that: the LLM still drives the run, but before each phase
it calls this CLI, and the CLI says either "OK, your next phase is X,
here are its inputs/tools/produces" or "blocked: gate Y is unsatisfied
because Z". Exit code is the contract — exit 0 = proceed, exit 1 = halt
and surface to user, exit 2 = error reading run state.

This does NOT replace `agents/orchestrator.md`. It is a watchdog that
catches the model when it tries to advance past an unsatisfied gate or a
missing predecessor artifact. Think of `agents/orchestrator.md` as the
playbook and `advance` as the referee.

Scope (intentional):
- Validates P0 interactive gate sources against the whitelist
  (references/p0_gates.md).
- Verifies predecessor artifacts exist on disk for phases that produce
  concrete files (skips JSON-key produces like `meta/run.json:ticker`).
- Returns the next phase from workflow_meta.json in declaration order,
  walking past `phase_exit` events found in run.jsonl.

Scope (intentional non-goals):
- Does not run subagents. Does not produce artifacts.
- Does not enforce P12 audit content (that's tools/audit/aggregate_p12.py).
- Does not enforce template SHA (that's
  tools/research/validate_report_html.py).
- Does not lint the schema of produced files (that's per-phase validators).

It is the cheapest correct gate that catches the "fast model skips
phases" failure mode at advance-time rather than at end-of-run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_META = PROJECT_ROOT / "workflow_meta.json"


GATE_SOURCE_WHITELIST: dict[str, set[str]] = {
    "P0_intent": {"prompt_unambiguous", "user_response"},
    "P0_lang": {"user_response", "USER.md sticky", "explicit_phrase"},
    "P0_sec_email": {"user_response", "USER.md sticky", "skipped", "declined"},
    "P0_palette": {"user_response", "USER.md sticky"},
}

INTERACTIVE_GATES = ("P0_lang", "P0_sec_email", "P0_palette")


@dataclass
class AdvanceResult:
    ok: bool
    next_phase_id: str | None
    reason: str  # human-readable
    phase_meta: dict | None  # full phase entry from workflow_meta.json


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _exited_phases(events: list[dict]) -> list[str]:
    """Phase ids that have a `phase_exit` event, in log order."""
    return [e.get("phase", "") for e in events
            if e.get("event") == "phase_exit" and e.get("phase")]


def _gate_source_valid(gates_json: dict, gate_id: str) -> tuple[bool, str]:
    """Return (valid, reason). Valid means source is in whitelist."""
    entry = gates_json.get(gate_id) or {}
    source = entry.get("source")
    whitelist = GATE_SOURCE_WHITELIST.get(gate_id, set())
    if source is None:
        return False, f"meta/gates.json has no entry for {gate_id}"
    if source not in whitelist:
        return False, (
            f"meta/gates.json[{gate_id}].source = {source!r} is not in the "
            f"whitelist {sorted(whitelist)} — interactive gate cannot be "
            f"satisfied by an invented default (see INCIDENTS.md I-001)"
        )
    return True, ""


_PRODUCES_PLACEHOLDER = re.compile(r"[{}:]")


def _concrete_produces(produces: list[str]) -> list[str]:
    """Filter `produces` to entries that look like real filesystem paths.

    Excludes `meta/run.json:ticker` (JSON key into a file) and
    `cards/{stem}.card_slots.json` (template placeholders).
    """
    out = []
    for p in produces or []:
        if _PRODUCES_PLACEHOLDER.search(p):
            continue
        out.append(p)
    return out


def _predecessor_artifacts_present(run_dir: Path, phases: list[dict], next_idx: int) -> tuple[bool, str]:
    """Verify that each prior blocking phase's concrete `produces[]` paths exist."""
    for prior in phases[:next_idx]:
        if not prior.get("blocking", True):
            continue
        for rel in _concrete_produces(prior.get("produces", [])):
            full = run_dir / rel
            if not full.exists():
                return False, (
                    f"predecessor {prior['id']} declared `{rel}` in `produces` "
                    f"but {full} is missing on disk. Fast-model skip suspected — "
                    f"do not advance until {prior['id']} actually ran."
                )
    return True, ""


def compute_next_phase(run_dir: Path, workflow_meta: dict) -> AdvanceResult:
    phases = workflow_meta.get("phases", [])
    if not phases:
        return AdvanceResult(False, None, "workflow_meta.json has no phases[]", None)

    events = _read_jsonl(run_dir / "meta" / "run.jsonl")
    exited = _exited_phases(events)

    # Find the index of the next phase: first phase whose id is not in
    # the exited set. Walk in declaration order — workflow_meta is
    # authoritative for ordering.
    next_idx: int | None = None
    for i, p in enumerate(phases):
        if p["id"] not in exited:
            next_idx = i
            break
    if next_idx is None:
        return AdvanceResult(True, None, "all phases have phase_exit events; run is complete", None)

    next_phase = phases[next_idx]

    # Check predecessor artifacts.
    ok, reason = _predecessor_artifacts_present(run_dir, phases, next_idx)
    if not ok:
        return AdvanceResult(False, next_phase["id"], reason, next_phase)

    # If the next phase is an interactive P0 gate, that gate itself needs
    # to be answered before phase_exit. We do not check it here — the
    # gate is what produces gates.json. But once we are PAST the
    # interactive gates, all prior interactive gates must be satisfied in
    # gates.json.
    gates_path = run_dir / "meta" / "gates.json"
    gates_json = {}
    if gates_path.exists():
        try:
            gates_json = json.loads(gates_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return AdvanceResult(False, next_phase["id"],
                                 f"meta/gates.json is not valid JSON", next_phase)

    for gate_id in INTERACTIVE_GATES:
        gate_idx = next((i for i, p in enumerate(phases) if p["id"] == gate_id), None)
        if gate_idx is None:
            continue
        # If this gate's phase is in the past (already exited), its source must be valid.
        if gate_id in exited or gate_idx < next_idx:
            valid, why = _gate_source_valid(gates_json, gate_id)
            if not valid:
                return AdvanceResult(False, next_phase["id"], why, next_phase)

    return AdvanceResult(True, next_phase["id"], "ok", next_phase)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--run-dir", required=True, help="Path to output/{Company}_{Date}_{RunID}/")
    p.add_argument("--format", default="human", choices=("human", "json"),
                   help="human = printable summary; json = machine-readable")
    p.add_argument("--workflow-meta", default=None,
                   help="Override path to workflow_meta.json (default: repo root)")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"error: run dir {run_dir} does not exist", file=sys.stderr)
        return 2

    meta_path = Path(args.workflow_meta) if args.workflow_meta else WORKFLOW_META
    if not meta_path.exists():
        print(f"error: workflow_meta.json not found at {meta_path}", file=sys.stderr)
        return 2
    workflow_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    result = compute_next_phase(run_dir, workflow_meta)

    if args.format == "json":
        payload = {
            "ok": result.ok,
            "next_phase_id": result.next_phase_id,
            "reason": result.reason,
        }
        if result.phase_meta is not None:
            payload["phase_meta"] = {
                k: result.phase_meta.get(k)
                for k in ("id", "agent", "agents", "tool", "tools", "produces",
                          "blocking", "interactive", "parallelism", "concurrency",
                          "executor_note")
                if result.phase_meta.get(k) is not None
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if result.ok and result.next_phase_id:
            print(f"next phase: {result.next_phase_id}")
            if result.phase_meta:
                for key in ("agent", "agents", "tool", "tools", "produces"):
                    val = result.phase_meta.get(key)
                    if val:
                        print(f"  {key}: {val}")
        elif result.ok:
            print(result.reason)
        else:
            print(f"BLOCKED at {result.next_phase_id or '<unknown>'}: {result.reason}",
                  file=sys.stderr)

    if not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
