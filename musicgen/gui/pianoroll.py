"""文档页：钢琴卷帘（Piano Roll）。

横轴小节/拍，纵轴音高；左侧画真实钢琴键，循环轨的重复周期用色条标出。
"""

from __future__ import annotations

import math
import tkinter as tk

from ..core.notes import midi_to_name
from . import theme

T = theme.PALETTE

BLACK_KEYS = (1, 3, 6, 8, 10)


class PianoRollPage(tk.Frame):
    RULER_H = 22
    KEY_W = 42  # 左侧钢琴键宽度

    def __init__(self, master: tk.Misc, on_pick_track) -> None:
        super().__init__(master, bg=T["editor"])
        self.app = None
        self.on_pick_track = on_pick_track
        self.project = None
        self.selected = -1
        self.zoom = 1.0
        self.scroll_beats = 0.0
        self._pending: str | None = None

        self.canvas = tk.Canvas(self, bg=T["editor"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.schedule_redraw())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_zoom)
        self.canvas.bind("<Shift-MouseWheel>", self._on_zoom)
        self.canvas.bind("<Motion>", self._on_motion)

    # ---- 数据 ---------------------------------------------------------
    def set_project(self, project, selected: int = -1) -> None:
        self.project = project
        self.selected = selected
        self.zoom = 1.0
        self.scroll_beats = 0.0
        self.schedule_redraw()

    def set_selected(self, index: int) -> None:
        self.selected = index
        self.schedule_redraw()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.scroll_beats = 0.0
        self.schedule_redraw()

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

    # ---- 重绘调度 -----------------------------------------------------
    def schedule_redraw(self) -> None:
        if self._pending is None:
            self._pending = self.after(16, self._flush)

    def _flush(self) -> None:
        self._pending = None
        self.redraw()

    # ---- 交互 ---------------------------------------------------------
    def _on_motion(self, event: tk.Event) -> None:
        if self.project is None:
            return
        beat = self._beat_at(event.x)
        bar = int(beat // max(1, self.project.beats_per_bar)) + 1
        self.canvas.configure(cursor="hand2" if self._note_at(event) else "arrow")
        self.canvas.delete("hint")
        if event.x > self.KEY_W:
            self.canvas.create_text(
                self.canvas.winfo_width() - 8, self.RULER_H + 10, anchor="ne",
                text=f"第 {bar} 小节 · 第 {beat % self.project.beats_per_bar + 1:.2f} 拍",
                fill=T["text_faint"], font=theme.font(8), tags="hint",
            )

    def _beat_at(self, x: float) -> float:
        ppb = self._pixels_per_beat()
        return max(0.0, (x - self.KEY_W) / ppb + self.scroll_beats)

    def _note_at(self, event: tk.Event) -> str | None:
        for item in self.canvas.find_overlapping(event.x - 1, event.y - 1, event.x + 1, event.y + 1):
            for tag in self.canvas.gettags(item):
                if tag.startswith("note:"):
                    return tag
        return None

    def _on_click(self, event: tk.Event) -> None:
        tag = self._note_at(event)
        if tag:
            self.on_pick_track(int(tag.split(":")[1]))
            return
        if event.y < self.RULER_H:
            self.reset_view()

    def _on_wheel(self, event: tk.Event) -> None:
        self.scroll_beats = max(0.0, self.scroll_beats - event.delta / 120.0 * 0.5)
        self._clamp()
        self.schedule_redraw()

    def _on_zoom(self, event: tk.Event) -> None:
        factor = 1.2 if event.delta > 0 else 1 / 1.2
        anchor = self._beat_at(max(event.x, self.KEY_W + 1))
        self.zoom = min(16.0, max(1.0, self.zoom * factor))
        if self.zoom <= 1.001:
            self.zoom, self.scroll_beats = 1.0, 0.0
        else:
            # 让缩放围绕鼠标位置展开
            self.scroll_beats = max(0.0, anchor - (event.x - self.KEY_W) / self._pixels_per_beat())
        self._clamp()
        self.schedule_redraw()

    def _clamp(self) -> None:
        total = max(1.0, self.total_beats())
        self.scroll_beats = max(0.0, min(self.scroll_beats, max(0.0, total - total / self.zoom)))

    def _pixels_per_beat(self) -> float:
        width = max(self.canvas.winfo_width(), 80)
        return max(0.5, (width - self.KEY_W - 10) / max(1.0, self.total_beats()) * self.zoom)

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
        top = self.RULER_H
        ppb = self._pixels_per_beat()

        pitches = [p for t in project.tracks for n in t.notes for p in n.pitches]
        low = max(0, (min(pitches) if pitches else 36) - 2)
        high = min(127, (max(pitches) if pitches else 84) + 2)
        low = min(low, high - 6)
        span = max(6, high - low + 1)
        row_h = max(3.0, (height - top - 8) / span)

        def y_of(pitch: int) -> float:
            return top + (high - pitch) * row_h + row_h / 2

        def x_of(beat: float) -> float:
            return self.KEY_W - self.scroll_beats * ppb + beat * ppb

        # 黑键行底色
        for pitch in range(low, high + 1):
            if pitch % 12 in BLACK_KEYS:
                y = top + (high - pitch) * row_h
                c.create_rectangle(self.KEY_W, y, width, y + row_h, fill=T["key_black"], outline="")

        # 左侧钢琴键
        c.create_rectangle(0, 0, self.KEY_W, height, fill=T["panel_alt"], outline="")
        for pitch in range(low, high + 1):
            y = top + (high - pitch) * row_h
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
        visible = (width - self.KEY_W) / ppb
        step = 1.0
        while ppb * step < 26 and step < 64:
            step *= 4 if step == 1 else 2
        first = math.floor(self.scroll_beats / step) * step
        for index in range(600):
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
            base = theme.TRACK_COLORS[index % len(theme.TRACK_COLORS)]
            active = index == selected
            color = base if selected < 0 or active else theme.mix(base, T["editor"], 0.35)
            if track.mute:
                color = theme.mix(color, T["editor"], 0.3)
            bar_h = max(2.0, row_h * 0.42)
            for note in track.notes:
                x1 = x_of(note.time)
                x2 = x_of(note.time + note.dur)
                if x2 < self.KEY_W or x1 > width:
                    continue
                width_px = max(2.0, x2 - x1 - 1.0)
                left = max(self.KEY_W, x1)
                for pitch in note.pitches:
                    if not low <= pitch <= high:
                        continue
                    cy = y_of(pitch)
                    c.create_rectangle(left, cy - bar_h, left + width_px, cy + bar_h,
                                       fill=color, outline=theme.lighten(color, 0.25)
                                       if active else "", tags=(f"note:{index}",))
                    if row_h >= 13 and width_px > 26:
                        c.create_text(left + 4, cy, anchor="w", text=midi_to_name(pitch),
                                      fill="#0B0B0C", font=theme.font(7))

        # 标尺
        c.create_rectangle(0, 0, width, top, fill=T["panel_alt"], outline="")
        for index in range(600):
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

        if self.zoom > 1.0:
            c.create_text(width - 8, height - 10, anchor="se",
                          text=f"缩放 {self.zoom:.1f}× · 滚轮平移 · Ctrl+滚轮缩放",
                          fill=T["text_faint"], font=theme.font(7))
