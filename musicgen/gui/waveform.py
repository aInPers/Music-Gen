"""文档页：波形（渲染结果的峰值包络 + 时间轴）。"""

from __future__ import annotations

import tkinter as tk

import numpy as np

from . import theme

T = theme.PALETTE


class WaveformPage(tk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["editor"])
        self.audio: np.ndarray | None = None
        self.sample_rate = 44100
        self.summary = ""
        self.canvas = tk.Canvas(self, bg=T["editor"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw())

    # ---- 数据 ---------------------------------------------------------
    def set_result(self, audio: np.ndarray, sample_rate: int, summary: str) -> None:
        self.audio = audio
        self.sample_rate = sample_rate
        self.summary = summary
        self.redraw()

    def clear(self) -> None:
        self.audio = None
        self.summary = ""
        self.redraw()

    # ---- 绘制 ---------------------------------------------------------
    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        width, height = c.winfo_width(), c.winfo_height()
        if width < 40 or height < 40:
            return

        top = 34
        if self.audio is None or self.audio.size == 0:
            c.create_text(width / 2, height / 2 - 8, text="尚未渲染",
                          fill=T["text_faint"], font=theme.font(11))
            c.create_text(width / 2, height / 2 + 14, text="按 Ctrl+R 或点击工具栏的渲染按钮",
                          fill=T["text_faint"], font=theme.font(8))
            return

        c.create_text(12, 12, anchor="w", text=self.summary, fill=T["text"], font=theme.font(9))
        c.create_text(width - 12, 12, anchor="e", text="峰值包络（L+R 平均）",
                      fill=T["text_faint"], font=theme.font(8))

        mono = self.audio.mean(axis=0)
        duration = len(mono) / self.sample_rate
        mid = top + (height - top - 30) / 2
        half = (height - top - 30) / 2 - 6

        # 时间网格：每秒一条细线，每 5 秒标数字
        step = 1.0
        while duration / step > 24:
            step *= 2
        t = 0.0
        while t <= duration:
            x = 8 + (width - 16) * (t / max(duration, 1e-6))
            c.create_line(x, top, x, height - 24,
                          fill=T["grid_strong"] if abs(t % (step * 5)) < 1e-9 else T["grid"])
            if abs(t % (step * 5)) < 1e-9:
                c.create_text(x, height - 12, text=f"{t:g}s", fill=T["text_faint"],
                              font=theme.font(7))
            t += step

        c.create_line(8, mid, width - 8, mid, fill=T["border_soft"])

        usable = max(1, int(width - 16))
        columns = max(1, min(usable, len(mono)))
        chunk = max(1, len(mono) // columns)
        for x in range(columns):
            block = mono[x * chunk : (x + 1) * chunk]
            if block.size == 0:
                break
            low = float(block.min())
            high = float(block.max())
            clipped = abs(high) >= 0.999 or abs(low) >= 0.999
            color = T["error"] if clipped else T["accent"]
            px = 8 + x
            c.create_line(px, mid - high * half, px, mid - low * half, fill=color)
