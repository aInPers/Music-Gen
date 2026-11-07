"""MusicGen 图形界面（Tkinter）。

单窗口三段式布局：
左侧音轨列表（可静音/独奏）、中间钢琴卷帘预览、右侧参数编辑；
底部是渲染进度与波形预览。渲染在后台线程执行，界面不卡。
"""

from __future__ import annotations

import math
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox

import numpy as np

from ..core.project import Project, ProjectError, load_project, save_track
from ..core.notes import midi_to_name
from ..core.render import RenderResult, render_project
from ..core.wavio import write_wav
from ..samples import fetch

# ---------------------------------------------------------------- 配色 / 字体

BG = "#14161a"
PANEL = "#1a1d23"
PANEL_HI = "#22262e"
FIELD = "#0f1116"
BORDER = "#2b303a"
FG = "#e7eaef"
MUTED = "#8b93a3"
ACCENT = "#4da3ff"
ACCENT_DIM = "#2b6ba8"
OK = "#5ed6a4"
WARN = "#ffb454"
ERR = "#ff6b6b"
GRID = "#232830"
GRID_STRONG = "#333a46"

TRACK_COLORS = ["#5ab0ff", "#7ee787", "#ffb86b", "#d2a8ff", "#ff8b8b", "#6fd6cf", "#c9d96b"]

_FONT_FAMILY = "Microsoft YaHei UI" if sys.platform == "win32" else "Helvetica"


def _mix(color: str, other: str, alpha: float) -> str:
    """把 color 按 alpha 混到 other 上（用于弱化非选中音轨）。"""
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(round(x * alpha + y * (1 - alpha))):02x}" for x, y in zip(a, b))


def _fmt_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


# ---------------------------------------------------------------- 钢琴卷帘


