"""主窗口：把菜单、工具栏、三栏工作区、文档标签页、状态栏装配起来。

界面本身由各 view 模块负责（pianoroll / waveform / info / properties /
tracklist / output / statusbar / toolbar / menubar），这里只做布局与业务调度。
"""

from __future__ import annotations

import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from ..core.project import (
    ProjectError,
    clear_track_overrides,
    create_project,
    load_project,
    save_track,
)
from ..core.render import RenderResult, render_project
from ..core.wavio import write_wav
from ..samples import fetch
from . import menubar, theme, widgets
from .info import ProjectInfoPage
from .output import OutputPanel
from .pianoroll import PianoRollPage
from .properties import PropertiesPanel
from .statusbar import StatusBar
from .toolbar import ToolBar
from .tracklist import SolutionExplorer
from .waveform import WaveformPage

T = theme.PALETTE

PAGES = [
    ("roll", "钢琴卷帘", "keys"),
    ("wave", "波形", "wave"),
    ("info", "工程信息", "info"),
]


def _fmt_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


class MusicGenApp(tk.Tk):
    def __init__(self, project_path: str | None = None) -> None:
        super().__init__()
        self.title("MusicGen")
        self.configure(bg=T["panel"])
        theme.apply_theme(self)

        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        win_w = max(1080, min(1440, screen_w - 60))
        win_h = max(660, min(880, screen_h - 120))
        self.geometry(
            f"{win_w}x{win_h}+{(screen_w - win_w) // 2}+{max(16, (screen_h - win_h) // 2 - 24)}"
        )
        self.minsize(1040, 640)

        self.project = None
        self.selected = -1
        self.result: RenderResult | None = None
        self.output_path: Path | None = None
        self.page = "roll"
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._output_visible = True

        self._build()
        theme.enable_dark_titlebar(self)
        self._bind_keys()

        self.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.after(90, self._poll_queue)
        self._refresh_sample_items()
        if project_path:
            self.after(80, lambda: self.open_project(project_path))

    # ---------------------------------------------------------------- 布局
    def _build(self) -> None:
        self.menu_bar = menubar.build(self, self)
        self.menu_bar.pack(side="top", fill="x")
        widgets.Separator(self, color=T["border_soft"]).pack(side="top", fill="x")

        self.toolbar = ToolBar(self, {
            "open": self.on_open,
            "render": self.on_render,
            "play": self.on_play,
            "export": self.on_export,
            "samples": self.on_samples,
            "refresh": self.on_refresh,
        })
        self.toolbar.pack(side="top", fill="x")
        widgets.Separator(self).pack(side="top", fill="x")

        self.status = StatusBar(self)
        self.status.pack(side="bottom", fill="x")

        self.vsplit = tk.PanedWindow(self, orient="vertical", bg=T["border"], bd=0,
                                     sashwidth=4, sashrelief="flat", showhandle=False)
        self.vsplit.pack(fill="both", expand=True)

        self.hsplit = tk.PanedWindow(self.vsplit, orient="horizontal", bg=T["border"], bd=0,
                                     sashwidth=4, sashrelief="flat", showhandle=False)
        self.vsplit.add(self.hsplit, minsize=240, stretch="always")

        # 左：解决方案资源管理器
        self.explorer = SolutionExplorer(
            self.hsplit, on_select=self.select_track, on_toggle=self._toggle,
            on_apply=self.on_apply,
        )
        self.hsplit.add(self.explorer, minsize=190, width=282, stretch="never")

        # 中：文档区（标签页 + 页面）
        editor = tk.Frame(self.hsplit, bg=T["editor"])
        self.hsplit.add(editor, minsize=360, stretch="always")

        self.tabs = widgets.TabStrip(editor, on_select=self.goto_page)
        self.tabs.pack(fill="x")
        widgets.Separator(editor, color=T["border_soft"]).pack(fill="x")

        self.page_host = tk.Frame(editor, bg=T["editor"])
        self.page_host.pack(fill="both", expand=True)

        self.roll = PianoRollPage(self.page_host, on_pick_track=self.select_track)
        self.wave = WaveformPage(self.page_host)
        self.info = ProjectInfoPage(self.page_host)
        self.pages = {"roll": self.roll, "wave": self.wave, "info": self.info}
        self.tabs.set_tabs(PAGES, self.page)
        self.roll.pack(fill="both", expand=True)

        # 右：属性
        self.props = PropertiesPanel(self.hsplit, on_apply=self.on_apply)
        self.hsplit.add(self.props, minsize=220, width=268, stretch="never")

        # 下：输出
        self.output = OutputPanel(self.vsplit, on_clear=self.clear_output)
        self.vsplit.add(self.output, minsize=80, height=150, stretch="never")

        self.output.log("MusicGen 已启动。打开一个 .musicproj 工程，按 Ctrl+R 渲染。")

    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda _e: self.on_open())
        self.bind("<Control-n>", lambda _e: self.on_new_project())
        self.bind("<Control-r>", lambda _e: self.on_render())
        self.bind("<Control-s>", lambda _e: self.on_export())
        self.bind("<Control-Return>", lambda _e: self.on_apply())
        self.bind("<Control-l>", lambda _e: self.toggle_output())
        self.bind("<Control-Key-1>", lambda _e: self.goto_page("roll"))
        self.bind("<Control-Key-2>", lambda _e: self.goto_page("wave"))
        self.bind("<Control-Key-3>", lambda _e: self.goto_page("info"))
        self.bind("<F5>", lambda _e: self.on_refresh())
        self.bind("<space>", lambda _e: self.on_play())
        self.bind("m", lambda _e: self.on_toggle_selected("mute"))
        self.bind("s", lambda _e: self.on_toggle_selected("solo"))

    # ---------------------------------------------------------------- 页面
    def goto_page(self, key: str) -> None:
        if key not in self.pages:
            return
        for page in self.pages.values():
            page.pack_forget()
        target = self.pages[key]
        target.pack(fill="both", expand=True)
        self.page = key
        self.tabs.set_active(key)
        if key == "roll":
            self.roll.schedule_redraw()
        elif key == "wave":
            self.wave.redraw()

    def reset_zoom(self) -> None:
        self.goto_page("roll")
        self.roll.reset_view()

    def toggle_output(self) -> None:
        if self._output_visible:
            self.vsplit.forget(self.output)
        else:
            self.vsplit.add(self.output, minsize=80, height=150, stretch="never")
        self._output_visible = not self._output_visible

    def clear_output(self) -> None:
        self.output.clear()

    # ---------------------------------------------------------------- 消息
    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    fraction, message = payload
                    self.status.set_progress(fraction)
                    self.status.set_message(message)
                elif kind == "render_done":
                    self._on_render_done(payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "samples_done":
                    self._on_samples_done(payload)
        except queue.Empty:
            pass
        self.after(90, self._poll_queue)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.toolbar.set_busy(busy)
        if busy:
            self.status.set_progress(0.0)
            self.status.set_message(message or "处理中…")
        else:
            self.toolbar.btn_export.set_enabled(self.result is not None)
            self.toolbar.btn_play.set_enabled(self.result is not None)
            self.status.set_progress(None)

    def _status_items(self) -> list[str]:
        items = []
        have, want = fetch.instrument_status("piano")
        items.append(f"钢琴音源 {have}/{want}" if want else "钢琴音源 —")
        if self.project is not None:
            items.append(f"{self.project.sample_rate / 1000:.1f} kHz")
            items.append(f"{self.project.bpm:g} BPM")
            items.append(f"{len(self.project.tracks)} 音轨")
            items.append(f"时长 {_fmt_time(self.project.compute_length_seconds())}")
        return items

    def _refresh_sample_items(self) -> None:
        self.status.set_items(self._status_items())

    def _sample_line(self) -> str:
        have, want = fetch.instrument_status("piano")
        return f"piano — {have}/{want} 采样"

    # ---------------------------------------------------------------- 工程
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
            self.output.log("载入失败：" + str(exc.issues[0]), "error")
            return

        self.project = project
        self.selected = 0 if project.tracks else -1
        self.result = None
        self.wave.clear()
        self.status.set_progress(None)
        self.title(f"{project.path.name} — MusicGen")
        self.toolbar.set_project_info(
            f"{project.name}  ·  {project.bpm:g} BPM  ·  {len(project.tracks)} 音轨  ·  "
            f"{_fmt_time(project.compute_length_seconds())}"
        )
        self.toolbar.btn_render.set_enabled(True)
        self.toolbar.btn_play.set_enabled(False)
        self.toolbar.btn_export.set_enabled(False)

        self.explorer.load(project, self._sample_line())
        self.roll.set_project(project, self.selected)
        self.info.set_project(project)
        self._select_ui(self.selected)
        self._refresh_sample_items()
        self.status.set_message(f"已打开 {project.path.name}")

        self.output.log(f"载入工程 {project.path}", "ok")
        self.output.log(
            f"BPM {project.bpm:g} · {project.sample_rate}Hz · {len(project.tracks)} 条音轨 · "
            f"时长 {project.compute_length_seconds():.2f}s"
        )
        for warning in project.warnings:
            self.output.log(str(warning), "warn")

    def on_refresh(self) -> None:
        if self.project is not None:
            self.open_project(str(self.project.path))

    def on_new_project(self) -> None:
        if self._busy:
            return
        directory = filedialog.askdirectory(title="选择新工程目录")
        if not directory:
            return
        try:
            path = create_project(Path(directory))
        except FileExistsError as exc:
            messagebox.showwarning("无法新建", f"{exc}\n请换一个空目录，或先删除旧工程。")
            return
        except OSError as exc:
            messagebox.showerror("无法新建", str(exc))
            return
        self.output.log(f"已创建示例工程 {path}", "ok")
        self.open_project(str(path))

    def on_quit(self) -> None:
        self.destroy()

    # ---------------------------------------------------------------- 音轨
    def select_track(self, index: int) -> None:
        if self.project is None or not (0 <= index < len(self.project.tracks)):
            return
        self.selected = index
        self.explorer.select_track(index)
        self._select_ui(index)

    def _select_ui(self, index: int) -> None:
        self.roll.set_selected(index)
        self.roll.schedule_redraw()
        track = self.project.tracks[index] if index >= 0 and self.project else None
        note = ""
        if track is not None and track.instrument in fetch.SOURCES:
            have, want = fetch.instrument_status(track.instrument)
            note = "" if have >= want else "（采样不全）"
        self.props.set_track(track, note)

    def on_toggle_selected(self, attr: str) -> None:
        if self.selected >= 0:
            self._toggle(self.selected, attr)

    def _toggle(self, index: int, attr: str) -> None:
        if self.project is None:
            return
        track = self.project.tracks[index]
        setattr(track, attr, not getattr(track, attr))
        state = "开" if getattr(track, attr) else "关"
        self.output.log(f"{track.name}：{'静音' if attr == 'mute' else '独奏'}{state}")
        self.selected = index
        self.explorer.load(self.project, self._sample_line())
        self.explorer.select_track(index)
        self.props.sync_flags()
        self.roll.schedule_redraw()

    def on_apply(self) -> None:
        if self.project is None or self.selected < 0:
            return
        track = self.project.tracks[self.selected]
        values = self.props.values()
        track.gain_db = float(values["gain_db"])
        track.pan = float(values["pan"])
        track.reverb = float(values["reverb"])
        track.loop = bool(values["loop"])
        track.mute = bool(values["mute"])
        track.solo = bool(values["solo"])
        raw_len = values["loop_len"]
        try:
            track.loop_len = float(raw_len) if raw_len else None
        except ValueError:
            messagebox.showerror("参数错误", f"循环长度无法解析：{raw_len}")
            return
        try:
            path = save_track(track)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        changed = clear_track_overrides(self.project.path, track.path)
        self.explorer.load(self.project, self._sample_line())
        self.explorer.select_track(self.selected)
        self.roll.schedule_redraw()
        self.info.set_project(self.project)
        self.status.set_message(f"已保存 {path.name}")
        self.output.log(
            f"保存音轨 {path.name}（音量 {track.gain_db:+.1f}dB / 声像 {track.pan:+.2f} / "
            f"混响 {track.reverb:.2f}{' / 循环' if track.loop else ''}）"
            + ("，并清除了工程层覆盖" if changed else ""),
            "ok",
        )

    # ---------------------------------------------------------------- 渲染
    def on_render(self) -> None:
        if self.project is None or self._busy:
            return
        self._set_busy(True, "正在渲染…")
        self.output.log(f"开始渲染 {self.project.name}")
        project = self.project

        def worker() -> None:
            started = time.time()
            try:
                result = render_project(
                    project,
                    progress=lambda f, m: self._queue.put(("progress", (f, m))),
                )
                self._queue.put(("render_done", (result, time.time() - started)))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", f"渲染失败：{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_render_done(self, payload) -> None:
        result, elapsed = payload
        self.result = result
        reel = result.duration / max(elapsed, 1e-6)
        summary = (
            f"{_fmt_time(result.duration)}  ·  峰值 {result.peak_db:+.1f} dBFS  ·  "
            f"RMS {result.rms_db:+.1f} dBFS  ·  {result.notes_rendered} 个音符  ·  "
            f"{elapsed:.1f}s（{reel:.1f}× 实时）"
        )
        self.wave.set_result(result.audio, result.sample_rate, summary)
        self._set_busy(False)
        self.status.set_progress(1.0)
        self.status.set_message(f"渲染完成 · {result.duration:.1f}s")
        self.after(1500, lambda: self.status.set_progress(None))
        self.output.log(f"渲染完成：{summary}", "ok")
        for warning in result.warnings:
            self.output.log(warning, "warn")
        self.goto_page("wave")

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self.status.set_message("出错")
        self.output.log(message, "error")
        messagebox.showerror("出错了", message)

    def on_play(self) -> None:
        if self.result is None:
            self.output.log("还没有可播放的结果，先渲染一次。", "warn")
            return
        target = Path(tempfile.gettempdir()) / "musicgen_preview.wav"
        try:
            write_wav(target, self.result.audio, self.result.sample_rate, bit_depth=16)
            os.startfile(str(target))  # type: ignore[attr-defined]
            self.status.set_message("已交给系统播放器预览")
            self.output.log(f"预览文件：{target}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("无法播放", str(exc))

    def on_export(self) -> None:
        if self.project is None or self.result is None:
            self.output.log("还没有可导出的结果，先渲染一次。", "warn")
            return
        default = self.project.path.with_suffix(".wav")
        chosen = filedialog.asksaveasfilename(
            title="导出 WAV", defaultextension=".wav", initialfile=default.name,
            initialdir=str(default.parent), filetypes=[("WAV 音频", "*.wav")],
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
        self.status.set_message(f"已导出 {path.name}（{size:.1f}MB）")
        self.output.log(f"导出 {path}（{size:.1f}MB, 24bit）", "ok")

    # ---------------------------------------------------------------- 音源
    def on_samples(self) -> None:
        if self._busy:
            return
        have, want = fetch.instrument_status("piano")
        if have >= want and want:
            if not messagebox.askyesno("音源已就绪",
                                       f"钢琴音源已缓存 {have}/{want} 个采样。\n要重新下载吗？"):
                return
        self._set_busy(True, "正在下载钢琴音源…")
        self.output.log("开始下载钢琴音源（FluidR3 GM）")

        def worker() -> None:
            try:
                report = fetch.fetch_instrument(
                    "piano",
                    progress=lambda c, t, n: self._queue.put(
                        ("progress", (c / max(t, 1), f"下载音源 {c}/{t} · {n}"))
                    ),
                )
                self._queue.put(("samples_done", report))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", f"音源下载失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_samples_done(self, report) -> None:
        self._set_busy(False)
        self.status.set_progress(1.0)
        total = len(report.downloaded) + len(report.skipped)
        self.status.set_message(f"音源就绪：{total} 个采样")
        self.output.log(report.summary(), "ok" if report.ok else "warn")
        self._refresh_sample_items()
        if self.project is not None:
            self._select_ui(self.selected)
        if report.failed:
            messagebox.showwarning("部分采样失败", report.summary())

    # ---------------------------------------------------------------- 帮助
    def show_format_help(self) -> None:
        doc = Path(__file__).resolve().parents[2] / "docs" / "项目文档.md"
        if doc.is_file():
            try:
                os.startfile(str(doc))  # type: ignore[attr-defined]
                return
            except Exception:  # noqa: BLE001
                pass
        messagebox.showinfo(
            "工程格式",
            'musicproj：<musicproj bpm= sampleRate= length= masterGain=>\n'
            '  <track file="tracks/xx.track.xml"/>\n\n'
            "track：<track name= instrument= gain= pan= reverb= loop= loopLen=>\n"
            '  <note time= dur= pitch="C4,E4" vel=/>\n\n'
            "时值单位为「拍」，支持 1/4 与 0.5 两种写法。",
        )

    def on_about(self) -> None:
        from .. import __version__

        messagebox.showinfo(
            "关于 MusicGen",
            f"MusicGen {__version__}\n把 *.musicproj 工程渲染成乐器纯音乐 WAV。\n\n"
            "音源：FluidR3 GM（自由使用）\n"
            "渲染：numpy + soundfile\n"
            "界面：Tkinter（Visual Studio 2022 风格）",
        )


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
