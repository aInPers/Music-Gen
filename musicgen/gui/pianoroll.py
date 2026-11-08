"""文档页：钢琴卷帘（Piano Roll）。

横轴小节/拍，纵轴音高；左侧画真实钢琴键，右侧/底部有滚动条。

交互（和常见 DAW 一致）::

    滚轮             上下滚动（音高方向）
    Alt + 滚轮       左右滚动（时间方向）
    Ctrl + 滚轮      横向缩放：改每个音符的显示宽度
    Shift + 滚轮     纵向缩放：改每个音符的显示高度
    中键拖动          两轴同时平移
    单击音符          选中该音符（右栏「音符」窗口可查看/修改属性）
    单击标尺          复位视图
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from ..core.drums import pad_label
from ..core.instruments import note_names
from ..core.notes import midi_to_name
from ..samples import fetch
from . import theme

T = theme.PALETTE


BLACK_KEYS = (1, 3, 6, 8, 10)

ZOOM_MIN, ZOOM_MAX = 1.0, 64.0        # 横向缩放（1 = 整段铺满）
SPAN_MIN, SPAN_MAX = 6.0, 60.0        # 纵向可见半音数
DEFAULT_SPAN = 30.0                   # 默认可见 2.5 个八度，留出上下滚动的空间


class PianoRollPage(tk.Frame):
    RULER_H = 22
    KEY_W = 46  # 左侧钢琴键宽度

    def __init__(self, master: tk.Misc, on_pick_track, on_pick_note=None) -> None:
        super().__init__(master, bg=T["editor"])
        self.on_pick_track = on_pick_track
        self.on_pick_note = on_pick_note or (lambda _ti, _ni: None)
        self.project = None
        self.selected = -1
        self.selected_note: tuple[int, int] | None = None
        self.only_selected = False     # True = 只画选中轨，其它轨完全隐藏
        self.zoom = 1.0
        self.scroll_beats = 0.0
        self.pitch_span = DEFAULT_SPAN
        self.pitch_top = 84.0          # 视图顶端对应的 MIDI 音高
        self.playhead: float | None = None
        self._pending: str | None = None
        self._pan: tuple[int, int, float, float] | None = None

        self.canvas = tk.Canvas(self, bg=T["editor"], highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", style="VS.Vertical.TScrollbar",
                                  command=self._on_vscroll)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", style="VS.Horizontal.TScrollbar",
                                  command=self._on_hscroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        c = self.canvas
        c.bind("<Configure>", lambda _e: self.schedule_redraw())
        c.bind("<Button-1>", self._on_click)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Alt-MouseWheel>", self._on_alt_wheel)
        c.bind("<Control-MouseWheel>", self._on_zoom)
        c.bind("<Shift-MouseWheel>", self._on_span_zoom)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda _e: c.delete("hint"))
        c.bind("<Button-2>", self._on_pan_start)
        c.bind("<B2-Motion>", self._on_pan_move)
        c.bind("<ButtonRelease-2>", lambda _e: setattr(self, "_pan", None))

    # ---- 数据 ---------------------------------------------------------
    def set_project(self, project, selected: int = -1) -> None:
        self.project = project
        self.selected = selected
        self.selected_note = None
        self._reset_pitch_view()
        self.reset_view()
        self._update_scrollbars()

    def set_selected(self, index: int) -> None:
        self.selected = index
        self.schedule_redraw()

    def set_only_selected(self, enabled: bool) -> None:
        """只看选中轨：隐藏其它轨的音符与循环条（区别于默认的「变暗」）。"""
        self.only_selected = bool(enabled)
        self.schedule_redraw()

    def set_selected_note(self, track_index: int, note_index: int) -> None:
        """高亮某个音符（``-1`` 表示取消）。"""
        if track_index < 0 or note_index < 0:
            self.selected_note = None
        else:
            self.selected_note = (track_index, note_index)
        self.schedule_redraw()

    def focus_note(self, track_index: int, note_index: int) -> None:
        """把视图滚到某个音符附近并高亮它（供音轨窗口的表格点行跳转）。"""
        project = self.project
        if (project is None or not 0 <= track_index < len(project.tracks)):
            return
        notes = project.tracks[track_index].notes
        if not 0 <= note_index < len(notes):
            return
        note = notes[note_index]
        self.selected = track_index
        self.selected_note = (track_index, note_index)
        visible = self.visible_beats()
        self.scroll_beats = max(0.0, note.time - visible * 0.25)
        if note.pitches:
            # pitch_top 是视图顶端音高，让音符落在视图中部
            self.pitch_top = max(0.0, min(127.0,
                                          float(max(note.pitches)) + self.pitch_span * 0.35))
        self._update_scrollbars()
        self.schedule_redraw()

    def set_playhead(self, seconds: float | None, follow: bool = True) -> None:
        """更新播放头（秒）。只重画那一条线，不整体重绘。"""
        if seconds is not None and follow and self.project is not None:
            visible = self.visible_beats()
            beat = seconds * self.project.bpm / 60.0
            if visible > 0 and (beat < self.scroll_beats or beat > self.scroll_beats + visible * 0.98):
                self.scroll_beats = beat - visible * 0.15
                self._clamp_h()
                self.schedule_redraw()
                self.playhead = seconds
                return
        self.playhead = seconds
        self._draw_playhead()

    # ---- 视图状态 -----------------------------------------------------
    def reset_view(self) -> None:
        self.zoom = 1.0
        self.scroll_beats = 0.0
        self._reset_pitch_view()
        self.schedule_redraw()

    def _reset_pitch_view(self) -> None:
        low, high = self._data_range()
        span = min(max(float(high - low + 1), 12.0), DEFAULT_SPAN)
        self.pitch_span = span
        self.pitch_top = min(127.0, (low + high) / 2 + span / 2)
        self._clamp_v()

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

    def _data_range(self) -> tuple[int, int]:
        """工程里实际用到的音高范围（带一点余量）。"""
        pitches = [p for t in (self.project.tracks if self.project else ())
                   for n in t.notes for p in n.pitches]
        low = min(pitches) if pitches else 48
        high = max(pitches) if pitches else 84
        low = max(0, low - 1)
        high = min(127, high + 1)
        if high - low < 8:
            grow = (8 - (high - low)) / 2
            low = max(0, int(low - grow))
            high = min(127, int(high + grow))
        return low, high

    def _row_h(self) -> float:
        height = max(self.canvas.winfo_height(), 80)
        return max(3.0, (height - self.RULER_H - 6) / max(1.0, self.pitch_span))

    def _pixels_per_beat(self) -> float:
        width = max(self.canvas.winfo_width(), 80)
        return max(0.5, (width - self.KEY_W - 12) / max(1.0, self.total_beats()) * self.zoom)

    def visible_beats(self) -> float:
        width = max(self.canvas.winfo_width(), 80)
        return max(0.5, (width - self.KEY_W - 12) / self._pixels_per_beat())

    def _beat_at(self, x: float) -> float:
        return max(0.0, (x - self.KEY_W) / self._pixels_per_beat() + self.scroll_beats)

    def _pitch_at(self, y: float) -> float:
        row_h = self._row_h()
        return self.pitch_top - (y - self.RULER_H) / max(row_h, 1e-6) + 0.5

    # ---- 滚动 / 缩放 ---------------------------------------------------
    @staticmethod
    def _steps(event: tk.Event) -> float:
        """把滚轮事件换算成「格」数（向上为正）。"""
        delta = getattr(event, "delta", 0)
        if delta == 0:  # X11 的 Button-4 / Button-5
            delta = 120 if getattr(event, "num", 0) == 4 else -120
        return delta / 120.0

    def _clamp_h(self) -> None:
        total = max(1.0, self.total_beats())
        limit = max(0.0, total - self.visible_beats())
        self.scroll_beats = max(0.0, min(self.scroll_beats, limit))

    def _clamp_v(self) -> None:
        low, high = self._data_range()
        span = min(self.pitch_span, float(high - low + 1))
        # 视图上端不高于最高音、下端不低于最低音
        self.pitch_top = max(float(low) + span - 1.0, min(float(high), self.pitch_top))

    def _scroll_beats_by(self, delta: float) -> None:
        self.scroll_beats += delta
        self._clamp_h()
        self.schedule_redraw()

    def _scroll_pitch_by(self, delta: float) -> None:
        self.pitch_top += delta
        self._clamp_v()
        self.schedule_redraw()

    def _on_wheel(self, event: tk.Event) -> str:
        """滚轮：上下滚动音高。"""
        self._scroll_pitch_by(self._steps(event) * max(1.0, round(self.pitch_span / 12)))
        return "break"

    def _on_alt_wheel(self, event: tk.Event) -> str:
        """Alt + 滚轮：左右滚动时间轴。"""
        self._scroll_beats_by(-self._steps(event) * max(0.25, self.visible_beats() * 0.1))
        return "break"

    def _on_zoom(self, event: tk.Event) -> str:
        """Ctrl + 滚轮：横向缩放（每个音符显示多宽），以鼠标位置为锚点。"""
        factor = 1.25 if self._steps(event) > 0 else 1 / 1.25
        new_zoom = min(ZOOM_MAX, max(ZOOM_MIN, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-6:
            return "break"
        x = max(event.x, self.KEY_W + 1)
        anchor = self._beat_at(x)
        self.zoom = new_zoom
        self.scroll_beats = anchor - (x - self.KEY_W) / self._pixels_per_beat()
        self._clamp_h()
        self.schedule_redraw()
        return "break"

    def _on_span_zoom(self, event: tk.Event) -> str:
        """Shift + 滚轮：纵向缩放（每个音符显示多高），以鼠标位置为锚点。"""
        anchor = self._pitch_at(event.y)
        ratio = (event.y - self.RULER_H) / max(1.0, self.canvas.winfo_height() - self.RULER_H - 6)
        factor = 1 / 1.25 if self._steps(event) > 0 else 1.25
        self.pitch_span = min(SPAN_MAX, max(SPAN_MIN, self.pitch_span * factor))
        self.pitch_top = anchor + ratio * (self.pitch_span - 1.0)
        self._clamp_v()
        self.schedule_redraw()
        return "break"

    def _on_hscroll(self, *args) -> None:
        if not args:
            return
        if args[0] == "moveto":
            self.scroll_beats = float(args[1]) * max(1.0, self.total_beats())
        elif args[0] == "scroll":
            amount = int(args[1])
            unit = self.visible_beats() * (0.1 if args[2] == "units" else 0.9)
            self.scroll_beats += amount * unit
        self._clamp_h()
        self.schedule_redraw()

    def _on_vscroll(self, *args) -> None:
        if not args:
            return
        low, high = self._data_range()
        total = max(1.0, float(high - low + 1))
        if args[0] == "moveto":
            self.pitch_top = high - float(args[1]) * total
        elif args[0] == "scroll":
            amount = int(args[1])
            unit = 1.0 if args[2] == "units" else self.pitch_span * 0.9
            self.pitch_top -= amount * unit
        self._clamp_v()
        self.schedule_redraw()

    # ---- 中键拖动平移 --------------------------------------------------
    def _on_pan_start(self, event: tk.Event) -> None:
        self._pan = (event.x, event.y, self.scroll_beats, self.pitch_top)
        self.canvas.configure(cursor="fleur")

    def _on_pan_move(self, event: tk.Event) -> None:
        if self._pan is None:
            return
        x0, y0, beats0, top0 = self._pan
        self.scroll_beats = beats0 - (event.x - x0) / self._pixels_per_beat()
        self.pitch_top = top0 + (event.y - y0) / self._row_h()
        self._clamp_h()
        self._clamp_v()
        self.schedule_redraw()

    # ---- 鼠标 ---------------------------------------------------------
    def _note_tag_at(self, event: tk.Event) -> str | None:
        for item in self.canvas.find_overlapping(event.x - 1, event.y - 1,
                                                 event.x + 1, event.y + 1):
            for tag in self.canvas.gettags(item):
                if tag.startswith("n:"):
                    return tag
        return None

    def _on_click(self, event: tk.Event) -> None:
        tag = self._note_tag_at(event)
        if tag:
            _, track_index, note_index = tag.split(":")
            self.on_pick_note(int(track_index), int(note_index))
            return
        if event.y < self.RULER_H:
            self.reset_view()
            return
        self.on_pick_note(-1, -1)

    def _on_motion(self, event: tk.Event) -> None:
        if self.project is None:
            return
        tag = self._note_tag_at(event)
        self.canvas.configure(cursor="hand2" if tag else "arrow")
        self.canvas.delete("hint")
        if event.x <= self.KEY_W:
            return

        if tag:
            _, track_index, note_index = tag.split(":")
            track = self.project.tracks[int(track_index)]
            note = track.notes[int(note_index)]
            names = note_names(track.instrument, note.pitches)
            text = (f"{track.name} · {names} · {note.time:g}→{note.time + note.dur:g} 拍"
                    f" · 力度 {note.velocity:.2f}")
            color = T["text_dim"]
        else:
            beat = self._beat_at(event.x)
            bar = int(beat // max(1, self.project.beats_per_bar)) + 1
            text = f"第 {bar} 小节 · 第 {beat % self.project.beats_per_bar + 1:.2f} 拍"
            color = T["text_faint"]
        self.canvas.create_text(
            self.canvas.winfo_width() - 8, self.RULER_H + 8, anchor="ne", text=text,
            fill=color, font=theme.font(8), tags="hint",
        )

    # ---- 重绘调度 -----------------------------------------------------
    def schedule_redraw(self) -> None:
        if self._pending is None:
            self._pending = self.after(16, self._flush)

    def _flush(self) -> None:
        self._pending = None
        self.redraw()

    def _draw_playhead(self) -> None:
        c = self.canvas
        c.delete("playhead")
        if self.playhead is None or self.project is None:
            return
        ppb = self._pixels_per_beat()
        beat = self.playhead * self.project.bpm / 60.0
        x = self.KEY_W - self.scroll_beats * ppb + beat * ppb
        if not (self.KEY_W - 1 <= x <= c.winfo_width()):
            return
        c.create_line(x, self.RULER_H, x, c.winfo_height(), fill="#FFFFFF", width=1,
                      tags="playhead")
        c.create_polygon(x - 4, self.RULER_H, x + 4, self.RULER_H, x, self.RULER_H + 7,
                         fill="#FFFFFF", outline="", tags="playhead")

    def _update_scrollbars(self) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 80 or height < 60:
            return
        total = max(1.0, self.total_beats())
        visible = self.visible_beats()
        first = max(0.0, min(1.0, self.scroll_beats / total))
        self.hbar.set(first, min(1.0, first + visible / total))

        low, high = self._data_range()
        span_total = max(float(high - low + 1), self.pitch_span)
        top = max(0.0, min(1.0, (high - self.pitch_top) / span_total))
        self.vbar.set(top, min(1.0, top + self.pitch_span / span_total))

    # ---- 绘制 ---------------------------------------------------------
    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        width, height = c.winfo_width(), c.winfo_height()
        if width < 80 or height < 60:
            return
        if self.project is None:
            c.create_text(width / 2, height / 2, text="打开一个 .musicproj 工程开始",
                          fill=T["text_faint"], font=theme.font(11))
            return

        project = self.project
        self._clamp_v()
        top = self.RULER_H
        ppb = self._pixels_per_beat()
        row_h = self._row_h()
        low, high = self._data_range()
        bottom_pitch = self.pitch_top - self.pitch_span + 1
        first_pitch = max(0, int(math.floor(bottom_pitch)) - 1)
        last_pitch = min(127, int(math.ceil(self.pitch_top)) + 1)

        def y_of(pitch: float) -> float:
            return top + (self.pitch_top - pitch) * row_h + row_h / 2

        def x_of(beat: float) -> float:
            return self.KEY_W - self.scroll_beats * ppb + beat * ppb

        # 黑键行底色
        for pitch in range(first_pitch, last_pitch + 1):
            if pitch % 12 in BLACK_KEYS:
                y = top + (self.pitch_top - pitch) * row_h
                if y + row_h < top or y > height:
                    continue
                c.create_rectangle(self.KEY_W, y, width, y + row_h,
                                   fill=T["key_black"], outline="")

        # 左侧钢琴键
        c.create_rectangle(0, 0, self.KEY_W, height, fill=T["panel_alt"], outline="")
        for pitch in range(first_pitch, last_pitch + 1):
            y = top + (self.pitch_top - pitch) * row_h
            if y + row_h < top or y > height:
                continue
            if pitch % 12 in BLACK_KEYS:
                c.create_rectangle(0, y, self.KEY_W * 0.62, y + row_h,
                                   fill="#121214", outline="")
            if pitch % 12 == 0 and row_h > 5:
                c.create_line(0, y, self.KEY_W, y, fill=T["border_soft"])
                c.create_text(self.KEY_W - 4, y + row_h * 0.55, anchor="e",
                              text=f"C{pitch // 12 - 1}", fill=T["text_faint"],
                              font=theme.font(7))
        c.create_line(self.KEY_W, 0, self.KEY_W, height, fill=T["border"])

        # 网格
        bpb = max(1, project.beats_per_bar)
        visible = self.visible_beats()
        step = 1.0
        while ppb * step < 26 and step < 64:
            step *= 4 if step == 1 else 2
        first = math.floor(self.scroll_beats / step) * step
        for index in range(800):
            beat = first + index * step
            if beat > self.scroll_beats + visible + step:
                break
            if beat < 0:
                continue
            x = x_of(beat)
            if x < self.KEY_W - 1:
                continue
            if abs(beat / bpb - round(beat / bpb)) < 1e-9:
                c.create_line(x, top, x, height, fill=T["grid_strong"])
            else:
                c.create_line(x, top, x, height, fill=T["grid"])

        # 循环区间底纹
        for index, track in enumerate(project.tracks):
            if self.only_selected and self.selected >= 0 and index != self.selected:
                continue
            if not track.loop:
                continue
            period = track.period_beats(project.beats_per_bar)
            x1, x2 = max(self.KEY_W, x_of(0)), min(width, x_of(period))
            if x2 > x1:
                c.create_rectangle(x1, height - 4, x2, height - 1,
                                   fill=theme.TRACK_COLORS[index % len(theme.TRACK_COLORS)],
                                   outline="")

        # 音符
        selected = self.selected
        for index, track in enumerate(project.tracks):
            if self.only_selected and selected >= 0 and index != selected:
                continue
            base = theme.TRACK_COLORS[index % len(theme.TRACK_COLORS)]
            active = selected < 0 or index == selected
            color = base if selected < 0 or index == selected else theme.mix(base, T["editor"], 0.35)
            if track.mute:
                color = theme.mix(color, T["editor"], 0.3)
            drum_track = fetch.resolve(track.instrument) in fetch.BUILTIN
            bar_h = max(2.0, row_h * 0.42)
            show_text = row_h >= 13
            for note_index, note in enumerate(track.notes):
                x1 = x_of(note.time)
                x2 = x_of(note.time + note.dur)
                if x2 < self.KEY_W or x1 > width:
                    continue
                width_px = max(2.0, x2 - x1 - 1.0)
                left = max(self.KEY_W, x1)
                picked = self.selected_note == (index, note_index)
                # 力度决定亮度：越轻越暗，一眼能看出强弱
                shade = theme.mix(color, T["editor"], 0.45 + 0.55 * max(0.0, min(1.0, note.velocity)))
                fill = theme.lighten(shade, 0.3) if picked else shade
                for pitch in note.pitches:
                    if not first_pitch - 1 <= pitch <= last_pitch + 1:
                        continue
                    cy = y_of(pitch)
                    if cy < top - row_h or cy > height + row_h:
                        continue
                    c.create_rectangle(
                        left, cy - bar_h, left + width_px, cy + bar_h,
                        fill=fill,
                        outline="#FFFFFF" if picked else (theme.lighten(color, 0.25) if active else ""),
                        width=2 if picked else 1,
                        tags=(f"note:{index}", f"n:{index}:{note_index}"),
                    )
                    if show_text and width_px > 26:
                        c.create_text(left + 4, cy, anchor="w",
                                      text=pad_label(pitch) if drum_track else midi_to_name(pitch),
                                      fill="#0B0B0C", font=theme.font(7))
                # 力度条：音符底部一小段，长度 ∝ 力度
                if bar_h >= 4 and width_px > 14:
                    vel_w = max(2.0, width_px * max(0.0, min(1.0, note.velocity)))
                    for pitch in note.pitches:
                        cy = y_of(pitch)
                        if cy < top or cy > height:
                            continue
                        c.create_rectangle(left + 1, cy + bar_h - 3, left + vel_w,
                                           cy + bar_h - 1,
                                           fill=theme.lighten(shade, 0.45), outline="")

        # 标尺
        c.create_rectangle(0, 0, width, top, fill=T["panel_alt"], outline="")
        for index in range(800):
            beat = first + index * step
            if beat > self.scroll_beats + visible + step:
                break
            x = x_of(beat)
            if x < self.KEY_W:
                continue
            if abs(beat / bpb - round(beat / bpb)) < 1e-9:
                c.create_text(x + 5, top / 2, anchor="w",
                              text=f"{int(round(beat / bpb)) + 1}",
                              fill=T["text_dim"], font=theme.font(8))
                c.create_line(x, top - 5, x, top, fill=T["border"])
        c.create_line(0, top, width, top, fill=T["border"])

        self._draw_playhead()

        c.create_text(width - 8, height - 8, anchor="se",
                      text="滚轮 上下 · Alt+滚轮 左右 · Ctrl+滚轮 缩放 · Shift+滚轮 行高",
                      fill=T["text_faint"], font=theme.font(7))
        self._update_scrollbars()
