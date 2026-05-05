"""Tests for the institutional-memory loop contract.

The orchestrator's claim — "relapse on a known incident blocks delivery" — is
written in markdown briefs that no test enforces. These tests bracket the loop
with mechanical assertions so a regression on either side surfaces in CI:

- The `incident_postcheck.json` schema accepts only `pass | flagged | skipped`
  and rejects fabricated statuses (e.g. `pass_with_scope_limitations`).
- A `skipped` entry must carry a `superseded_by` pointer; otherwise it is a
  silent skip — exactly the failure the lifecycle adds were designed to prevent.
- `tools/io/lint_incidents.py` exits 0 against the committed `INCIDENTS.md`.
- The supersede graph is bidirectional (A says superseded-by-B ⇔ B says
  supersedes-A).
- `workflow_meta.json` wires `P_DB_INDEX.requires` to BOTH `P12_final_audit`
  and `P_INCIDENT_POSTCHECK`, so any contract-honoring runner blocks DB write
  on either gate.
- Every phase id mentioned in an `INCIDENTS.md` `Phase:` line resolves to a
  real phase id in `workflow_meta.json`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCIDENTS = ROOT / "INCIDENTS.md"
WORKFLOW_META = ROOT / "workflow_meta.json"
LINT = ROOT / "tools" / "io" / "lint_incidents.py"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _parse_incidents():
    """Use the lint module's parser so the test and the tool agree on format."""
    sys.path.insert(0, str(ROOT))
    from tools.io.lint_incidents import _parse_entries  # type: ignore[import]
    return _parse_entries(INCIDENTS.read_text(encoding="utf-8"))


_VALID_STATUSES = {"pass", "flagged", "skipped"}


def _validate_postcheck_schema(payload: dict) -> list[str]:
    """Return a list of human-readable error strings (empty list = valid)."""
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version != 1")
    incidents = payload.get("incidents")
    if not isinstance(incidents, list):
        errors.append("incidents must be a list")
        return errors
    for i, entry in enumerate(incidents):
        if not isinstance(entry, dict):
            errors.append(f"incidents[{i}] not an object")
            continue
        for required in ("id", "status", "evidence"):
            if required not in entry:
                errors.append(f"incidents[{i}] missing {required}")
        if entry.get("status") not in _VALID_STATUSES:
            errors.append(
                f"incidents[{i}] status={entry.get('status')!r} not in pass | flagged | skipped"
            )
        if entry.get("status") == "skipped" and "superseded_by" not in entry:
            errors.append(f"incidents[{i}] skipped requires superseded_by pointer")
    flagged = payload.get("flagged")
    if not isinstance(flagged, list):
        errors.append("flagged must be a list")
    return errors


# ─────────────────────────────────────────────────────────────────────
# Lint integration
# ─────────────────────────────────────────────────────────────────────

def test_lint_passes_on_committed_incidents():
    """The committed INCIDENTS.md must pass `tools/io/lint_incidents.py` cold."""
    rc = subprocess.call([sys.executable, str(LINT)])
    assert rc == 0, "lint_incidents.py reported drift on the committed INCIDENTS.md"


# ─────────────────────────────────────────────────────────────────────
# Post-check schema
# ─────────────────────────────────────────────────────────────────────

def test_postcheck_schema_accepts_valid_payload():
    valid = {
        "schema_version": 1,
        "incidents": [
            {"id": "I-001", "status": "pass", "evidence": "meta/gates.json"},
            {
                "id": "I-007",
                "status": "skipped",
                "superseded_by": "I-019",
                "evidence": "INCIDENTS.md",
            },
            {"id": "I-013", "status": "flagged", "evidence": "validation/foo.json"},
        ],
        "flagged": ["I-013"],
    }
    assert _validate_postcheck_schema(valid) == []


def test_postcheck_schema_rejects_invented_status():
    """`pass_with_scope_limitations` and friends must never be accepted (per I-002)."""
    invalid = {
        "schema_version": 1,
        "incidents": [
            {"id": "I-001", "status": "pass_with_scope_limitations", "evidence": "x"}
        ],
        "flagged": [],
    }
    errors = _validate_postcheck_schema(invalid)
    assert any("pass | flagged | skipped" in e for e in errors), errors


def test_postcheck_schema_rejects_skipped_without_supersedes_pointer():
    """A `skipped` entry without `superseded_by` is indistinguishable from a
    silent skip — the very failure the lifecycle adds were designed to surface.
    """
    invalid = {
        "schema_version": 1,
        "incidents": [{"id": "I-001", "status": "skipped", "evidence": "x"}],
        "flagged": [],
    }
    errors = _validate_postcheck_schema(invalid)
    assert any("superseded_by" in e for e in errors), errors


def test_postcheck_schema_rejects_missing_required_field():
    invalid = {
        "schema_version": 1,
        "incidents": [{"id": "I-001", "status": "pass"}],  # no evidence
        "flagged": [],
    }
    errors = _validate_postcheck_schema(invalid)
    assert any("evidence" in e for e in errors), errors


