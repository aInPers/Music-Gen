"""底部状态栏：VS 的蓝色条，左侧消息、右侧指标、内嵌细进度条。"""

from __future__ import annotations

import tkinter as tk

from . import theme

T = theme.PALETTE


class StatusBar(tk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["accent"], height=22)
        self.pack_propagate(False)

        self.message = tk.Label(self, text="就绪", bg=T["accent"], fg="#FFFFFF",
                                font=theme.font(9), anchor="w")
        self.message.pack(side="left", padx=10)

        self.progress = tk.Canvas(self, width=90, height=6, bg=T["accent"],
                                  highlightthickness=0, bd=0)
        self.progress.pack(side="left", padx=(10, 0))
        self._fraction = 0.0
        self.progress.bind("<Configure>", lambda _e: self._draw_progress())
        self._draw_progress()

        self._right = tk.Frame(self, bg=T["accent"])
        self._right.pack(side="right", padx=6)
        self.items: list[str] = []
        self._labels: list[tk.Label] = []

    # ---- API ----------------------------------------------------------
    def set_message(self, text: str, level: str = "info") -> None:
        colors = {"info": "#FFFFFF", "ok": "#FFFFFF", "warn": "#FFE6A8", "error": "#FFD0C7"}
        self.message.configure(text=text, fg=colors.get(level, "#FFFFFF"))

    def set_items(self, items: list[str]) -> None:
        if items == self.items:
            return
        self.items = items
        for label in self._labels:
            label.destroy()
        self._labels = []
        for index, text in enumerate(reversed(items)):
            if index:
                sep = tk.Label(self._right, text="│", bg=T["accent"], fg="#7FC4EE",
                               font=theme.font(8))
                sep.pack(side="right", padx=2)
                self._labels.append(sep)
            label = tk.Label(self._right, text=text, bg=T["accent"], fg="#FFFFFF",
                             font=theme.font(9))
            label.pack(side="right", padx=6)
            self._labels.append(label)

    def set_progress(self, fraction: float | None) -> None:
        if fraction is None:
            self._fraction = 0.0
            if self.progress.winfo_manager():
                self.progress.pack_forget()
            return
        if not self.progress.winfo_manager():
            self.progress.pack(side="left", padx=(10, 0))
        self._fraction = max(0.0, min(1.0, fraction))
        self._draw_progress()

    def _draw_progress(self) -> None:
        c = self.progress
        c.delete("all")
        width = max(c.winfo_width(), 1)
        height = max(c.winfo_height(), 1)
        c.create_rectangle(0, 0, width, height, fill="#0B5E96", outline="")
        if self._fraction > 0:
            c.create_rectangle(0, 0, width * self._fraction, height,
                               fill="#9CDCFE", outline="")
