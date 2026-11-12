"""分轨卷帘：一条音轨一条泳道，共享时间轴。

和 :mod:`pianoroll` 的区别：那边是「一条轨铺满整页、按半音排布」，适合改音符；
这边把每条轨压成一条横向泳道，用来一眼看**各轨在时间上怎么咬合**——
谁在铺底、谁在打点、哪里是空档。左侧轨头带一条小电平表，播放时跟着跳。

交互：

    单击泳道        选中该音轨（右栏属性/资源管理器同步）
    滚轮            上下滚动泳道
    拖动滚动条      同上
    Ctrl+滚轮       缩放时间轴（看细节）
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

T = theme.PALETTE

RULER_H = 22
HEAD_W = 168
MIN_LANE = 44
MAX_LANE = 108
PAD = 6


class TrackRollPage(tk.Frame):
    def __init__(self, master: tk.Misc, on_pick_track=None) -> None:
        super().__init__(master, bg=T["editor"])
        self.on_pick_track = on_pick_track or (lambda _index: None)
        self.project = None
        self.selected = -1
        self.playhead: float | None = None
        self.zoom = 1.0                 # 1 = 整首铺满
        self.offset = 0.0               # 左边界（拍）
        self._lanes: list[tuple[int, float, float]] = []   # (轨序号, 顶部 y, 高度)
        self._meters: dict[int, tuple[int, int]] = {}      # 轨序号 -> (底, 填充)
        self._levels: dict[str, float] = {}
        self._pending: str | None = None
        self._playhead_item: int | None = None

        self.canvas = tk.Canvas(self, bg=T["editor"], highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", style="VS.Vertical.TScrollbar",
                                  command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.canvas.bind("<Configure>", lambda _e: self.schedule_redraw())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_zoom)

    # ---- API ----------------------------------------------------------
    def set_project(self, project) -> None:
        self.project = project
        self.selected = -1
        self.playhead = None
        self.zoom = 1.0
        self.offset = 0.0
        self.schedule_redraw()

    def set_selected(self, index: int) -> None:
        if index != self.selected:
            self.selected = index
            self.schedule_redraw()

    def set_playhead(self, seconds: float | None) -> None:
        self.playhead = seconds
        if self.project is None or seconds is None:
            if self._playhead_item is not None:
                self.canvas.itemconfigure(self._playhead_item, state="hidden")
            return
        x = self._x_of_seconds(seconds)
        if self._playhead_item is None:
            self._playhead_item = self.canvas.create_line(
                x, 0, x, self.canvas.bbox("all")[3] if self.canvas.bbox("all") else 0,
                fill=T["accent"], width=1, tags="playhead")
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        self.canvas.coords(self._playhead_item, x, top, x, bottom)
        self.canvas.itemconfigure(self._playhead_item, state="normal")

    def set_levels(self, levels: dict[str, float]) -> None:
        """``{轨名: 0–1 线性幅度}``，只更新轨头的小电平条，不重绘整页。"""
        self._levels = levels
        if self.project is None:
            return
        for index, fill_item in self._meters.items():
            track = self.project.tracks[index]
            value = max(0.0, min(1.0, float(levels.get(track.name, 0.0))))
            coords = self.canvas.coords(fill_item)
            if len(coords) != 4:
                continue
            y0, y1 = coords[1], coords[3]
            self.canvas.coords(fill_item, 12, y0, 12 + (HEAD_W - 32) * value, y1)

    def schedule_redraw(self) -> None:
        if self._pending is not None:
            return
        self._pending = self.after_idle(self._redraw)

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.offset = 0.0
        self.schedule_redraw()

    # ---- 坐标 ---------------------------------------------------------
    def _beats(self) -> float:
        project = self.project
        seconds = project.compute_length_seconds()
        return max(1.0, seconds * project.bpm / 60.0)

    def _visible_beats(self) -> float:
        return self._beats() / max(1.0, self.zoom)

    def _x_of_beat(self, beat: float) -> float:
        width = max(1, self.canvas.winfo_width() - HEAD_W - 2)
        return HEAD_W + (beat - self.offset) / self._visible_beats() * width

    def _x_of_seconds(self, seconds: float) -> float:
        if self.project is None:
            return HEAD_W
        return self._x_of_beat(seconds * self.project.bpm / 60.0)

    # ---- 交互 ---------------------------------------------------------
    def _on_click(self, event: tk.Event) -> None:
        if self.project is None:
            return
        y = self.canvas.canvasy(event.y)
        for index, top, height in self._lanes:
            if top <= y <= top + height:
                self.on_pick_track(index)
                return

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_zoom(self, event: tk.Event) -> None:
        factor = 1.25 if event.delta > 0 else 0.8
        self.zoom = max(1.0, min(8.0, self.zoom * factor))
        self.schedule_redraw()
        return "break"

    # ---- 绘制 ---------------------------------------------------------
    def _redraw(self) -> None:
        self._pending = None
        canvas = self.canvas
        canvas.delete("all")
        self._meters.clear()
        self._playhead_item = None
        project = self.project
        width = max(1, canvas.winfo_width())
        if project is None or width < 40:
            canvas.configure(scrollregion=(0, 0, width, 1))
            return

        lanes = self._measure_lanes(project)
        total = RULER_H + sum(height for _i, _t, height in lanes) + PAD
        canvas.configure(scrollregion=(0, 0, width, total))

        self._draw_ruler(project, width)
        self._lanes = []
        y = RULER_H
        for index, track, height in lanes:
            self._draw_lane(index, track, y, height, width)
            self._lanes.append((index, y, height))
            y += height

        if self.selected >= 0:
            for index, top, height in self._lanes:
                if index == self.selected:
                    canvas.create_rectangle(0, top, width, top + height,
                                            outline=T["accent"], width=1)
                    break

        if self.playhead is not None:
            self._playhead_item = None
            self.set_playhead(self.playhead)
        else:
            self._playhead_item = None

    def _measure_lanes(self, project) -> list[tuple[int, object, float]]:
        out = []
        for index, track in enumerate(project.tracks):
            pitches = [pitch for note in track.notes for pitch in note.pitches]
            if pitches:
                span = max(pitches) - min(pitches)
                height = max(MIN_LANE, min(MAX_LANE, 38 + span * 1.2))
            else:
                height = MIN_LANE
            out.append((index, track, float(height)))
        return out

    def _draw_ruler(self, project, width: int) -> None:
        canvas = self.canvas
        canvas.create_rectangle(0, 0, width, RULER_H, fill=T["chrome_hi"], outline="")
        canvas.create_text(12, RULER_H / 2, text="分轨", anchor="w",
                           fill=T["text_dim"], font=theme.font(8))
        beats_per_bar = max(1, project.beats_per_bar)
        visible = self._visible_beats()
        step = beats_per_bar
        while visible / step > 40:
            step *= 2
        beat = self.offset - (self.offset % step)
        while True:
            x = self._x_of_beat(beat)
            if x > width:
                break
            if x >= HEAD_W:
                bar = int(beat // beats_per_bar) + 1
                canvas.create_line(x, 0, x, RULER_H, fill=T["border_soft"])
                label = f"{bar}" if beat % step == 0 else ""
                if label:
                    canvas.create_text(x + 3, RULER_H / 2, text=label, anchor="w",
                                       fill=T["text_faint"], font=theme.mono(7))
            beat += step
        canvas.create_line(HEAD_W - 1, 0, HEAD_W - 1, RULER_H, fill=T["border"])

    def _draw_lane(self, index: int, track, top: float, height: float, width: int) -> None:
        canvas = self.canvas
        base = theme.TRACK_COLORS[index % len(theme.TRACK_COLORS)]
        selected = index == self.selected
        lane_bg = T["selection_idle"] if selected else (T["editor"] if index % 2 else T["panel"])
        canvas.create_rectangle(0, top, width, top + height, fill=lane_bg, outline="")
        canvas.create_line(0, top + height, width, top + height, fill=T["border_soft"])

        # 轨头
        header_bg = T["chrome_hi"] if selected else T["chrome"]
        canvas.create_rectangle(0, top, HEAD_W - 1, top + height, fill=header_bg, outline="")
        canvas.create_rectangle(0, top, 3, top + height, fill=base, outline="")
        name = track.name if len(track.name) <= 12 else track.name[:11] + "…"
        canvas.create_text(12, top + 10, text=f"{index + 1}. {name}", anchor="w",
                           fill=T["text"], font=theme.font(9, bold=selected))
        meta = f"{track.instrument} · {len(track.notes)} 音"
        canvas.create_text(12, top + 24, text=meta, anchor="w",
                           fill=T["text_faint"], font=theme.font(7))

        # 电平条（底槽 + 填充）
        bar_y = top + height - 8
        canvas.create_rectangle(12, bar_y, HEAD_W - 20, bar_y + 4,
                                fill=T["editor"], outline="")
        fill = canvas.create_rectangle(12, bar_y, 12, bar_y + 4, fill=base, outline="")
        self._meters[index] = fill
        value = float(self._levels.get(track.name, 0.0))
        if value > 0:
            canvas.coords(fill, 12, bar_y, 12 + (HEAD_W - 32) * min(1.0, value), bar_y + 4)

        if track.mute:
            canvas.create_text(HEAD_W - 14, top + 12, text="M", anchor="e",
                               fill=T["error"], font=theme.font(8, bold=True))

        if not track.notes:
            canvas.create_text(HEAD_W + 10, top + height / 2, text="（无音符）",
                               anchor="w", fill=T["text_faint"], font=theme.font(8))
            return

        pitches = [pitch for note in track.notes for pitch in note.pitches]
        low, high = min(pitches), max(pitches)
        span = max(1, high - low)
        inner_top = top + 5
        inner_h = max(6.0, height - 12)
        beat_beats = self._visible_beats()
        left = max(0.0, self.offset)
        right = self.offset + beat_beats
        color = theme.mix(base, T["editor"], 0.25) if track.mute else base
        drawn = 0
        for note in track.notes:
            if note.time > right or note.time + note.dur < left:
                continue
            for pitch in note.pitches:
                x1 = self._x_of_beat(note.time)
                x2 = self._x_of_beat(note.time + max(note.dur, 0.05))
                if x2 < HEAD_W:
                    continue
                x1 = max(x1, HEAD_W + 1)
                if x2 - x1 < 1.5:
                    x2 = x1 + 1.5
                y1 = inner_top + inner_h * (1.0 - (pitch - low) / span)
                canvas.create_rectangle(x1, y1 - 1.5, x2, y1 + 1.5,
                                        fill=color, outline="")
                drawn += 1
        if drawn > 1200:
            canvas.create_text(width - 8, top + 6, text=f"{drawn} 音（已密集绘制）",
                               anchor="ne", fill=T["text_faint"], font=theme.font(7))
