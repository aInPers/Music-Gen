"""「音轨」独立窗口：单独查看一条音轨。

三件事合一：
* 表格列出该轨全部音符（点一行跳到钢琴卷帘并高亮）；
* 把这一轨**单独渲染**成音频，用窗口自带的播放器试听（不影响主预览）；
* 把分轨音频导出成 WAV。

渲染在后台线程，结果通过队列回主线程；窗口关闭时会停掉播放器与所有定时器。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from ..core import trackedit
from ..core.instruments import note_names
from ..core.render import RenderResult, render_project
from ..core.wavio import write_wav
from ..samples import fetch
from . import theme, widgets
from .player import AudioPlayer, PlaybackUnavailable

T = theme.PALETTE


class TrackWindow(tk.Toplevel):
    def __init__(self, master: tk.Misc, app, track_index: int) -> None:
        super().__init__(master)
        self.app = app
        self.track_index = track_index
        self.result: RenderResult | None = None
        self.player = AudioPlayer()
        self._queue: queue.Queue = queue.Queue()
        self._rendering = False
        self._pending: str | None = None
        self._export_path: Path | None = None
        self._poll_after: str | None = None
        self._tick_after: str | None = None

        self.title("音轨")
        self.configure(bg=T["panel"])
        self.geometry("760x520")
        self.minsize(560, 360)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._fill()
        theme.enable_dark_titlebar(self)

    # ---- 构建 ---------------------------------------------------------
    def _build(self) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.title_var = tk.StringVar(value="音轨")
        tk.Label(header, textvariable=self.title_var, bg=T["chrome"], fg=T["text"],
                 font=theme.font(12, bold=True)).pack(side="left", padx=16)
        self.subtitle_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self.subtitle_var, bg=T["chrome"],
                 fg=T["text_faint"], font=theme.font(8)).pack(side="left", padx=(10, 0))
        widgets.Separator(self).pack(fill="x")

        info = tk.Frame(self, bg=T["panel"])
        info.pack(fill="x", padx=14, pady=(10, 6))
        self.info_var = tk.StringVar(value="")
        tk.Label(info, textvariable=self.info_var, bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(9), anchor="w", justify="left").pack(fill="x")

        table = tk.Frame(self, bg=T["panel"])
        table.pack(fill="both", expand=True, padx=14, pady=4)
        columns = ("idx", "bar", "time", "dur", "pitch", "vel")
        self.tree = ttk.Treeview(table, style="VS.Treeview", columns=columns,
                                 show="headings", selectmode="browse")
        for key, label, width, anchor in (
            ("idx", "#", 46, "center"),
            ("bar", "小节·拍", 92, "center"),
            ("time", "起始", 80, "center"),
            ("dur", "时长", 80, "center"),
            ("pitch", "音高", 200, "w"),
            ("vel", "力度", 70, "center"),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == "pitch"))
        scroll = ttk.Scrollbar(table, orient="vertical", style="VS.Vertical.TScrollbar",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Double-1>", self._on_row_activate)

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=56)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        self.status_var = tk.StringVar(value="单行点击可跳到钢琴卷帘里的对应音符")
        tk.Label(footer, textvariable=self.status_var, bg=T["panel"], fg=T["text_dim"],
                 font=theme.font(8), anchor="w").pack(side="top", fill="x", padx=14, pady=(4, 0))
        bar = tk.Frame(footer, bg=T["panel"])
        bar.pack(fill="x", padx=12, pady=(4, 6))
        self.btn_isolate = self._button(bar, "只看这一轨", self._toggle_isolate)
        self.btn_play = self._button(bar, "▶ 单独试听", self._on_play)
        self.btn_export = self._button(bar, "导出分轨 WAV…", self._on_export)
        self._button(bar, "关闭", self._close).pack(side="right", padx=(6, 2))
        self.btn_isolate.pack(side="left", padx=2)
        self.btn_play.pack(side="left", padx=2)
        self.btn_export.pack(side="left", padx=2)
        self._sync_isolate_button()

    def _button(self, parent: tk.Misc, text: str,
                command: Callable[[], None]) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=T["chrome_hi"],
                         fg=T["text"], activebackground=T["accent"],
                         activeforeground="#FFFFFF", relief="flat", bd=0,
                         font=theme.font(9), padx=12, pady=4, cursor="hand2")

    # ---- 数据 ---------------------------------------------------------
    def _track(self):
        project = self.app.project
        if project is None or not 0 <= self.track_index < len(project.tracks):
            return None, None
        return project, project.tracks[self.track_index]

    def _fill(self) -> None:
        project, track = self._track()
        if track is None:
            self.title_var.set("音轨已不存在")
            self.status_var.set("工程里的这条音轨已被删除或工程已切换，请关闭窗口。")
            return
        self.title(f"音轨 · {track.name}")
        self.title_var.set(track.name)
        self.subtitle_var.set(fetch.display(track.instrument))
        beats = project.beats_per_bar
        flags = []
        if track.loop:
            flags.append("循环")
        if track.mute:
            flags.append("静音")
        if track.solo:
            flags.append("独奏")
        self.info_var.set(
            f"音符 {len(track.notes)}  ·  {track.gain_db:+g}dB  ·  pan {track.pan:+.2f}  ·  "
            f"混响 {track.reverb:.2f}  ·  {project.bpm:g}BPM {beats}/4"
            + (f"  ·  [{'、'.join(flags)}]" if flags else "")
            + f"\n文件 {(track.path.name if track.path else '—')}"
        )

        self.tree.delete(*self.tree.get_children())
        for index, note in enumerate(track.notes, start=1):
            bar = int(note.time // beats) + 1
            beat = note.time % beats + 1
            self.tree.insert(
                "", "end", iid=str(index - 1),
                values=(index, f"{bar} : {beat:g}", f"{note.time:g}",
                        f"{note.dur:g}", note_names(track.instrument, note.pitches),
                        f"{note.velocity:g}"),
            )

    def _on_row_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            note_index = int(selection[0])
        except ValueError:
            return
        self.app.focus_track_note(self.track_index, note_index)

    def _on_row_activate(self, _event=None) -> None:
        self._on_row_select()
        self.app.goto_page("roll")

    # ---- 视图开关 -----------------------------------------------------
    def _sync_isolate_button(self) -> None:
        on = bool(getattr(self.app, "only_selected_track", False)) and \
            self.app.selected == self.track_index
        self.btn_isolate.configure(text="✓ 只看这一轨" if on else "只看这一轨")

    def _toggle_isolate(self) -> None:
        on = not (bool(getattr(self.app, "only_selected_track", False))
                  and self.app.selected == self.track_index)
        self.app.set_only_selected(on)
        if on:
            self.app.select_track(self.track_index)
        self._sync_isolate_button()
        self._set_status("钢琴卷帘已切换为只看这一轨" if on else "钢琴卷帘已恢复显示全部音轨")

    # ---- 单独渲染 / 试听 / 导出 ---------------------------------------
    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _ensure_rendered(self, action: str) -> bool:
        if self._rendering:
            self._set_status("正在单独渲染，请稍候…")
            return False
        if self.result is not None:
            return True
        project, _ = self._track()
        if project is None:
            return False
        try:
            _, isolated = trackedit.isolate_project(project, self.track_index)
        except trackedit.TrackEditError as exc:
            self._set_status(str(exc))
            return False
        self._pending = action
        self._rendering = True
        self._set_status("正在单独渲染这条音轨…")

        def worker() -> None:
            try:
                self._queue.put(("ok", render_project(isolated)))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("err", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()
        self._poll_render()
        return False

    def _poll_render(self) -> None:
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            self._poll_after = self.after(80, self._poll_render)
            return
        self._poll_after = None
        self._rendering = False
        if kind == "err":
            self._pending = None
            self._set_status("单独渲染失败")
            messagebox.showerror("渲染失败", payload, parent=self)
            return
        self.result = payload
        summary = (f"分轨已渲染 · {payload.duration:.2f}s · 峰值 {payload.peak_db:+.1f}dBFS · "
                   f"{payload.notes_rendered} 个音符")
        self._set_status(summary)
        self.app.log(f"单独渲染 {self.title_var.get()}：{summary}", "ok")
        action, self._pending = self._pending, None
        if action == "play":
            self._play()
        elif action == "export":
            self._write_export()

    def _on_play(self) -> None:
        if not self._ensure_rendered("play"):
            return
        self._play()

    def _play(self) -> None:
        if self.result is None:
            return
        if not self.player.has_audio:
            self.player.load(self.result.audio, self.result.sample_rate)
        if self.player.playing:
            self.player.pause()
            self.btn_play.configure(text="▶ 继续")
            self._stop_tick()
            return
        if self.player.position >= self.player.duration - 0.02:
            self.player.seek(0.0)
        try:
            self.player.play()
        except PlaybackUnavailable as exc:
            messagebox.showwarning("无法试听", f"声卡不可用：{exc}\n可以先导出分轨 WAV。",
                                   parent=self)
            return
        self.btn_play.configure(text="⏸ 暂停")
        self._tick()

    def _tick(self) -> None:
        if not self.player.playing:
            self.btn_play.configure(text="▶ 单独试听")
            self._tick_after = None
            return
        self._set_status(f"试听中 {self.player.position:.1f} / {self.player.duration:.1f}s")
        self._tick_after = self.after(200, self._tick)

    def _stop_tick(self) -> None:
        if self._tick_after is not None:
            try:
                self.after_cancel(self._tick_after)
            except tk.TclError:
                pass
            self._tick_after = None

    def _on_export(self) -> None:
        if self.result is None:
            project, track = self._track()
            if track is None:
                return
            chosen = filedialog.asksaveasfilename(
                parent=self, title="导出分轨 WAV", defaultextension=".wav",
                filetypes=[("WAV 音频", "*.wav")],
                initialfile=f"{track.name}.wav",
            )
            if not chosen:
                return
            self._export_path = Path(chosen)
            self._ensure_rendered("export")
            return
        self._write_export()

    def _write_export(self) -> None:
        if self.result is None or self._export_path is None:
            return
        try:
            write_wav(self._export_path, self.result.audio, self.result.sample_rate,
                      bit_depth=24)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self._set_status(f"已导出 {self._export_path.name}")
        self.app.log(f"导出分轨 WAV：{self._export_path}", "ok")

    # ---- 收尾 ---------------------------------------------------------
    def close(self) -> None:
        """供主窗口在工程切换 / 退出时调用（会停掉播放器与定时器）。"""
        self._close()

    def _close(self) -> None:
        if self._poll_after is not None:
            try:
                self.after_cancel(self._poll_after)
            except tk.TclError:
                pass
        self._stop_tick()
        try:
            self.player.stop()
            self.player.unload()
        except Exception:  # noqa: BLE001 - 关窗时设备异常不影响退出
            pass
        self.app.on_track_window_closed(self.track_index)
        self.destroy()
