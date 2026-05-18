"""Run EP's renderer (skills_repo/ep/scripts/generate_social_cards.py).

Always pass the SAME P0-confirmed --palette as Validator 1 used. Default --output-root is the
per-run cards/ directory.

Usage:
    python tools/photo/render_cards.py \
        --input <run_dir>/research/Apple_Research_CN.html \
        --slots <run_dir>/cards/Apple_Research_CN.card_slots.json \
        --brand "金融豹" \
        --palette <confirmed_palette> \
        --output-root <run_dir>/cards
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
from _common import find_skill_root, python_exec, script_path  # noqa: E402


EXPECTED_CARD_FILES = (
    "01_cover.png",
    "02_background_industry.png",
    "03_revenue.png",
    "04_business_outlook.png",
    "05_brand.png",
    "06_post_copy.png",
)
FULL_RENDER_SIZE = (2160, 2700)
LOGICAL_RENDER_SIZE = (1080, 1350)


def _validate_rendered_cards(rendered_dir: Path, *, expected_size: tuple[int, int]) -> None:
    """Fail before touching the delivery folder if the renderer produced a partial set."""
    missing = [name for name in EXPECTED_CARD_FILES if not (rendered_dir / name).is_file()]
    if missing:
        raise RuntimeError(
            f"EP renderer produced an incomplete card set in {rendered_dir}: missing {missing}"
        )

    wrong_size: list[str] = []
    for name in EXPECTED_CARD_FILES:
        path = rendered_dir / name
        with Image.open(path) as img:
            if img.size != expected_size:
                wrong_size.append(f"{name}={img.size[0]}x{img.size[1]}")
    if wrong_size:
        expected = f"{expected_size[0]}x{expected_size[1]}"
        raise RuntimeError(
            f"EP renderer produced cards with unexpected dimensions; expected {expected}, got {wrong_size}"
        )


def _sync_complete_render(rendered_dir: Path, output_root: Path, *, expected_size: tuple[int, int]) -> None:
    """Copy a verified complete card set into the delivery folder.

    The EP renderer always writes to output_root / stem (its batch-mode convention).
    The wrapper now renders into a temporary output root first, verifies all six
    card PNGs, then overwrites the delivery cards. That keeps an existing good
    delivery set intact if a render crashes or produces only a subset.
    """
    _validate_rendered_cards(rendered_dir, expected_size=expected_size)
    output_root.mkdir(parents=True, exist_ok=True)
    for name in EXPECTED_CARD_FILES:
        shutil.copy2(rendered_dir / name, output_root / name)

    # Preserve existing sidecars in cards/, but copy any renderer-created bundle
    # files (e.g. slots JSON when --no-copy-slots is not used) after the PNG set
    # is known-good.
    for item in rendered_dir.iterdir():
        if item.name in EXPECTED_CARD_FILES or not item.is_file():
            continue
        shutil.copy2(item, output_root / item.name)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--input", required=True)
    p.add_argument("--slots", required=True)
    p.add_argument("--brand", default="金融豹")
    p.add_argument("--palette", required=True, choices=["macaron", "default", "b", "c"])
    p.add_argument("--output-root", default=None,
                   help="Default: skills_repo/ep/output/<stem>/. Override to per-run cards/.")
    p.add_argument("--export-logical-size", action="store_true",
                   help="Export 1080x1350 instead of 2160x2700.")
    p.add_argument("--no-copy-slots", action="store_true",
                   help="Do not copy card_slots.json into the output dir.")
    args = p.parse_args(argv)

    ep_root = find_skill_root("ep")
    renderer = script_path("ep", "scripts", "generate_social_cards.py")
    final_output_root = Path(args.output_root).expanduser().resolve() if args.output_root else None
    expected_size = LOGICAL_RENDER_SIZE if args.export_logical_size else FULL_RENDER_SIZE

    cmd = [
        python_exec(),
        str(renderer),
        "--input", args.input,
        "--slots", args.slots,
        "--brand", args.brand,
        "--palette", args.palette,
    ]
    if args.export_logical_size:
        cmd.append("--export-logical-size")
    if args.no_copy_slots:
        cmd.append("--no-copy-slots")

    if final_output_root is None:
        result = subprocess.run(cmd, cwd=str(ep_root), capture_output=True, text=True, check=False)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode

    final_output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="anamnesis-cards-render-") as tmp:
        tmp_root = Path(tmp)
        render_cmd = [*cmd, "--output-root", str(tmp_root)]
        result = subprocess.run(render_cmd, cwd=str(ep_root), capture_output=True, text=True, check=False)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode

        rendered_dir = tmp_root / Path(args.input).stem
        try:
            _sync_complete_render(rendered_dir, final_output_root, expected_size=expected_size)
        except RuntimeError as exc:
            print(f"render_cards.py: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
