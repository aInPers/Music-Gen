"""工具栏（VS 命令条）：新建 / 打开 / 应用 / 渲染 / 播放 / 停止 / 导出 / 音源 / 重载。

按钮的可用状态由一个地方统一算（``set_state``），避免出现「看着能点但没反应」的情况。
"""

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

        self.btn_new = self._button(left, "new", "新建工程  Ctrl+N", handlers.get("new"))
        self.btn_open = self._button(left, "open", "打开工程  Ctrl+O", handlers.get("open"))
        self._sep(left)

        self.btn_apply = self._button(left, "save", "应用参数并保存音轨  Ctrl+Enter",
                                     handlers.get("apply"))
        self.btn_render = self._button(left, "bolt", "渲染工程  Ctrl+R", handlers.get("render"),
                                       accent=True)
        self._sep(left)

        self.btn_play = self._button(left, "play", "播放 / 暂停  空格", handlers.get("play"))
        self.btn_stop = self._button(left, "stop", "停止（回到开头）  Esc", handlers.get("stop"))
        self._sep(left)

        self.btn_export = self._button(left, "export", "导出 WAV  Ctrl+S", handlers.get("export"))
        self.btn_samples = self._button(left, "download", "下载 / 更新音源", handlers.get("samples"))
        self.btn_refresh = self._button(left, "refresh", "重新载入工程  F5", handlers.get("refresh"))

        self.info = tk.Label(self, text="", bg=T["chrome"], fg=T["text_faint"],
                             font=theme.font(9), anchor="e")
        self.info.pack(side="right", padx=12)

        self.set_state(has_project=False, has_result=False, busy=False, playing=False)

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
    @property
    def playing(self) -> bool:
        return getattr(self, "_playing", False)

    def set_project_info(self, text: str) -> None:
        self.info.configure(text=text)

    def set_state(self, has_project: bool, has_result: bool, busy: bool,
                  playing: bool = False) -> None:
        """按「有没有工程 / 有没有渲染结果 / 是否在忙 / 是否在播」统一刷新按钮。"""
        self.btn_new.set_enabled(not busy)
        self.btn_open.set_enabled(not busy)
        self.btn_samples.set_enabled(not busy)
        self.btn_refresh.set_enabled(has_project and not busy)
        self.btn_apply.set_enabled(has_project and not busy)
        self.btn_render.set_enabled(has_project and not busy)
        self.btn_play.set_enabled(has_result and not busy)
        self.btn_stop.set_enabled(has_result and not busy)
        self.btn_export.set_enabled(has_result and not busy)
        self._playing = playing
        self.btn_play.set_icon("pause" if playing else "play")
        self.btn_play.set_tooltip("暂停  空格" if playing else "播放  空格")
