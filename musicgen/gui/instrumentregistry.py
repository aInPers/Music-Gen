"""「乐器注册表」对话框：查看 / 添加 / 删除 / 恢复默认乐器。

与 CLI 的 ``musicgen instrument ...`` 共用 ``samples/registry.py`` 的同一个 JSON 文件；
每次改动后立即 ``fetch.reload_registry()`` 并通知主窗口刷新属性下拉框与状态栏。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..samples import fetch, registry
from . import theme, widgets

T = theme.PALETTE


class AddInstrumentDialog(tk.Toplevel):
    """新增一件采样乐器：只需给注册键、中文名和 FluidR3 GM 包名。"""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("注册新乐器")
        self.configure(bg=T["panel"])
        self.resizable(False, False)
        self.transient(master)
        self.result: dict | None = None
        self.vars = {
            "key": tk.StringVar(),
            "label": tk.StringVar(),
            "gm": tk.StringVar(),
            "urls": tk.StringVar(),
            "aliases": tk.StringVar(),
            "low": tk.StringVar(value="21"),
            "high": tk.StringVar(value="108"),
            "gain": tk.StringVar(value="10"),
            "range_label": tk.StringVar(),
        }
        self._build()
        self.update_idletasks()
        self._center(master)
        self.grab_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())

    def _build(self) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="注册新乐器", bg=T["chrome"], fg=T["text"],
                 font=theme.font(11, bold=True)).pack(side="left", padx=16)
        tk.Label(header, text="采样来自 FluidR3 GM，也可填自定义下载地址",
                 bg=T["chrome"], fg=T["text_faint"], font=theme.font(8)).pack(side="left")
        widgets.Separator(self).pack(fill="x")

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=12)
        body.columnconfigure(1, weight=1)
        rows = [
            ("注册键", "key"),
            ("中文名", "label"),
            ("GM 包名", "gm"),
            ("自定义地址", "urls"),
            ("别名", "aliases"),
            ("最低音 MIDI", "low"),
            ("最高音 MIDI", "high"),
            ("增益", "gain"),
            ("音域说明", "range_label"),
        ]
        for row, (label, key) in enumerate(rows):
            tk.Label(body, text=label, bg=T["panel"], fg=T["text_dim"],
                     font=theme.font(9), width=12, anchor="w").grid(
                         row=row, column=0, sticky="w", pady=3)
            tk.Entry(body, textvariable=self.vars[key], width=42, bg=T["editor"],
                     fg=T["text"], insertbackground=T["text"], relief="flat",
                     font=theme.font(9), highlightthickness=1,
                     highlightbackground=T["border"], highlightcolor=T["accent"]).grid(
                         row=row, column=1, sticky="ew", pady=3)
        tk.Label(body,
                 text="GM 包名与自定义地址二选一（地址需含 {name} / {ext} 占位符）；"
                      "别名与中文名都可写在工程里。",
                 bg=T["panel"], fg=T["text_faint"], font=theme.font(8),
                 anchor="w", wraplength=430, justify="left").grid(
                     row=len(rows), column=0, columnspan=2, sticky="w", pady=(10, 0))

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=50)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        tk.Button(footer, text="注册", command=self._ok, bg=T["accent_dim"], fg="#FFFFFF",
                  activebackground=T["accent"], relief="flat", bd=0, font=theme.font(9),
                  padx=20, pady=5, cursor="hand2").pack(side="right", padx=(6, 14), pady=10)
        tk.Button(footer, text="取消", command=self._cancel, bg=T["chrome_hi"], fg=T["text"],
                  relief="flat", bd=0, font=theme.font(9), padx=16, pady=5,
                  cursor="hand2").pack(side="right", pady=10)

    def _center(self, master: tk.Misc) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - w) // 2
            y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        except tk.TclError:
            x = y = 200
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _ok(self) -> None:
        values = {key: var.get().strip() for key, var in self.vars.items()}
        try:
            low = int(values["low"])
            high = int(values["high"])
            gain = float(values["gain"])
        except ValueError:
            messagebox.showwarning("参数不对", "音域 / 增益必须是数字。", parent=self)
            return
        spec = {
            "key": values["key"], "label": values["label"], "kind": "sample",
            "gm": values["gm"], "urls": [u.strip() for u in values["urls"].split(",") if u.strip()],
            "aliases": [a.strip() for a in values["aliases"].split(",") if a.strip()],
            "midi_low": low, "midi_high": high, "gain": gain,
            "range_label": values["range_label"],
            "title": (f"FluidR3 GM — {values['gm'].replace('_', ' ').title()}"
                      if values["gm"] else ""),
        }
        try:
            entry = registry.add(registry.load(), spec)
        except registry.RegistryError as exc:
            messagebox.showerror("无法注册", str(exc), parent=self)
            return
        self.result = entry
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()


class InstrumentRegistryDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, on_changed=None) -> None:
        super().__init__(master)
        self.title("乐器注册表")
        self.configure(bg=T["panel"])
        self.geometry("720x440")
        self.minsize(600, 360)
        self.transient(master)
        self.on_changed = on_changed
        self._build()
        self._fill()
        self.update_idletasks()
        theme.enable_dark_titlebar(self)

    def _build(self) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="乐器注册表", bg=T["chrome"], fg=T["text"],
                 font=theme.font(12, bold=True)).pack(side="left", padx=16)
        self.path_label = tk.Label(header, text=str(registry.path()), bg=T["chrome"],
                                   fg=T["text_faint"], font=theme.font(8))
        self.path_label.pack(side="left", padx=(10, 0))
        widgets.Separator(self).pack(fill="x")

        table = tk.Frame(self, bg=T["panel"])
        table.pack(fill="both", expand=True, padx=14, pady=10)
        columns = ("key", "label", "kind", "range", "samples", "gain")
        self.tree = ttk.Treeview(table, style="VS.Treeview", columns=columns,
                                 show="headings", selectmode="browse")
        for key, label, width in (
            ("key", "注册键", 110), ("label", "名称", 90), ("kind", "类型", 80),
            ("range", "音域", 170), ("samples", "采样", 100), ("gain", "增益", 60),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=(key == "range"))
        bar = ttk.Scrollbar(table, orient="vertical", style="VS.Vertical.TScrollbar",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=54)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self.status = tk.StringVar(value="改动会立即影响属性面板的乐器下拉框")
        tk.Label(footer, textvariable=self.status, bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8), anchor="w").pack(side="top", fill="x", padx=14, pady=(4, 0))
        box = tk.Frame(footer, bg=T["panel"])
        box.pack(fill="x", padx=12, pady=(4, 6))
        self._button(box, "添加乐器…", self._add).pack(side="left", padx=2)
        self._button(box, "删除选中", self._remove).pack(side="left", padx=2)
        self._button(box, "恢复默认", self._reset).pack(side="left", padx=2)
        self._button(box, "关闭", self.destroy).pack(side="right", padx=2)

    def _button(self, parent: tk.Misc, text: str, command) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=T["chrome_hi"],
                         fg=T["text"], activebackground=T["accent"],
                         activeforeground="#FFFFFF", relief="flat", bd=0,
                         font=theme.font(9), padx=12, pady=4, cursor="hand2")

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        entries = registry.load()
        for entry in entries:
            key = entry["key"]
            if entry.get("kind") == "builtin":
                kind, rng, samples, gain = "内置", entry.get("description", ""), "无需下载", "—"
            else:
                have, want = fetch.instrument_status(key)
                state = "就绪" if want and have >= want else ("部分" if have else "未下载")
                kind = "采样"
                rng = entry.get("range_label") or f"MIDI {entry.get('midi_low', 21)}–{entry.get('midi_high', 108)}"
                samples = f"{have}/{want} [{state}]"
                gain = f"{float(entry.get('gain', 10.0)):g}"
            self.tree.insert("", "end", iid=key,
                             values=(key, entry["label"], kind, rng, samples, gain))
        self.path_label.configure(text=str(registry.path()))
        self.status.set(f"共 {len(entries)} 件乐器 · 改动会立即刷新主窗口的下拉框")

    def _changed(self, message: str) -> None:
        fetch.reload_registry()
        self._fill()
        self.status.set(message)
        if self.on_changed:
            self.on_changed()

    def _add(self) -> None:
        dialog = AddInstrumentDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self._changed(f"已注册 {dialog.result['label']} ({dialog.result['key']})，"
                          "可用 samples fetch 下载采样")

    def _remove(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("未选中", "请先选中一件乐器。", parent=self)
            return
        key = selection[0]
        entry = registry.find(registry.load(), key)
        name = entry["label"] if entry else key
        if not messagebox.askyesno(
            "删除乐器",
            f"从注册表删除「{name} ({key})」？\n\n"
            "采样缓存会保留；工程里仍写这个名字时会回退到内置合成器。\n"
            "可用「恢复默认」找回内置乐器。",
            parent=self,
        ):
            return
        try:
            registry.remove(registry.load(), key)
        except registry.RegistryError as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self._changed(f"已删除 {name} ({key})")

    def _reset(self) -> None:
        if not messagebox.askyesno("恢复默认",
                                   "用内置默认乐器列表覆盖当前注册表？自定义条目会丢失。",
                                   parent=self):
            return
        registry.reset()
        self._changed("已恢复内置默认乐器列表")
