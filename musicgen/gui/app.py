"""主窗口：装配菜单/工具栏/传输条/三栏工作区/状态栏，并负责业务调度。

界面本身由各 view 模块负责（pianoroll / waveform / info / properties /
tracklist / output / statusbar / toolbar / transport / menubar），
面板的显示位置由 :mod:`docking` 管理，这里只做布局、事件与数据流。
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
    load_project,
    save_track,
)
from ..core.render import RenderResult, render_project
from ..core.wavio import write_wav
from ..samples import fetch
from . import layout, menubar, theme, widgets
from .docking import DockManager
from .info import ProjectInfoPage
from .newproject import NewProjectDialog
from .output import OutputPanel
from .pianoroll import PianoRollPage
from .player import AudioPlayer, PlaybackUnavailable
from .properties import PropertiesPanel
from .statusbar import StatusBar
from .toolbar import ToolBar
from .tracklist import SolutionExplorer
from .transport import TransportBar
from .waveform import WaveformPage

T = theme.PALETTE

PAGES = [
    ("roll", "钢琴卷帘", "keys"),
    ("wave", "波形", "wave"),
    ("info", "工程信息", "info"),
]

SASH = 6


def _fmt_time(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


class MusicGenApp(tk.Tk):
    def __init__(self, project_path: str | None = None) -> None:
        super().__init__()
        self.title("MusicGen")
        self.configure(bg=T["panel"])
        theme.apply_theme(self)

        self._ui = layout.load_layout()
        self._apply_window_geometry()

        self.project = None
        self.selected = -1
        self.result: RenderResult | None = None
        self.output_path: Path | None = None
        self.page = "roll"
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._log_lines: list[tuple[str, str]] = []
        self._play_timer: str | None = None
        self._save_timer: str | None = None
        self.player = AudioPlayer()
        self.player.volume = float(self._ui.get("volume", 0.9) or 0.9)

        self._build()
        self._bind_keys()
        theme.enable_dark_titlebar(self)
        self.protocol("WM_DELETE_WINDOW", self.on_quit)

        self.goto_page(self._ui.get("page") if self._ui.get("page") in
                       [key for key, _, _ in PAGES] else "roll")
        self.after(90, self._poll_queue)
        self.after(200, self._restore_sashes)
        if project_path:
            self.after(60, lambda: self.open_project(project_path))
        self.log("MusicGen 已启动。打开或新建一个工程，按 Ctrl+R 渲染，空格预览。")

    # ---------------------------------------------------------------- 窗口
    def _apply_window_geometry(self) -> None:
        geometry = self._ui.get("geometry")
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        if isinstance(geometry, str) and "x" in geometry:
            self.geometry(geometry)
        else:
            win_w = max(1080, min(1440, screen_w - 60))
            win_h = max(660, min(880, screen_h - 120))
            self.geometry(
                f"{win_w}x{win_h}+{(screen_w - win_w) // 2}+{max(16, (screen_h - win_h) // 2 - 24)}"
            )
        self.minsize(1040, 640)

    def _restore_sashes(self) -> None:
        self.update_idletasks()
        self.dock_manager.apply_measured_sizes(self._ui.get("sash"))

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
        widgets.Separator(self, color=T["border_soft"]).pack(side="top", fill="x")

        self.transport = TransportBar(
            self, on_toggle=self.on_play, on_stop=self.on_stop_playback,
            on_seek=self.on_seek, on_volume=self.on_volume,
        )
        self.transport.volume.set(self.player.volume)
        self.transport.pack(side="top", fill="x")
        widgets.Separator(self).pack(side="top", fill="x")

        self.status = StatusBar(self)
        self.status.pack(side="bottom", fill="x")

        # 主体：左停靠区 | 编辑区（上下分栏） | 右停靠区
        self.hsplit = tk.PanedWindow(self, orient="horizontal", bg=T["border"], bd=0,
                                     sashwidth=SASH, sashrelief="flat", showhandle=False)
        self.hsplit.pack(fill="both", expand=True)

        self.left_area = tk.Frame(self.hsplit, bg=T["panel"])
        self.center_host = tk.PanedWindow(self.hsplit, orient="vertical", bg=T["border"], bd=0,
                                          sashwidth=SASH, sashrelief="flat", showhandle=False)
        self.right_area = tk.Frame(self.hsplit, bg=T["panel"])
        self.hsplit.add(self.center_host, minsize=360, stretch="always")

        self.editor = tk.Frame(self.center_host, bg=T["editor"])
        self.center_host.add(self.editor, minsize=180, stretch="always")
        self.bottom_area = tk.Frame(self.center_host, bg=T["panel"])

        self._build_editor()
        self._build_docks()
        self._refresh_status_items()

    def _build_editor(self) -> None:
        self.tabs = widgets.TabStrip(self.editor, on_select=self.goto_page)
        self.tabs.pack(fill="x")
        widgets.Separator(self.editor, color=T["border_soft"]).pack(fill="x")

        self.page_host = tk.Frame(self.editor, bg=T["editor"])
        self.page_host.pack(fill="both", expand=True)

        self.roll = PianoRollPage(self.page_host, on_pick_track=self.select_track)
        self.wave = WaveformPage(self.page_host)
        self.info = ProjectInfoPage(self.page_host)
        self.pages = {"roll": self.roll, "wave": self.wave, "info": self.info}
        self.tabs.set_tabs(PAGES, self.page)
        self.roll.pack(fill="both", expand=True)

    def _build_docks(self) -> None:
        self.dock_manager = DockManager(
            containers={"left": self.left_area, "right": self.right_area,
                        "bottom": self.bottom_area},
            paned={"left": self.hsplit, "right": self.hsplit, "bottom": self.center_host},
            factories={
                "explorer": lambda parent: SolutionExplorer(
                    parent, on_select=self.select_track, on_toggle=self._toggle,
                    on_apply=self.on_apply,
                ),
                "properties": lambda parent: PropertiesPanel(parent, on_apply=self.on_apply),
                "output": lambda parent: OutputPanel(parent),
            },
            titles={"explorer": "解决方案资源管理器", "properties": "属性", "output": "输出"},
            defaults={"explorer": "left", "properties": "right", "output": "bottom"},
            on_created=self._on_panel_created,
            center=self.center_host,
            on_layout_changed=self._schedule_save_layout,
        )
        self.dock_manager.apply_state(self._ui.get("docks"), self._ui.get("sizes"))
        self._sync_view_menu()

    def _on_panel_created(self, key: str, widget: tk.Widget) -> None:
        if key == "explorer":
            self.explorer = widget
            if self.project is not None:
                widget.load(self.project, self._sample_line())
                widget.select_track(self.selected)
            else:
                widget.load_empty()
        elif key == "properties":
            self.props = widget
            track = self._current_track()
            widget.set_track(track, "" if track is None else self._instrument_note(track))
        elif key == "output":
            self.output = widget
            for message, level in self._log_lines[-200:]:
                widget.log(message, level)

    # ---------------------------------------------------------------- 快捷键
    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda _e: self.on_open())
        self.bind("<Control-n>", lambda _e: self.on_new_project())
        self.bind("<Control-r>", lambda _e: self.on_render())
        self.bind("<Control-s>", lambda _e: self.on_export())
        self.bind("<Control-Return>", lambda _e: self.on_apply())
        self.bind("<Control-l>", lambda _e: self.toggle_panel("output"))
        self.bind("<Control-Key-1>", lambda _e: self.goto_page("roll"))
        self.bind("<Control-Key-2>", lambda _e: self.goto_page("wave"))
        self.bind("<Control-Key-3>", lambda _e: self.goto_page("info"))
        self.bind("<F4>", lambda _e: self.toggle_panel("properties"))
        self.bind("<Control-Shift-KeyPress-E>", lambda _e: self.toggle_panel("explorer"))
        self.bind("<F5>", lambda _e: self.on_refresh())
        self.bind("<space>", lambda _e: self.on_play())
        self.bind("<Escape>", lambda _e: self.on_stop_playback())
        self.bind("m", lambda _e: self.on_toggle_selected("mute"))
        self.bind("s", lambda _e: self.on_toggle_selected("solo"))

    # ---------------------------------------------------------------- 页面
    def goto_page(self, key: str) -> None:
        if key not in self.pages:
            return
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.page = key
        self.tabs.set_active(key)
        if key == "roll":
            self.roll.schedule_redraw()
        elif key == "wave":
            self.wave.redraw()

    def reset_zoom(self) -> None:
        self.goto_page("roll")
        self.roll.reset_view()

    def toggle_panel(self, key: str) -> None:
        self.dock_manager.toggle(key)
        self._sync_view_menu()

    def dock_panel(self, key: str, area: str) -> None:
        self.dock_manager.dock(key, area)
        self._sync_view_menu()

    def reset_layout(self) -> None:
        self.dock_manager.reset()
        self._sync_view_menu()
        self.log("布局已重置")

    def _sync_view_menu(self) -> None:
        for key, var in getattr(self, "_view_vars", {}).items():
            var.set(self.dock_manager.visible(key))

    # ---------------------------------------------------------------- 消息
    def log(self, message: str, level: str = "info") -> None:
        self._log_lines.append((message, level))
        if len(self._log_lines) > 400:
            del self._log_lines[:100]
        panel = self.dock_manager.panel("output") if self.dock_manager else None
        if panel is not None:
            panel.log(message, level)

    def clear_output(self) -> None:
        self._log_lines.clear()
        panel = self.dock_manager.panel("output") if self.dock_manager else None
        if panel is not None:
            panel.clear()

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
            has_result = self.result is not None
            self.toolbar.btn_export.set_enabled(has_result)
            self.transport.set_enabled(has_result)
            self.status.set_progress(None)

    def _sample_line(self) -> str:
        have, want = fetch.instrument_status("piano")
        return f"piano — {have}/{want} 采样"

    def _refresh_status_items(self) -> None:
        items = []
        have, want = fetch.instrument_status("piano")
        items.append(f"钢琴音源 {have}/{want}" if want else "钢琴音源 —")
        if self.project is not None:
            items.append(f"{self.project.sample_rate / 1000:.1f} kHz")
            items.append(f"{self.project.bpm:g} BPM")
            items.append(f"{len(self.project.tracks)} 音轨")
            items.append(f"时长 {_fmt_time(self.project.compute_length_seconds())}")
        self.status.set_items(items)

    # ---------------------------------------------------------------- 工程
    def on_open(self) -> None:
        self.open_project(None)

    def on_new_project(self) -> None:
        if self._busy:
            return
        dialog = NewProjectDialog(self, default_dir=Path.cwd())
        self.wait_window(dialog)
        if not dialog.result:
            return
        info = dialog.result
        self.log(
            f"新建工程 {info['name']}（{info['bpm']:g}BPM · {info['sample_rate']}Hz · "
            f"{info['beats_per_bar']}/4 · 模板 {info['template']}）",
            "ok",
        )
        self.open_project(str(info["path"]))

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
            self.log("载入失败：" + str(exc.issues[0]), "error")
            return

        self._stop_playback(reset=True)
        self.project = project
        self.selected = 0 if project.tracks else -1
        self.result = None
        self.player.unload()
        self.transport.set_enabled(False)
        self.transport.update_time(0.0, 0.0)
        self.wave.clear()
        self.status.set_progress(None)
        self.title(f"{project.path.name} — MusicGen")
        self.toolbar.set_project_info(
            f"{project.name}  ·  {project.bpm:g} BPM  ·  {len(project.tracks)} 音轨  ·  "
            f"{_fmt_time(project.compute_length_seconds())}"
        )
        self.toolbar.btn_render.set_enabled(True)
        self.toolbar.btn_export.set_enabled(False)

        self.roll.set_project(project, self.selected)
        self.info.set_project(project)
        self._refresh_explorer()
        self._select_ui(self.selected)
        self._refresh_status_items()
        self.status.set_message(f"已打开 {project.path.name}")

        self.log(f"载入工程 {project.path}", "ok")
        self.log(
            f"BPM {project.bpm:g} · {project.sample_rate}Hz · {len(project.tracks)} 条音轨 · "
            f"时长 {project.compute_length_seconds():.2f}s"
        )
        for warning in project.warnings:
            self.log(str(warning), "warn")

    def on_refresh(self) -> None:
        if self.project is not None:
            self.open_project(str(self.project.path))

    # ---------------------------------------------------------------- 音轨
    def _current_track(self):
        if self.project is None or not (0 <= self.selected < len(self.project.tracks)):
            return None
        return self.project.tracks[self.selected]

    def _instrument_note(self, track) -> str:
        if track.instrument in fetch.SOURCES:
            have, want = fetch.instrument_status(track.instrument)
            if have < want:
                return "（采样不全）"
        return ""

    def select_track(self, index: int) -> None:
        if self.project is None or not (0 <= index < len(self.project.tracks)):
            return
        self.selected = index
        explorer = self.dock_manager.panel("explorer")
        if explorer is not None:
            explorer.select_track(index)
        self._select_ui(index)

    def _select_ui(self, index: int) -> None:
        self.roll.set_selected(index)
        self.roll.schedule_redraw()
        props = self.dock_manager.panel("properties")
        if props is not None:
            track = self._current_track()
            props.set_track(track, "" if track is None else self._instrument_note(track))

    def _refresh_explorer(self) -> None:
        explorer = self.dock_manager.panel("explorer")
        if explorer is None:
            return
        if self.project is None:
            explorer.load_empty()
        else:
            explorer.load(self.project, self._sample_line())
            explorer.select_track(self.selected)

    def on_toggle_selected(self, attr: str) -> None:
        if self.selected >= 0:
            self._toggle(self.selected, attr)

    def _toggle(self, index: int, attr: str) -> None:
        if self.project is None:
            return
        track = self.project.tracks[index]
        setattr(track, attr, not getattr(track, attr))
        state = "开" if getattr(track, attr) else "关"
        self.log(f"{track.name}：{'静音' if attr == 'mute' else '独奏'}{state}")
        self.selected = index
        self._refresh_explorer()
        props = self.dock_manager.panel("properties")
        if props is not None:
            props.sync_flags()
        self.roll.schedule_redraw()

    def on_apply(self) -> None:
        track = self._current_track()
        props = self.dock_manager.panel("properties")
        if track is None or props is None:
            return
        values = props.values()
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
        self._refresh_explorer()
        self.roll.schedule_redraw()
        self.info.set_project(self.project)
        self.status.set_message(f"已保存 {path.name}")
        self.log(
            f"保存音轨 {path.name}（音量 {track.gain_db:+.1f}dB / 声像 {track.pan:+.2f} / "
            f"混响 {track.reverb:.2f}{' / 循环' if track.loop else ''}）"
            + ("，并清除了工程层覆盖" if changed else ""),
            "ok",
        )

    # ---------------------------------------------------------------- 渲染
    def on_render(self) -> None:
        if self.project is None or self._busy:
            return
        self._stop_playback(reset=True)
        self._set_busy(True, "正在渲染…")
        self.log(f"开始渲染 {self.project.name}")
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
        self.player.load(result.audio, result.sample_rate)
        self._set_busy(False)
        self.transport.set_enabled(True)
        self.transport.update_time(0.0, self.player.duration)
        self.status.set_progress(1.0)
        self.status.set_message(f"渲染完成 · {result.duration:.1f}s")
        self.after(1500, lambda: self.status.set_progress(None))
        self.log(f"渲染完成：{summary}", "ok")
        for warning in result.warnings:
            self.log(warning, "warn")
        self.goto_page("wave")

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self.status.set_message("出错")
        self.log(message, "error")
        messagebox.showerror("出错了", message)

    # ---------------------------------------------------------------- 预览
    def on_play(self) -> None:
        if self.result is None:
            self.log("还没有可播放的结果，先按 Ctrl+R 渲染一次。", "warn")
            return
        if not self.player.has_audio:
            self.player.load(self.result.audio, self.result.sample_rate)
            self.transport.update_time(0.0, self.player.duration)
        if self.player.playing:
            self._pause_playback()
            return
        if self.player.position >= self.player.duration - 0.02:
            self.player.seek(0.0)
        try:
            self.player.play()
        except PlaybackUnavailable as exc:
            self.log(f"内置预览不可用（{exc}），改用系统播放器。", "warn")
            self._open_in_system_player()
            return
        self.transport.set_playing(True)
        self.status.set_message("预览中…")
        self._start_tick()

    def _pause_playback(self) -> None:
        self.player.pause()
        self.transport.set_playing(False)
        self._stop_tick()
        self.status.set_message(
            f"已暂停 · {_fmt_time(self.player.position)} / {_fmt_time(self.player.duration)}"
        )

    def on_stop_playback(self) -> None:
        self._stop_playback(reset=True)

    def _stop_playback(self, reset: bool = True) -> None:
        self.player.pause()
        if reset:
            self.player.seek(0.0)
        self.transport.set_playing(False)
        self._stop_tick()
        self.roll.set_playhead(None)
        self.wave.set_playhead(None)
        if self.result is not None:
            self.transport.update_time(0.0, self.player.duration)

    def on_seek(self, seconds: float) -> None:
        if not self.player.has_audio:
            return
        self.player.seek(seconds)
        self.transport.update_time(self.player.position, self.player.duration)
        self.roll.set_playhead(self.player.position)
        self.wave.set_playhead(self.player.position)
        if self.player.playing:
            self._start_tick()

    def on_volume(self, value: float) -> None:
        self.player.volume = max(0.0, min(1.0, value))

    def _start_tick(self) -> None:
        self._stop_tick()
        self._tick()

    def _tick(self) -> None:
        if not self.player.playing:
            self._play_timer = None
            self.transport.set_playing(False)
            self.transport.update_time(self.player.position, self.player.duration)
            if self.player.position >= self.player.duration - 0.05:
                self.roll.set_playhead(None)
                self.wave.set_playhead(None)
                self.transport.update_time(0.0, self.player.duration)
                self.status.set_message("预览结束")
            else:
                self.status.set_message("已暂停")
            return
        position = self.player.position
        self.transport.update_time(position, self.player.duration)
        self.roll.set_playhead(position)
        self.wave.set_playhead(position)
        self._play_timer = self.after(40, self._tick)

    def _stop_tick(self) -> None:
        if self._play_timer is not None:
            self.after_cancel(self._play_timer)
            self._play_timer = None

    def _open_in_system_player(self) -> None:
        if self.result is None:
            return
        target = Path(tempfile.gettempdir()) / "musicgen_preview.wav"
        try:
            write_wav(target, self.result.audio, self.result.sample_rate, bit_depth=16)
            os.startfile(str(target))  # type: ignore[attr-defined]
            self.log(f"已交给系统播放器：{target}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("无法播放", str(exc))

    # ---------------------------------------------------------------- 导出
    def on_export(self) -> None:
        if self.project is None or self.result is None:
            self.log("还没有可导出的结果，先渲染一次。", "warn")
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
        self.log(f"导出 {path}（{size:.1f}MB, 24bit）", "ok")

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
        self.log("开始下载钢琴音源（FluidR3 GM）")

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
        self.log(report.summary(), "ok" if report.ok else "warn")
        self._refresh_status_items()
        if self.project is not None:
            self._select_ui(self.selected)
            self._refresh_explorer()
        if report.failed:
            messagebox.showwarning("部分采样失败", report.summary())

    # ---------------------------------------------------------------- 布局持久化
    def _schedule_save_layout(self) -> None:
        if self._save_timer is not None:
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(700, self.save_layout)

    def _collect_layout(self) -> dict:
        manager = self.dock_manager
        sizes = dict(manager.sizes)
        for area in ("left", "right"):
            if str(manager.containers[area]) in manager.paned[area].panes():
                measured = manager.containers[area].winfo_width()
                if measured > 40:
                    sizes[area] = measured
        if str(manager.containers["bottom"]) in manager.paned["bottom"].panes():
            measured = manager.containers["bottom"].winfo_height()
            if measured > 40:
                sizes["bottom"] = measured
        sashes = []
        if len(self.hsplit.panes()) >= 2:
            sashes.append(self.hsplit.sash_coord(0))
        if len(self.center_host.panes()) >= 2:
            sashes.append(self.center_host.sash_coord(0))
        return {
            "geometry": self.geometry(),
            "page": self.page,
            "docks": manager.state(),
            "sizes": sizes,
            "sash": sashes,
            "volume": float(self.player.volume),
        }

    def save_layout(self) -> None:
        self._save_timer = None
        layout.save_layout(self._collect_layout())

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
            f"预览：{'内置播放器（sounddevice）' if self.player.available else '系统播放器'}\n"
            "界面：Tkinter（Visual Studio 2022 风格）",
        )

    def on_quit(self) -> None:
        self._stop_tick()
        self.player.pause()
        try:
            self.save_layout()
        finally:
            self.destroy()


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
