"""Lint INCIDENTS.md for structural integrity.

Catches the slow-rot failures that erode the institutional-memory loop:

1. Monotonic IDs — `I-001`, `I-002`, … with no gaps and no duplicates.
2. Cross-references — every `Superseded by:` and `Supersedes:` resolves to a real
   incident id present in this file.
3. Status whitelist — `Status:` is one of `active` / `superseded`. Active is the
   default and may be omitted; superseded entries MUST carry both `Status:` and
   `Superseded by:`.
4. Bidirectional supersede — if A supersedes B, B must declare `Superseded by: A`.
5. Detection-clause path freshness — every source-tree path mentioned in a
   `Detection:` bullet of an `active` incident must still exist. Runtime-only
   paths (`validation/…`, `meta/…`, `output/…`, `cards/…`, `db_export/…`) and
   external URLs are skipped because they only materialise during a run.

Exit 0 if everything passes, 1 if any check fails. Output is human-readable; the
errors are also bullet-listed so they can be eyeballed in CI logs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INCIDENTS_PATH = REPO_ROOT / "INCIDENTS.md"

ID_RE = re.compile(r"^##\s+(I-\d{3})\b", re.MULTILINE)
HEADING_RE = re.compile(r"^##\s+(I-\d{3})\b.*$", re.MULTILINE)
FIELD_RE = re.compile(r"^-\s+\*\*([^*]+):\*\*\s+(.*?)$", re.MULTILINE)
SUPERSEDED_BY_RE = re.compile(r"\bI-\d{3}\b")

# Path-shaped tokens we'll try to resolve under the repo root.
# Backtick-wrapped paths are the strict signal; we accept those primarily.
PATH_TOKEN_RE = re.compile(r"`([^`]+)`")

# Runtime artifact prefixes — paths under these only exist during/after a run,
# so we don't lint their existence in the source tree.
RUNTIME_PREFIXES = (
    "validation/",
    "meta/",
    "output/",
    "cards/",
    "db_export/",
    "research/",
    "logs/",
)
# Things that aren't repo paths at all.
NON_PATH_PREFIXES = ("http://", "https://", "*.sec.gov", "*.")
# Suffixes that look like real source-tree files we should be able to resolve.
SOURCE_SUFFIXES = (".py", ".md", ".json", ".sql", ".html", ".yaml", ".yml", ".toml")


def _looks_like_source_path(token: str) -> bool:
    """True iff `token` is a path-shaped string we can/should check on disk."""
    t = token.strip()
    if not t or t.startswith(NON_PATH_PREFIXES):
        return False
    if t.startswith(RUNTIME_PREFIXES):
        return False
    # Must contain a slash and end in a known source suffix to be a path.
    if "/" not in t:
        return False
    if not t.endswith(SOURCE_SUFFIXES):
        return False
    return True


def _parse_entries(text: str) -> list[dict]:
    """Slice INCIDENTS.md into one dict per `## I-NNN` block."""
    matches = list(HEADING_RE.finditer(text))
    entries: list[dict] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        fields: dict[str, str] = {}
        for fm in FIELD_RE.finditer(block):
            fields[fm.group(1).strip()] = fm.group(2).strip()
        entries.append({"id": m.group(1), "block": block, "fields": fields})
    return entries


def _check_monotonic(entries: list[dict], errors: list[str]) -> None:
    seen: set[str] = set()
    expected = 1
    for e in entries:
        eid = e["id"]
        if eid in seen:
            errors.append(f"duplicate id: {eid}")
        seen.add(eid)
        try:
            n = int(eid.split("-", 1)[1])
        except (IndexError, ValueError):
            errors.append(f"unparseable id: {eid}")
            continue
        if n != expected:
            errors.append(f"id gap or out-of-order: expected I-{expected:03d}, got {eid}")
        expected = n + 1


def _check_cross_refs(entries: list[dict], errors: list[str]) -> None:
    by_id = {e["id"]: e for e in entries}

    for e in entries:
        eid = e["id"]
        status = e["fields"].get("Status", "active").lower()
        sup_by = e["fields"].get("Superseded by", "")
        sup = e["fields"].get("Supersedes", "")

        if status not in ("active", "superseded"):
            errors.append(f"{eid}: Status='{status}' not in {{active, superseded}}")

        if status == "superseded" and not sup_by:
            errors.append(f"{eid}: Status=superseded but no `Superseded by:` field")

        if sup_by:
            for ref in SUPERSEDED_BY_RE.findall(sup_by):
                if ref not in by_id:
                    errors.append(f"{eid}: `Superseded by: {ref}` points to unknown incident")
                    continue
                # Reciprocal check
                ref_sup = by_id[ref]["fields"].get("Supersedes", "")
                if eid not in SUPERSEDED_BY_RE.findall(ref_sup):
                    errors.append(
                        f"{eid}: superseded by {ref}, but {ref} does not declare `Supersedes: {eid}`"
                    )

        if sup:
            for ref in SUPERSEDED_BY_RE.findall(sup):
                if ref not in by_id:
                    errors.append(f"{eid}: `Supersedes: {ref}` points to unknown incident")
                    continue
                ref_sb = by_id[ref]["fields"].get("Superseded by", "")
                ref_status = by_id[ref]["fields"].get("Status", "active").lower()
                if eid not in SUPERSEDED_BY_RE.findall(ref_sb):
                    errors.append(
                        f"{eid}: supersedes {ref}, but {ref} does not declare `Superseded by: {eid}`"
                    )
                if ref_status != "superseded":
                    errors.append(
                        f"{eid}: supersedes {ref}, but {ref} is not marked `Status: superseded`"
                    )


def _check_detection_paths(entries: list[dict], errors: list[str]) -> None:
    for e in entries:
        status = e["fields"].get("Status", "active").lower()
        if status != "active":
            continue  # Don't enforce paths for retired rules.
        detection = e["fields"].get("Detection", "")
        if not detection:
            errors.append(f"{e['id']}: missing `Detection:` field on active incident")
            continue
        for token in PATH_TOKEN_RE.findall(detection):
            if not _looks_like_source_path(token):
                continue
            target = REPO_ROOT / token
            if not target.exists():
                errors.append(
                    f"{e['id']}: Detection references missing path `{token}` (source tree drift)"
                )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument(
        "--file",
        default=str(INCIDENTS_PATH),
        help="Path to INCIDENTS.md. Defaults to repo root.",
    )
    args = p.parse_args(argv)

    target = Path(args.file)
    if not target.exists():
        print(f"error: {target} not found", file=sys.stderr)
        return 2
    text = target.read_text(encoding="utf-8")
    entries = _parse_entries(text)

    if not entries:
        print(f"warning: no incidents found in {target}", file=sys.stderr)
        return 0

    errors: list[str] = []
    print(f"linting {target.name} ({len(entries)} entries)")
    print("  check: monotonic ids")
    _check_monotonic(entries, errors)
    print("  check: supersede cross-references")
    _check_cross_refs(entries, errors)
    print("  check: detection-clause source paths")
    _check_detection_paths(entries, errors)

    if errors:
        print(f"\nFAIL: {len(errors)} issue(s)", file=sys.stderr)
        for line in errors:
            print(f"  ✗ {line}", file=sys.stderr)
        return 1

    print(f"OK: {target.name} ({len(entries)} entries; "
          f"{sum(1 for e in entries if e['fields'].get('Status', 'active').lower() == 'superseded')} superseded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
