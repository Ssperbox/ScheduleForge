"""Generate the Schedule Forge icon (PNG + multi-size ICO) with Pillow.

Run once when the artwork changes: py assets/make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
SIZE = 1024
BG_TOP = (28, 24, 22)
BG_BOTTOM = (10, 9, 8)
AMBER = (255, 176, 46)
EMBER = (255, 96, 32)
HOT = (255, 226, 140)


def _hexagon(cx: float, cy: float, r: float, rotate: float = 30.0) -> list[tuple[float, float]]:
    points = []
    for i in range(6):
        angle = math.radians(60 * i + rotate)
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(base)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color + (255,))
    return base


def build() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Rounded dark tile with a vertical gradient.
    tile_mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle(
        (32, 32, SIZE - 32, SIZE - 32), radius=200, fill=255
    )
    tile = _gradient(SIZE, BG_TOP, BG_BOTTOM)
    canvas.paste(tile, (0, 0), tile_mask)

    # Hexagonal badge outline (the "mod" hex) in amber.
    outline = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    od = ImageDraw.Draw(outline)
    od.polygon(_hexagon(SIZE / 2, SIZE / 2, 380), outline=AMBER + (255,), width=34)
    glow = outline.filter(ImageFilter.GaussianBlur(18))
    canvas = Image.alpha_composite(canvas, glow)
    canvas = Image.alpha_composite(canvas, outline)

    # Anvil silhouette: forge reference, drawn as a flat polygon.
    anvil = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ad = ImageDraw.Draw(anvil)
    dark = (150, 140, 134, 255)
    ad.polygon(
        [
            (250, 430), (774, 430), (842, 500), (680, 500),
            (640, 560), (600, 560), (600, 640), (700, 690),
            (700, 740), (324, 740), (324, 690), (424, 640),
            (424, 560), (384, 560), (344, 500), (182, 500),
        ],
        fill=dark,
    )
    # Ember spark above the anvil, a radial glow fading into the background.
    glow_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    cx, cy, radius = SIZE / 2, 330, 150
    for i in range(radius, 0, -4):
        t = 1 - i / radius
        color = (
            int(EMBER[0] + (HOT[0] - EMBER[0]) * t),
            int(EMBER[1] + (HOT[1] - EMBER[1]) * t),
            int(EMBER[2] + (HOT[2] - EMBER[2]) * t),
            int(40 + 200 * t),
        )
        gd.ellipse((cx - i, cy - i, cx + i, cy + i), fill=color)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(6))
    canvas = Image.alpha_composite(canvas, glow_layer)

    # Three small sparks rising from the forge.
    sparks = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sparks)
    for x, y, r in ((470, 250, 16), (540, 200, 12), (500, 150, 9)):
        sd.ellipse((x - r, y - r, x + r, y + r), fill=HOT + (255,))
    canvas = Image.alpha_composite(canvas, sparks)

    # The anvil goes last so the glow never tints its top surface.
    canvas = Image.alpha_composite(canvas, anvil)
    return canvas


def main() -> None:
    icon = build()
    png_path = HERE / "icon.png"
    ico_path = HERE / "icon.ico"
    icon.save(png_path)
    icon.save(
        ico_path,
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"wrote {png_path.name} and {ico_path.name}")


if __name__ == "__main__":
    main()
