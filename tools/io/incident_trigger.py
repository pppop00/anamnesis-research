"""Shared logic for the UserPromptSubmit hooks across hosts.

Each host (Claude Code, Codex, Cursor, …) has its own hook directory and event
shape, but the *substance* of the reminder we want to inject on a research-style
prompt is host-agnostic: detect a research trigger phrase, then emit a pointer
to SKILL.md / MEMORY.md / INCIDENTS.md plus a hard reminder of the two
load-bearing P0/P5 failure modes.

Per-host hook files (`.claude/hooks/inject_incidents.py`,
`.codex/hooks/inject_incidents.py`, …) should be thin adapters that read stdin
in their host's event shape, extract the prompt string, call `looks_like_research`
and `build_context` here, and write back in their host's expected output shape.

This module deliberately has no host-specific I/O. It does not read stdin and
does not write stdout. That keeps the wrappers small and prevents drift between
hosts when the trigger list or reminder text changes.
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolve the repo root from this file's own location: tools/io/incident_trigger.py
# → parents[0]=io, [1]=tools, [2]=repo root. Hook wrappers can compute their own
# repo root if they live elsewhere; we expose this constant for the common case.
REPO_ROOT = Path(__file__).resolve().parents[2]

TRIGGER_PATTERNS = [
    r"\bresearch\b",
    r"\bwriteup\b",
    r"\bone[- ]?pager\b",
    r"\bequity\s+research\b",
    r"\bcards?\s+for\b",
    r"\banalyst.{0,10}note\b",
    r"研究",
    r"分析",
    r"研报",
    r"做.{0,4}研究",
    r"看看.{0,8}(公司|股票|苹果|腾讯|阿里|美股|港股)",
    r"build\s+cards",
]

TRIGGER_RE = re.compile("|".join(TRIGGER_PATTERNS), re.IGNORECASE)


def looks_like_research(prompt: str | None) -> bool:
    """True iff the prompt looks like an equity-research request."""
    return bool(TRIGGER_RE.search(prompt or ""))


def build_context(host_label: str = "Anamnesis Research harness", invoked_by: str | None = None) -> str:
    """Return the host-agnostic reminder string to inject.

    Args:
        host_label: appears in the leading bracket (e.g. "Anamnesis Research harness reminder").
        invoked_by: optional path-or-id of the wrapping hook script, for breadcrumb purposes.
            If omitted, the bracket only mentions the host label.
    """
    incidents = REPO_ROOT / "INCIDENTS.md"
    memory = REPO_ROOT / "MEMORY.md"
    skill = REPO_ROOT / "SKILL.md"

    breadcrumb = f"{host_label} reminder" + (f" — injected by {invoked_by}" if invoked_by else "")

    lines = [
        f"[{breadcrumb}]",
        "",
        "This prompt looks like an equity-research request. This project is Anamnesis Research, an implementation of the Anamnesis Pattern. Before any phase work:",
        f"1. Read {skill.relative_to(REPO_ROOT)} for the boot order and P0 gates.",
        f"2. Read {memory.relative_to(REPO_ROOT)} for project invariants (frozen at session start).",
        f"3. Read {incidents.relative_to(REPO_ROOT)} end-to-end. Acknowledge each incident in meta/run.jsonl as `incident_precheck.acknowledged` during P_INCIDENT_PRECHECK.",
        "",
        "Hard reminders (the two recurring failure modes):",
        "- Interactive P0 gates (P0_lang / P0_sec_email / P0_palette) cannot be auto-defaulted. Auto mode does not waive them. Halt and ask if no user_response and no USER.md sticky exists. (See INCIDENTS I-001.)",
        "- The locked HTML template applies to EVERY company — public, private, fund, family office, government. There is no scope-limited / institution-compatible / simplified bypass. Fill the locked skeleton with proxies and label gaps; never hand-write a simplified report. (See INCIDENTS I-002.)",
        "",
        "Bootstrap + advance discipline (externalised state machine — the floor against silent step-skipping):",
        "- Bootstrap a run dir via `python anamnesis.py bootstrap --company <name> --date <YYYY-MM-DD> --orchestrator-model <your-own-model-id>`. The CLI refuses Haiku/Instant families; subagents may still use them. Declare yourself honestly — see MEMORY.md §Orchestrator model gate.",
        "- BEFORE every phase, run `python anamnesis.py advance --run-dir <run_dir>`. It returns the next phase id + agent/tool/produces and exits non-zero if a predecessor artifact is missing or an interactive P0 gate has a non-whitelisted source. Do not advance from memory; the watchdog catches what prose cannot.",
    ]
    return "\n".join(lines)
