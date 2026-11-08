"""左侧「解决方案资源管理器」：工程 → tracks 文件夹 → 各音轨文件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import icons, theme, widgets

T = theme.PALETTE


class SolutionExplorer(tk.Frame):
    PREFERRED_HEIGHT = 320

    def __init__(
        self,
        master: tk.Misc,
        on_select: Callable[[int], None],
        on_toggle: Callable[[int, str], None] | None = None,
        on_apply: Callable[[], None] | None = None,
        on_new_track: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, bg=T["panel"])
        self.on_select = on_select
        self.on_toggle = on_toggle
        self.on_apply = on_apply
        self.on_new_track = on_new_track

        self.header = widgets.PanelHeader(self, "解决方案资源管理器")
        self.header.pack(fill="x")

        self._icons: dict[str, tk.PhotoImage] = {}
        self._kinds: dict[str, tuple[str, int]] = {}
        self.track_items: list[str] = []
        self._project = None

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(body, style="VS.Treeview", show="tree",
                                 selectmode="browse")
        self.scroll = ttk.Scrollbar(body, orient="vertical", style="VS.Vertical.TScrollbar",
                                    command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_context)
        self.tree.tag_configure("dim", foreground=T["text_faint"])
        self.tree.tag_configure("folder", foreground=T["text"])

        self.menu = theme.make_menu(self)
        self.menu.add_command(label="新建轨道…", command=self._ctx_new_track)
        self.menu.add_separator()
        self.menu.add_command(label="静音选中音轨", command=lambda: self._ctx_toggle("mute"))
        self.menu.add_command(label="独奏选中音轨", command=lambda: self._ctx_toggle("solo"))
        self.menu.add_separator()
        self.menu.add_command(label="在属性中应用修改", command=self._ctx_apply)

    # ---- 数据 ---------------------------------------------------------
    def load(self, project, sample_status: str = "") -> None:
        """重建树；保留当前选中音轨。"""
        self._project = project
        previous = getattr(self, "_selected_index", -1)
        self.tree.delete(*self.tree.get_children())
        self._kinds.clear()
        self.track_items = []

        root = self.tree.insert(
            "", "end", text=f"  {project.name}", open=True,
            image=self._icon("solution"),
        )
        folder = self.tree.insert(
            root, "end", text="  tracks", open=True, image=self._icon("folder"),
        )
        self._kinds[root] = ("root", -1)
        self._kinds[folder] = ("folder", -1)

        for index, track in enumerate(project.tracks):
            name = f"{track.path.name if track.path else track.name}"
            marks = []
            if track.mute:
                marks.append("静音")
            if track.solo:
                marks.append("独奏")
            if track.loop:
                marks.append("↻")
            label = f"  {name}" + (f"  {' '.join(marks)}" if marks else "")
            item = self.tree.insert(
                folder, "end", text=label, image=self._icon("track"),
                tags=("dim",) if track.mute else (),
            )
            self._kinds[item] = ("track", index)
            self.track_items.append(item)

        if sample_status:
            deps = self.tree.insert(
                root, "end", text="  音源", open=False, image=self._icon("folder"),
            )
            self._kinds[deps] = ("folder", -1)
            self.tree.insert(deps, "end", text=f"  {sample_status}", image=self._icon("wave"))

        if 0 <= previous < len(self.track_items):
            self.tree.selection_set(self.track_items[previous])
        elif self.track_items:
            self.tree.selection_set(self.track_items[0])

    def load_empty(self) -> None:
        """没有工程时显示占位提示。"""
        self._project = None
        self.tree.delete(*self.tree.get_children())
        self._kinds.clear()
        self.track_items = []
        self.tree.insert("", "end", text="  尚未打开工程", image=self._icon("solution"))

    def _icon(self, kind: str) -> tk.PhotoImage:
        if kind not in self._icons:
            self._icons[kind] = icons.image(kind, T["panel"], self.tree)
        return self._icons[kind]

    def select_track(self, index: int) -> None:
        self._selected_index = index
        if 0 <= index < len(self.track_items):
            item = self.track_items[index]
            if self.tree.selection() != (item,):
                self.tree.selection_set(item)
            self.tree.see(item)

    # ---- 事件 ---------------------------------------------------------
    def _current_track(self) -> int:
        selection = self.tree.selection()
        if not selection:
            return -1
        kind, index = self._kinds.get(selection[0], ("", -1))
        return index if kind == "track" else -1

    def _on_select(self, _event=None) -> None:
        index = self._current_track()
        if index >= 0:
            self._selected_index = index
            self.on_select(index)

    def _on_context(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item and item in self._kinds and self._kinds[item][0] == "track":
            self.tree.selection_set(item)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _ctx_toggle(self, attr: str) -> None:
        index = self._current_track()
        if index >= 0 and self.on_toggle:
            self.on_toggle(index, attr)

    def _ctx_apply(self) -> None:
        if self.on_apply:
            self.on_apply()

    def _ctx_new_track(self) -> None:
        if self.on_new_track:
            self.on_new_track()
