"""「渲染轨道」对话框：勾选要渲染的音轨。

默认全选；当前选中的音轨会被预勾选并高亮。只选一条时也能当「单轨导出」用。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme, widgets

T = theme.PALETTE


class RenderDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, project, selected: int = -1) -> None:
        super().__init__(master)
        self.title("渲染轨道")
        self.configure(bg=T["panel"])
        self.resizable(False, True)
        self.transient(master)
        self.project = project
        self.result: list[int] | None = None
        self._vars: list[tk.BooleanVar] = []

        self._build(selected)
        self.update_idletasks()
        self._center_on(master)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self._cancel())

    # ---- 构建 ---------------------------------------------------------
    def _build(self, selected: int) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=46)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="渲染轨道", bg=T["chrome"], fg=T["text"],
                 font=theme.font(12, bold=True)).pack(side="left", padx=16)
        tk.Label(header, text="不勾的轨不参与混音，也不会写进波形预览",
                 bg=T["chrome"], fg=T["text_faint"], font=theme.font(8)).pack(
            side="left", padx=(4, 0))
        widgets.Separator(self).pack(fill="x")

        toolbar = tk.Frame(self, bg=T["panel"])
        toolbar.pack(fill="x", padx=14, pady=(8, 4))
        self._small_button(toolbar, "全选", lambda: self._set_all(True)).pack(side="left")
        self._small_button(toolbar, "全不选", lambda: self._set_all(False)).pack(
            side="left", padx=(6, 0))
        self._small_button(toolbar, "只选当前", self._only_current).pack(
            side="left", padx=(6, 0))

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        canvas = tk.Canvas(body, bg=T["panel"], highlightthickness=0, bd=0,
                           width=460, height=min(360, 34 * max(3, len(self.project.tracks))))
        bar = ttk.Scrollbar(body, orient="vertical", style="VS.Vertical.TScrollbar",
                            command=canvas.yview)
        inner = tk.Frame(canvas, bg=T["panel"])
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(
            window, width=event.width))

        for index, track in enumerate(self.project.tracks):
            var = tk.BooleanVar(value=True)
            self._vars.append(var)
            row = tk.Frame(inner, bg=T["panel"])
            row.pack(fill="x", pady=1)
            check = tk.Checkbutton(
                row, variable=var, bg=T["panel"], activebackground=T["panel"],
                selectcolor=T["editor"], bd=0, highlightthickness=0,
                text=f"{index + 1}. {track.name}", fg=T["text"],
                font=theme.font(9, bold=index == selected), anchor="w",
            )
            check.pack(side="left")
            detail = f"{track.instrument} · {len(track.notes)} 音 · {track.gain_db:+.1f}dB"
            if track.mute:
                detail += " · 静音"
            tk.Label(row, text=detail, bg=T["panel"], fg=T["text_faint"],
                     font=theme.font(8)).pack(side="left", padx=(10, 0))
        self._only_index = selected

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=50)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self._button(footer, "开始渲染", self._accept, accent=True).pack(
            side="right", padx=(6, 16), pady=9)
        self._button(footer, "取消", self._cancel, width=8).pack(side="right", pady=9)
        self.bind("<Return>", lambda _e: self._accept())

    def _small_button(self, parent, text, command) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=T["chrome_hi"],
                         fg=T["text"], activebackground=T["chrome_lo"],
                         activeforeground=T["text"], relief="flat", bd=0,
                         font=theme.font(8), padx=10, pady=2, cursor="hand2")

    def _button(self, parent, text, command, accent: bool = False,
                width: int = 0) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command,
            bg=T["accent_dim"] if accent else T["chrome_hi"],
            fg="#FFFFFF" if accent else T["text"],
            activebackground=T["accent"] if accent else T["chrome_lo"],
            activeforeground="#FFFFFF",
            relief="flat", bd=0, font=theme.font(9), padx=16, pady=5, cursor="hand2",
        )
        if width:
            button.configure(width=width)
        return button

    def _center_on(self, master: tk.Misc) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 3
        except tk.TclError:
            x = y = 200
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ---- 交互 ---------------------------------------------------------
    def _set_all(self, value: bool) -> None:
        for var in self._vars:
            var.set(value)

    def _only_current(self) -> None:
        if 0 <= self._only_index < len(self._vars):
            self._set_all(False)
            self._vars[self._only_index].set(True)

    def _accept(self) -> None:
        chosen = [index for index, var in enumerate(self._vars) if var.get()]
        if not chosen:
            from tkinter import messagebox

            messagebox.showwarning("没有选中轨道", "至少勾一条音轨再渲染。", parent=self)
            return
        self.result = chosen
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
