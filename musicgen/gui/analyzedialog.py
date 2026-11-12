"""「从音频重建工程」对话框。

收集音频文件、输出目录、速度/量化、分离模型与音频轨选项，交给
:func:`musicgen.analysis.analyze` 在后台跑完整条管线。
人声固定是一条音频轨，不需要选音色。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from ..analysis.audio import AUDIO_SUFFIXES
from ..analysis.separate import DEFAULT_MODEL
from . import theme, widgets

T = theme.PALETTE

MODELS = [
    ("htdemucs_6s", "6 轨（推荐）", "人声 / 鼓 / 贝斯 / 钢琴 / 吉他 / 其他"),
    ("htdemucs", "4 轨（快）", "人声 / 鼓 / 贝斯 / 其他，钢琴吉他会混在「其他」"),
    ("htdemucs_ft", "4 轨（精细）", "微调模型，质量更好，速度慢约 4 倍"),
]

QUANTIZE = [
    ("1/16", "1/16 音符（推荐）"),
    ("1/8", "1/8 音符"),
    ("1/4", "1/4 音符"),
    ("1/32", "1/32 音符"),
    ("off", "不量化（保留原始时值）"),
]



class AnalyzeDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, default_dir: Path | None = None) -> None:
        super().__init__(master)
        self.title("从音频重建工程")
        self.configure(bg=T["panel"])
        self.resizable(False, False)
        self.transient(master)
        self.result: dict | None = None
        self._default_dir = Path(default_dir or Path.cwd())
        self._auto_output = True

        self._build()
        self.update_idletasks()
        self._center_on(master)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self._cancel())

    # ---- 构建 ---------------------------------------------------------
    def _build(self) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="从音频重建工程", bg=T["chrome"], fg=T["text"],
                 font=theme.font(12, bold=True)).pack(side="left", padx=16)
        tk.Label(header, text="Demucs 分轨 → FFT 转谱 → 音色识别 → musicproj",
                 bg=T["chrome"], fg=T["text_faint"], font=theme.font(8)).pack(side="left")
        widgets.Separator(self).pack(fill="x")

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="both", expand=True, padx=18, pady=12)
        body.columnconfigure(1, weight=1)

        row = 0
        self.audio_var = tk.StringVar(value="")
        self._label(body, "音频文件", row)
        audio_box = tk.Frame(body, bg=T["panel"])
        audio_box.grid(row=row, column=1, sticky="ew", pady=4)
        audio_box.columnconfigure(0, weight=1)
        self.audio_entry = self._entry(audio_box, self.audio_var)
        self.audio_entry.grid(row=0, column=0, sticky="ew")
        self._button(audio_box, "浏览…", self._browse_audio).grid(row=0, column=1, padx=(6, 0))
        row += 1

        self.dir_var = tk.StringVar(value="")
        self._label(body, "输出目录", row)
        dir_box = tk.Frame(body, bg=T["panel"])
        dir_box.grid(row=row, column=1, sticky="ew", pady=4)
        dir_box.columnconfigure(0, weight=1)
        self.dir_entry = self._entry(dir_box, self.dir_var)
        self.dir_entry.grid(row=0, column=0, sticky="ew")
        self._button(dir_box, "浏览…", self._browse_dir).grid(row=0, column=1, padx=(6, 0))
        row += 1

        self.name_var = tk.StringVar(value="")
        self._label(body, "工程名", row)
        self._entry(body, self.name_var).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1

        params = tk.Frame(body, bg=T["panel"])
        params.grid(row=row, column=1, sticky="ew", pady=4)
        for index in range(3):
            params.columnconfigure(index, weight=1)

        self.bpm_var = tk.StringVar(value="")
        bpm_box = tk.Frame(params, bg=T["panel"])
        bpm_box.grid(row=0, column=0, sticky="w")
        tk.Label(bpm_box, text="速度（空=自动）", bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8)).pack(anchor="w")
        self._entry(bpm_box, self.bpm_var, width=10).pack(anchor="w", pady=(2, 0))

        self.quant_var = tk.StringVar(value="1/16")
        quant_box = tk.Frame(params, bg=T["panel"])
        quant_box.grid(row=0, column=1, sticky="w", padx=(12, 0))
        tk.Label(quant_box, text="量化网格", bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8)).pack(anchor="w")
        self._combo(quant_box, self.quant_var, [(k, v) for k, v in QUANTIZE])

        vocal_box = tk.Frame(params, bg=T["panel"])
        vocal_box.grid(row=0, column=2, sticky="w", padx=(12, 0))
        tk.Label(vocal_box, text="人声", bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8)).pack(anchor="w")
        tk.Label(vocal_box, text="原样音频轨 · 不转谱", bg=T["panel"], fg=T["ok"],
                 font=theme.font(9)).pack(anchor="w", pady=(2, 0))
        row += 1

        self._label(body, "分离模型", row)
        model_box = tk.Frame(body, bg=T["panel"])
        model_box.grid(row=row, column=1, sticky="ew", pady=(4, 0))
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        for key, title, hint in MODELS:
            option = tk.Frame(model_box, bg=T["panel"])
            option.pack(fill="x", pady=1)
            tk.Radiobutton(
                option, text=title, value=key, variable=self.model_var,
                bg=T["panel"], fg=T["text"], activebackground=T["panel"],
                activeforeground=T["text"], selectcolor=T["editor"], bd=0,
                highlightthickness=0, font=theme.font(9), anchor="w",
            ).pack(side="left")
            tk.Label(option, text=hint, bg=T["panel"], fg=T["text_faint"],
                     font=theme.font(8)).pack(side="left", padx=(8, 0))
        row += 1

        options = tk.Frame(body, bg=T["panel"])
        options.grid(row=row, column=1, sticky="w", pady=(6, 0))
        self.stems_var = tk.BooleanVar(value=True)
        self.force_var = tk.BooleanVar(value=False)
        self.audio_tracks_var = tk.BooleanVar(value=False)
        for text, variable in (("音频轨（保留原音色，不转谱）", self.audio_tracks_var),
                               ("保存分轨 WAV", self.stems_var),
                               ("覆盖已有工程", self.force_var)):
            tk.Checkbutton(
                options, text=text, variable=variable, bg=T["panel"], fg=T["text_dim"],
                activebackground=T["panel"], activeforeground=T["text"],
                selectcolor=T["editor"], bd=0, highlightthickness=0,
                font=theme.font(9),
            ).pack(side="left", padx=(0, 14))

        hint = tk.Label(
            body,
            text="首次运行会下载 Demucs 权重（约 300MB）；国内网络可设置 "
                 "HF_ENDPOINT=https://hf-mirror.com。分析在后台进行，可在输出窗口看进度。",
            bg=T["panel"], fg=T["text_faint"], font=theme.font(8), justify="left",
            wraplength=520,
        )
        hint.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=54)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self._button(footer, "开始分析", self._start, accent=True).pack(
            side="right", padx=(6, 16), pady=12)
        self._button(footer, "取消", self._cancel, width=8).pack(side="right", pady=12)

    def _label(self, parent: tk.Misc, text: str, row: int) -> None:
        tk.Label(parent, text=text, bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 anchor="w", width=8).grid(row=row, column=0, sticky="nw", pady=8)

    def _entry(self, parent: tk.Misc, variable: tk.StringVar, width: int = 40) -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, width=width, bg=T["editor"], fg=T["text"],
                        insertbackground=T["text"], relief="flat", font=theme.font(9),
                        highlightthickness=1, highlightbackground=T["border"],
                        highlightcolor=T["accent"])

    def _button(self, parent: tk.Misc, text: str, command, accent: bool = False,
                width: int = 0) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command,
            bg=T["accent_dim"] if accent else T["chrome_hi"],
            fg="#FFFFFF" if accent else T["text"],
            activebackground=T["accent"] if accent else T["chrome_lo"],
            activeforeground="#FFFFFF" if accent else T["text"],
            relief="flat", bd=0, font=theme.font(9), padx=16, pady=5, cursor="hand2",
        )
        if width:
            button.configure(width=width)
        return button

    def _combo(self, parent: tk.Misc, variable: tk.StringVar,
               items: list[tuple[str, str]]) -> None:
        holder = tk.Frame(parent, bg=T["editor"], highlightthickness=1,
                          highlightbackground=T["border"])
        holder.pack(anchor="w", pady=(2, 0))
        label = tk.Label(holder, text=dict(items).get(variable.get(), variable.get()),
                         bg=T["editor"], fg=T["text"], font=theme.font(9), padx=8, pady=3,
                         anchor="w", width=14)
        label.pack(side="left")

        def choose(key: str, title: str) -> None:
            variable.set(key)
            label.configure(text=title)

        menu = theme.make_menu(holder)
        for key, title in items:
            menu.add_command(label=title, command=lambda k=key, t=title: choose(k, t))
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
    def _browse_audio(self) -> None:
        patterns = tuple(f"*{suffix}" for suffix in AUDIO_SUFFIXES)
        chosen = filedialog.askopenfilename(
            parent=self, title="选择音频文件",
            filetypes=[("音频", patterns), ("所有文件", "*.*")],
            initialdir=self._default_dir.as_posix(),
        )
        if not chosen:
            return
        path = Path(chosen)
        self.audio_var.set(str(path))
        if not self.name_var.get().strip():
            self.name_var.set(path.stem)
        if self._auto_output or not self.dir_var.get().strip():
            self.dir_var.set(str(path.parent / f"{path.stem}-musicproj"))

    def _browse_dir(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="选择输出目录",
                                         initialdir=self.dir_var.get() or str(self._default_dir))
        if chosen:
            self.dir_var.set(chosen)
            self._auto_output = False

    def _start(self) -> None:
        audio = self.audio_var.get().strip()
        if not audio:
            messagebox.showwarning("需要音频", "请选择一个音频文件。", parent=self)
            return
        source = Path(audio)
        if not source.is_file():
            messagebox.showwarning("文件不存在", f"找不到：{source}", parent=self)
            return
        if source.suffix.lower() not in AUDIO_SUFFIXES:
            messagebox.showwarning(
                "格式不支持", f"可用格式：{'、'.join(AUDIO_SUFFIXES)}", parent=self
            )
            return
        directory = self.dir_var.get().strip()
        if not directory:
            messagebox.showwarning("需要目录", "请选择工程输出目录。", parent=self)
            return

        bpm_text = self.bpm_var.get().strip()
        bpm = None
        if bpm_text:
            try:
                bpm = float(bpm_text)
            except ValueError:
                messagebox.showwarning("速度不对", "速度要么留空自动检测，要么填 5–600 的数字。",
                                       parent=self)
                return
            if not 5 <= bpm <= 600:
                messagebox.showwarning("速度不对", "速度需在 5–600BPM 之间。", parent=self)
                return

        target = Path(directory)
        project_file = target / f"{target.name or 'analysis'}.musicproj"
        force = bool(self.force_var.get())
        if project_file.exists() and not force:
            if not messagebox.askyesno(
                "已存在", f"{project_file} 已存在，要覆盖吗？\n（分轨缓存会复用，节省时间）",
                parent=self,
            ):
                return
            force = True

        self.result = {
            "source": source,
            "output": target,
            "name": self.name_var.get().strip() or source.stem,
            "bpm": bpm,
            "quantize": self.quant_var.get(),
            "model": self.model_var.get(),
            "keep_stems": bool(self.stems_var.get()),
            "audio_tracks": bool(self.audio_tracks_var.get()),
            "force": force,
        }
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
