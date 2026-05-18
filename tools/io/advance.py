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


_ANNOTATION_RE = re.compile(r"\s*\([^)]+\)\s*$")
_TEMPLATE_RE = re.compile(r"\{[^}]+\}")


@dataclass
class ParsedProduces:
    """A parsed entry from a phase's `produces[]` list.

    workflow_meta.json's `produces` strings come in four flavours and the
    pre-fix watchdog (Codex P2#2) just skipped anything with `{` or `:`,
    which silently let through unverified the very gates that matter most
    (`cards/{stem}.card_slots.json`, `research/structure_conformance.json:html_template_gate`,
    `validation/ocr_dump/card_{1..6}.txt`). Each kind needs a different
    on-disk check:

    - **exact**: a literal filesystem path (`research/edge_insights.json`).
      Check the file exists.
    - **glob**: a path with `{template}` placeholders or `{N..M}` ranges
      (`cards/{stem}.card_slots.json`, `validation/ocr_dump/card_{1..6}.txt`).
      Replace every `{...}` with `*` and require at least one match.
    - **json_key**: `path:key_name` (`meta/run.json:ticker`,
      `research/structure_conformance.json:html_template_gate`). Check the
      file exists AND the named top-level key is set (supports dotted
      traversal so `a.b.c` walks nested dicts).
    - **jsonl_event**: `path.jsonl:event_name` (`meta/run.jsonl:incident_precheck.acknowledged`).
      Check the file exists AND at least one line has `event == event_name`.

    Trailing annotations like `(audited)` / `(compressed)` are stripped
    before parsing — they document the intent of an in-place update phase
    and aren't part of the filename.
    """
    kind: str  # 'exact' | 'glob' | 'json_key' | 'jsonl_event'
    path: str  # filesystem path or glob (no annotation, no `:key` suffix)
    json_key: str | None  # for json_key / jsonl_event kinds


def _parse_produces(spec: str) -> ParsedProduces:
    spec = _ANNOTATION_RE.sub("", spec).strip()

    json_key: str | None = None
    path = spec
    if ":" in spec:
        path, _, json_key = spec.partition(":")

    kind: str
    if json_key is not None:
        kind = "jsonl_event" if path.endswith(".jsonl") else "json_key"
    else:
        kind = "exact"

    if _TEMPLATE_RE.search(path):
        if kind == "exact":
            kind = "glob"
        path = _TEMPLATE_RE.sub("*", path)

    return ParsedProduces(kind=kind, path=path, json_key=json_key)


def _file_has_json_key(path: Path, key: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    cur: object = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    # We accept `null`/`""`/`0`/`false` as "present" — the key being there
    # is enough; downstream validators judge the value.
    return True


def _file_has_jsonl_event(path: Path, event_name: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("event") == event_name:
            return True
    return False


def _verify_produces(run_dir: Path, spec: str) -> tuple[bool, str]:
    parsed = _parse_produces(spec)

    if parsed.kind == "exact":
        full = run_dir / parsed.path
        if not full.exists():
            return False, f"declared produce `{spec}` missing on disk ({full})"
        return True, ""

    if parsed.kind == "glob":
        matches = list(run_dir.glob(parsed.path))
        if not matches:
            return False, f"declared produce `{spec}` matched no file (glob: {parsed.path})"
        return True, ""

    if parsed.kind == "json_key":
        # Path may still contain glob characters from {template} substitution.
        if any(ch in parsed.path for ch in "*?["):
            matches = list(run_dir.glob(parsed.path))
            if not matches:
                return False, f"declared produce `{spec}`: no file matches glob {parsed.path}"
            for m in matches:
                if _file_has_json_key(m, parsed.json_key or ""):
                    return True, ""
            return False, (
                f"declared produce `{spec}`: file(s) exist but none carry json key "
                f"`{parsed.json_key}` ({[str(m) for m in matches]})"
            )
        full = run_dir / parsed.path
        if not full.exists():
            return False, f"declared produce `{spec}`: file {full} missing"
        if not _file_has_json_key(full, parsed.json_key or ""):
            return False, f"declared produce `{spec}`: file exists but json key `{parsed.json_key}` not found"
        return True, ""

    if parsed.kind == "jsonl_event":
        full = run_dir / parsed.path
        if not full.exists():
            return False, f"declared produce `{spec}`: jsonl {full} missing"
        if not _file_has_jsonl_event(full, parsed.json_key or ""):
            return False, (
                f"declared produce `{spec}`: jsonl exists but no event line "
                f"with event=`{parsed.json_key}`"
            )
        return True, ""

    return True, ""


def _predecessor_artifacts_present(run_dir: Path, phases: list[dict], next_idx: int) -> tuple[bool, str]:
    """Verify that each prior blocking phase's `produces[]` entries are satisfied.

    AND/OR semantics by kind:

    - Multiple `jsonl_event` entries that target the **same** .jsonl path
      are treated as alternatives (ANY-of). Example: P_INCIDENT_PRECHECK
      declares both `meta/run.jsonl:incident_precheck.acknowledged` and
      `meta/run.jsonl:incident_precheck.skipped` — a run with no
      superseded incidents only emits `acknowledged`, and that should
      satisfy the check. Treating them as AND would block legitimate runs.
    - Everything else (exact / glob / json_key) is AND — each entry must
      hold independently. Two files in produces[] means *both* exist;
      a glob and a key on the same file is still two checks.

    Missing artifacts are fast-model-skip evidence — refuse to advance
    until the predecessor actually ran.
    """
    for prior in phases[:next_idx]:
        if not prior.get("blocking", True):
            continue

        # Bucket jsonl_event entries by file path so we can ANY-of them.
        jsonl_groups: dict[str, list[str]] = {}
        non_jsonl_specs: list[str] = []
        for spec in prior.get("produces") or []:
            parsed = _parse_produces(spec)
            if parsed.kind == "jsonl_event":
                jsonl_groups.setdefault(parsed.path, []).append(spec)
            else:
                non_jsonl_specs.append(spec)

        for spec in non_jsonl_specs:
            ok, reason = _verify_produces(run_dir, spec)
            if not ok:
                return False, (
                    f"predecessor {prior['id']} failed produces check: {reason}. "
                    f"Do not advance until {prior['id']} actually ran."
                )

        for path, specs in jsonl_groups.items():
            full = run_dir / path
            if not full.exists():
                return False, (
                    f"predecessor {prior['id']} failed produces check: jsonl {full} missing"
                )
            any_match = False
            for spec in specs:
                ok, _ = _verify_produces(run_dir, spec)
                if ok:
                    any_match = True
                    break
            if not any_match:
                events = [_parse_produces(s).json_key for s in specs]
                return False, (
                    f"predecessor {prior['id']} failed produces check: "
                    f"{path} exists but has none of the declared events {events}. "
                    f"At least one alternative event is required as evidence the phase ran."
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
