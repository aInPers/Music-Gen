"""「新建工程」对话框（VS 风格模态窗）。

收集名称 / 位置 / 速度 / 采样率 / 拍号 / 模板，校验后调用
:func:`musicgen.core.project.create_project` 生成工程文件。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from ..core.project import VALID_SAMPLE_RATES, create_project
from . import theme, widgets

T = theme.PALETTE

TEMPLATES = [
    ("demo", "示例工程", "两条音轨：旋律 + 4 拍循环音型，可直接渲染试听"),
    ("empty", "空白工程", "一条空音轨，注释里写了音符语法，自己动手写"),
]


class NewProjectDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, default_dir: Path | None = None) -> None:
        super().__init__(master)
        self.title("新建工程")
        self.configure(bg=T["panel"])
        self.resizable(False, False)
        self.transient(master)
        self.result: dict | None = None
        self._default_dir = Path(default_dir or Path.cwd())

        self._build()
        self.update_idletasks()
        self._center_on(master)
        self.grab_set()
        self.name_entry.focus_set()
        self.bind("<Return>", lambda _e: self._create())
        self.bind("<Escape>", lambda _e: self._cancel())

    # ---- 构建 ---------------------------------------------------------
    def _build(self) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="新建工程", bg=T["chrome"], fg=T["text"],
                 font=theme.font(12, bold=True)).pack(side="left", padx=16)
        tk.Label(header, text="创建一个 musicproj 工程与音轨文件", bg=T["chrome"],
                 fg=T["text_faint"], font=theme.font(8)).pack(side="left")
        widgets.Separator(self).pack(fill="x")

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="both", expand=True, padx=18, pady=14)
        body.columnconfigure(1, weight=1)

        row = 0
        self.name_var = tk.StringVar(value="新工程")
        self._label(body, "名称", row)
        self.name_entry = self._entry(body, self.name_var)
        self.name_entry.grid(row=row, column=1, sticky="ew", pady=5)
        row += 1

        self.dir_var = tk.StringVar(value=str(self._default_dir))
        self._label(body, "位置", row)
        dir_box = tk.Frame(body, bg=T["panel"])
        dir_box.grid(row=row, column=1, sticky="ew", pady=5)
        dir_box.columnconfigure(0, weight=1)
        self.dir_entry = self._entry(dir_box, self.dir_var)
        self.dir_entry.grid(row=0, column=0, sticky="ew")
        tk.Button(dir_box, text="浏览…", command=self._browse, bg=T["chrome_hi"], fg=T["text"],
                  activebackground=T["chrome_lo"], activeforeground=T["text"], relief="flat",
                  bd=0, font=theme.font(9), padx=10, cursor="hand2").grid(row=0, column=1,
                                                                          padx=(6, 0))
        row += 1

        params = tk.Frame(body, bg=T["panel"])
        params.grid(row=row, column=1, sticky="ew", pady=5)
        for index in range(3):
            params.columnconfigure(index, weight=1)

        self.bpm_var = tk.StringVar(value="100")
        bpm_box = tk.Frame(params, bg=T["panel"])
        bpm_box.grid(row=0, column=0, sticky="w")
        tk.Label(bpm_box, text="速度", bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8)).pack(anchor="w")
        self._entry(bpm_box, self.bpm_var, width=8).pack(anchor="w", pady=(2, 0))

        self.rate_var = tk.StringVar(value="44100")
        rate_box = tk.Frame(params, bg=T["panel"])
        rate_box.grid(row=0, column=1, sticky="w", padx=(12, 0))
        tk.Label(rate_box, text="采样率", bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8)).pack(anchor="w")
        self._combo(rate_box, self.rate_var, [str(v) for v in VALID_SAMPLE_RATES[:4]])

        self.meter_var = tk.StringVar(value="4")
        meter_box = tk.Frame(params, bg=T["panel"])
        meter_box.grid(row=0, column=2, sticky="w", padx=(12, 0))
        tk.Label(meter_box, text="拍号", bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8)).pack(anchor="w")
        self._combo(meter_box, self.meter_var, ["2", "3", "4", "6"], suffix="/4")
        row += 1

        self._label(body, "模板", row)
        template_box = tk.Frame(body, bg=T["panel"])
        template_box.grid(row=row, column=1, sticky="ew", pady=(5, 0))
        self.template_var = tk.StringVar(value="demo")
        for key, title, hint in TEMPLATES:
            option = tk.Frame(template_box, bg=T["panel"])
            option.pack(fill="x", pady=2)
            tk.Radiobutton(
                option, text=title, value=key, variable=self.template_var,
                bg=T["panel"], fg=T["text"], activebackground=T["panel"],
                activeforeground=T["text"], selectcolor=T["editor"], bd=0,
                highlightthickness=0, font=theme.font(9), anchor="w",
            ).pack(side="left")
            tk.Label(option, text=hint, bg=T["panel"], fg=T["text_faint"],
                     font=theme.font(8)).pack(side="left", padx=(8, 0))
        row += 1

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=54)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        tk.Button(footer, text="创建", command=self._create, bg=T["accent_dim"], fg="#FFFFFF",
                  activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
                  font=theme.font(9), padx=22, pady=6, cursor="hand2").pack(side="right", padx=(6, 16),
                                                                            pady=12)
        tk.Button(footer, text="取消", command=self._cancel, bg=T["chrome_hi"], fg=T["text"],
                  activebackground=T["chrome_lo"], activeforeground=T["text"], relief="flat", bd=0,
                  font=theme.font(9), padx=18, pady=6, cursor="hand2").pack(side="right", pady=12)

    def _label(self, parent: tk.Misc, text: str, row: int) -> None:
        tk.Label(parent, text=text, bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 anchor="w", width=8).grid(row=row, column=0, sticky="nw", pady=8)

    def _entry(self, parent: tk.Misc, variable: tk.StringVar, width: int = 40) -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, width=width, bg=T["editor"], fg=T["text"],
                        insertbackground=T["text"], relief="flat", font=theme.font(9),
                        highlightthickness=1, highlightbackground=T["border"],
                        highlightcolor=T["accent"])

    def _combo(self, parent: tk.Misc, variable: tk.StringVar, values: list[str],
               suffix: str = "") -> None:
        holder = tk.Frame(parent, bg=T["editor"], highlightthickness=1,
                          highlightbackground=T["border"])
        holder.pack(anchor="w", pady=(2, 0))
        label = tk.Label(holder, text=f"{variable.get()}{suffix}", bg=T["editor"], fg=T["text"],
                         font=theme.font(9), padx=8, pady=3, anchor="w", width=6)
        label.pack(side="left")

        def choose(value: str) -> None:
            variable.set(value)
            label.configure(text=f"{value}{suffix}")

        menu = theme.make_menu(holder)
        for value in values:
            menu.add_command(label=f"{value}{suffix}", command=lambda v=value: choose(v))
        arrow = tk.Label(holder, text="▾", bg=T["editor"], fg=T["text_dim"],
                         font=theme.font(8), padx=6)
        arrow.pack(side="right")
        for widget in (label, arrow):
            widget.bind("<Button-1>", lambda _e: menu.tk_popup(
                holder.winfo_rootx(), holder.winfo_rooty() + holder.winfo_height()))

    def _center_on(self, master: tk.Misc) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 3
        except tk.TclError:
            x = y = 200
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ---- 动作 ---------------------------------------------------------
    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="选择工程目录",
                                         initialdir=self.dir_var.get() or str(Path.cwd()))
        if chosen:
            self.dir_var.set(chosen)
            if self.name_var.get() in ("新工程", ""):
                self.name_var.set(Path(chosen).name)

    def _create(self) -> None:
        name = self.name_var.get().strip()
        directory = self.dir_var.get().strip()
        if not name:
            messagebox.showwarning("需要名称", "请填写工程名称。", parent=self)
            return
        if not directory:
            messagebox.showwarning("需要位置", "请选择工程所在目录。", parent=self)
            return
        try:
            bpm = float(self.bpm_var.get())
            sample_rate = int(self.rate_var.get())
            beats_per_bar = int(self.meter_var.get())
        except ValueError:
            messagebox.showwarning("参数不对", "速度/采样率/拍号必须是数字。", parent=self)
            return
        target = Path(directory) / name
        try:
            project_file = create_project(
                target, name, force=False, bpm=bpm, sample_rate=sample_rate,
                beats_per_bar=beats_per_bar, template=self.template_var.get(),
            )
        except FileExistsError:
            if not messagebox.askyesno("已存在", f"{target} 下已有同名工程，要覆盖吗？", parent=self):
                return
            try:
                project_file = create_project(
                    target, name, force=True, bpm=bpm, sample_rate=sample_rate,
                    beats_per_bar=beats_per_bar, template=self.template_var.get(),
                )
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("创建失败", str(exc), parent=self)
                return
        except (ValueError, OSError) as exc:
            messagebox.showerror("创建失败", str(exc), parent=self)
            return
        self.result = {
            "path": project_file,
            "name": name,
            "bpm": bpm,
            "sample_rate": sample_rate,
            "beats_per_bar": beats_per_bar,
            "template": self.template_var.get(),
        }
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
