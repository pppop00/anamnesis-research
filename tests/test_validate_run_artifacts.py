from __future__ import annotations

from pathlib import Path

from tools.io.validate_run_artifacts import ALLOWED_ROOT_DIRS, validate


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "Acme_2026-05-17_abcd1234"
    run_dir.mkdir()
    for dirname in ALLOWED_ROOT_DIRS:
        (run_dir / dirname).mkdir()
    return run_dir


def test_validate_run_artifacts_flags_root_clutter(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    (run_dir / "qc_macro_peer_a.json").write_text("{}", encoding="utf-8")
    (run_dir / "_locked_cn_skeleton.html").write_text("<html></html>", encoding="utf-8")
    (run_dir / "notes.txt").write_text("debug", encoding="utf-8")

    result = validate(run_dir)

    assert result["status"] == "critical"
    assert "misplaced root artifact: qc_macro_peer_a.json (expected research/)" in result["errors"]
    assert "misplaced root artifact: _locked_cn_skeleton.html (expected research/)" in result["errors"]
    assert "unknown root artifact: notes.txt" in result["errors"]


def test_validate_run_artifacts_fix_moves_known_files_only(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    (run_dir / "qc_macro_peer_a.json").write_text("{}", encoding="utf-8")
    (run_dir / "Acme_Research_CN.card_slots.json").write_text("{}", encoding="utf-8")
    (run_dir / "01_cover.png").write_bytes(b"png")
    (run_dir / "notes.txt").write_text("debug", encoding="utf-8")

    fixed = validate(run_dir, fix=True)

    assert fixed["status"] == "critical"
    assert (run_dir / "research" / "qc_macro_peer_a.json").exists()
    assert (run_dir / "cards" / "Acme_Research_CN.card_slots.json").exists()
    assert (run_dir / "cards" / "01_cover.png").exists()
    assert (run_dir / "notes.txt").exists()
    assert fixed["errors"] == ["unknown root artifact: notes.txt"]
