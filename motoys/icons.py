# -*- coding: utf-8 -*-
"""会话首字图标：按会话标题的首文字现画一张深色圆角图标，缓存到 icon_cache/。

Flow 的 IcoPath 收绝对路径，所以这里直接返回生成的 PNG 路径（进程内缓存 + 磁盘缓存）。
画图要 Pillow，且只在这台机器上装过；任何一步失败都退回 images/kimi.png —— 绝不让
查询因为画图标而失败。色相按标题哈希偏移，好让首字相同的会话也能一眼区分。

画布沿用插件图标的视觉语言：BG_TOP/BG_BOTTOM 纵向渐变 + BORDER 描边 + CYAN 渐变文字。
"""

import colorsys
import hashlib
import os
import unicodedata

from .base import ICON_KIMI, PLUGIN_DIR, log

ICON_CACHE_DIR = os.path.join(PLUGIN_DIR, "icon_cache")
ICON_CACHE_LIMIT = 240      # 缓存上限，超了从最旧的开始删
ICON_STYLE = "v1"           # 画法改了就把这个 bump 一下，旧缓存自动失效

SC = 512                    # 超采样画布
SO = 128                    # 输出尺寸
REF = 100                   # 参考字号：先按这个量一遍，再线性推算真正要多大
MAX_W = 0.74                # 文字最大宽度（占画布比例）
MAX_H = 0.52                # 文字最大高度
SPREAD = 20                 # 色相偏移范围：±20°

BG_TOP = (34, 41, 63)
BG_BOTTOM = (14, 17, 28)
BORDER = (62, 76, 112)
CYAN_A = (56, 189, 248)     # #38bdf8
CYAN_B = (34, 211, 238)     # #22d3ee

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
)

_fonts = {}


def initials(title):
    """标题的首文字：只取字母/数字，最多两个字，拉丁字母转大写。"""
    chars = [c for c in str(title or "") if unicodedata.category(c)[0] in ("L", "N")]
    return "".join(chars[:2]).upper()


def _load_font(size):
    if size in _fonts:
        return _fonts[size]
    try:
        from PIL import ImageFont
    except Exception:
        _fonts[size] = None
        return None
    font = None
    for path in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    _fonts[size] = font
    return font


def _layout(chars):
    """按参考字号量一次，线性推算字号；再小步回退保证不越界。返回 (字号, 字距, 字体)。"""
    ref = _load_font(REF)
    if ref is None:
        return None
    gap_ref = int(REF * 0.06) if len(chars) > 1 else 0
    width = sum(ref.getlength(c) for c in chars) + gap_ref * (len(chars) - 1)
    ascent, descent = ref.getmetrics()
    if width <= 0 or ascent + descent <= 0:
        return None

    size = int(min(SC * MAX_W / width, SC * MAX_H / (ascent + descent)) * REF)
    size = max(10, min(size, int(SC * 0.62)))
    for _ in range(8):
        font = _load_font(size)
        if font is None:
            return None
        gap = int(size * 0.06) if len(chars) > 1 else 0
        width = sum(font.getlength(c) for c in chars) + gap * (len(chars) - 1)
        ascent, descent = font.getmetrics()
        if width <= SC * MAX_W and ascent + descent <= SC * MAX_H:
            return size, gap, font
        size = int(size * 0.94)
    return None


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _shift(rgb, degrees):
    """把颜色绕色相盘转一点（保持亮度/饱和度），用来拉开同首字会话的区分度。"""
    if not degrees:
        return rgb
    h, l, s = colorsys.rgb_to_hls(*[c / 255.0 for c in rgb])
    h = (h + degrees / 360.0) % 1.0
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))


def _background(size):
    from PIL import Image, ImageDraw
    bg = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(bg)
    for y in range(size):
        draw.line([(0, y), (size, y)], fill=_mix(BG_TOP, BG_BOTTOM, y / max(1, size - 1)))

    radius = max(1, int(size * 0.22))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)

    edge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius,
        outline=BORDER + (255,), width=max(1, int(size * 0.012)))
    return Image.alpha_composite(out, edge)


def _gradient(size, degrees):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    top, bottom = _shift(CYAN_A, degrees), _shift(CYAN_B, degrees)
    for y in range(size):
        draw.line([(0, y), (size, y)], fill=_mix(top, bottom, y / max(1, size - 1)))
    return img


def _draw(chars, degrees, path):
    from PIL import Image, ImageDraw
    layout = _layout(chars)
    if layout is None:
        raise RuntimeError("找不到可用字体或文字量不出尺寸")
    _, gap, font = layout

    layer = Image.new("L", (SC, SC), 0)
    pen = ImageDraw.Draw(layer)
    widths = [font.getlength(c) for c in chars]
    total = sum(widths) + gap * (len(chars) - 1)
    x = (SC - total) / 2.0
    for char, width in zip(chars, widths):
        pen.text((x + width / 2.0, SC / 2.0), char, fill=255, font=font, anchor="mm")
        x += width + gap

    base = _background(SC)
    base.paste(_gradient(SC, degrees), (0, 0), layer)

    os.makedirs(ICON_CACHE_DIR, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    base.resize((SO, SO), Image.LANCZOS).save(tmp, "PNG")
    os.replace(tmp, path)


def _prune():
    try:
        files = [entry for entry in os.scandir(ICON_CACHE_DIR) if entry.is_file()]
        if len(files) <= ICON_CACHE_LIMIT:
            return
        files.sort(key=lambda entry: entry.stat().st_mtime)
        for entry in files[:len(files) - ICON_CACHE_LIMIT]:
            try:
                os.remove(entry.path)
            except OSError:
                pass
    except Exception as exc:
        log("清理首字图标缓存失败：%r" % (exc,))


def icon_for(title, fallback=ICON_KIMI):
    """按标题首文字取图标路径；缓存命中直接返回，出任何问题都退回 fallback。"""
    chars = initials(title)
    if not chars:
        return fallback

    seed = hashlib.sha1(("%s\x1f%s" % (ICON_STYLE, title or "")).encode("utf-8")).hexdigest()
    path = os.path.join(ICON_CACHE_DIR, seed[:20] + ".png")
    if os.path.exists(path):
        return path
    try:
        _draw(chars, (int(seed[:4], 16) % (SPREAD * 2 + 1)) - SPREAD, path)
    except Exception as exc:
        log("画首字图标失败（%r）：%r" % (chars, exc))
        return fallback
    _prune()
    return path
