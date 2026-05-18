"""Validate porter_analysis.json shape (schema v2 — plan v3).

Plan v3 collapses Porter Five Forces from three perspectives
(company / industry / forward) into a **single perspective with deeper
per-force analysis**. Each of the five forces must carry, at minimum:

    name, key, score, qc_statement, data_anchor (metric/value/comp),
    mechanism, falsifier, primary_signal (speaker/quote/url_or_filing),
    look_ahead

The five forces appear in canonical order:

    supplier_power → buyer_power → new_entrants → substitutes → rivalry

This is a deterministic Phase 3 / Phase 5 gate. Run after Phase 3 (Porter
draft) and again before Phase 5 (report writing) so the writer is
guaranteed a per-force, depth-validated input.

Usage:
    python tools/research/validate_porter_analysis.py --run-dir <path>
    python tools/research/validate_porter_analysis.py --json <porter_analysis.json>

Exit codes:
    0 = pass
    1 = critical (schema does not match contract)
    2 = invocation error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Canonical force order (plan v3).
FORCE_KEYS: tuple[str, ...] = (
    "supplier_power",
    "buyer_power",
    "new_entrants",
    "substitutes",
    "rivalry",
)

# Allowed Chinese names per force key. Some forces have synonym variants
# in upstream prose (see references/report_style_guide_cn.md).
ALLOWED_FORCE_NAMES: dict[str, tuple[str, ...]] = {
    "supplier_power": ("供应商议价能力",),
    "buyer_power": ("买方议价能力", "买家议价能力", "购买者议价能力"),
    "new_entrants": ("新进入者威胁",),
    "substitutes": ("替代品威胁",),
    "rivalry": ("行业内竞争", "行业竞争强度", "行业内竞争强度"),
}

V1_PERSPECTIVE_KEYS: tuple[str, ...] = (
    "company_perspective",
    "industry_perspective",
    "forward_perspective",
)

V1_MIGRATION_MESSAGE = (
    "porter_analysis.json uses v1 schema (3 perspectives). Plan v3 "
    "requires v2 (single perspective with forces[] depth). Migrate via "
    "the QC peers' new contract (see agents/qc_porter_peer_a.md in the "
    "upstream ER skill)."
)

# Required leaf fields on each force object.
REQUIRED_FORCE_KEYS: tuple[str, ...] = (
    "name",
    "key",
    "score",
    "qc_statement",
    "data_anchor",
    "mechanism",
    "falsifier",
    "primary_signal",
    "look_ahead",
)

REQUIRED_ANCHOR_KEYS: tuple[str, ...] = ("metric", "value", "comp")
REQUIRED_SIGNAL_KEYS: tuple[str, ...] = ("speaker", "quote", "url_or_filing")

# Length thresholds.
MIN_QC_STATEMENT = 40
MIN_ANCHOR_METRIC = 5
MIN_ANCHOR_COMP = 10
MIN_MECHANISM = 80
MIN_FALSIFIER = 30
MIN_SIGNAL_SPEAKER = 3
MIN_SIGNAL_QUOTE = 15
MIN_SIGNAL_URL_OR_FILING = 5
MIN_LOOK_AHEAD = 30

# QC statement patterns. The score word inside the sentence must match
# the force's score (or score_after when changed). We capture it and
# verify against the integer afterwards.
QC_MAINTAIN_RE = re.compile(r"经QC合议[,，]\s*维持(?P<force>[一-鿿]+?)为(?P<score>\d+)分")
QC_ADJUST_RE = re.compile(
    r"经QC合议[,，]\s*决定将(?P<force>[一-鿿]+?)评分从(?P<before>\d+)调整为(?P<after>\d+)"
)
NO_QC_RE = re.compile(r"基于初稿评分[,，]\s*(?P<force>[一-鿿]+?)为(?P<score>\d+)分")

# data_anchor.value must contain a digit (optionally with %, $, currency,
# multiplier or unit). The simplest reliable signal is "at least one digit".
ANCHOR_VALUE_NUMBER_RE = re.compile(r"\d")

# Falsifier observable-event hints.
FALSIFIER_HINT_RE = re.compile(
    r"(\d|Q[1-4]|H[12]|FY\d|20\d{2}|<\s*\d+%?|>\s*\d+%?|下降|上升|超过|低于|<|>)"
)

# URL-ish OR filing-path. Generous: any of http(s) URL, www., or
# filing-shaped string like "10-K", "10-Q", "Form 8-K", "Annual Report",
# "Q3-26 earnings call", or a clear file path.
URL_OR_FILING_RE = re.compile(
    r"(https?://|www\.|10-?K|10-?Q|8-?K|20-?F|earnings call|annual report|"
    r"年报|季报|公告|prospectus|filing|\bS-1\b|\bF-1\b|/[A-Za-z0-9._\-]+)",
    re.IGNORECASE,
)


def _is_int_1_to_5(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _looks_v1(data: dict[str, Any]) -> bool:
    """Return True if the JSON has the v1 three-perspective shape."""
    return any(k in data for k in V1_PERSPECTIVE_KEYS)


def _expected_score(force: dict[str, Any]) -> int | None:
    """The score the QC sentence should mention.

    If qc_audit_trail_present and score_changed, the sentence should
    reference score_after; otherwise score.
    """
    if force.get("score_changed") is True and _is_int(force.get("score_after")):
        return force["score_after"]
    score = force.get("score")
    return score if _is_int(score) else None


def _validate_qc_statement(
    statement: str,
    force: dict[str, Any],
    expected_score: int | None,
    expected_names: tuple[str, ...],
    qc_trail_present: bool,
    idx: int,
    errors: list[str],
) -> None:
    """Match qc_statement against canonical opening patterns."""
    if qc_trail_present:
        m = QC_MAINTAIN_RE.search(statement)
        if m:
            sentence_score = int(m.group("score"))
            sentence_force = m.group("force")
            if expected_score is not None and sentence_score != expected_score:
                errors.append(
                    f"forces[{idx}].qc_statement: 维持-pattern score {sentence_score} "
                    f"does not match force's effective score {expected_score}"
                )
            if sentence_force not in expected_names:
                errors.append(
                    f"forces[{idx}].qc_statement: 维持-pattern force name "
                    f"'{sentence_force}' is not one of {list(expected_names)}"
                )
            return
        m = QC_ADJUST_RE.search(statement)
        if m:
            sentence_after = int(m.group("after"))
            sentence_before = int(m.group("before"))
            sentence_force = m.group("force")
            if expected_score is not None and sentence_after != expected_score:
                errors.append(
                    f"forces[{idx}].qc_statement: 调整-pattern after-score "
                    f"{sentence_after} does not match force's score_after {expected_score}"
                )
            if _is_int(force.get("score_before")) and sentence_before != force["score_before"]:
                errors.append(
                    f"forces[{idx}].qc_statement: 调整-pattern before-score "
                    f"{sentence_before} does not match force.score_before {force['score_before']}"
                )
            if sentence_force not in expected_names:
                errors.append(
                    f"forces[{idx}].qc_statement: 调整-pattern force name "
                    f"'{sentence_force}' is not one of {list(expected_names)}"
                )
            return
        errors.append(
            f"forces[{idx}].qc_statement: QC mode requires an opening of the "
            f"form '经QC合议，维持<force-name>为<N>分' or '经QC合议，决定将"
            f"<force-name>评分从<X>调整为<Y>'; got: {statement[:60]!r}"
        )
    else:
        m = NO_QC_RE.search(statement)
        if not m:
            errors.append(
                f"forces[{idx}].qc_statement: no-QC mode requires an opening "
                f"of the form '基于初稿评分，<force-name>为<N>分'; got: "
                f"{statement[:60]!r}"
            )
            return
        sentence_score = int(m.group("score"))
        sentence_force = m.group("force")
        if expected_score is not None and sentence_score != expected_score:
            errors.append(
                f"forces[{idx}].qc_statement: no-QC pattern score {sentence_score} "
                f"does not match force's score {expected_score}"
            )
        if sentence_force not in expected_names:
            errors.append(
                f"forces[{idx}].qc_statement: no-QC pattern force name "
                f"'{sentence_force}' is not one of {list(expected_names)}"
            )


def _validate_force(
    force: Any,
    idx: int,
    expected_key: str,
    qc_trail_present: bool,
    errors: list[str],
) -> None:
    if not isinstance(force, dict):
        errors.append(f"forces[{idx}] is not a JSON object")
        return

    # 1. Required keys present.
    for slot in REQUIRED_FORCE_KEYS:
        if slot not in force:
            errors.append(f"forces[{idx}] missing required slot '{slot}'")

    # 2. Key whitelist + canonical order.
    actual_key = force.get("key")
    if actual_key != expected_key:
        errors.append(
            f"forces[{idx}].key='{actual_key}' but canonical order expects "
            f"'{expected_key}' at position {idx}"
        )
    expected_names = ALLOWED_FORCE_NAMES.get(expected_key, ())
    name = force.get("name")
    if isinstance(name, str) and expected_names and name not in expected_names:
        errors.append(
            f"forces[{idx}].name='{name}' is not one of the allowed names "
            f"for key='{expected_key}': {list(expected_names)}"
        )

    # 3. Score.
    score = force.get("score")
    if not _is_int_1_to_5(score):
        errors.append(
            f"forces[{idx}].score={score!r} is not an integer in [1, 5]"
        )
    if force.get("score_changed") is True:
        sa = force.get("score_after")
        if not _is_int_1_to_5(sa):
            errors.append(
                f"forces[{idx}].score_after={sa!r} required when score_changed=true "
                f"and must be int in [1, 5]"
            )
        elif _is_int(score) and score != sa:
            errors.append(
                f"forces[{idx}].score={score} mismatches score_after={sa} "
                f"(score must equal score_after when score_changed=true)"
            )

    # 4. qc_statement.
    qc = force.get("qc_statement")
    if isinstance(qc, str):
        if len(qc) < MIN_QC_STATEMENT:
            errors.append(
                f"forces[{idx}].qc_statement is too short "
                f"({len(qc)} chars < {MIN_QC_STATEMENT})"
            )
        else:
            _validate_qc_statement(
                qc, force, _expected_score(force), expected_names,
                qc_trail_present, idx, errors,
            )
    elif "qc_statement" in force:
        errors.append(f"forces[{idx}].qc_statement is not a string")

    # 5. data_anchor.
    anchor = force.get("data_anchor")
    if isinstance(anchor, dict):
        for k in REQUIRED_ANCHOR_KEYS:
            if k not in anchor:
                errors.append(f"forces[{idx}].data_anchor missing '{k}'")
        metric = anchor.get("metric")
        value = anchor.get("value")
        comp = anchor.get("comp")
        if isinstance(metric, str) and len(metric) < MIN_ANCHOR_METRIC:
            errors.append(
                f"forces[{idx}].data_anchor.metric too short "
                f"({len(metric)} chars < {MIN_ANCHOR_METRIC})"
            )
        if isinstance(value, str) and not ANCHOR_VALUE_NUMBER_RE.search(value):
            errors.append(
                f"forces[{idx}].data_anchor.value must contain a number; got: {value!r}"
            )
        elif "value" in anchor and not isinstance(value, str):
            # numeric value is acceptable too; coerce-check
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(
                    f"forces[{idx}].data_anchor.value must be a string or number"
                )
        if isinstance(comp, str) and len(comp) < MIN_ANCHOR_COMP:
            errors.append(
                f"forces[{idx}].data_anchor.comp too short "
                f"({len(comp)} chars < {MIN_ANCHOR_COMP})"
            )
    elif "data_anchor" in force:
        errors.append(f"forces[{idx}].data_anchor is not a JSON object")

    # 6. mechanism.
    mech = force.get("mechanism")
    if isinstance(mech, str):
        if len(mech) < MIN_MECHANISM:
            errors.append(
                f"forces[{idx}].mechanism is too short "
                f"({len(mech)} chars < {MIN_MECHANISM}); must explain why "
                f"score is X not X±1"
            )
    elif "mechanism" in force:
        errors.append(f"forces[{idx}].mechanism is not a string")

    # 7. falsifier.
    fals = force.get("falsifier")
    if isinstance(fals, str):
        if len(fals) < MIN_FALSIFIER:
            errors.append(
                f"forces[{idx}].falsifier is too short "
                f"({len(fals)} chars < {MIN_FALSIFIER})"
            )
        elif not FALSIFIER_HINT_RE.search(fals):
            errors.append(
                f"forces[{idx}].falsifier lacks an observable-event hint "
                f"(needs a number, Q1-4 / H1 / FY / 20xx, or directional word "
                f"like 下降/上升/超过/低于/</>); got: {fals[:60]!r}"
            )
    elif "falsifier" in force:
        errors.append(f"forces[{idx}].falsifier is not a string")

    # 8. primary_signal.
    sig = force.get("primary_signal")
    if isinstance(sig, dict):
        for k in REQUIRED_SIGNAL_KEYS:
            if k not in sig:
                errors.append(f"forces[{idx}].primary_signal missing '{k}'")
        speaker = sig.get("speaker")
        quote = sig.get("quote")
        url = sig.get("url_or_filing")
        if isinstance(speaker, str) and len(speaker) < MIN_SIGNAL_SPEAKER:
            errors.append(
                f"forces[{idx}].primary_signal.speaker too short "
                f"({len(speaker)} chars < {MIN_SIGNAL_SPEAKER})"
            )
        if isinstance(quote, str) and len(quote) < MIN_SIGNAL_QUOTE:
            errors.append(
                f"forces[{idx}].primary_signal.quote too short "
                f"({len(quote)} chars < {MIN_SIGNAL_QUOTE})"
            )
        if isinstance(url, str):
            if len(url) < MIN_SIGNAL_URL_OR_FILING:
                errors.append(
                    f"forces[{idx}].primary_signal.url_or_filing too short "
                    f"({len(url)} chars < {MIN_SIGNAL_URL_OR_FILING})"
                )
            elif not URL_OR_FILING_RE.search(url):
                errors.append(
                    f"forces[{idx}].primary_signal.url_or_filing must look "
                    f"like a URL or filing reference; got: {url[:60]!r}"
                )
    elif "primary_signal" in force:
        errors.append(f"forces[{idx}].primary_signal is not a JSON object")

    # 9. look_ahead.
    look = force.get("look_ahead")
    if isinstance(look, str):
        if len(look) < MIN_LOOK_AHEAD:
            errors.append(
                f"forces[{idx}].look_ahead is too short "
                f"({len(look)} chars < {MIN_LOOK_AHEAD})"
            )
    elif "look_ahead" in force:
        errors.append(f"forces[{idx}].look_ahead is not a string")


def validate_porter_analysis(data: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return {
            "status": "critical",
            "errors": ["porter_analysis.json root is not a JSON object"],
            "warnings": [],
        }

    # ---- v1 reject ----
    if _looks_v1(data):
        return {
            "status": "critical",
            "errors": [V1_MIGRATION_MESSAGE],
            "warnings": [],
        }

    # ---- schema_version ----
    sv = data.get("schema_version")
    if sv != 2:
        errors.append(
            f"schema_version must be exactly 2 (got {sv!r}). " + V1_MIGRATION_MESSAGE
        )

    # ---- qc_audit_trail_present ----
    qc_trail_present_raw = data.get("qc_audit_trail_present")
    if not _is_bool(qc_trail_present_raw):
        errors.append(
            f"qc_audit_trail_present must be a boolean (got {qc_trail_present_raw!r})"
        )
        qc_trail_present = False
    else:
        qc_trail_present = qc_trail_present_raw

    # ---- forces ----
    forces = data.get("forces")
    if not isinstance(forces, list):
        errors.append("forces must be a JSON array")
        forces_list: list[Any] = []
    else:
        forces_list = forces
        if len(forces_list) != 5:
            errors.append(
                f"forces must contain exactly 5 entries (got {len(forces_list)})"
            )

    for i, expected_key in enumerate(FORCE_KEYS):
        if i >= len(forces_list):
            errors.append(
                f"forces[{i}] missing (canonical order expects '{expected_key}')"
            )
            continue
        _validate_force(forces_list[i], i, expected_key, qc_trail_present, errors)

    # If qc_audit_trail_present, every force should carry score_changed,
    # score_before, score_after (the audit trail fields). If false, those
    # fields are optional.
    if qc_trail_present:
        for i, force in enumerate(forces_list[: len(FORCE_KEYS)]):
            if not isinstance(force, dict):
                continue
            if not _is_bool(force.get("score_changed")):
                errors.append(
                    f"forces[{i}].score_changed must be a boolean when "
                    f"qc_audit_trail_present=true"
                )
            if not _is_int(force.get("score_before")):
                errors.append(
                    f"forces[{i}].score_before must be an int when "
                    f"qc_audit_trail_present=true"
                )
            if not _is_int(force.get("score_after")):
                errors.append(
                    f"forces[{i}].score_after must be an int when "
                    f"qc_audit_trail_present=true"
                )

    # ---- top-level scores array ----
    scores = data.get("scores")
    if not isinstance(scores, list) or len(scores) != 5:
        errors.append("scores must be a JSON array of exactly 5 integers")
    else:
        for i, s in enumerate(scores):
            if not _is_int_1_to_5(s):
                errors.append(
                    f"scores[{i}]={s!r} is not an integer in [1, 5]"
                )
        # Must equal [f["score"] for f in forces].
        force_scores: list[Any] = []
        for f in forces_list[: len(FORCE_KEYS)]:
            if isinstance(f, dict):
                force_scores.append(f.get("score"))
            else:
                force_scores.append(None)
        if len(force_scores) == 5 and scores != force_scores:
            errors.append(
                f"scores {scores} must equal forces[].score {force_scores} "
                f"(in canonical order)"
            )

    return {
        "status": "critical" if errors else ("warn" if warnings else "pass"),
        "schema_version": 2,
        "forces_required": list(FORCE_KEYS),
        "errors": errors,
        "warnings": warnings,
    }


def _find_porter_json(run_dir: Path) -> Path | None:
    for candidate in (
        run_dir / "research" / "porter_analysis.json",
        run_dir / "porter_analysis.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _emit_errors(result: dict[str, Any]) -> None:
    """Print each error line with an ERROR: prefix for grep-ability."""
    for err in result.get("errors", []):
        print(f"ERROR: {err}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--run-dir", default=None)
    p.add_argument("--json", dest="json_path", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    if args.run_dir:
        path = _find_porter_json(Path(args.run_dir).resolve())
        if path is None:
            result = {
                "status": "critical",
                "errors": [
                    f"porter_analysis.json not found under {args.run_dir} "
                    f"(looked in research/ and root)"
                ],
                "warnings": [],
            }
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                result = {
                    "status": "critical",
                    "errors": [f"failed to parse {path}: {exc}"],
                    "warnings": [],
                }
            else:
                result = validate_porter_analysis(data)
                result["json_file"] = str(path)
    elif args.json_path:
        path = Path(args.json_path).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: failed to parse {path}: {exc}", file=sys.stderr)
            return 2
        result = validate_porter_analysis(data)
        result["json_file"] = str(path)
    else:
        print("error: provide --run-dir or --json", file=sys.stderr)
        return 2

    out_path = Path(args.out).resolve() if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit_errors(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "critical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