class PianoRoll(tk.Canvas):
    """自绘钢琴卷帘：横轴节拍，纵轴音高。"""

    RULER_H = 20
    GUTTER = 34  # 左侧音名栏宽度

    def __init__(self, master: tk.Misc, app: "MusicGenApp", **kw) -> None:
        super().__init__(master, bg=FIELD, highlightthickness=0, **kw)
        self.app = app
        self.zoom = 1.0
        self.scroll_beats = 0.0
        self._pending: str | None = None
        self.bind("<Configure>", lambda _e: self.schedule_redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Control-MouseWheel>", self._on_zoom)
        self.bind("<Shift-MouseWheel>", self._on_zoom)

    # ---- 重绘调度（合并多次 Configure，避免抖动） -----------------------
    def schedule_redraw(self) -> None:
        if self._pending is None:
            self._pending = self.after(16, self._flush_redraw)

    def _flush_redraw(self) -> None:
        self._pending = None
        self.redraw()

    # ---- 交互 ---------------------------------------------------------
    def _on_click(self, event: tk.Event) -> None:
        for item in self.find_overlapping(event.x - 1, event.y - 1, event.x + 1, event.y + 1):
            for tag in self.gettags(item):
                if tag.startswith("note:"):
                    self.app.select_track(int(tag.split(":")[1]))
                    return
        if event.y < self.RULER_H:  # 点标尺：复位缩放
            self.zoom = 1.0
            self.scroll_beats = 0.0
            self.schedule_redraw()

    def _on_wheel(self, event: tk.Event) -> None:
        self.scroll_beats = max(0.0, self.scroll_beats - event.delta / 120.0 * 0.5)
        self._clamp_scroll()
        self.schedule_redraw()

    def _on_zoom(self, event: tk.Event) -> None:
        factor = 1.2 if event.delta > 0 else 1 / 1.2
        self.zoom = min(16.0, max(1.0, self.zoom * factor))
        if self.zoom <= 1.001:
            self.zoom = 1.0
            self.scroll_beats = 0.0
        self._clamp_scroll()
        self.schedule_redraw()

    def _clamp_scroll(self) -> None:
        total = max(1.0, self.app.total_beats())
        visible = total / self.zoom
        self.scroll_beats = max(0.0, min(self.scroll_beats, max(0.0, total - visible)))

    # ---- 绘制 ---------------------------------------------------------
    def redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        project = self.app.project
        if width < 50 or height < 50:
            return
        if project is None:
            self.create_text(width / 2, height / 2, text="打开一个 .musicproj 工程开始",
                             fill=MUTED, font=(_FONT_FAMILY, 11))
            return

        top = self.RULER_H
        usable_h = max(20.0, height - top - 8)
        total_beats = max(1.0, self.app.total_beats())
        plot_w = max(20.0, width - self.GUTTER - 8)
        ppb = max(0.5, plot_w / total_beats * self.zoom)
        origin_x = self.GUTTER - self.scroll_beats * ppb

        pitches = [p for t in project.tracks for n in t.notes for p in n.pitches]
        low = min(pitches) - 2 if pitches else 36
        high = max(pitches) + 2 if pitches else 84
        low = max(0, min(low, high - 6))
        high = min(127, high)
        span = max(6, high - low + 1)
        row_h = usable_h / span

        def y_of(pitch: int) -> float:
            return top + (high - pitch) * row_h + row_h / 2

        def x_of(beat: float) -> float:
            return origin_x + beat * ppb

        # 黑键行
        for pitch in range(low, high + 1):
            if pitch % 12 in (1, 3, 6, 8, 10):
                y = top + (high - pitch) * row_h
                self.create_rectangle(self.GUTTER, y, width, y + row_h, fill="#12151a", outline="")

        # C 线 + 音名栏
        self.create_rectangle(0, 0, self.GUTTER, height, fill=PANEL, outline="")
        for pitch in range(low, high + 1):
            if pitch % 12 == 0:
                y = top + (high - pitch) * row_h
                self.create_line(self.GUTTER, y, width, y, fill=GRID)
                self.create_text(self.GUTTER - 6, y + row_h * 0.5, anchor="e",
                                 text=f"C{pitch // 12 - 1}", fill="#5a6472",
                                 font=(_FONT_FAMILY, 7))

        # 纵向网格
        bpb = max(1, project.beats_per_bar)
        visible_beats = plot_w / ppb
        step = 1.0
        while ppb * step < 26 and step < 64:
            step *= 4 if step == 1 else 2
        first = math.floor(self.scroll_beats / step) * step
        index = 0
        while index < 400:
            beat = first + index * step
            index += 1
            if beat > self.scroll_beats + visible_beats + step:
                break
            if beat < 0:
                continue
            x = x_of(beat)
            if x < self.GUTTER - 1:
                continue
            if abs(beat / bpb - round(beat / bpb)) < 1e-9:
                self.create_line(x, top, x, height, fill=GRID_STRONG)
                self.create_text(x + 4, 3, anchor="nw", text=str(int(round(beat / bpb)) + 1),
                                 fill=MUTED, font=(_FONT_FAMILY, 8))
            else:
                self.create_line(x, top, x, height, fill=GRID)
        self.create_line(0, top, width, top, fill=BORDER)

        # 音符
        selected = self.app.selected
        for ti, track in enumerate(project.tracks):
            base = TRACK_COLORS[ti % len(TRACK_COLORS)]
            active = ti == selected
            color = base if selected < 0 or active else _mix(base, FIELD, 0.3)
            if track.mute:
                color = _mix(color, FIELD, 0.3)
            bar_h = max(2.5, row_h * 0.4)
            show_text = row_h >= 14
            for note in track.notes:
                x1 = x_of(note.time)
                x2 = x_of(note.time + note.dur)
                if x2 < self.GUTTER or x1 > width:
                    continue
                wx = max(2.0, x2 - x1 - 1.0)
                for pitch in note.pitches:
                    if not low <= pitch <= high:
                        continue
                    cy = y_of(pitch)
                    self.create_rectangle(max(self.GUTTER, x1), cy - bar_h,
                                          max(self.GUTTER, x1) + wx, cy + bar_h,
                                          fill=color, outline="", tags=(f"note:{ti}",))
                    if show_text and wx > 20:
                        self.create_text(max(self.GUTTER, x1) + 3, cy, anchor="w",
                                         text=_pitch_name(pitch), fill="#0b0d11",
                                         font=(_FONT_FAMILY, 7))

        # 循环周期标记
        for ti, track in enumerate(project.tracks):
            if not track.loop:
                continue
            period = track.period_beats(project.beats_per_bar)
            x1, x2 = x_of(0), min(x_of(period), width)
            if x2 > self.GUTTER:
                self.create_line(max(self.GUTTER, x1), height - 3, x2, height - 3,
                                 fill=TRACK_COLORS[ti % len(TRACK_COLORS)], width=3)

        if self.zoom > 1.0:
            self.create_text(width - 6, height - 6, anchor="se",
                             text=f"缩放 {self.zoom:.1f}× · 滚轮平移 · Ctrl+滚轮缩放 · 点标尺复位",
                             fill="#5a6472", font=(_FONT_FAMILY, 7))


def _pitch_name(midi: int) -> str:
    return midi_to_name(midi)


# ---------------------------------------------------------------- 波形预览


class WaveformView(tk.Canvas):
    """渲染结果的峰值包络图。"""

    def __init__(self, master: tk.Misc, **kw) -> None:
        super().__init__(master, bg=FIELD, highlightthickness=0, height=84, **kw)
        self._audio: np.ndarray | None = None
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_audio(self, audio: np.ndarray | None) -> None:
        self._audio = audio
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 20 or h < 20:
            return
        if self._audio is None or self._audio.size == 0:
            self.create_text(w / 2, h / 2, text="渲染后在这里显示波形", fill="#4c5563",
                             font=(_FONT_FAMILY, 9))
            return
        mono = self._audio.mean(axis=0)
        cols = max(1, min(int(w), len(mono)))
        step = max(1, len(mono) // cols)
        mid = h / 2
        self.create_line(0, mid, w, mid, fill="#242a33")
        for x in range(cols):
            chunk = mono[x * step : (x + 1) * step]
            if chunk.size == 0:
                break
            lo = float(chunk.min())
            hi = float(chunk.max())
            color = ACCENT if abs(hi) < 0.98 and abs(lo) < 0.98 else WARN
            self.create_line(x, mid - hi * mid * 0.95, x, mid - lo * mid * 0.95, fill=color)


# ---------------------------------------------------------------- 主窗口


class MusicGenApp(tk.Tk):
    def __init__(self, project_path: str | None = None) -> None:
        super().__init__()
        self.title("MusicGen — 音乐渲染器")
        self.configure(bg=BG)
        # 自适应屏幕：小屏不超出可视区域
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = max(1024, min(1320, screen_w - 60))
        win_h = max(640, min(820, screen_h - 120))
        self.geometry(f"{win_w}x{win_h}+{(screen_w - win_w) // 2}+{max(20, (screen_h - win_h) // 2 - 20)}")
        self.minsize(1000, 620)

        self.project: Project | None = None
        self.selected = -1
        self.result: RenderResult | None = None
        self.output_path: Path | None = None
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._vars: dict[str, tk.Variable] = {}

        self._init_fonts()
        self._init_style()
        self._build_ui()
        self._bind_keys()

        self._refresh_sample_status()
        if project_path:
            self.after(60, lambda: self.open_project(project_path))
        self.after(80, self._poll_queue)

    # ---- 初始化 -------------------------------------------------------
    def _init_fonts(self) -> None:
        families = set(tkfont.families(self))
        family = _FONT_FAMILY if _FONT_FAMILY in families else "TkDefaultFont"
        self.font_ui = (family, 9)
        self.font_ui_b = (family, 9, "bold")
        self.font_small = (family, 8)
        self.font_title = (family, 13, "bold")
        self.font_mono = ("Consolas" if "Consolas" in families else family, 9)

    def _init_style(self) -> None:
        from tkinter import ttk

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=self.font_ui,
                        fieldbackground=FIELD, bordercolor=BORDER, focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=self.font_small)
        style.configure("TButton", background=PANEL_HI, foreground=FG, borderwidth=0,
                        focusthickness=0, padding=(12, 6), relief="flat")
        style.map("TButton",
                  background=[("pressed", ACCENT_DIM), ("active", "#2c333d")],
                  foreground=[("disabled", "#5a6472")])
        style.configure("Accent.TButton", background=ACCENT_DIM, foreground="#ffffff")
        style.map("Accent.TButton", background=[("pressed", "#1f5488"), ("active", ACCENT)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=4)
        style.configure("TCheckbutton", background=PANEL, foreground=FG,
                        indicatorcolor=FIELD, focuscolor=PANEL)
        style.map("TCheckbutton", background=[("active", PANEL)],
                  indicatorcolor=[("selected", ACCENT)])
        style.configure("TProgressbar", background=ACCENT, troughcolor=FIELD,
                        bordercolor=FIELD, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("TSeparator", background=BORDER)

    def _build_ui(self) -> None:
        from tkinter import ttk

        # ---- 顶部状态栏（先 pack 底部，主体才能正确吃掉剩余空间） ----
        status = tk.Frame(self, bg="#0f1116", height=26)
        status.pack(side="bottom", fill="x")
        status.pack_propagate(False)
        self.lbl_status = tk.Label(status, text="就绪", bg="#0f1116", fg=MUTED,
                                   font=self.font_small, anchor="w")
        self.lbl_status.pack(side="left", padx=12)
        self.lbl_samples = tk.Label(status, text="", bg="#0f1116", fg=MUTED,
                                    font=self.font_small, anchor="e")
        self.lbl_samples.pack(side="right", padx=12)

        # ---- 底部：波形 + 进度 ----
        bottom = tk.Frame(self, bg=PANEL)
        bottom.pack(side="bottom", fill="x", padx=10, pady=(8, 8))
        info = tk.Frame(bottom, bg=PANEL)
        info.pack(fill="x", padx=10, pady=(6, 0))
        self.lbl_render_info = tk.Label(info, text="尚未渲染", bg=PANEL, fg=MUTED,
                                        font=self.font_small, anchor="w")
        self.lbl_render_info.pack(side="left")
        self.progress = ttk.Progressbar(info, mode="determinate", maximum=100, length=160)
        self.progress.pack(side="right")
        self.wave = WaveformView(bottom)
        self.wave.pack(fill="x", padx=10, pady=(4, 6))

        # ---- 顶栏 ----
        header = tk.Frame(self, bg=PANEL, height=62)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        titles = tk.Frame(header, bg=PANEL)
        titles.pack(side="left", padx=14)
        tk.Label(titles, text="MusicGen", bg=PANEL, fg=FG, font=self.font_title).pack(anchor="w")
        self.lbl_project = tk.Label(titles, text="未打开工程", bg=PANEL, fg=MUTED,
                                    font=self.font_small, anchor="w")
        self.lbl_project.pack(anchor="w")

        actions = tk.Frame(header, bg=PANEL)
        actions.pack(side="right", padx=10)
        self.btn_open = ttk.Button(actions, text="打开工程", command=self.on_open)
        self.btn_open.pack(side="left", padx=4)
        self.btn_render = ttk.Button(actions, text="渲染", style="Accent.TButton",
                                     command=self.on_render, width=8)
        self.btn_render.pack(side="left", padx=4)
        self.btn_play = ttk.Button(actions, text="播放", command=self.on_play,
                                   state="disabled", width=6)
        self.btn_play.pack(side="left", padx=4)
        self.btn_export = ttk.Button(actions, text="导出 WAV", command=self.on_export,
                                     state="disabled")
        self.btn_export.pack(side="left", padx=4)
        self.btn_samples = ttk.Button(actions, text="音源", command=self.on_samples, width=6)
        self.btn_samples.pack(side="left", padx=(12, 4))

        # ---- 主体：左中右三栏用 pack，宽度可预测 ----
        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 0))

        left_panel = tk.Frame(body, bg=PANEL, width=248)
        left_panel.pack(side="left", fill="y")
        left_panel.pack_propagate(False)
        tk.Label(left_panel, text="音轨", bg=PANEL, fg=MUTED, font=self.font_small,
                 anchor="w").pack(fill="x", padx=12, pady=(10, 6))
        self.track_holder = tk.Frame(left_panel, bg=PANEL)
        self.track_holder.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        right_panel = tk.Frame(body, bg=PANEL, width=264)
        right_panel.pack(side="right", fill="y", padx=(8, 0))
        right_panel.pack_propagate(False)
        tk.Label(right_panel, text="音轨参数", bg=PANEL, fg=MUTED, font=self.font_small,
                 anchor="w").pack(fill="x", padx=14, pady=(10, 6))
        self.prop_holder = tk.Frame(right_panel, bg=PANEL)
        self.prop_holder.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self._build_props()

        center = tk.Frame(body, bg=PANEL)
        center.pack(side="left", fill="both", expand=True, padx=(8, 0))
        bar = tk.Frame(center, bg=PANEL)
        bar.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(bar, text="钢琴卷帘", bg=PANEL, fg=MUTED, font=self.font_small).pack(side="left")
        self.lbl_roll_hint = tk.Label(bar, text="", bg=PANEL, fg="#5a6472", font=self.font_small)
        self.lbl_roll_hint.pack(side="right")
        self.roll = PianoRoll(center, app=self, height=200)
        self.roll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_props(self) -> None:
        holder = self.prop_holder
        self.prop_labels: dict[str, tk.Widget] = {}
        self.prop_widgets: dict[str, tk.Widget] = {}
        row = 0
        for key, label in (
            ("name", "名称"),
            ("instrument", "乐器"),
            ("notes", "音符数"),
        ):
            tk.Label(holder, text=label, bg=PANEL, fg=MUTED, font=self.font_small,
                     anchor="w").grid(row=row, column=0, sticky="w", pady=(0, 2))
            value = tk.Label(holder, text="—", bg=PANEL, fg=FG, font=self.font_ui, anchor="w")
            value.grid(row=row + 1, column=0, sticky="ew", pady=(0, 8))
            self.prop_labels[key] = value
            row += 2

        holder.columnconfigure(0, weight=1)

        def add_scale(key: str, label: str, lo: float, hi: float, res: float, unit: str) -> None:
            nonlocal row
            head = tk.Frame(holder, bg=PANEL)
            head.grid(row=row, column=0, sticky="ew", pady=(2, 0))
            tk.Label(head, text=label, bg=PANEL, fg=MUTED, font=self.font_small).pack(side="left")
            readout = tk.Label(head, text="—", bg=PANEL, fg=ACCENT, font=self.font_small)
            readout.pack(side="right")
            var = tk.DoubleVar(value=0.0)
            scale = tk.Scale(
                holder, from_=lo, to=hi, resolution=res, orient="horizontal", variable=var,
                bg=PANEL, fg=FG, troughcolor=FIELD, highlightthickness=0, bd=0,
                sliderrelief="flat", activebackground=ACCENT, showvalue=False,
                sliderlength=14, width=10,
            )
            scale.grid(row=row + 1, column=0, sticky="ew", pady=(0, 6))
            self.prop_widgets[key] = scale
            self._vars[key] = var
            var.trace_add("write", lambda *_a, k=key, u=unit, r=readout: self._update_readout(k, u, r))
            self.prop_labels[f"{key}_readout"] = readout
            row += 2

        add_scale("gain_db", "音量", -24.0, 12.0, 0.5, "dB")
        add_scale("pan", "声像", -1.0, 1.0, 0.05, "")
        add_scale("reverb", "混响", 0.0, 1.0, 0.01, "")

        loop_row = tk.Frame(holder, bg=PANEL)
        loop_row.grid(row=row, column=0, sticky="ew", pady=(2, 4))
        self._vars["loop"] = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(loop_row, text="循环播放", variable=self._vars["loop"],
                             bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
                             selectcolor=FIELD, font=self.font_small, bd=0,
                             highlightthickness=0)
        chk.pack(side="left")
        self.prop_widgets["loop"] = chk
        row += 1

        tk.Label(holder, text="循环长度（拍）", bg=PANEL, fg=MUTED,
                 font=self.font_small, anchor="w").grid(row=row, column=0, sticky="w")
        self._vars["loop_len"] = tk.StringVar(value="")
        entry = tk.Entry(holder, textvariable=self._vars["loop_len"], bg=FIELD, fg=FG,
                         insertbackground=FG, relief="flat", font=self.font_ui,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
        entry.grid(row=row + 1, column=0, sticky="ew", ipady=3, pady=(2, 10))
        self.prop_widgets["loop_len"] = entry
        row += 2

        self.btn_apply = tk.Button(
            holder, text="应用并保存", command=self.on_apply, bg=ACCENT_DIM, fg="#ffffff",
            activebackground=ACCENT, activeforeground="#ffffff", relief="flat", bd=0,
            font=self.font_ui, cursor="hand2", pady=6,
        )
        self.btn_apply.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        self.btn_apply.configure(state="disabled")
        tk.Label(holder, text="修改后写入音轨 XML 文件", bg=PANEL, fg="#5a6472",
                 font=self.font_small).grid(row=row + 1, column=0, sticky="w")

    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda _e: self.on_open())
        self.bind("<Control-r>", lambda _e: self.on_render())
        self.bind("<Control-s>", lambda _e: self.on_export())
        self.bind("<space>", lambda _e: self.on_play())

    # ---- 进度条 / 线程 -------------------------------------------------
    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    fraction, message = payload
                    self.progress["value"] = fraction * 100
                    self.set_status(message)
                elif kind == "render_done":
                    self._on_render_done(payload)
                elif kind == "render_error":
                    self._on_render_error(payload)
                elif kind == "samples_done":
                    self._on_samples_done(payload)
                elif kind == "status":
                    self.set_status(payload)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def set_status(self, text: str, color: str = MUTED) -> None:
        self.lbl_status.configure(text=text, fg=color)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_open, self.btn_render, self.btn_samples):
            btn.configure(state=state)
        if busy:
            self.progress["value"] = 0
            self.set_status(message or "处理中…", ACCENT)
        else:
            self.set_status("就绪")
            self.btn_export.configure(state="normal" if self.result is not None else "disabled")
            self.btn_play.configure(state="normal" if self.result is not None else "disabled")

    # ---- 工程 ---------------------------------------------------------
    def on_open(self) -> None:
        self.open_project(None)

    def open_project(self, path: str | None = None) -> None:
        if self._busy:
            return
        if path is None:
            chosen = filedialog.askopenfilename(
                title="打开工程",
                filetypes=[("MusicGen 工程", "*.musicproj"), ("所有文件", "*.*")],
                initialdir=str(Path.cwd()),
            )
            if not chosen:
                return
            path = chosen
        try:
            project = load_project(path)
        except ProjectError as exc:
            messagebox.showerror("工程有错误", "\n".join(str(i) for i in exc.issues[:12]))
            return
        self.project = project
        self.selected = 0 if project.tracks else -1
        self.result = None
        self.wave.set_audio(None)
        self.progress["value"] = 0
        self.lbl_render_info.configure(text="尚未渲染")
        self.btn_play.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.lbl_project.configure(
            text=f"{project.name} · {project.bpm:g}BPM · {len(project.tracks)} 轨 · "
                 f"{_fmt_time(project.compute_length_seconds())}",
            fg=MUTED,
        )
        self.title(f"MusicGen — {project.path.name}")
        self._refresh_tracks()
        self._refresh_props()
        self.roll.redraw()
        self._refresh_sample_status()
        warnings = project.warnings
        if warnings:
            self.set_status(f"载入成功，{len(warnings)} 条警告：" + str(warnings[0]), WARN)
        else:
            self.set_status(f"已载入 {project.path.name}", OK)
        self.lbl_roll_hint.configure(text=f"{len(project.tracks)} 轨 · 共 "
                                          f"{sum(len(t.notes) for t in project.tracks)} 个音符")

    def total_beats(self) -> float:
        if self.project is None:
            return 8.0
        beats = 0.0
        for track in self.project.tracks:
            bpm = track.resolve_bpm(self.project.bpm)
            factor = bpm / self.project.bpm
            end = track.period_beats(self.project.beats_per_bar) * factor
            if track.loop and track.loop_count > 0:
                end *= track.loop_count
            beats = max(beats, end)
        return max(beats, 4.0)

    # ---- 音轨列表 -----------------------------------------------------
    def _refresh_tracks(self) -> None:
        for child in self.track_holder.winfo_children():
            child.destroy()
        if self.project is None:
            return
        for index, track in enumerate(self.project.tracks):
            selected = index == self.selected
            row = tk.Frame(self.track_holder, bg=PANEL_HI if selected else PANEL,
                           highlightthickness=1,
                           highlightbackground=ACCENT if selected else PANEL)
            row.pack(fill="x", pady=1)

            swatch = tk.Frame(row, bg=TRACK_COLORS[index % len(TRACK_COLORS)], width=4)
            swatch.pack(side="left", fill="y")

            text_box = tk.Frame(row, bg=row["bg"])
            text_box.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=6)
            name = tk.Label(text_box, text=track.name, bg=row["bg"], fg=FG, font=self.font_ui,
                            anchor="w")
            name.pack(anchor="w")
            sub = f"{track.instrument} · {track.gain_db:+g}dB"
            if track.loop:
                sub += " · 循环"
            tk.Label(text_box, text=sub, bg=row["bg"], fg=MUTED, font=self.font_small,
                     anchor="w").pack(anchor="w")

            buttons = tk.Frame(row, bg=row["bg"])
            buttons.pack(side="right", padx=6)
            mute = tk.Label(buttons, text="M", width=2, bg=ERR if track.mute else "#2a3038",
                            fg="#ffffff" if track.mute else MUTED, font=self.font_small,
                            cursor="hand2")
            mute.pack(side="left", padx=1)
            mute.bind("<Button-1>", lambda _e, i=index: self._toggle(i, "mute"))
            solo = tk.Label(buttons, text="S", width=2,
                            bg=WARN if track.solo else "#2a3038",
                            fg="#1a1d23" if track.solo else MUTED, font=self.font_small,
                            cursor="hand2")
            solo.pack(side="left", padx=1)
            solo.bind("<Button-1>", lambda _e, i=index: self._toggle(i, "solo"))

            for widget in (row, text_box, name, swatch):
                widget.bind("<Button-1>", lambda _e, i=index: self.select_track(i))

    def _toggle(self, index: int, attr: str) -> None:
        if self.project is None:
            return
        track = self.project.tracks[index]
        setattr(track, attr, not getattr(track, attr))
        self.selected = index
        self._refresh_tracks()
        self._refresh_props()
        self.roll.redraw()
        state = "开" if getattr(track, attr) else "关"
        self.set_status(f"{track.name} 的{'静音' if attr == 'mute' else '独奏'}已{state}")

    def select_track(self, index: int) -> None:
        if self.project is None or not (0 <= index < len(self.project.tracks)):
            return
        self.selected = index
        self._refresh_tracks()
        self._refresh_props()
        self.roll.redraw()

    # ---- 参数面板 -----------------------------------------------------
    def _refresh_props(self) -> None:
        if self.project is None or self.selected < 0:
            for widget in self.prop_widgets.values():
                widget.configure(state="disabled")
            self.btn_apply.configure(state="disabled")
            for label in self.prop_labels.values():
                label.configure(text="—")
            return
        track = self.project.tracks[self.selected]
        self.prop_labels["name"].configure(text=track.name)
        self.prop_labels["instrument"].configure(
            text=track.instrument + ("（无采样，用合成器）" if track.instrument not in fetch.SOURCES
                                     or not fetch.available_notes(track.instrument) else "")
        )
        self.prop_labels["notes"].configure(text=str(len(track.notes)))
        self._vars["gain_db"].set(track.gain_db)
        self._vars["pan"].set(track.pan)
        self._vars["reverb"].set(track.reverb)
        self._vars["loop"].set(track.loop)
        self._vars["loop_len"].set("" if not track.loop_len else f"{track.loop_len:g}")
        for key, widget in self.prop_widgets.items():
            widget.configure(state="normal")
        self.btn_apply.configure(state="normal")
        for key, unit in (("gain_db", "dB"), ("pan", ""), ("reverb", "")):
            self._update_readout(key, unit, self.prop_labels[f"{key}_readout"])

    def _update_readout(self, key: str, unit: str, label: tk.Label) -> None:
        var = self._vars.get(key)
        if var is None:
            return
        value = var.get()
        if key == "gain_db":
            label.configure(text=f"{value:+.1f}dB")
        elif key == "pan":
            if abs(value) < 0.02:
                label.configure(text="居中")
            else:
                label.configure(text=f"{'右' if value > 0 else '左'} {abs(value):.2f}")
        else:
            label.configure(text=f"{value:.2f}")

    def on_apply(self) -> None:
        if self.project is None or self.selected < 0:
            return
        track = self.project.tracks[self.selected]
        track.gain_db = float(self._vars["gain_db"].get())
        track.pan = float(self._vars["pan"].get())
        track.reverb = float(self._vars["reverb"].get())
        track.loop = bool(self._vars["loop"].get())
        raw_len = self._vars["loop_len"].get().strip()
        try:
            track.loop_len = float(raw_len) if raw_len else None
        except ValueError:
            messagebox.showerror("参数错误", f"循环长度无法解析：{raw_len}")
            return
        try:
            path = save_track(track)
            _clear_overrides(self.project.path, track.path)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self._refresh_tracks()
        self.roll.redraw()
        self.set_status(f"已保存 {path.name}", OK)

    # ---- 渲染 ---------------------------------------------------------
    def on_render(self) -> None:
        if self.project is None or self._busy:
            return
        self._set_busy(True, "开始渲染…")
        project = self.project

        def worker() -> None:
            started = time.time()
            try:
                result = render_project(
                    project,
                    cache_root=None,
                    progress=lambda f, m: self._queue.put(("progress", (f, m))),
                )
                self._queue.put(("render_done", (result, time.time() - started)))
            except Exception as exc:  # noqa: BLE001 - 回传给界面显示
                self._queue.put(("render_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_render_done(self, payload: tuple[RenderResult, float]) -> None:
        result, elapsed = payload
        self.result = result
        self.wave.set_audio(result.audio)
        self._set_busy(False)
        self.progress["value"] = 100
        reel = result.duration / max(elapsed, 1e-6)
        self.lbl_render_info.configure(
            text=f"{_fmt_time(result.duration)} · 峰值 {result.peak_db:+.1f}dBFS · "
                 f"RMS {result.rms_db:+.1f}dBFS · {result.notes_rendered} 个音符 · "
                 f"{elapsed:.1f}s（{reel:.1f}× 实时）"
        )
        self.btn_play.configure(state="normal")
        self.btn_export.configure(state="normal")
        self.set_status("渲染完成，可播放或导出", OK)
        for warning in result.warnings:
            self.set_status(warning, WARN)
            break

    def _on_render_error(self, message: str) -> None:
        self._set_busy(False)
        self.set_status("渲染失败", ERR)
        messagebox.showerror("渲染失败", message)

    def on_play(self) -> None:
        if self.result is None:
            return
        target = Path(tempfile.gettempdir()) / "musicgen_preview.wav"
        try:
            write_wav(target, self.result.audio, self.result.sample_rate, bit_depth=16)
            os.startfile(str(target))  # type: ignore[attr-defined]
            self.set_status("已交给系统播放器预览", OK)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("无法播放", str(exc))

    def on_export(self) -> None:
        if self.project is None or self.result is None:
            return
        default = self.project.path.with_suffix(".wav")
        chosen = filedialog.asksaveasfilename(
            title="导出 WAV",
            defaultextension=".wav",
            initialfile=default.name,
            initialdir=str(default.parent),
            filetypes=[("WAV 音频", "*.wav")],
        )
        if not chosen:
            return
        try:
            path = write_wav(Path(chosen), self.result.audio, self.result.sample_rate, bit_depth=24)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.output_path = path
        size = path.stat().st_size / (1024 * 1024)
        self.set_status(f"已导出 {path.name}（{size:.1f}MB）", OK)

    # ---- 音源 ---------------------------------------------------------
    def _refresh_sample_status(self) -> None:
        have, want = fetch.instrument_status("piano")
        if have >= want and want:
            self.lbl_samples.configure(text=f"钢琴音源 {have}/{want} 就绪", fg=OK)
        elif have:
            self.lbl_samples.configure(text=f"钢琴音源 {have}/{want}（不完整）", fg=WARN)
        else:
            self.lbl_samples.configure(text="钢琴音源未下载 — 点右上「音源」", fg=WARN)

    def on_samples(self) -> None:
        if self._busy:
            return
        have, want = fetch.instrument_status("piano")
        if have >= want and want:
            if not messagebox.askyesno("音源已就绪", f"钢琴音源已缓存 {have}/{want} 个采样。\n"
                                                     "要重新下载吗？"):
                return
        self._set_busy(True, "下载钢琴音源…")

        def worker() -> None:
            try:
                report = fetch.fetch_instrument(
                    "piano",
                    progress=lambda c, t, n: self._queue.put(
                        ("progress", (c / max(t, 1), f"下载采样 {c}/{t} · {n}"))
                    ),
                )
                self._queue.put(("samples_done", report))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("render_error", f"音源下载失败: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_samples_done(self, report) -> None:
        self._set_busy(False)
        self._refresh_sample_status()
        self.progress["value"] = 100
        self.set_status(f"音源就绪：{len(report.downloaded) + len(report.skipped)} 个采样", OK)
        if report.failed:
            messagebox.showwarning("部分采样失败", report.summary())


# ---------------------------------------------------------------- 工具


def _clear_overrides(project_path: Path, track_path: Path | None) -> None:
    """保存音轨后，移除工程文件里对同一音轨的参数覆盖，避免下次载入被盖回去。"""
    if track_path is None or not project_path.is_file():
        return
    rel = os.path.relpath(track_path, project_path.parent).replace("\\", "/")
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        tree = ET.parse(str(project_path), parser=parser)
    except ET.ParseError:
        return
    changed = False
    for element in tree.getroot():
        if element.tag != "track":
            continue
        file_attr = (element.get("file") or "").replace("\\", "/")
        if file_attr == rel:
            for attr in ("gain", "pan", "mute", "solo", "loop"):
                if element.get(attr) is not None:
                    del element.attrib[attr]
                    changed = True
    if changed:
        tree.write(str(project_path), encoding="utf-8", xml_declaration=True)


def run_gui(project_path: str | None = None) -> int:
    """启动 GUI，返回进程退出码。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 高 DPI 感知失败不影响使用
            pass
    try:
        app = MusicGenApp(project_path)
    except tk.TclError as exc:
        print(f"无法创建窗口：{exc}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0
