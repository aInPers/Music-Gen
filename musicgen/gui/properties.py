"""右侧「属性」工具窗口：编辑选中音轨的混音与播放参数。

内容放在可滚动容器里：窗口压矮时不会把行裁掉，滚轮即可看全。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import theme, widgets

T = theme.PALETTE


class PropertiesPanel(tk.Frame):
    PREFERRED_HEIGHT = 420

    def __init__(self, master: tk.Misc, on_apply: Callable[[], None]) -> None:
        super().__init__(master, bg=T["panel"])
        self.on_apply = on_apply
        self.track = None

        self.header = widgets.PanelHeader(self, "属性")
        self.header.pack(fill="x")

        footer = tk.Frame(self, bg=T["panel"])
        footer.pack(side="bottom", fill="x", pady=(8, 8), padx=10)
        self.btn_apply = tk.Button(
            footer, text="应用并保存", command=self._apply, bg=T["accent_dim"], fg="#FFFFFF",
            activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
            font=theme.font(9), cursor="hand2", pady=5,
        )
        self.btn_apply.pack(fill="x")
        self.hint = tk.Label(footer, text="修改后写入音轨 XML", bg=T["panel"], fg=T["text_faint"],
                             font=theme.font(8))
        self.hint.pack(anchor="w", pady=(4, 0))

        self.scroll = widgets.ScrollArea(self, background=T["panel"])
        self.scroll.pack(fill="both", expand=True)
        self.grid = widgets.PropertyGrid(self.scroll.body, label_width=76)
        self.grid.pack(fill="x")

        self.labels: dict[str, tk.Label] = {}
        self.vars: dict[str, object] = {}

        self.grid.add_category("常规")
        self.labels["name"] = self.grid.add_text("名称")
        self.labels["instrument"] = self.grid.add_text("乐器")
        self.labels["notes"] = self.grid.add_text("音符数")
        self.labels["file"] = self.grid.add_text("文件", color=T["text_dim"])

        self.grid.add_category("混音")
        self.vars["gain_db"] = self._slider("音量", -24.0, 12.0, 0.0, 0.5,
                                            lambda v: f"{v:+.1f} dB")
        self.vars["pan"] = self._slider("声像", -1.0, 1.0, 0.0, 0.05, self._format_pan)
        self.vars["reverb"] = self._slider("混响", 0.0, 1.0, 0.0, 0.01, lambda v: f"{v:.2f}")

        self.grid.add_category("播放")
        self.vars["loop"] = self._check("循环")
        self.vars["mute"] = self._check("静音")
        self.vars["solo"] = self._check("独奏")

        row = tk.Frame(self.grid, bg=T["panel"])
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text="循环长度", bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 padx=10).pack(side="left")
        self.vars["loop_len"] = tk.StringVar(value="")
        self.entry_loop = ttk.Entry(row, textvariable=self.vars["loop_len"], width=8,
                                    style="TEntry", font=theme.font(9))
        self.entry_loop.pack(side="left", padx=(0, 6))
        tk.Label(row, text="拍", bg=T["panel"], fg=T["text_faint"], font=theme.font(8)).pack(side="left")

        self.scroll.bind_wheels()
        self.set_track(None)

    # ---- 构建小工具 ---------------------------------------------------
    def _slider(self, label: str, low: float, high: float, value: float,
                resolution: float, formatter) -> widgets.MiniSlider:
        return self.grid.add_row(  # type: ignore[return-value]
            label,
            lambda holder: widgets.MiniSlider(holder, low, high, value, resolution, formatter),
        )

    def _check(self, label: str) -> widgets.CheckBox:
        return self.grid.add_row(  # type: ignore[return-value]
            "", lambda holder: widgets.CheckBox(holder, label)
        )

    @staticmethod
    def _format_pan(value: float) -> str:
        if abs(value) < 0.02:
            return "居中"
        return f"{'右' if value > 0 else '左'} {abs(value):.2f}"

    # ---- 数据 ---------------------------------------------------------
    def set_track(self, track, instrument_note: str = "") -> None:
        self.track = track
        editable = track is not None
        for widget in (self.btn_apply,):
            widget.configure(state="normal" if editable else "disabled")
        for key in ("gain_db", "pan", "reverb", "loop", "mute", "solo"):
            self.vars[key].set_enabled(editable)  # type: ignore[attr-defined]
        self.entry_loop.configure(state="normal" if editable else "disabled")

        if track is None:
            self.labels["name"].configure(text="未选中音轨")
            self.labels["instrument"].configure(text="—")
            self.labels["notes"].configure(text="—")
            self.labels["file"].configure(text="—")
            return

        self.labels["name"].configure(text=track.name)
        self.labels["instrument"].configure(text=track.instrument + (instrument_note or ""))
        self.labels["notes"].configure(text=str(len(track.notes)))
        self.labels["file"].configure(text=track.path.name if track.path else "—")
        self.vars["gain_db"].set(track.gain_db)      # type: ignore[attr-defined]
        self.vars["pan"].set(track.pan)              # type: ignore[attr-defined]
        self.vars["reverb"].set(track.reverb)        # type: ignore[attr-defined]
        self.vars["loop"].set(track.loop)            # type: ignore[attr-defined]
        self.vars["mute"].set(track.mute)            # type: ignore[attr-defined]
        self.vars["solo"].set(track.solo)            # type: ignore[attr-defined]
        self.vars["loop_len"].set("" if not track.loop_len else f"{track.loop_len:g}")

    def sync_flags(self) -> None:
        """外部（右键菜单等）改了静音/独奏后，同步复选框。"""
        if self.track is None:
            return
        self.vars["mute"].set(self.track.mute)  # type: ignore[attr-defined]
        self.vars["solo"].set(self.track.solo)  # type: ignore[attr-defined]

    def values(self) -> dict:
        return {
            "gain_db": self.vars["gain_db"].get(),
            "pan": self.vars["pan"].get(),
            "reverb": self.vars["reverb"].get(),
            "loop": self.vars["loop"].get(),
            "mute": self.vars["mute"].get(),
            "solo": self.vars["solo"].get(),
            "loop_len": self.vars["loop_len"].get().strip(),
        }

    def _apply(self) -> None:
        self.on_apply()
