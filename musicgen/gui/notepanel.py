"""「音符」工具窗口：查看 / 修改钢琴卷帘里选中的那一小段。

钢琴卷帘里一格 = 音轨里的一个 ``<note>``（可能是和弦，多个音高用逗号分隔）。
这个面板把它的属性摊开：所属音轨、小节位置、起始/时长、音高、力度，
改完点「应用」写回音轨 XML，也可以复制、删除或跳到相邻音符。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..core.instruments import note_names
from . import theme, widgets

T = theme.PALETTE


class NotePanel(tk.Frame):
    PREFERRED_HEIGHT = 268

    def __init__(
        self,
        master: tk.Misc,
        on_apply: Callable[[dict], None],
        on_delete: Callable[[], None],
        on_duplicate: Callable[[], None],
        on_step: Callable[[int], None],
    ) -> None:
        super().__init__(master, bg=T["panel"])
        self.on_apply = on_apply
        self.on_delete = on_delete
        self.on_duplicate = on_duplicate
        self.on_step = on_step
        self.context: tuple | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}

        self.header = widgets.PanelHeader(self, "音符")
        self.header.pack(fill="x")

        # 底部按钮先占位，滚动区才能正确吃掉剩余高度
        footer = tk.Frame(self, bg=T["panel"])
        footer.pack(side="bottom", fill="x", padx=10, pady=(2, 8))
        self.btn_apply = tk.Button(
            footer, text="应用并保存", command=self._apply, bg=T["accent_dim"], fg="#FFFFFF",
            activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
            font=theme.font(9), cursor="hand2", pady=4,
        )
        self.btn_apply.pack(fill="x")
        row = tk.Frame(footer, bg=T["panel"])
        row.pack(fill="x", pady=(4, 0))
        self.btn_prev = self._flat_button(row, "◀", lambda: self.on_step(-1))
        self.btn_prev.pack(side="left", padx=(0, 3))
        self.btn_next = self._flat_button(row, "▶", lambda: self.on_step(1))
        self.btn_next.pack(side="left", padx=(0, 6))
        self.btn_duplicate = self._flat_button(row, "复制", self.on_duplicate)
        self.btn_duplicate.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.btn_delete = self._flat_button(row, "删除", self.on_delete)
        self.btn_delete.pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.scroll = widgets.ScrollArea(self, background=T["panel"])
        self.scroll.pack(fill="both", expand=True)
        self.grid = widgets.PropertyGrid(self.scroll.body, label_width=58)
        self.grid.pack(fill="x")

        self.labels: dict[str, tk.Label] = {}
        self.labels["track"] = self.grid.add_text("音轨")
        self.labels["pos"] = self.grid.add_text("位置", color=T["text_dim"])

        self._span_row()
        self.entries["pitch"] = self._entry("音高")

        self.velocity = self.grid.add_row(
            "力度", lambda holder: widgets.MiniSlider(
                holder, 0.0, 1.0, 0.8, 0.01, lambda v: f"{v:.2f}"
            ),
        )

        self.scroll.bind_wheels()
        self.set_note(None, None, -1)

    # ---- 构建小工具 ---------------------------------------------------
    @staticmethod
    def _flat_button(master: tk.Misc, text: str, command) -> tk.Button:
        return tk.Button(
            master, text=text, command=command, bg=T["chrome_hi"], fg=T["text"],
            activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
            font=theme.font(9), cursor="hand2", pady=3,
        )

    def _span_row(self) -> None:
        """起始与时长放在同一行：``[1] → [0.95] 拍``。"""
        self.vars["time"] = tk.StringVar()
        self.vars["dur"] = tk.StringVar()

        def build(holder: tk.Misc) -> tk.Frame:
            box = tk.Frame(holder, bg=T["panel"])
            box.pack(fill="x", expand=True)
            for key, width in (("time", 5), ("dur", 5)):
                entry = ttk.Entry(box, textvariable=self.vars[key], width=width,
                                  style="TEntry", font=theme.font(9))
                entry.pack(side="left", fill="x", expand=True)
                entry.bind("<Return>", lambda _e: self._apply())
                self.entries[key] = entry
                if key == "time":
                    tk.Label(box, text="→", bg=T["panel"], fg=T["text_faint"],
                             font=theme.font(9)).pack(side="left", padx=3)
            tk.Label(box, text="拍", bg=T["panel"], fg=T["text_faint"],
                     font=theme.font(8)).pack(side="left", padx=(3, 0))
            return box

        self.grid.add_row("起始/时长", build)

    def _entry(self, label: str) -> ttk.Entry:
        key = "pitch"
        var = tk.StringVar()
        self.vars[key] = var

        def build(holder: tk.Misc) -> ttk.Entry:
            entry = ttk.Entry(holder, textvariable=var, style="TEntry", font=theme.font(9))
            entry.pack(fill="x", expand=True)
            entry.bind("<Return>", lambda _e: self._apply())
            return entry

        return self.grid.add_row(label, build)  # type: ignore[return-value]

    # ---- 数据 ---------------------------------------------------------
    def set_note(self, project, track, index: int) -> None:
        valid = track is not None and 0 <= index < len(track.notes)
        self.context = (project, track, index) if valid else None

        state = "normal" if valid else "disabled"
        for entry in self.entries.values():
            entry.configure(state=state)
        self.velocity.set_enabled(valid)  # type: ignore[attr-defined]
        for button in (self.btn_apply, self.btn_duplicate, self.btn_delete,
                       self.btn_prev, self.btn_next):
            button.configure(state=state)

        if not valid:
            self.labels["track"].configure(text="未选中音符")
            self.labels["pos"].configure(text="在卷帘里点一下音符")
            for var in self.vars.values():
                var.set("")
            self.velocity.set(0.8)  # type: ignore[attr-defined]
            return

        note = track.notes[index]
        bpb = max(1, project.beats_per_bar) if project is not None else 4
        bar = int(note.time // bpb) + 1
        in_bar = note.time - (bar - 1) * bpb
        self.labels["track"].configure(text=track.name)
        self.labels["pos"].configure(
            text=f"{bar} 小节 {in_bar + 1:.2f} 拍 · {index + 1}/{len(track.notes)}"
        )
        self.vars["time"].set(f"{note.time:g}")
        self.vars["dur"].set(f"{note.dur:g}")
        self.vars["pitch"].set(note_names(track.instrument, note.pitches))
        self.velocity.set(note.velocity)  # type: ignore[attr-defined]

    def values(self) -> dict:
        return {
            "time": self.vars["time"].get().strip(),
            "dur": self.vars["dur"].get().strip(),
            "pitch": self.vars["pitch"].get().strip(),
            "velocity": float(self.velocity.get()),  # type: ignore[attr-defined]
        }

    def summary(self) -> str:
        if not self.context:
            return "未选中音符"
        _, track, index = self.context
        note = track.notes[index]
        return (f"{track.name} 第 {index + 1} 个音符 · "
                f"{note_names(track.instrument, note.pitches)} · "
                f"{note.time:g}→{note.time + note.dur:g} 拍")

    def _apply(self) -> None:
        if self.context:
            self.on_apply(self.values())
