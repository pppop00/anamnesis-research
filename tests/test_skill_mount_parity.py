"""The host skill-mount stubs MUST mirror root SKILL.md's frontmatter.

`.claude/skills/anamnesis-research/SKILL.md` and
`.agents/skills/anamnesis-research/SKILL.md` are stubs whose only job is to give
each host an auto-discoverable trigger location. The frontmatter (`name` +
`description`) is what actually triggers the skill — drift between root and
mount means the mounts trigger differently from the canonical body, which is
exactly the failure mode the stubs were introduced to prevent.

The mount bodies intentionally diverge from root (they're stubs that point at
root). We check frontmatter only.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_SKILL = ROOT / "SKILL.md"
MOUNTS = [
    ROOT / ".claude" / "skills" / "anamnesis-research" / "SKILL.md",
    ROOT / ".agents" / "skills" / "anamnesis-research" / "SKILL.md",
]


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Lenient YAML-frontmatter parser. Handles `key: value` and `key: >-`
    folded-block syntax that SKILL.md descriptions use. Concatenates folded
    lines with single spaces; collapses internal whitespace.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    body = m.group(1)

    fields: dict[str, list[str]] = {}
    cur_key: str | None = None

    for line in body.split("\n"):
        if (line.startswith(" ") or line.startswith("\t")) and cur_key is not None:
            fields[cur_key].append(line.strip())
            continue
        if ":" not in line:
            cur_key = None
            continue
        key, _, raw_val = line.partition(":")
        cur_key = key.strip()
        raw_val = raw_val.strip()
        fields[cur_key] = []
        if raw_val and raw_val not in (">", ">-", "|", "|-"):
            fields[cur_key].append(raw_val)

    return {k: " ".join(v).strip() for k, v in fields.items()}


def _normalise(text: str) -> str:
    """Collapse all whitespace runs to single spaces so line-break differences
    between the YAML folded-block and the source text don't cause false
    positives.
    """
    return re.sub(r"\s+", " ", text).strip()


def test_root_skill_has_required_frontmatter():
    fm = _parse_frontmatter(ROOT_SKILL)
    assert fm.get("name") == "anamnesis-research", (
        f"root SKILL.md must declare name: anamnesis-research (got {fm.get('name')!r})"
    )
    assert fm.get("description"), "root SKILL.md description is missing"


def test_each_mount_exists():
    for mount in MOUNTS:
        assert mount.exists(), f"missing mount stub: {mount.relative_to(ROOT)}"


def test_mounts_mirror_root_name():
    root_name = _parse_frontmatter(ROOT_SKILL).get("name")
    for mount in MOUNTS:
        mount_name = _parse_frontmatter(mount).get("name")
        assert mount_name == root_name, (
            f"name drift: {mount.relative_to(ROOT)} = {mount_name!r}, "
            f"root = {root_name!r}"
        )


def test_mounts_mirror_root_description():
    root_desc = _normalise(_parse_frontmatter(ROOT_SKILL).get("description", ""))
    assert root_desc, "root SKILL.md description is empty"
    for mount in MOUNTS:
        mount_desc = _normalise(_parse_frontmatter(mount).get("description", ""))
        assert mount_desc == root_desc, (
            f"description drift in {mount.relative_to(ROOT)}: "
            f"first 80 chars differ — root={root_desc[:80]!r}, "
            f"mount={mount_desc[:80]!r}"
        )