# ─────────────────────────────────────────────────────────────────────
# Coverage: every incident reachable from a well-formed payload
# ─────────────────────────────────────────────────────────────────────

def test_all_incidents_reachable_in_a_well_formed_payload():
    """The post-check contract is one entry per incident. Build a synthetic
    pass-payload from the parsed INCIDENTS and assert it validates and that
    every I-NNN appears.
    """
    entries = _parse_incidents()
    assert entries, "INCIDENTS.md should contain at least one entry"
    payload = {
        "schema_version": 1,
        "incidents": [
            {"id": e["id"], "status": "pass", "evidence": "meta/gates.json"}
            for e in entries
        ],
        "flagged": [],
    }
    assert _validate_postcheck_schema(payload) == []
    assert {x["id"] for x in payload["incidents"]} == {e["id"] for e in entries}


# ─────────────────────────────────────────────────────────────────────
# Supersede lifecycle (bidirectional pointers)
# ─────────────────────────────────────────────────────────────────────

_ID_RE = re.compile(r"\bI-\d{3}\b")


def test_supersede_graph_is_bidirectional():
    """If A is `Status: superseded` with `Superseded by: B`, then B must
    declare `Supersedes: A`. The lint enforces this; we double-check at
    unit-test level so a regression in either side surfaces independently.
    """
    entries = _parse_incidents()
    by_id = {e["id"]: e for e in entries}
    for e in entries:
        status = e["fields"].get("Status", "active").lower()
        if status != "superseded":
            continue
        refs = _ID_RE.findall(e["fields"].get("Superseded by", ""))
        assert refs, f"{e['id']}: Status=superseded but no Superseded by pointer"
        for ref in refs:
            assert ref in by_id, f"{e['id']}: Superseded by {ref!r} not found"
            back = _ID_RE.findall(by_id[ref]["fields"].get("Supersedes", ""))
            assert e["id"] in back, (
                f"{e['id']}: superseded by {ref}, but {ref} does not declare "
                f"Supersedes: {e['id']}"
            )


# ─────────────────────────────────────────────────────────────────────
# Phase wiring: P_DB_INDEX must require BOTH gates
# ─────────────────────────────────────────────────────────────────────

def test_db_index_requires_both_p12_and_postcheck():
    """The dual-gate contract: any runner must check `requires`, not just
    `runs_after`, so neither P12 nor the post-check can be silently dropped.
    """
    meta = json.loads(WORKFLOW_META.read_text())
    phases = {p["id"]: p for p in meta["phases"]}
    assert "P_DB_INDEX" in phases
    requires = phases["P_DB_INDEX"].get("requires", [])
    assert "P12_final_audit" in requires, "P_DB_INDEX must require P12_final_audit"
    assert "P_INCIDENT_POSTCHECK" in requires, (
        "P_DB_INDEX must require P_INCIDENT_POSTCHECK"
    )


def test_postcheck_phase_runs_after_p12():
    """Spirit check: the post-check is positioned after P12, not before."""
    meta = json.loads(WORKFLOW_META.read_text())
    phases = {p["id"]: p for p in meta["phases"]}
    pc = phases.get("P_INCIDENT_POSTCHECK", {})
    assert pc.get("runs_after") == "P12_final_audit", (
        "P_INCIDENT_POSTCHECK must declare runs_after: P12_final_audit"
    )


# ─────────────────────────────────────────────────────────────────────
# INCIDENTS.md doesn't reference invented phases
# ─────────────────────────────────────────────────────────────────────

def test_incident_phase_lines_resolve_to_real_phases():
    """Every phase token in `- **Phase:**` lines must resolve to a real id in
    workflow_meta.json (literal id or its dotted form).
    """
    meta = json.loads(WORKFLOW_META.read_text())
    phase_ids = {p["id"] for p in meta["phases"]}

    def _alternates(pid: str) -> set[str]:
        m = re.match(r"^P(\d+)_(\d+)(?:_|$)", pid)
        if m:
            return {pid, f"P{m.group(1)}.{m.group(2)}"}
        return {pid}

    accepted: set[str] = set()
    for pid in phase_ids:
        accepted.update(_alternates(pid))

    text = INCIDENTS.read_text(encoding="utf-8")
    token_re = re.compile(r"\b(?:P\d+\.\d+|P_[A-Za-z0-9_]+|P\d+(?:_[A-Za-z0-9]+)+)\b")
    phase_line_re = re.compile(r"-\s+\*\*Phase:\*\*\s+(.*)$", re.MULTILINE)

    for phase_line in phase_line_re.findall(text):
        for token in token_re.findall(phase_line):
            assert token in accepted, (
                f"INCIDENTS.md references unknown phase {token!r} not in "
                f"workflow_meta.json"
            )
