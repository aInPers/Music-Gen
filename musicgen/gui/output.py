"""底部「输出」工具窗口：渲染日志、错误与耗时信息。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme, widgets

T = theme.PALETTE


class OutputPanel(tk.Frame):
    def __init__(self, master: tk.Misc, on_clear: Callable[[], None] | None = None) -> None:
        super().__init__(master, bg=T["panel"])
        self.header = widgets.PanelHeader(self, "输出")
        self.header.pack(fill="x")
        self.header.add_action("close", on_clear or self.clear, "清空输出")

        self.log_view = widgets.LogView(self)
        self.log_view.pack(fill="both", expand=True)
        widgets.Separator(self).pack(fill="x", side="bottom")

    # ---- API ----------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self.log_view.log(message, level)

    def clear(self) -> None:
        self.log_view.clear()
