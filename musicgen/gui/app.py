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

from ..core import trackedit
from ..core.notes import PitchError, parse_pitches
from ..core.project import (
    Note,
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
from .dragdock import AREA_NAMES, DockDrag
from .info import ProjectInfoPage
from .newproject import NewProjectDialog
from .newtrack import NewTrackDialog
from .notepanel import NotePanel
from .output import OutputPanel
from .pianoroll import PianoRollPage
from .player import AudioPlayer, PlaybackUnavailable
from .properties import PropertiesPanel
from .statusbar import StatusBar
from .toolbar import ToolBar
from .tracklist import SolutionExplorer
from .trackview import TrackWindow
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
        self.note_sel: tuple[int, int] | None = None
        self.result: RenderResult | None = None
        self.output_path: Path | None = None
        self.page = "roll"
        self.only_selected_track = False       # 钢琴卷帘只看选中轨
        self._track_windows: dict[int, TrackWindow] = {}
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._log_lines: list[tuple[str, str]] = []
        self._play_timer: str | None = None
        self._save_timer: str | None = None
        self._poll_timer: str | None = None
        self.player = AudioPlayer()
        self.player.volume = float(self._ui.get("volume", 0.9) or 0.9)

        self._build()
        self._bind_keys()
        theme.enable_dark_titlebar(self)
        self.protocol("WM_DELETE_WINDOW", self.on_quit)

        self.goto_page(self._ui.get("page") if self._ui.get("page") in
                       [key for key, _, _ in PAGES] else "roll")
        self._poll_timer = self.after(90, self._poll_queue)
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
        self.dock_manager.apply_measured_sizes(
            self._ui.get("sashes") or self._ui.get("sash") or {}
        )

    # ---------------------------------------------------------------- 布局
    def _build(self) -> None:
        self.menu_bar = menubar.build(self, self)
        self.menu_bar.pack(side="top", fill="x")
        widgets.Separator(self, color=T["border_soft"]).pack(side="top", fill="x")

        self.toolbar = ToolBar(self, {
            "new": self.on_new_project,
            "new_track": self.on_new_track,
            "delete_track": self.on_delete_track,
            "view_track": self.on_view_track,
            "isolate": self.on_toggle_isolate,
            "open": self.on_open,
            "apply": self.on_apply,
            "render": self.on_render,
            "play": self.on_play,
            "stop": self.on_stop_playback,
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

        def dock_area(parent: tk.Misc) -> tk.PanedWindow:
            """停靠区：竖向分栏，同一区里的多个面板之间也能拖动分隔条。"""
            return tk.PanedWindow(parent, orient="vertical", bg=T["border"], bd=0,
                                  sashwidth=SASH, sashrelief="flat", showhandle=False)

        self.left_area = dock_area(self.hsplit)
        self.center_host = dock_area(self.hsplit)
        self.right_area = dock_area(self.hsplit)
        self.hsplit.add(self.center_host, minsize=360, stretch="always")

        self.editor = tk.Frame(self.center_host, bg=T["editor"])
        self.center_host.add(self.editor, minsize=180, stretch="always")
        self.bottom_area = dock_area(self.center_host)

        self._build_editor()
        self._build_docks()
        self._refresh_status_items()

    def _build_editor(self) -> None:
        self.tabs = widgets.TabStrip(self.editor, on_select=self.goto_page)
        self.tabs.pack(fill="x")
        widgets.Separator(self.editor, color=T["border_soft"]).pack(fill="x")

        self.page_host = tk.Frame(self.editor, bg=T["editor"])
        self.page_host.pack(fill="both", expand=True)

        self.roll = PianoRollPage(self.page_host, on_pick_track=self.select_track,
                                  on_pick_note=self.select_note)
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
                    on_apply=self.on_apply, on_new_track=self.on_new_track,
                    on_view_track=self.on_view_track,
                    on_delete_track=self.on_delete_track,
                ),
                "properties": lambda parent: PropertiesPanel(parent, on_apply=self.on_apply),
                "note": lambda parent: NotePanel(
                    parent, on_apply=self.on_note_apply, on_delete=self.on_note_delete,
                    on_duplicate=self.on_note_duplicate, on_step=self.on_note_step,
                ),
                "output": lambda parent: OutputPanel(parent),
            },
            titles={"explorer": "解决方案资源管理器", "properties": "属性",
                    "note": "音符", "output": "输出"},
            defaults={"explorer": "left", "properties": "right", "note": "left",
                      "output": "bottom"},
            on_created=self._on_panel_created,
            center=self.center_host,
            on_layout_changed=self._schedule_save_layout,
            dragger=lambda header, key, title: self.drag_dock.bind(header, key, title),
        )
        self.drag_dock = DockDrag(self, lambda: self.dock_manager, self._dock_by_drag)
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
        elif key == "note":
            self.note_panel = widget
            self._refresh_note_panel()
        elif key == "output":
            self.output = widget
            for message, level in self._log_lines[-200:]:
                widget.log(message, level)

    # ---------------------------------------------------------------- 快捷键
    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda _e: self.on_open())
        self.bind("<Control-n>", lambda _e: self.on_new_project())
        self.bind("<Control-Shift-KeyPress-T>", lambda _e: self.on_new_track())
        self.bind("<Control-Shift-KeyPress-V>", lambda _e: self.on_view_track())
        self.bind("<Control-Shift-KeyPress-I>", lambda _e: self.on_toggle_isolate())
        self.bind("<Control-Shift-Delete>", lambda _e: self.on_delete_track())
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
        self.bind("<space>", self._hotkey(self.on_play))
        self.bind("<Escape>", self._hotkey(self.on_stop_playback))
        self.bind("m", self._hotkey(lambda: self.on_toggle_selected("mute")))
        self.bind("s", self._hotkey(lambda: self.on_toggle_selected("solo")))
        # 音符编辑
        self.bind("<Delete>", self._hotkey(self.on_note_delete))
        self.bind("<Control-d>", self._hotkey(self.on_note_duplicate))
        self.bind("<Control-Left>", self._hotkey(lambda: self.on_note_step(-1)))
        self.bind("<Control-Right>", self._hotkey(lambda: self.on_note_step(1)))

    def _typing(self) -> bool:
        """焦点在输入框里时，单键快捷键要让位给打字。"""
        widget = self.focus_get()
        return widget is not None and widget.winfo_class() in (
            "Entry", "TEntry", "Text", "Spinbox", "TCombobox"
        )

    def _hotkey(self, action):
        def handler(_event=None):
            if self._typing():
                return None
            action()
            return "break"

        return handler

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

    def _dock_by_drag(self, key: str, area: str) -> None:
        """拖拽停靠落地后的回调：停靠、刷新菜单、在状态栏说一声。"""
        title = self.dock_manager.titles.get(key, key)
        self.dock_panel(key, area)
        self.status.set_message(f"「{title}」已停靠到{AREA_NAMES.get(area, area)}")

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
        self._poll_timer = self.after(90, self._poll_queue)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self._sync_toolbar()
        if busy:
            self.status.set_progress(0.0)
            self.status.set_message(message or "处理中…")
        else:
            has_result = self.result is not None
            self.transport.set_enabled(has_result)
            self.status.set_progress(None)

    def _sync_toolbar(self) -> None:
        """工具栏按钮的唯一状态来源：有没有工程、有没有结果、是否在忙、是否在播。"""
        if not hasattr(self, "toolbar"):
            return
        self.toolbar.set_state(
            has_project=self.project is not None,
            has_result=self.result is not None,
            busy=bool(self._busy),
            playing=bool(getattr(self.player, "playing", False)),
        )

    def _project_instruments(self) -> list[str]:
        """工程里实际用到的、需要下载采样的乐器（按注册顺序）。"""
        if self.project is None:
            return []
        used = {fetch.resolve(track.instrument) for track in self.project.tracks}
        return [key for key in fetch.SOURCES if key in used]

    def _project_all_instruments(self) -> list[str]:
        """工程里用到的全部乐器，含内置合成乐器（状态栏、音源摘要用）。"""
        if self.project is None:
            return []
        used = {fetch.resolve(track.instrument) for track in self.project.tracks}
        return [key for key in fetch.instrument_keys() if key in used]

    def _sample_line(self) -> str:
        keys = self._project_all_instruments()
        if not keys:
            return "音源：—"
        parts = []
        for key in keys:
            if key in fetch.BUILTIN:
                parts.append(f"{fetch.label(key)} 内置")
                continue
            have, want = fetch.instrument_status(key)
            parts.append(f"{fetch.label(key)} {have}/{want}")
        return "音源：" + " · ".join(parts)

    def _refresh_status_items(self) -> None:
        items = []
        keys = self._project_all_instruments() or list(fetch.instrument_keys())
        ready = 0
        for key in keys:
            if key in fetch.BUILTIN:
                ready += 1
                continue
            have, want = fetch.instrument_status(key)
            if want and have >= want:
                ready += 1
        items.append(f"音源 {ready}/{len(keys)} 就绪")
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

    def on_new_track(self) -> None:
        """菜单 / 工具栏 / 资源管理器右键 / Ctrl+Shift+T 的共用入口。"""
        if self._busy or self.project is None:
            return
        dialog = NewTrackDialog(self, len(self.project.tracks))
        self.wait_window(dialog)
        if dialog.result:
            self._create_track(dialog.result)

    def _create_track(self, info: dict) -> bool:
        """真正写入工程（与对话框解耦，方便自动化测试）。"""
        if self.project is None:
            return False
        template = info.get("template", "blank")
        notes: list[tuple[float, float, list[int], float]] = []
        loop = False
        if template == "note":
            notes = [(0.0, 1.0, [60], 0.75)]
        elif template == "loop":
            loop = True
            notes = [
                (0.0, 3.5, [48, 55], 0.45),
                (0.0, 0.4, [60], 0.35),
                (1.0, 0.4, [60], 0.30),
                (2.0, 0.4, [60], 0.32),
                (3.0, 0.4, [62], 0.28),
            ]
        try:
            ref = trackedit.add_track(
                self.project.path,
                name=info["name"],
                instrument=info.get("instrument") or "piano",
                notes=notes,
                loop=loop,
                loop_len=4.0 if loop else None,
            )
        except trackedit.TrackEditError as exc:
            messagebox.showerror("新建轨道失败", str(exc))
            self.log(f"新建轨道失败：{exc}", "error")
            return False

        self.open_project(str(self.project.path))
        self.select_track(ref.index - 1)
        self.status.set_message(f"已新增音轨 {ref.index}. {ref.name}")
        self.log(f"新建轨道 {ref.index}. {ref.name}（{fetch.display(ref.instrument)}）"
                 f"→ {ref.relative}", "ok")
        return True

    # ---------------------------------------------------------------- 音轨管理
    def on_delete_track(self) -> None:
        """删除选中音轨：菜单 / 工具栏 / 右键 / Ctrl+Shift+Delete。"""
        track = self._current_track()
        if track is None or self._busy:
            return
        if len(self.project.tracks) <= 1:
            messagebox.showinfo("无法删除", "工程至少要保留一条音轨。")
            return
        if not messagebox.askyesno(
            "删除音轨",
            f"从工程里移除「{track.name}」？\n\n"
            "音轨文件会保留（可用 CLI 的 track add --file 加回来），此操作只改工程文件。",
        ):
            return
        self._delete_track(self.selected)

    def _delete_track(self, index: int, delete_file: bool = False) -> bool:
        """真正执行删除（与确认对话框解耦，便于测试）。"""
        if self.project is None:
            return False
        try:
            name, relative, deleted = trackedit.remove_track(
                self.project.path, index + 1, delete_file=delete_file
            )
        except trackedit.TrackEditError as exc:
            messagebox.showerror("删除失败", str(exc))
            self.log(f"删除音轨失败：{exc}", "error")
            return False
        self.open_project(str(self.project.path))
        self.select_track(max(0, min(index, len(self.project.tracks) - 1)))
        self.status.set_message(f"已删除音轨 {name}")
        self.log(f"删除音轨 {name}（{relative}）"
                 + ("，文件已删除" if deleted else "，文件保留"), "ok")
        return True

    def on_view_track(self) -> None:
        """打开（或激活）选中音轨的独立窗口。"""
        track = self._current_track()
        if track is None:
            return
        index = self.selected
        window = self._track_windows.get(index)
        if window is not None:
            try:
                if window.winfo_exists():
                    window.lift()
                    window.focus_set()
                    return
            except tk.TclError:
                pass
            self._track_windows.pop(index, None)
        self._track_windows[index] = TrackWindow(self, self, index)
        self.log(f"单独查看音轨 {index + 1}. {track.name}", "ok")

    def on_track_window_closed(self, index: int) -> None:
        self._track_windows.pop(index, None)

    def _close_track_windows(self) -> None:
        for window in list(self._track_windows.values()):
            try:
                window.close()
            except tk.TclError:
                pass
        self._track_windows.clear()

    def set_only_selected(self, enabled: bool) -> None:
        """钢琴卷帘只看选中轨（False = 恢复显示所有轨，非选中轨变暗）。"""
        self.only_selected_track = bool(enabled)
        self.roll.set_only_selected(self.only_selected_track)
        self._sync_isolate_ui()

    def on_toggle_isolate(self) -> None:
        if self.project is None:
            return
        self.set_only_selected(not self.only_selected_track)
        self.log("卷帘只看选中音轨" if self.only_selected_track else "卷帘恢复显示全部音轨")

    def _sync_isolate_ui(self) -> None:
        on = bool(self.only_selected_track)
        var = getattr(self, "_isolate_var", None)
        if var is not None:
            var.set(on)
        if hasattr(self, "toolbar"):
            self.toolbar.set_isolate(on)
        for window in self._track_windows.values():
            window._sync_isolate_button()
        if on and self.project is not None and 0 <= self.selected < len(self.project.tracks):
            self.status.set_message(
                f"只看：{self.project.tracks[self.selected].name}（Ctrl+Shift+I 恢复）"
            )

    def focus_track_note(self, track_index: int, note_index: int) -> None:
        """音轨窗口点表格行：切到卷帘、选中该音符并把视图滚过去。"""
        self.goto_page("roll")
        self.select_note(track_index, note_index)
        self.roll.focus_note(track_index, note_index)

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
        self._close_track_windows()
        self.project = project
        self.selected = 0 if project.tracks else -1
        self.note_sel = None
        self.result = None
        self.player.unload()
        self.transport.set_enabled(False)
        self.transport.update_time(0.0, 0.0)
        self.wave.clear()
        self.status.set_progress(None)
        self.title(f"{project.path.name} — MusicGen")
        self._refresh_toolbar_info()
        self._sync_toolbar()

        self.roll.set_project(project, self.selected)
        self.roll.set_only_selected(self.only_selected_track)
        self.info.set_project(project)
        self._refresh_explorer()
        self._select_ui(self.selected)
        self._refresh_status_items()
        self._sync_isolate_ui()
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
        key = fetch.resolve(track.instrument)
        if key in fetch.SOURCES:
            have, want = fetch.instrument_status(key)
            if have < want:
                return "（采样不全）"
        return ""

    def select_track(self, index: int) -> None:
        if self.project is None or not (0 <= index < len(self.project.tracks)):
            return
        if index != self.selected:
            self.note_sel = None
            self.roll.set_selected_note(-1, -1)
        self.selected = index
        explorer = self.dock_manager.panel("explorer")
        if explorer is not None:
            explorer.select_track(index)
        self._select_ui(index)
        for window in self._track_windows.values():
            try:
                window._sync_isolate_button()
            except tk.TclError:  # 窗口刚被关闭
                pass
        if self.only_selected_track:
            self._sync_isolate_ui()

    def _select_ui(self, index: int) -> None:
        self.roll.set_selected(index)
        self.roll.schedule_redraw()
        props = self.dock_manager.panel("properties")
        if props is not None:
            track = self._current_track()
            props.set_track(track, "" if track is None else self._instrument_note(track))
        self._refresh_note_panel()

    # ---------------------------------------------------------------- 音符
    def _note_panel(self):
        manager = getattr(self, "dock_manager", None)
        return None if manager is None else manager.panel("note")

    def _refresh_note_panel(self) -> None:
        panel = self._note_panel()
        if panel is None:
            return
        if self.project is None or self.note_sel is None:
            panel.set_note(None, None, -1)
            return
        track_index, note_index = self.note_sel
        if not (0 <= track_index < len(self.project.tracks)):
            panel.set_note(None, None, -1)
            return
        panel.set_note(self.project, self.project.tracks[track_index], note_index)

    def select_note(self, track_index: int, note_index: int) -> None:
        """钢琴卷帘里点中某个音符（``-1`` 表示点了空白）。"""
        if self.project is None:
            return
        if track_index < 0 or note_index < 0:
            self.note_sel = None
            self.roll.set_selected_note(-1, -1)
            self._refresh_note_panel()
            return
        if not (0 <= track_index < len(self.project.tracks)):
            return
        track = self.project.tracks[track_index]
        if not (0 <= note_index < len(track.notes)):
            return
        self.selected = track_index
        self.note_sel = (track_index, note_index)
        explorer = self.dock_manager.panel("explorer")
        if explorer is not None:
            explorer.select_track(track_index)
        self._select_ui(track_index)
        self.roll.set_selected_note(track_index, note_index)
        self._refresh_note_panel()
        self.status.set_message(self._note_line(track, note_index))

    @staticmethod
    def _note_line(track, note_index: int) -> str:
        note = track.notes[note_index]
        return (f"{track.name} · 第 {note_index + 1} 个音符 · "
                f"{note.time:g}→{note.time + note.dur:g} 拍 · 力度 {note.velocity:.2f}")

    def on_note_apply(self, values: dict) -> None:
        if self.project is None or self.note_sel is None:
            return
        track_index, note_index = self.note_sel
        track = self.project.tracks[track_index]
        if not (0 <= note_index < len(track.notes)):
            return
        note = track.notes[note_index]
        try:
            time = float(values["time"])
            dur = float(values["dur"])
        except ValueError:
            messagebox.showerror("参数错误", "起始 / 时长必须是数字（单位：拍）")
            return
        if time < 0 or dur <= 0:
            messagebox.showerror("参数错误", "起始不能为负，时长必须大于 0。")
            return
        try:
            pitches = parse_pitches(values["pitch"])
        except PitchError as exc:
            messagebox.showerror("音高有误", str(exc))
            return
        note.time = time
        note.dur = dur
        note.pitches = pitches
        note.velocity = max(0.0, min(1.0, float(values["velocity"])))
        self._save_track(track, f"更新音符：{self._note_line(track, note_index)}")
        self.roll.set_selected_note(track_index, note_index)
        self._refresh_note_panel()

    def on_note_delete(self) -> None:
        if self.project is None or self.note_sel is None:
            return
        track_index, note_index = self.note_sel
        track = self.project.tracks[track_index]
        if not (0 <= note_index < len(track.notes)):
            return
        removed = self._note_line(track, note_index)
        track.notes.pop(note_index)
        if track.notes:
            self.note_sel = (track_index, min(note_index, len(track.notes) - 1))
        else:
            self.note_sel = None
        self._save_track(track, f"删除音符：{removed}", level="warn")
        self._after_note_change(track_index)

    def on_note_duplicate(self) -> None:
        if self.project is None or self.note_sel is None:
            return
        track_index, note_index = self.note_sel
        track = self.project.tracks[track_index]
        if not (0 <= note_index < len(track.notes)):
            return
        source = track.notes[note_index]
        clone = Note(time=source.time + source.dur, dur=source.dur,
                     pitches=list(source.pitches), velocity=source.velocity)
        track.notes.insert(note_index + 1, clone)
        self.note_sel = (track_index, note_index + 1)
        self._save_track(track, f"复制音符：{self._note_line(track, note_index + 1)}")
        self._after_note_change(track_index)

    def on_note_step(self, delta: int) -> None:
        if self.project is None or self.note_sel is None:
            return
        track_index, note_index = self.note_sel
        track = self.project.tracks[track_index]
        if not track.notes:
            return
        note_index = (note_index + delta) % len(track.notes)
        self.note_sel = (track_index, note_index)
        self.roll.set_selected_note(track_index, note_index)
        self._refresh_note_panel()
        self.status.set_message(self._note_line(track, note_index))

    def _after_note_change(self, track_index: int) -> None:
        """音符增删后同步卷帘、列表、属性与状态栏。"""
        self.roll.set_selected(track_index)
        self.roll.set_selected_note(*(self.note_sel or (-1, -1)))
        self._select_ui(track_index)
        self._refresh_explorer()

    def _refresh_toolbar_info(self) -> None:
        if self.project is None:
            return
        self.toolbar.set_project_info(
            f"{self.project.name}  ·  {self.project.bpm:g} BPM  ·  "
            f"{len(self.project.tracks)} 音轨  ·  "
            f"{_fmt_time(self.project.compute_length_seconds())}"
        )

    def _save_track(self, track, message: str, level: str = "ok") -> bool:
        """把一条音轨写回 XML，并同步界面上的派生信息。"""
        try:
            path = save_track(track)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        changed = clear_track_overrides(self.project.path, track.path)
        self._refresh_explorer()
        self.roll.schedule_redraw()
        self.info.set_project(self.project)
        self._refresh_status_items()
        self._refresh_toolbar_info()
        self.status.set_message(f"已保存 {path.name}")
        self.log(message + ("，并清除了工程层覆盖" if changed else ""), level)
        return True

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
        new_instrument = fetch.resolve(values.get("instrument") or track.instrument)
        instrument_changed = new_instrument != fetch.resolve(track.instrument)
        track.instrument = new_instrument
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
        name = track.path.name if track.path else track.name
        self._save_track(
            track,
            f"保存音轨 {name}（{fetch.display(track.instrument)}"
            + (" · 乐器已更换" if instrument_changed else "")
            + f" / 音量 {track.gain_db:+.1f}dB / 声像 {track.pan:+.2f} / "
            f"混响 {track.reverb:.2f}{' / 循环' if track.loop else ''}）",
        )
        if instrument_changed:
            have, want = fetch.instrument_status(new_instrument)
            if new_instrument in fetch.SOURCES and have < want:
                self.log(
                    f"{fetch.label(new_instrument)}的采样还没下全（{have}/{want}），"
                    f"渲染时会用合成器顶替；点「音源」下载后音色才正确。",
                    "warn",
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
        self._sync_toolbar()
        self.status.set_message("预览中…")
        self._start_tick()

    def _pause_playback(self) -> None:
        self.player.pause()
        self.transport.set_playing(False)
        self._sync_toolbar()
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
        self._sync_toolbar()
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
            self._sync_toolbar()
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
        wanted = self._project_instruments() or [key for key in fetch.SOURCES]
        missing = [
            key for key in wanted
            if fetch.instrument_status(key)[0] < fetch.instrument_status(key)[1]
        ]
        if not missing:
            names = "、".join(fetch.label(key) for key in wanted)
            if not messagebox.askyesno(
                "音源已就绪", f"{names} 的采样都已缓存。\n要全部重新下载吗？"
            ):
                return
            targets = wanted
        else:
            targets = missing

        self._set_busy(True, "正在下载音源…")
        self.log(
            "开始下载音源（FluidR3 GM）："
            + "、".join(f"{fetch.label(key)} {fetch.instrument_status(key)[0]}/"
                        f"{fetch.instrument_status(key)[1]}" for key in targets)
        )

        def worker() -> None:
            reports = []
            count = max(1, len(targets))
            try:
                for index, key in enumerate(targets):
                    label = fetch.label(key)

                    def progress(done: int, total: int, note: str, i=index, name=label) -> None:
                        fraction = (i + done / max(total, 1)) / count
                        self._queue.put(("progress", (fraction, f"下载{name} {done}/{total} · {note}")))

                    reports.append(fetch.fetch_instrument(key, progress=progress))
                self._queue.put(("samples_done", reports))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", f"音源下载失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_samples_done(self, reports) -> None:
        self._set_busy(False)
        self.status.set_progress(1.0)
        total = sum(len(r.downloaded) + len(r.skipped) for r in reports)
        self.status.set_message(f"音源就绪：{total} 个采样")
        self.log(f"音源就绪：{len(reports)} 件乐器 · {total} 个采样", "ok")
        failed = [r for r in reports if r.failed]
        for report in reports:
            self.log(report.summary(), "ok" if report.ok else "warn")
        self._refresh_status_items()
        if self.project is not None:
            self._select_ui(self.selected)
            self._refresh_explorer()
        if failed:
            messagebox.showwarning("部分采样失败", "\n\n".join(r.summary() for r in failed))

    # ---------------------------------------------------------------- 布局持久化
    def _schedule_save_layout(self) -> None:
        if self._save_timer is not None:
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(700, self.save_layout)

    def _collect_layout(self) -> dict:
        manager = self.dock_manager
        sizes = dict(manager.sizes)
        for area in ("left", "right"):
            if manager.has_pane(manager.paned[area], manager.containers[area]):
                measured = manager.containers[area].winfo_width()
                if measured > 40:
                    sizes[area] = measured
        if manager.has_pane(manager.paned["bottom"], manager.containers["bottom"]):
            measured = manager.containers["bottom"].winfo_height()
            if measured > 40:
                sizes["bottom"] = measured
        sashes: dict = {"main": [], "areas": {}}
        if len(self.hsplit.panes()) >= 2:
            sashes["main"].append(list(self.hsplit.sash_coord(0)))
        if len(self.center_host.panes()) >= 2:
            sashes["main"].append(list(self.center_host.sash_coord(0)))
        for area, container in manager.containers.items():
            count = len(container.panes())
            if count >= 2:
                sashes["areas"][area] = [list(container.sash_coord(i)) for i in range(count - 1)]
        return {
            "geometry": self.geometry(),
            "page": self.page,
            "docks": manager.state(),
            "sizes": sizes,
            "sashes": sashes,
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
        self._close_track_windows()
        self.player.pause()
        try:
            self.save_layout()
        finally:
            self.destroy()

    def destroy(self) -> None:
        """退出前取消所有定时器，避免 Tk 报 "after script" 残留。"""
        for attr in ("_poll_timer", "_play_timer", "_save_timer"):
            timer = getattr(self, attr, None)
            if timer:
                try:
                    self.after_cancel(timer)
                except Exception:  # noqa: BLE001 - 已销毁时忽略
                    pass
                setattr(self, attr, None)
        super().destroy()


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
