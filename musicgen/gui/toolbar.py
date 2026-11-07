"""工具栏（VS 命令条）：一条扁平图标按钮，右侧显示工程摘要。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme, widgets

T = theme.PALETTE


class ToolBar(tk.Frame):
    def __init__(self, master: tk.Misc, handlers: dict[str, Callable[[], None]]) -> None:
        super().__init__(master, bg=T["chrome"], height=36)
        self.pack_propagate(False)

        left = tk.Frame(self, bg=T["chrome"])
        left.pack(side="left", padx=(8, 0), pady=3)

        self.btn_open = self._button(left, "open", "打开工程  Ctrl+O", handlers.get("open"))
        self._sep(left)
        self.btn_render = self._button(left, "play", "渲染工程  Ctrl+R", handlers.get("render"),
                                       accent=True)
        self.btn_play = self._button(left, "wave", "播放  Space", handlers.get("play"))
        self.btn_export = self._button(left, "save", "导出 WAV  Ctrl+S", handlers.get("export"))
        self._sep(left)
        self.btn_samples = self._button(left, "download", "下载 / 更新音源", handlers.get("samples"))
        self.btn_refresh = self._button(left, "refresh", "重新载入工程  F5", handlers.get("refresh"))

        self.btn_render.set_enabled(False)
        self.btn_play.set_enabled(False)
        self.btn_export.set_enabled(False)

        self.info = tk.Label(self, text="", bg=T["chrome"], fg=T["text_faint"],
                             font=theme.font(9), anchor="e")
        self.info.pack(side="right", padx=12)

    # ---- 构建 ---------------------------------------------------------
    def _button(self, parent: tk.Misc, icon: str, tooltip: str,
                command: Callable[[], None] | None, accent: bool = False) -> widgets.ToolButton:
        button = widgets.ToolButton(parent, icon=icon, command=command, tooltip=tooltip,
                                    accent=accent, width=30, height=30)
        button.pack(side="left", padx=1)
        return button

    def _sep(self, parent: tk.Misc) -> None:
        tk.Frame(parent, bg=T["border"], width=1, height=20).pack(side="left", padx=6, pady=5)

    # ---- 状态 ---------------------------------------------------------
    def set_project_info(self, text: str) -> None:
        self.info.configure(text=text)

    def set_busy(self, busy: bool) -> None:
        self.btn_open.set_enabled(not busy)
        self.btn_render.set_enabled(not busy)
        self.btn_samples.set_enabled(not busy)
        self.btn_refresh.set_enabled(not busy)
