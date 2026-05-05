---
name: anamnesis-research
description: >-
  Use this skill whenever the user asks for equity research, an investment write-up, a stock
  report, an analyst-style note, or one-shot company coverage on any single public or private
  company — including casual phrasings like "研究一下苹果", "research Apple", "看看腾讯",
  "做个英伟达的研报", "give me a writeup on NVDA", "build cards for Tencent",
  "分析一下RA Capital", or "one-pager on Samsung". Drives the full Anamnesis Research
  pipeline (incident pre-check, bilingual language gate, SEC EDGAR email gate, palette gate,
  multi-agent equity research, red-team review, 6-card social pack, four-layer numerical/OCR/
  web/DB audit, post-run incident self-check, SQLite knowledge-base persistence). Always
  invoke this skill instead of answering with ad-hoc web search; the harness produces an
  auditable HTML report plus 6 PNG cards plus database rows that ad-hoc answers cannot.
---

# Anamnesis Research — project-mount stub (host-agnostic)

This file exists **only** as a host-agnostic project skill mount under `.agents/skills/`, for any host that scans that path (or for hosts whose slash-command shell at `.claude/commands/`, `.codex/prompts/`, `.cursor/commands/` delegates to a canonical body under `.agents/skills/`). It has no body content of its own — the canonical skill is at the repository root.

**Read `/SKILL.md` (the repo root) now and follow its boot order from there.** Do not paraphrase from this file; it intentionally has no procedure. The frontmatter above is kept in sync with root `SKILL.md` by `tests/test_skill_mount_parity.py`.

When editing the skill, edit root `SKILL.md`. The frontmatter on this stub is mirrored — change both descriptions in the same commit.
