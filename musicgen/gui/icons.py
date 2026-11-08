"""图标：工具栏用 Canvas 矢量绘制，资源管理器用 PhotoImage 位图。

不依赖任何图片资源，全部按 VS 2022 的观感现画，颜色随主题变化。
"""

from __future__ import annotations

import tkinter as tk

from . import theme

_IMAGE_CACHE: dict[tuple, tk.PhotoImage] = {}


# ---------------------------------------------------------------- 位图（Treeview）


def _paint(size: int, painter, background: str, master: tk.Misc) -> tk.PhotoImage:
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            row.append(painter(x, y) or background)
        rows.append("{" + " ".join(row) + "}")
    image = tk.PhotoImage(master=master, width=size, height=size)
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


def image(kind: str, background: str, master: tk.Misc, size: int = 16) -> tk.PhotoImage:
    """取一张图标位图（按 解释器+kind+背景 缓存；master 必须传入，否则会挂到默认根窗口）。"""
    key = (id(master.tk), kind, background, size)
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]
    painter = {
        "folder": _folder,
        "track": _track,
        "solution": _solution,
        "file": _file,
        "wave": _wave,
    }.get(kind, _file)
    img = _paint(size, painter, background, master)
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
        # 向右箭头穿过一条边界 = 「导出到外部」，与向下箭头的「下载音源」区分开
        width = max(1, int(1.7 * s))
        canvas.create_line(px(2.8), py(8), px(10.4), py(8), fill=color, width=width)
        canvas.create_polygon(
            px(9.0), py(4.6), px(13.2), py(8), px(9.0), py(11.4), fill=color, outline=""
        )
        canvas.create_line(px(13.6), py(3.0), px(13.6), py(13.0), fill=color, width=width)
    elif kind == "open":
        canvas.create_polygon(
            px(2), py(4), px(7), py(4), px(8.5), py(6), px(14), py(6),
            px(14), py(12.5), px(2), py(12.5), fill="", outline=color, width=max(1, int(1.3 * s)),
        )
    elif kind == "new":
        canvas.create_polygon(
            px(3.2), py(2.6), px(9.4), py(2.6), px(12.8), py(6.0),
            px(12.8), py(13.4), px(3.2), py(13.4), fill="", outline=color,
            width=max(1, int(1.3 * s)),
        )
        canvas.create_line(px(9.4), py(2.6), px(9.4), py(6.0), px(12.8), py(6.0),
                           fill=color, width=max(1, int(1.1 * s)))
        canvas.create_line(px(8.0), py(8.2), px(8.0), py(11.8), fill=color,
                           width=max(1, int(1.5 * s)))
        canvas.create_line(px(6.2), py(10.0), px(9.8), py(10.0), fill=color,
                           width=max(1, int(1.5 * s)))
    elif kind == "download":
        canvas.create_line(px(8), py(3), px(8), py(9), fill=color, width=max(1, int(1.5 * s)))
        canvas.create_polygon(px(5), py(8.2), px(11), py(8.2), px(8), py(11.8), fill=color, outline="")
        canvas.create_line(px(3.5), py(13.5), px(12.5), py(13.5), fill=color, width=max(1, int(1.5 * s)))
    elif kind == "refresh":
        # 两段圆弧 + 两个三角箭头 = 循环刷新；单段弧在 16px 下会看成字母 C
        width = max(1, int(1.7 * s))
        for start in (45, 225):
            canvas.create_arc(
                px(3.2), py(3.2), px(12.8), py(12.8), start=start, extent=110,
                style="arc", outline=color, width=width,
            )
        canvas.create_polygon(px(10.8), py(2.6), px(13.6), py(3.2), px(11.2), py(5.6),
                              fill=color, outline="")
        canvas.create_polygon(px(5.2), py(10.4), px(2.4), py(12.8), px(4.8), py(13.4),
                              fill=color, outline="")
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
    elif kind == "add":
        # 粗加号：和「新建工程」（文件+加号）区分开
        width = max(1, int(2.0 * s))
        canvas.create_line(px(8), py(3.2), px(8), py(12.8), fill=color, width=width)
        canvas.create_line(px(3.2), py(8), px(12.8), py(8), fill=color, width=width)
    elif kind == "trash":
        width = max(1, int(1.3 * s))
        canvas.create_line(px(3.4), py(4), px(12.6), py(4), fill=color, width=width)
        canvas.create_line(px(6.2), py(4), px(6.2), py(2.6), px(9.8), py(2.6),
                           px(9.8), py(4), fill=color, width=width)
        canvas.create_polygon(px(4.6), py(5), px(11.4), py(5), px(10.6), py(13),
                              px(5.4), py(13), fill="", outline=color, width=width)
        for value in (6.8, 9.2):
            canvas.create_line(px(value), py(6.6), px(value), py(11.4),
                               fill=color, width=max(1, int(1.0 * s)))
    elif kind == "eye":
        width = max(1, int(1.3 * s))
        canvas.create_oval(px(2), py(4.8), px(14), py(11.2), outline=color, width=width)
        canvas.create_oval(px(6.5), py(6.5), px(9.5), py(9.5), fill=color, outline="")
    elif kind in ("focus", "focus_on"):
        width = max(1, int(1.4 * s))
        for x1, y1, x2, y2 in (
            (2.6, 2.6, 5.6, 2.6), (2.6, 2.6, 2.6, 5.6),
            (13.4, 2.6, 10.4, 2.6), (13.4, 2.6, 13.4, 5.6),
            (2.6, 13.4, 5.6, 13.4), (2.6, 13.4, 2.6, 10.4),
            (13.4, 13.4, 10.4, 13.4), (13.4, 13.4, 13.4, 10.4),
        ):
            canvas.create_line(px(x1), py(y1), px(x2), py(y2), fill=color, width=width)
        if kind == "focus_on":
            canvas.create_oval(px(6.4), py(6.4), px(9.6), py(9.6), fill=color, outline="")
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
