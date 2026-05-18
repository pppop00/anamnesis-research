"""Orchestrator model-id gate.

Anamnesis Research has 35+ phases, four blocking P0 gates, an incident
pre/post-check loop, and a four-layer P12 audit. Fast/cheap model families
(Haiku, Instant) have repeatedly skipped these steps in practice — they
compress the prose contract aggressively and treat interactive gates as
optional. The cost of a single skipped gate is a full re-run, so we'd
rather refuse upfront than re-run later.

This gate fires at `anamnesis.py bootstrap` so the failure is visible
before any work happens. Subagents (logo production, card content,
single research scrapes) can still use Haiku — the gate only applies to
the orchestrator declared at bootstrap.

Behaviour by declared model id:
    contains "opus"   -> allowed
    contains "sonnet" -> allowed
    contains "haiku"  -> refused (exit 2)
    contains "instant"-> refused (exit 2)
    other             -> warning printed, allowed (forward-compat for
                         future Claude families and third-party models)

The check is substring-based on lowercase model id so future suffixes
("claude-opus-5", "claude-sonnet-4-7-1m") all pass without code change.

Honest note: a model can lie about its own id when it calls this CLI.
We can't verify against the runtime. The gate raises the floor, it
doesn't lock the door — combined with the boot-order instruction in
SKILL.md ("declare your model"), it catches the common case where
the host invokes a Haiku model and the model self-reports honestly.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOWED_FAMILIES = ("opus", "sonnet")
REFUSED_FAMILIES = ("haiku", "instant")


@dataclass
class ModelGateResult:
    allowed: bool
    family: str  # "opus" | "sonnet" | "haiku" | "instant" | "unknown"
    message: str  # human-readable; empty when silently allowed


def classify(model_id: str) -> ModelGateResult:
    """Return whether `model_id` is allowed to act as the orchestrator.

    Substring match on lowercase. See module docstring for policy.
    """
    if not model_id or not model_id.strip():
        return ModelGateResult(
            allowed=False,
            family="unknown",
            message=(
                "orchestrator model id is empty. Pass --orchestrator-model "
                "with the model you are running (e.g. claude-opus-4-7, "
                "claude-sonnet-4-6). See MEMORY.md §Orchestrator model gate."
            ),
        )

    lower = model_id.strip().lower()

    for fam in REFUSED_FAMILIES:
        if fam in lower:
            return ModelGateResult(
                allowed=False,
                family=fam,
                message=(
                    f"orchestrator model gate: {model_id!r} ({fam} family) "
                    "is not allowed to drive an Anamnesis Research run.\n"
                    "Reason: the harness has 35+ phases, four P0 gates, and "
                    "an incident pre/post-check loop. Fast/cheap models "
                    "have repeatedly skipped these steps in practice (see "
                    "INCIDENTS.md I-001, I-002). Use Opus or Sonnet for the "
                    "orchestrator; subagents may still use Haiku.\n"
                    "To switch: re-invoke with an Opus or Sonnet model, or "
                    "set --orchestrator-model to the actual model running."
                ),
            )

    for fam in ALLOWED_FAMILIES:
        if fam in lower:
            return ModelGateResult(allowed=True, family=fam, message="")

    return ModelGateResult(
        allowed=True,
        family="unknown",
        message=(
            f"orchestrator model gate: {model_id!r} is not a recognised "
            "Claude family (opus/sonnet/haiku). Proceeding, but if this is "
            "a fast/cheap model, expect step-skipping; if this is an unknown "
            "or future Claude family, you can ignore this warning."
        ),
    )
