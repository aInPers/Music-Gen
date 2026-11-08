"""「新建轨道」对话框：名称 / 乐器 / 初始内容。

只负责收集参数，真正的文件写入交给 :func:`musicgen.core.trackedit.add_track`
（由 ``app._create_track`` 调用），这样命令行与界面走的是同一套逻辑。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ..samples import fetch
from . import theme, widgets
from .properties import instrument_items

T = theme.PALETTE

TEMPLATES = [
    ("blank", "空白轨道", "不写音符，建好后在钢琴卷帘或 XML 里自己填"),
    ("note", "一个示例音符", "放一个 C4 四分音符，方便直接试听"),
    ("loop", "一小节循环音型", "4 拍循环的低音 + 分解和弦，可直接当伴奏"),
]


class NewTrackDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, track_count: int = 0,
                 default_instrument: str = "piano") -> None:
        super().__init__(master)
        self.title("新建轨道")
        self.configure(bg=T["panel"])
        self.resizable(False, False)
        self.transient(master)
        self.result: dict | None = None
        self._track_count = track_count

        self._build(default_instrument)
        self.update_idletasks()
        self._center_on(master)
        self.grab_set()
        self.name_entry.focus_set()
        self.name_entry.selection_range(0, "end")
        self.bind("<Return>", lambda _e: self._create())
        self.bind("<Escape>", lambda _e: self._cancel())

    # ---- 构建 ---------------------------------------------------------
    def _build(self, default_instrument: str) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="新建轨道", bg=T["chrome"], fg=T["text"],
                 font=theme.font(12, bold=True)).pack(side="left", padx=16)
        tk.Label(header, text="在当前工程里加一条音轨", bg=T["chrome"],
                 fg=T["text_faint"], font=theme.font(8)).pack(side="left")
        widgets.Separator(self).pack(fill="x")

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="both", expand=True, padx=18, pady=14)
        body.columnconfigure(1, weight=1)

        self.name_var = tk.StringVar(value=f"Track {self._track_count + 1}")
        self._label(body, "名称", 0)
        self.name_entry = self._entry(body, self.name_var, width=34)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=6)

        self._label(body, "乐器", 1)
        holder = tk.Frame(body, bg=T["panel"], height=23)
        holder.grid(row=1, column=1, sticky="ew", pady=6)
        holder.pack_propagate(False)
        default = fetch.resolve(default_instrument)
        self.instrument = widgets.Dropdown(holder, instrument_items(), default)
        self.instrument.pack(fill="x")

        self._label(body, "初始内容", 2)
        options = tk.Frame(body, bg=T["panel"])
        options.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        self.template_var = tk.StringVar(value="blank")
        for key, title, hint in TEMPLATES:
            row = tk.Frame(options, bg=T["panel"])
            row.pack(fill="x", pady=2)
            tk.Radiobutton(
                row, text=title, value=key, variable=self.template_var,
                bg=T["panel"], fg=T["text"], activebackground=T["panel"],
                activeforeground=T["text"], selectcolor=T["editor"], bd=0,
                highlightthickness=0, font=theme.font(9), anchor="w",
            ).pack(side="left")
            tk.Label(row, text=hint, bg=T["panel"], fg=T["text_faint"],
                     font=theme.font(8)).pack(side="left", padx=(8, 0))

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=54)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        tk.Button(footer, text="创建", command=self._create, bg=T["accent_dim"], fg="#FFFFFF",
                  activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
                  font=theme.font(9), padx=22, pady=6, cursor="hand2").pack(side="right",
                                                                            padx=(6, 16), pady=12)
        tk.Button(footer, text="取消", command=self._cancel, bg=T["chrome_hi"], fg=T["text"],
                  activebackground=T["chrome_lo"], activeforeground=T["text"], relief="flat", bd=0,
                  font=theme.font(9), padx=18, pady=6, cursor="hand2").pack(side="right", pady=12)

    def _label(self, parent: tk.Misc, text: str, row: int) -> None:
        tk.Label(parent, text=text, bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 anchor="w", width=8).grid(row=row, column=0, sticky="nw", pady=9)

    def _entry(self, parent: tk.Misc, variable: tk.StringVar, width: int = 34) -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, width=width, bg=T["editor"], fg=T["text"],
                        insertbackground=T["text"], relief="flat", font=theme.font(9),
                        highlightthickness=1, highlightbackground=T["border"],
                        highlightcolor=T["accent"])

    def _center_on(self, master: tk.Misc) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 3
        except tk.TclError:
            x = y = 200
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ---- 动作 ---------------------------------------------------------
    def _create(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("需要名称", "请填写音轨名称。", parent=self)
            return
        self.result = {
            "name": name,
            "instrument": self.instrument.get(),
            "template": self.template_var.get(),
        }
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
