from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from toolmanager.core import icon  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_icon.py"
spec = importlib.util.spec_from_file_location("make_icon", SCRIPT)
make_icon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = make_icon
spec.loader.exec_module(make_icon)

STEEL = "#2a4a6e"  # a customer candidate: the base colour's saturation and lightness


def test_standard_icon_is_the_file_as_drawn() -> None:
    assert icon.svg() == icon.ICON_FILE.read_text("utf-8")
    assert icon.BASE_COLOR in icon.svg().lower()


def test_recolour_moves_every_shade_and_keeps_white() -> None:
    assert icon.recolor(icon.BASE_COLOR, STEEL) == STEEL  # the frame becomes the accent
    assert icon.recolor("#ffffff", STEEL) == "#ffffff"
    text = icon.svg(STEEL).lower()
    for shade in ("#2a6e6a", "#43918b", "#1f5753", "#55a39d", "#225a57", "#0e2e2d"):
        assert shade not in text
    assert 'fill="#fff"' in text


def _close(a, b, tolerance=6) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


def test_render_draws_frame_inside_and_transparent_corners() -> None:
    image = icon.render(icon.svg(), 64)
    assert image.size == (64, 64)
    assert image.getpixel((0, 0))[3] == 0  # outside the rounded frame
    assert _close(image.getpixel((2, 32))[:3], (0x2A, 0x6E, 0x6A))  # left side of the frame
    assert _close(image.getpixel((14, 50)), (255, 255, 255, 255))  # white inside
    tie = image.getpixel((36, 30))[:3]  # right half of the tie: the dark shade
    assert _close(tie, (0x1F, 0x57, 0x53), 12)


def test_render_refuses_curves() -> None:
    bad = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
           '<path d="M0,0 C1,1 2,2 3,3 Z" fill="#000000"/></svg>')
    with pytest.raises(icon.IconError, match="straight"):
        icon.render(bad, 16)


def test_ico_holds_every_size(tmp_path: Path) -> None:
    from PIL import Image

    path = icon.write_ico(tmp_path / "x.ico", STEEL)
    with Image.open(path) as ico:
        assert set(ico.info["sizes"]) == {(s, s) for s in icon.ICO_SIZES}


def test_make_icon_reads_the_customer_accent(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.toml"
    defaults.write_text(f'portable = true\naccent = "{STEEL.upper()}"\n', "utf-8")
    assert make_icon.accent_of(defaults) == STEEL
    assert make_icon.accent_of(None) == icon.BASE_COLOR
    defaults.write_text('accent = "steel"\n', "utf-8")
    with pytest.raises(SystemExit):
        make_icon.accent_of(defaults)
    assert make_icon.main(["--out", str(tmp_path / "a.ico")]) == 0
