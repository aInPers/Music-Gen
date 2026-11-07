"""图标：工具栏用 Canvas 矢量绘制，资源管理器用 PhotoImage 位图。

不依赖任何图片资源，全部按 VS 2022 的观感现画，颜色随主题变化。
"""

from __future__ import annotations

import tkinter as tk

from . import theme

_IMAGE_CACHE: dict[tuple, tk.PhotoImage] = {}


# ---------------------------------------------------------------- 位图（Treeview）


def _paint(size: int, painter, background: str) -> tk.PhotoImage:
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            row.append(painter(x, y) or background)
        rows.append("{" + " ".join(row) + "}")
    image = tk.PhotoImage(width=size, height=size)
    image.put("\n".join(rows))
    return image


def _folder(x: int, y: int) -> str | None:
    if 2 <= x <= 7 and 3 <= y <= 5:
        return "#D7BA7D"
    if 2 <= x <= 13 and y == 4 and x <= 7:
        return "#D7BA7D"
    if 2 <= x <= 13 and 6 <= y <= 12:
        return "#DCB67A" if y < 8 else "#C9A363"
    return None


def _track(x: int, y: int) -> str | None:
    # 一排钢琴键
    if 2 <= x <= 13 and 4 <= y <= 12:
        if y in (4, 12) or x in (2, 13):
            return "#8A8A8A"
        if x in (5, 9) and 4 <= y <= 8:
            return "#1E1E1E"
        return "#E8E8E8"
    return None


def _solution(x: int, y: int) -> str | None:
    # 三个方块，像“解决方案”图标
    if 3 <= x <= 7 and 3 <= y <= 7:
        return "#4FC1FF"
    if 9 <= x <= 13 and 3 <= y <= 7:
        return "#4EC9B0"
    if 6 <= x <= 10 and 9 <= y <= 13:
        return "#DCDCAA"
    return None


def _file(x: int, y: int) -> str | None:
    if 4 <= x <= 11 and 3 <= y <= 13:
        if x == 11 and y <= 6:
            return None
        return "#B8B8B8" if y not in (3, 13) else "#8A8A8A"
    return None


def _wave(x: int, y: int) -> str | None:
    heights = {2: 2, 3: 4, 4: 3, 5: 6, 6: 5, 7: 8, 8: 5, 9: 7, 10: 4, 11: 5, 12: 3, 13: 2}
    half = heights.get(x, 0)
    if half and abs(y - 8) <= half:
        return "#4FC1FF" if x < 8 else "#4EC9B0"
    return None


def image(kind: str, background: str, size: int = 16) -> tk.PhotoImage:
    """取一张图标位图（按 kind+背景缓存，Treeview 需要 PhotoImage）。"""
    key = (kind, background, size)
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]
    painter = {
        "folder": _folder,
        "track": _track,
        "solution": _solution,
        "file": _file,
        "wave": _wave,
    }.get(kind, _file)
    img = _paint(size, painter, background)
    _IMAGE_CACHE[key] = img
    return img


# ---------------------------------------------------------------- 矢量（工具栏）


