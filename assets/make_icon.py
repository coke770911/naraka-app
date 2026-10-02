"""產生 assets/icon.ico。

圖示概念：深色圓角底 + 金色四角星（星格）+ 底部三顆星格指示點，
呼應「謫星 / Star」商品與三格星格條件。

以 8 倍超取樣作畫再縮圖，確保 16px 時邊緣乾淨。

    python assets/make_icon.py

輸出：
    assets/icon.ico    多尺寸（16~256），給 PyInstaller 與檔案總管用
    assets/icon.png    1024px 原始圖，可自行替換後重跑
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SS = 8  # 超取樣倍率
BASE = 1024
ASSETS = Path(__file__).resolve().parent

BG_TOP = (28, 32, 44)
BG_BOTTOM = (12, 14, 20)
GOLD = (255, 206, 92)
GOLD_DEEP = (232, 158, 38)
DOT_ON = (255, 226, 150)
DOT_OFF = (58, 64, 80)


def rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def vertical_gradient(size: int) -> Image.Image:
    """垂直漸層底色。"""
    grad = Image.new("RGB", (1, size))
    pixels = grad.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        pixels[0, y] = tuple(
            round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        )
    return grad.resize((size, size), Image.BILINEAR)


def four_point_star(cx: float, cy: float, outer: float, inner: float):
    """回傳四角星（ sparkle）的 8 個頂點。

    內凹比例用 inner/outer 控制；數值越小越接近菱形，越大越尖銳。
    """
    points = []
    for index in range(8):
        angle = math.radians(-90 + index * 45)
        radius = outer if index % 2 == 0 else inner
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def draw_star(layer: ImageDraw.ImageDraw, cx: float, cy: float, outer: float,
              inner: float, color) -> None:
    points = four_point_star(cx, cy, outer, inner)
    layer.polygon(points, fill=color)


def build_image(size: int) -> Image.Image:
    canvas = Image.new("RGB", (size, size), BG_BOTTOM)
    canvas.paste(vertical_gradient(size), (0, 0))

    # 底層：淡色圓角遮罩，做出圖示形狀
    mask = Image.new("L", (size * 1, size * 1), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * 1 - 1, size * 1 - 1),
        radius=int(size * 0.22),
        fill=255,
    )
    mask = mask.resize((size, size), Image.LANCZOS)

    # 星芒畫在獨立的透明層，縮圖後再疊上，避免直接畫在小畫布上失真
    inner_layer = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    star_draw = ImageDraw.Draw(inner_layer)
    center = size * SS / 2
    # 主星略偏上，底部留給三顆指示點
    star_cy = center - size * SS * 0.045

    # 外層光暈
    draw_star(star_draw, center, star_cy, size * SS * 0.335, size * SS * 0.115,
              GOLD_DEEP + (90,))
    # 主星
    draw_star(star_draw, center, star_cy, size * SS * 0.315, size * SS * 0.108,
              GOLD + (255,))

    # 三顆星格指示點（對應第 1、2、3 格）
    dot_r = size * SS * 0.043
    dot_y = size * SS * 0.815
    spread = size * SS * 0.185
    for offset, lit in ((-spread, True), (0.0, True), (spread, False)):
        draw_star(
            star_draw,
            center + offset,
            dot_y,
            dot_r * 1.9,
            dot_r * 0.72,
            (DOT_ON if lit else DOT_OFF) + (255,),
        )

    inner_layer = inner_layer.resize((size, size), Image.LANCZOS)

    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(canvas, (0, 0), mask)
    result.alpha_composite(inner_layer)
    return result


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    master = build_image(BASE)
    master.save(ASSETS / "icon.png")

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ASSETS / "icon.ico", sizes=sizes, append_images=[])

    print(f"wrote {ASSETS / 'icon.png'}  ({BASE}x{BASE})")
    print(f"wrote {ASSETS / 'icon.ico'}  ({', '.join(str(s[0]) for s in sizes)})")


if __name__ == "__main__":
    main()