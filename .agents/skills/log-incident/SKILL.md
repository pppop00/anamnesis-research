---
name: log-incident
description: Draft a new INCIDENTS.md entry from a one-line user description plus the latest run.jsonl. The user reviews and confirms before the entry is appended.
argument-hint: <one-line description of what went wrong>
---

# log-incident (canonical body)

**Single source of truth.** The shells under `.claude/commands/`, `.codex/prompts/`, `.cursor/commands/` delegate here — edit only this file.

The user's one-line description arrives as `$ARGUMENTS` (Claude Code) or the trailing slash-command text (other harnesses). Treat it as the *what*; you derive *why / rule / detection* from evidence, not from the description.

## Procedure

1. **Collect evidence.** Run `python3 tools/io/log_incident.py --collect` (add `--run-dir <path>` if the user names a specific run). It returns the run dir + a digest of `meta/run.jsonl`, `meta/gates.json`, `meta/run.json`, and `validation/` + `research/structure_conformance.json` outputs. Trust the digest for paths and phase ids — never the user's prose.

2. **Check for recurrence.** Read `INCIDENTS.md`. Find max existing `I-NNN` (next id = max + 1). If this is a recurrence of an existing entry, **do not draft a new one** — surface the match and ask whether to amend it (add a new `Date observed` line) or treat it as a genuinely new variant.

3. **Draft the entry**, matching existing format exactly:
   - `## I-NNN — <short title>`
   - `**Date observed:**` — today, `YYYY-MM-DD`
   - `**Phase:**` — phase id from `workflow_meta.json`
   - `**What happened:**` — specific, with paths from the digest (not generic)
   - `**Root cause:**` — the assumption/shortcut that produced it
   - `**Rule (load-bearing):**` — the enforceable contract that prevents recurrence (not advice)
   - `**Detection:**` — which tool/test catches it; propose one as a follow-up if none exists
   - `**Related contract:**` — files that must be cross-referenced

   Any detail not in the digest or the user's description → `<unknown — to be filled in by user>`. Do not fabricate.

4. **Confirm with the user.** Print the draft and ask "ready to append? (y/n)". **Do not write to `INCIDENTS.md` until the user replies `y` or equivalent** — this is the curation throttle.

5. **Append on confirm.** Insert the new block **before** the trailing `## How this file is used` section, preserving the `---` separator above each entry. Do not reorder existing entries.

6. **Verify.** Re-read `INCIDENTS.md`, show the inserted block as a diff, and remind the user that future sessions will see this entry as part of the frozen system prompt.

## Hard NOs

- No invented details — use `<unknown — …>` instead.
- No append without explicit user confirmation.
- No deleting or editing past entries. Append-only; corrections happen via a new entry that supersedes the old one with a back-link.
- No entries for warn-level findings. `INCIDENTS.md` is reserved for load-bearing failure modes worth re-reading every run; one-off edge cases are not incidents.