def draw(canvas: tk.Canvas, kind: str, cx: float, cy: float, size: float, color: str) -> None:
    """在 canvas 上以 (cx, cy) 为中心画一个 size 大小的图标。"""
    s = size / 16.0

    def px(value: float) -> float:
        return cx + (value - 8) * s

    def py(value: float) -> float:
        return cy + (value - 8) * s

    if kind == "play":
        canvas.create_polygon(
            px(5), py(3.5), px(13), py(8), px(5), py(12.5),
            fill=color, outline="",
        )
    elif kind == "stop":
        canvas.create_rectangle(px(4.5), py(4.5), px(11.5), py(11.5), fill=color, outline="")
    elif kind == "export":
        canvas.create_line(px(8), py(3), px(8), py(9.5), fill=color, width=max(1, int(1.6 * s)))
        canvas.create_polygon(
            px(4.8), py(9), px(11.2), py(9), px(8), py(12.6), fill=color, outline=""
        )
        canvas.create_line(px(3.5), py(14), px(12.5), py(14), fill=color, width=max(1, int(1.6 * s)))
    elif kind == "open":
        canvas.create_polygon(
            px(2), py(4), px(7), py(4), px(8.5), py(6), px(14), py(6),
            px(14), py(12.5), px(2), py(12.5), fill="", outline=color, width=max(1, int(1.3 * s)),
        )
    elif kind == "download":
        canvas.create_line(px(8), py(3), px(8), py(9), fill=color, width=max(1, int(1.5 * s)))
        canvas.create_polygon(px(5), py(8.2), px(11), py(8.2), px(8), py(11.8), fill=color, outline="")
        canvas.create_line(px(3.5), py(13.5), px(12.5), py(13.5), fill=color, width=max(1, int(1.5 * s)))
    elif kind == "refresh":
        canvas.create_arc(
            px(3.5), py(3.5), px(12.5), py(12.5), start=40, extent=280,
            style="arc", outline=color, width=max(1, int(1.5 * s)),
        )
        canvas.create_polygon(px(11), py(5), px(12.8), py(2.6), px(8.6), py(3.2), fill=color, outline="")
    elif kind == "gear":
        canvas.create_oval(px(5.5), py(5.5), px(10.5), py(10.5), outline=color,
                           width=max(1, int(1.4 * s)))
        for dx, dy in ((0, -5.6), (0, 5.6), (-5.6, 0), (5.6, 0)):
            canvas.create_line(px(8 + dx), py(8 + dy), px(8 + dx * 0.55), py(8 + dy * 0.55),
                               fill=color, width=max(1, int(1.6 * s)))
    elif kind == "wave":
        for i, h in enumerate((3.5, 6.0, 4.5, 7.5, 4.0)):
            x = 3.4 + i * 2.3
            canvas.create_line(px(x), py(8 - h * 0.55), px(x), py(8 + h * 0.55),
                               fill=color, width=max(1, int(2.0 * s)), capstyle="round")
    elif kind == "save":
        canvas.create_rectangle(px(2.8), py(2.8), px(13.2), py(13.2),
                                outline=color, width=max(1, int(1.4 * s)))
        canvas.create_line(px(5.6), py(2.8), px(5.6), py(6.4), px(10.4), py(6.4),
                           px(10.4), py(2.8), fill=color, width=max(1, int(1.2 * s)))
        canvas.create_rectangle(px(5.2), py(9.0), px(10.8), py(13.2),
                                outline=color, width=max(1, int(1.2 * s)))
    elif kind == "bolt":
        canvas.create_polygon(
            px(9.2), py(2.6), px(4.4), py(9.0), px(7.6), py(9.0),
            px(6.8), py(13.4), px(11.6), py(6.8), px(8.4), py(6.8),
            fill=color, outline="",
        )
    elif kind == "pause":
        for value in (5.6, 9.4):
            canvas.create_rectangle(px(value), py(3.5), px(value + 1.8), py(12.5),
                                    fill=color, outline="")
    elif kind == "dock":
        canvas.create_rectangle(px(2.8), py(3.2), px(13.2), py(12.8),
                                outline=color, width=max(1, int(1.3 * s)))
        canvas.create_rectangle(px(2.8), py(3.2), px(6.6), py(12.8),
                                fill=color, outline="")
    elif kind == "close":
        canvas.create_line(px(4.5), py(4.5), px(11.5), py(11.5), fill=color,
                           width=max(1, int(1.3 * s)))
        canvas.create_line(px(11.5), py(4.5), px(4.5), py(11.5), fill=color,
                           width=max(1, int(1.3 * s)))
    elif kind == "keys":
        canvas.create_rectangle(px(2.5), py(3.5), px(13.5), py(12.5),
                                outline=color, width=max(1, int(1.2 * s)))
        for value in (6.0, 10.0):
            canvas.create_line(px(value), py(3.5), px(value), py(12.5), fill=color,
                               width=max(1, int(1.0 * s)))
        for value in (4.3, 8.3):
            canvas.create_rectangle(px(value), py(3.5), px(value + 2.4), py(8.0),
                                    fill=color, outline="")
    elif kind == "info":
        canvas.create_oval(px(3.5), py(3.5), px(12.5), py(12.5), outline=color,
                           width=max(1, int(1.3 * s)))
        canvas.create_line(px(8), py(7.2), px(8), py(11.2), fill=color, width=max(1, int(1.5 * s)))
        canvas.create_oval(px(7.1), py(4.6), px(8.9), py(5.8), fill=color, outline="")


def accent_dot(canvas: tk.Canvas, x: float, y: float, color: str, radius: float = 3.0) -> None:
    canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="")


TRACK_COLOR = theme.TRACK_COLORS
