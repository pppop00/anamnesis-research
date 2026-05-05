#!/usr/bin/env python3
"""UserPromptSubmit hook for Claude Code.

Thin adapter over `tools/io/incident_trigger.py`. The shared module owns the
trigger pattern list and the reminder body so the Claude Code, Codex, and any
other host wrappers can never drift apart on substance.

Hook protocol (Claude Code): read JSON event from stdin, write JSON to stdout
with `additionalContext` if we want to inject text. Exit 0 even on no-op; exit
non-zero only on hook errors (we never want to block the user).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.io.incident_trigger import build_context, looks_like_research  # noqa: E402


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        # Don't block the user on a malformed event — just no-op.
        return 0

    prompt = event.get("prompt") or event.get("user_prompt") or ""
    if not looks_like_research(prompt):
        return 0

    hook_rel = Path(__file__).resolve().relative_to(REPO_ROOT)
    output = {"additionalContext": build_context(invoked_by=str(hook_rel))}
    sys.stdout.write(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
