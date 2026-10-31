"""The ToolManager icon in the edition's main colour (see docs/DESIGN.md §11.1).

assets/icon.svg is drawn in BASE_COLOR. Another accent (the customer edition's) gets the
same icon with every coloured shade moved to the accent's hue, so the two editions differ
in the taskbar, Start menu, "Apps" list and tray, not only inside the window.

render() rasterises the icon with Pillow alone, so neither the app nor the build needs an
SVG library. It understands what the icon uses: paths of straight lines (M/L/H/V/Z),
fill-rule, fill-opacity, and horizontal or vertical linear gradients.
"""

from __future__ import annotations

import colorsys
import functools
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ICON_FILE = Path(__file__).resolve().parents[1] / "assets" / "icon.svg"
# The frame colour of assets/icon.svg, and the internal edition's accent.
BASE_COLOR = "#2a6e6a"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

_SVG = "{http://www.w3.org/2000/svg}"
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


class IconError(ValueError):
    pass


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, v)) * 255):02x}" for v in rgb)


def recolor(color: str, accent: str, base: str = BASE_COLOR) -> str:
    """Move one shade of the base colour to the accent's hue, keeping its saturation and
    lightness: the icon keeps its muted look, highlights and shadows stay where they are.
    Greys and white are left alone. Accents with the base colour's saturation and lightness
    (like the customer accents in docs/CUSTOMER.md) give a frame exactly in the accent."""
    h, light, s = colorsys.rgb_to_hls(*_rgb(color))
    if s < 0.08 or light > 0.97 or light < 0.03:
        return color
    bh = colorsys.rgb_to_hls(*_rgb(base))[0]
    ah = colorsys.rgb_to_hls(*_rgb(accent))[0]
    return _hex(colorsys.hls_to_rgb((ah + (h - bh)) % 1.0, light, s))


def svg(accent: str = BASE_COLOR) -> str:
    text = ICON_FILE.read_text("utf-8")
    if accent.lower() == BASE_COLOR:
        return text
    return _HEX.sub(lambda m: recolor(m.group(0), accent), text)


# --- rasterising -------------------------------------------------------------------------

def _subpaths(d: str) -> list[list[tuple[float, float]]]:
    tokens = re.findall(r"[A-Za-z]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cmd, i = "", 0
    x = y = 0.0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if current:
                    paths.append(current)
                current = []
                continue
        if cmd not in "MLHV":
            raise IconError(f"unsupported path command {cmd!r}: the icon may only use straight "
                            "lines (M, L, H, V, Z) in absolute coordinates")
        if cmd == "H":
            x = float(tokens[i])
            i += 1
        elif cmd == "V":
            y = float(tokens[i])
            i += 1
        else:
            x, y = float(tokens[i]), float(tokens[i + 1])
            i += 2
        if cmd == "M":
            if current:
                paths.append(current)
            current = []
            cmd = "L"  # further pairs after M are line-tos
        current.append((x, y))
    if current:
        paths.append(current)
    return paths


def _gradient_fill(grad: ET.Element, size: int, scale: float):
    from PIL import Image

    x1, y1, x2, y2 = (float(grad.get(a, "0")) for a in ("x1", "y1", "x2", "y2"))
    if grad.get("gradientUnits") != "userSpaceOnUse" or (x1 != x2 and y1 != y2):
        raise IconError("only horizontal or vertical userSpaceOnUse gradients are supported")
    stops = []
    for stop in grad.iter(f"{_SVG}stop"):
        rgb = tuple(round(v * 255) for v in _rgb(stop.get("stop-color", "#000000")))
        stops.append((float(stop.get("offset", "0")), rgb,
                      float(stop.get("stop-opacity", "1"))))
    vertical = x1 == x2
    start, end = (y1, y2) if vertical else (x1, x2)
    strip = Image.new("RGBA", (1, size) if vertical else (size, 1))
    for p in range(size):
        t = 0.0 if end == start else ((p + 0.5) / scale - start) / (end - start)
        t = min(1.0, max(0.0, t))
        lo = max((s for s in stops if s[0] <= t), key=lambda s: s[0], default=stops[0])
        hi = min((s for s in stops if s[0] >= t), key=lambda s: s[0], default=stops[-1])
        f = 0.0 if hi[0] == lo[0] else (t - lo[0]) / (hi[0] - lo[0])
        rgba = tuple(round(a + (b - a) * f) for a, b in zip(lo[1], hi[1], strict=True)) + (
            round((lo[2] + (hi[2] - lo[2]) * f) * 255),)
        strip.putpixel((0, p) if vertical else (p, 0), rgba)
    return strip.resize((size, size))


def render(svg_text: str, size: int):
    """The icon as a size x size RGBA Pillow image (drawn 4x larger, then reduced)."""
    from PIL import Image, ImageChops, ImageDraw

    root = ET.fromstring(svg_text)
    _, _, vw, vh = (float(v) for v in root.get("viewBox", "0 0 256 256").split())
    big = size * 4
    scale = big / max(vw, vh)
    gradients = {g.get("id"): g for g in root.iter(f"{_SVG}linearGradient")}
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    for path in root.iter(f"{_SVG}path"):
        mask = Image.new("L", (big, big), 0)
        evenodd = path.get("fill-rule") == "evenodd"
        for points in _subpaths(path.get("d", "")):
            if len(points) < 3:
                continue
            layer = Image.new("L", (big, big), 0)
            ImageDraw.Draw(layer).polygon([(x * scale, y * scale) for x, y in points], fill=255)
            mask = ImageChops.difference(mask, layer) if evenodd else ImageChops.lighter(
                mask, layer)
        fill = path.get("fill", "#000000")
        opacity = float(path.get("fill-opacity", "1"))
        ref = re.fullmatch(r"url\(#([^)]+)\)", fill)
        if ref:
            paint = _gradient_fill(gradients[ref.group(1)], big, scale)
        else:
            rgb = tuple(round(v * 255) for v in _rgb(fill if fill != "#fff" else "#ffffff"))
            paint = Image.new("RGBA", (big, big), rgb + (255,))
        alpha = ImageChops.multiply(paint.getchannel("A"), mask)
        if opacity < 1:
            alpha = alpha.point(lambda a, o=opacity: round(a * o))
        paint.putalpha(alpha)
        image = Image.alpha_composite(image, paint)
    return image.resize((size, size), Image.Resampling.LANCZOS)


@functools.lru_cache(maxsize=4)
def image(accent: str = BASE_COLOR, size: int = 64):
    return render(svg(accent), size)


def write_ico(path: Path, accent: str = BASE_COLOR) -> Path:
    """A Windows .ico with every size drawn separately (sharper than scaling one image)."""
    text = svg(accent)
    frames = [render(text, s) for s in ICO_SIZES]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                    append_images=frames[:-1])
    return path
