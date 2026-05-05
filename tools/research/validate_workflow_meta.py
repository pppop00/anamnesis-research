"""Validate Anamnesis Research's workflow_meta.json contract.

Default: validates Anamnesis Research's own root workflow_meta.json (the fusion contract that
the orchestrator drives). Pass --target er to delegate to skills_repo/er's own
validator over ER's contract instead — those are different schemas and must not be
mixed.

Usage:
    python tools/research/validate_workflow_meta.py                     # validate Anamnesis Research root
    python tools/research/validate_workflow_meta.py --meta path/to/file # validate a specific file as Anamnesis Research schema
    python tools/research/validate_workflow_meta.py --target er         # delegate to ER's validator over ER's own meta
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from _common import find_skill_root, python_exec, script_path  # type: ignore[import-not-found]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Anamnesis Research fusion-contract requirements. Keep in sync with workflow_meta.json shape.
REQUIRED_TOP_LEVEL = [
    "schema_version",
    "name",
    "phases",
    "subagent_concurrency_cap",
    "subagent_timeouts_seconds",
    "submodules",
    "memory_files",
    "freeze_system_prompt_at",
    "system_prompt_audit_path",
]

REQUIRED_PHASE_KEYS = ["id", "produces", "blocking", "interactive", "parallelism"]
ALLOWED_PARALLELISM = {"sequential", "parallel"}
REQUIRED_TIMEOUT_FAMILIES = {"research", "photo", "qc", "audit"}


def _err(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def _phase_id_alternates(pid: str) -> list[str]:
    """Return the set of acceptable spellings for a phase id.

    Always includes the literal underscore form. If the id has a numeric
    "Pn_m_..." prefix, also includes the dotted "Pn.m" form. Ids like
    'P0_intent', 'P_INCIDENT_PRECHECK', 'P_DB_INDEX' have no dotted form.
    """
    forms = [pid]
    # Match leading P + integer + _ + integer (the "Pn_m" numeric prefix).
    m = re.match(r"^P(\d+)_(\d+)(?:_|$)", pid)
    if m:
        forms.append(f"P{m.group(1)}.{m.group(2)}")
    return forms


def validate_root_meta(meta_path: Path) -> int:
    if not meta_path.exists():
        print(f"error: not found: {meta_path}", file=sys.stderr)
        return 2

    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError as e:
        print(f"error: {meta_path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    print(f"validating Anamnesis Research contract: {meta_path}")
    errors = 0

    for key in REQUIRED_TOP_LEVEL:
        if key not in meta:
            _err(f"missing top-level key: {key!r}")
            errors += 1

    phases = meta.get("phases")
    if not isinstance(phases, list) or not phases:
        _err("'phases' must be a non-empty array")
        errors += 1
        phases = []

    seen_ids: set[str] = set()
    for i, phase in enumerate(phases):
        prefix = f"phase[{i}]"
        if not isinstance(phase, dict):
            _err(f"{prefix} is not an object")
            errors += 1
            continue
        pid = phase.get("id")
        if pid:
            prefix = f"phase {pid!r}"
            if pid in seen_ids:
                _err(f"duplicate phase id: {pid!r}")
                errors += 1
            seen_ids.add(pid)

        for k in REQUIRED_PHASE_KEYS:
            if k not in phase:
                _err(f"{prefix} missing required key: {k!r}")
                errors += 1

        par = phase.get("parallelism")
        if par is not None and par not in ALLOWED_PARALLELISM:
            _err(f"{prefix} parallelism={par!r} not in {sorted(ALLOWED_PARALLELISM)}")
            errors += 1

        if par == "parallel" and "agents" not in phase:
            _err(f"{prefix} parallelism='parallel' but no 'agents' array")
            errors += 1

        # A phase must drive *something*: an agent, a list of agents, a tool,
        # or be explicitly marked inline (orchestrator runs it directly).
        if not any(k in phase for k in ("agent", "agents", "tool")) and not phase.get("inline"):
            _err(f"{prefix} declares no executor (need 'agent' / 'agents' / 'tool' / 'inline: true')")
            errors += 1

    # retry_to targets must reference known phase IDs.
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        target = phase.get("retry_to")
        if target and target not in seen_ids:
            _err(f"phase {phase.get('id')!r} retry_to={target!r} is not a known phase id")
            errors += 1

    timeouts = meta.get("subagent_timeouts_seconds", {})
    if isinstance(timeouts, dict):
        missing = REQUIRED_TIMEOUT_FAMILIES - set(timeouts)
        if missing:
            _err(f"subagent_timeouts_seconds missing: {sorted(missing)}")
            errors += 1

    submodules = meta.get("submodules", {})
    if isinstance(submodules, dict):
        for expected in ("skills_repo/er", "skills_repo/ep"):
            if expected not in submodules:
                _err(f"submodules missing entry: {expected!r}")
                errors += 1

    # ---- Semantic cross-reference checks ----------------------------------
    # These guard against drift between workflow_meta.json and the prose docs
    # that describe the phases. Each check accumulates into `errors` so the
    # existing fail/pass logic still applies.

    phase_ids = [p["id"] for p in phases if isinstance(p, dict) and p.get("id")]

    # Check 1: orchestrator.md must mention every phase id at least once.
    print("cross-ref: orchestrator coverage")
    orch_path = PROJECT_ROOT / "agents" / "orchestrator.md"
    if not orch_path.exists():
        _err(f"agents/orchestrator.md not found at {orch_path}")
        errors += 1
    else:
        orch_text = orch_path.read_text()
        for pid in phase_ids:
            if pid not in orch_text:
                _err(f"phase {pid!r} is not mentioned in agents/orchestrator.md (orchestrator drift)")
                errors += 1

    # Check 2: phase_contract.md must mention every phase id, accepting either
    # the literal underscore form or the dotted equivalent (P5_7_RED_TEAM ↔ P5.7).
    print("cross-ref: phase_contract coverage")
    pc_path = PROJECT_ROOT / "references" / "phase_contract.md"
    if not pc_path.exists():
        _err(f"references/phase_contract.md not found at {pc_path}")
        errors += 1
    else:
        pc_text = pc_path.read_text()
        for pid in phase_ids:
            alts = _phase_id_alternates(pid)
            if not any(alt in pc_text for alt in alts):
                _err(f"phase {pid!r} missing from references/phase_contract.md (prose drift)")
                errors += 1

    # Check 3: tokens on `- **Phase:** ...` lines in INCIDENTS.md must resolve
    # to a known phase id (literal or dotted form).
    print("cross-ref: INCIDENTS.md phase ids")
    incidents_path = PROJECT_ROOT / "INCIDENTS.md"
    if not incidents_path.exists():
        _err(f"INCIDENTS.md not found at {incidents_path}")
        errors += 1
    else:
        # Build the acceptance set: every literal id plus every dotted alternate.
        known: set[str] = set()
        for pid in phase_ids:
            for alt in _phase_id_alternates(pid):
                known.add(alt)

        # Match phase-id-shaped tokens. We accept three families:
        #   * `P\d+\.\d+`       dotted numeric (P5.7, P10.5)
        #   * `P_[A-Za-z0-9_]+` underscore-prefixed (P_INCIDENT_PRECHECK)
        #   * `P\d+(?:_\w+)+`   numeric prefix with at least one underscore tail
        # We deliberately do NOT match bare `P5` or `P10` — those are too noisy
        # in narrative text to be considered phase references.
        token_re = re.compile(
            r"\b(?:P\d+\.\d+|P_[A-Za-z0-9_]+|P\d+(?:_[A-Za-z0-9]+)+)\b"
        )
        phase_line_re = re.compile(r"-\s*\*\*Phase:\*\*\s*(.*)$")

        for raw in incidents_path.read_text().splitlines():
            m = phase_line_re.search(raw)
            if not m:
                continue
            tail = m.group(1)
            for tok in token_re.findall(tail):
                if tok not in known:
                    _err(f"INCIDENTS.md references unknown phase {tok!r} (incident drift)")
                    errors += 1

    if errors:
        print(f"\nFAIL: {errors} error(s) in {meta_path.name}", file=sys.stderr)
        return 1

    print(f"OK: {meta_path.name} ({len(phases)} phases, {len(seen_ids)} unique IDs)")
    return 0


def validate_er_meta(meta_path: str | None) -> int:
    er_root = find_skill_root("er")
    er_validator = script_path("er", "scripts", "validate_workflow_meta.py")
    target = meta_path or str(er_root / "workflow_meta.json")
    cmd = [python_exec(), str(er_validator), "--meta", target]
    try:
        result = subprocess.run(cmd, cwd=str(er_root), capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument(
        "--target",
        choices=["root", "er"],
        default="root",
        help="Which contract to validate. Default 'root' validates this repo's root workflow_meta.json.",
    )
    p.add_argument(
        "--meta",
        default=None,
        help="Path to workflow_meta.json. If omitted, validates the default for the chosen target.",
    )
    args = p.parse_args(argv)

    if args.target == "er":
        return validate_er_meta(args.meta)

    meta_path = Path(args.meta) if args.meta else PROJECT_ROOT / "workflow_meta.json"
    return validate_root_meta(meta_path)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
