from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RENDER_CARDS_PATH = PROJECT_ROOT / "tools" / "photo" / "render_cards.py"


def _load_render_cards_module():
    spec = importlib.util.spec_from_file_location("render_cards_wrapper", RENDER_CARDS_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 255, 255, 0)).save(path)


def test_sync_complete_render_copies_all_six_cards(tmp_path: Path) -> None:
    render_cards = _load_render_cards_module()
    rendered = tmp_path / "rendered"
    final = tmp_path / "cards"

    for name in render_cards.EXPECTED_CARD_FILES:
        _write_png(rendered / name, render_cards.FULL_RENDER_SIZE)
    (rendered / "Example.card_slots.json").write_text("{}", encoding="utf-8")

    render_cards._sync_complete_render(
        rendered,
        final,
        expected_size=render_cards.FULL_RENDER_SIZE,
    )

    assert sorted(p.name for p in final.glob("*.png")) == list(render_cards.EXPECTED_CARD_FILES)
    assert (final / "Example.card_slots.json").is_file()


def test_incomplete_render_does_not_touch_existing_delivery_cards(tmp_path: Path) -> None:
    render_cards = _load_render_cards_module()
    rendered = tmp_path / "rendered"
    final = tmp_path / "cards"

    old_cover = final / "01_cover.png"
    _write_png(old_cover, render_cards.FULL_RENDER_SIZE)
    before = old_cover.read_bytes()

    for name in render_cards.EXPECTED_CARD_FILES[:3]:
        _write_png(rendered / name, render_cards.FULL_RENDER_SIZE)

    with pytest.raises(RuntimeError, match="incomplete card set"):
        render_cards._sync_complete_render(
            rendered,
            final,
            expected_size=render_cards.FULL_RENDER_SIZE,
        )

    assert old_cover.read_bytes() == before
    assert sorted(p.name for p in final.glob("*.png")) == ["01_cover.png"]


def test_unexpected_card_dimensions_block_sync(tmp_path: Path) -> None:
    render_cards = _load_render_cards_module()
    rendered = tmp_path / "rendered"
    final = tmp_path / "cards"

    for name in render_cards.EXPECTED_CARD_FILES:
        size = render_cards.LOGICAL_RENDER_SIZE if name == "03_revenue.png" else render_cards.FULL_RENDER_SIZE
        _write_png(rendered / name, size)

    with pytest.raises(RuntimeError, match="unexpected dimensions"):
        render_cards._sync_complete_render(
            rendered,
            final,
            expected_size=render_cards.FULL_RENDER_SIZE,
        )

    assert not list(final.glob("*.png"))
