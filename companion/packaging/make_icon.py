"""生成 pcmond 程序化图标（256×256，深色底 + 青色屏幕/CPU 柱），输出 assets/pcmond.ico。

不引用任何外部图片，构建脚本在 PyInstaller 打包前自动调用。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
CYAN = (64, 224, 208, 255)
CYAN_DIM = (64, 224, 208, 150)
BG = (15, 22, 31, 255)
BORDER = (40, 62, 78, 255)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_image(size: int = SIZE) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = size / 256.0

    def px(value: float) -> int:
        return max(1, int(round(value * unit)))

    # 深色圆角底
    draw.rounded_rectangle(
        (px(3), px(3), size - px(3), size - px(3)),
        radius=px(34),
        fill=BG,
        outline=BORDER,
        width=px(3),
    )
    # 屏幕外框
    left, top, right, bottom = px(38), px(52), size - px(38), size - px(176)
    draw.rounded_rectangle((left, top, right, bottom), radius=px(12), outline=CYAN, width=px(6))
    # 屏幕内 CPU 柱
    bar_width = px(22)
    gap = px(13)
    floors = (px(46), px(72), px(34), px(58))
    x = left + px(16)
    base = bottom - px(14)
    for index, height in enumerate(floors):
        color = CYAN if index % 2 == 0 else CYAN_DIM
        draw.rounded_rectangle((x, base - height, x + bar_width, base), radius=px(4), fill=color)
        x += bar_width + gap
    # 显示器底座
    draw.line((size // 2 - px(34), bottom + px(10), size // 2 + px(34), bottom + px(10)), fill=CYAN, width=px(7))
    return image


def main() -> None:
    output = Path(__file__).resolve().parent.parent / "assets" / "pcmond.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    make_image().save(output, format="ICO", sizes=ICO_SIZES)
    print(f"图标已生成：{output}（{output.stat().st_size} 字节，{len(ICO_SIZES)} 个尺寸）")


if __name__ == "__main__":
    main()
