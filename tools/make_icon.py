# -*- coding: utf-8 -*-
"""MoToys 插件图标生成器（Pillow，4× 超采样，输出 128×128）

    python tools/make_icon.py

产物（插件目录的 images/ 下）：
    icon.png    MoToys 总览：青渐变 V 字
    kimi.png    Kimi 会话：实心对话气泡 + 放大镜挖空
    config.png  配置：三条滑杆

视觉语言与 RestartExplorer / TerminalHere 一致：深色圆角底 + 青色渐变字形。
这两个插件的图标分别是 restart.png / terminal.png，直接复用，不在这里生成。
"""

import os

from PIL import Image, ImageDraw

S = 512          # 超采样画布
OUT = 128        # 最终尺寸

BG_TOP = (34, 41, 63)
BG_BOTTOM = (14, 17, 28)
BORDER = (62, 76, 112)
CYAN_A = (56, 189, 248)     # #38bdf8
CYAN_B = (34, 211, 238)     # #22d3ee

IMAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")


def background(size):
    """深色纵向渐变 + 圆角 + 细描边。"""
    img = Image.new("RGB", (size, size), BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line(
            [(0, y), (size, y)],
            fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)),
        )

    radius = int(size * 0.22)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)

    out = Image.new("RGB", (size, size), BG_BOTTOM)
    out.paste(img, (0, 0), mask)
    ImageDraw.Draw(out).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, outline=BORDER, width=max(1, int(size * 0.012))
    )
    return out


def gradient(size):
    """青色渐变，给字形当填充。"""
    img = Image.new("RGB", (size, size), CYAN_A)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line([(0, y), (size, y)],
                  fill=tuple(int(CYAN_A[i] + (CYAN_B[i] - CYAN_A[i]) * t) for i in range(3)))
    return img


def apply_glyph(base, glyph):
    """把字形按蒙版刷到渐变上。glyph(draw) 里用 255 画实心、0 挖空。"""
    mask = Image.new("L", (S, S), 0)
    glyph(ImageDraw.Draw(mask))
    base.paste(gradient(S), (0, 0), mask)
    return base


# --------------------------------------------------------------------------- #
# 三个字形
# --------------------------------------------------------------------------- #

def glyph_v(draw):
    """MoToys 的 V：粗折线 + 圆头。"""
    width = int(S * 0.085)
    r = width / 2
    points = [(0.29 * S, 0.30 * S), (0.50 * S, 0.72 * S), (0.71 * S, 0.30 * S)]
    draw.line(points, fill=255, width=width, joint="curve")
    for x, y in points:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=255)


def glyph_kimi(draw):
    """实心对话气泡 + 尾巴，气泡里挖一柄放大镜（会话搜索）。"""
    draw.rounded_rectangle([0.17 * S, 0.21 * S, 0.83 * S, 0.61 * S],
                           radius=int(S * 0.115), fill=255)
    draw.polygon([(0.30 * S, 0.55 * S), (0.25 * S, 0.79 * S), (0.52 * S, 0.61 * S)], fill=255)

    width = int(S * 0.048)
    r = 0.115 * S
    cx, cy = 0.45 * S, 0.405 * S
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=width)
    draw.line([(cx + r * 0.72, cy + r * 0.72), (cx + r * 1.42, cy + r * 1.42)], fill=0, width=width)


def glyph_config(draw):
    """三条滑杆：线 + 空心旋钮（旋钮处把线挖断）。"""
    width = int(S * 0.045)
    for y_rel, knob in ((0.28, 0.38), (0.50, 0.64), (0.72, 0.44)):
        y = y_rel * S
        x0, x1 = 0.20 * S, 0.80 * S
        draw.line([(x0, y), (x1, y)], fill=255, width=width)
        kx = knob * S
        draw.ellipse([kx - 0.105 * S, y - 0.105 * S, kx + 0.105 * S, y + 0.105 * S], fill=0)
        rr = 0.072 * S
        draw.ellipse([kx - rr, y - rr, kx + rr, y + rr], outline=255, width=int(S * 0.034))


def main():
    os.makedirs(IMAGES, exist_ok=True)
    for name, glyph in (("icon", glyph_v), ("kimi", glyph_kimi), ("config", glyph_config)):
        img = apply_glyph(background(S), glyph).resize((OUT, OUT), Image.LANCZOS)
        path = os.path.join(IMAGES, name + ".png")
        img.save(path, "PNG")
        print("%-28s %s  %d bytes" % (path, "%dx%d" % img.size, os.path.getsize(path)))


if __name__ == "__main__":
    main()
