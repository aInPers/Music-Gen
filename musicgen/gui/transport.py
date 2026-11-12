"""传输条：播放/暂停、停止、时间、进度条、音量。

放在命令条下面，对应 VS 里调试控制条的位置。进度条支持拖动定位。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import icons, theme, widgets

T = theme.PALETTE


def _fmt(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


class SeekBar(tk.Canvas):
    """扁平进度条：已播部分用强调色，拖动时只移动滑块，松手才真正定位。"""

    def __init__(self, master: tk.Misc, on_seek: Callable[[float], None]) -> None:
        super().__init__(master, height=22, bg=T["chrome"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self.on_seek = on_seek
        self._position = 0.0
        self._duration = 0.0
        self._drag_ratio: float | None = None
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ---- API ----------------------------------------------------------
    def set(self, position: float, duration: float) -> None:
        if self._drag_ratio is None:
            self._position = max(0.0, position)
        self._duration = max(0.0, duration)
        self._render()

    # ---- 交互 ---------------------------------------------------------
    def _ratio_from_x(self, x: float) -> float:
        width = max(1, self.winfo_width())
        return min(1.0, max(0.0, (x - 4) / max(1, width - 8)))

    def _on_press(self, event: tk.Event) -> None:
        if self._duration <= 0:
            return
        self._drag_ratio = self._ratio_from_x(event.x)
        self._render()

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_ratio is None:
            return
        self._drag_ratio = self._ratio_from_x(event.x)
        self._render()

    def _on_release(self, _event=None) -> None:
        if self._drag_ratio is None:
            return
        ratio = self._drag_ratio
        self._drag_ratio = None
        self.on_seek(ratio * self._duration)

    # ---- 绘制 ---------------------------------------------------------
    def _render(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width < 20:
            return
        mid = 11
        left, right = 4, width - 4
        ratio = self._drag_ratio
        if ratio is None:
            ratio = self._position / self._duration if self._duration > 0 else 0.0
        ratio = min(1.0, max(0.0, ratio))
        knob = left + ratio * (right - left)

        self.create_line(left, mid, right, mid, fill=T["border"], width=3, capstyle="round")
        if knob > left + 1:
            self.create_line(left, mid, knob, mid, fill=T["accent"], width=3, capstyle="round")
        radius = 5 if self._drag_ratio is not None else 4
        self.create_oval(knob - radius, mid - radius, knob + radius, mid + radius,
                         fill="#FFFFFF" if self._duration > 0 else T["text_faint"], outline="")


class TransportBar(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        on_toggle: Callable[[], None],
        on_stop: Callable[[], None],
        on_seek: Callable[[float], None],
        on_volume: Callable[[float], None],
    ) -> None:
        super().__init__(master, bg=T["chrome"], height=34)
        self.pack_propagate(False)
        self._on_toggle = on_toggle

        left = tk.Frame(self, bg=T["chrome"])
        left.pack(side="left", padx=(8, 0), pady=2)
        self.btn_toggle = widgets.ToolButton(
            left, icon="play", command=on_toggle, tooltip="播放 / 暂停（空格）",
            accent=True, width=32, height=30,
        )
        self.btn_toggle.pack(side="left", padx=1)
        self.btn_stop = widgets.ToolButton(
            left, icon="stop", command=on_stop, tooltip="停止（回到开头）",
            width=30, height=30,
        )
        self.btn_stop.pack(side="left", padx=1)

        self.time_label = tk.Label(self, text="0:00.0 / 0:00.0", bg=T["chrome"],
                                   fg=T["text_dim"], font=theme.mono(9), width=16, anchor="w")
        self.time_label.pack(side="left", padx=(10, 6))

        self.seek = SeekBar(self, on_seek=on_seek)
        self.seek.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.meter = widgets.LevelMeter(self, width=104, height=16, channels=2,
                                        background=T["chrome"])
        self.meter.pack(side="right", padx=(0, 12))

        volume_box = tk.Frame(self, bg=T["chrome"])
        volume_box.pack(side="right", padx=(0, 12))
        tk.Label(volume_box, text="音量", bg=T["chrome"], fg=T["text_faint"],
                 font=theme.font(8)).pack(side="left", padx=(0, 4))
        self.volume = widgets.MiniSlider(
            volume_box, 0.0, 1.0, 0.9, 0.02,
            lambda v: f"{int(round(v * 100))}%", command=on_volume,
            background=T["chrome"],
        )
        self.volume.configure(width=110)
        self.volume.pack(side="left")

        self.set_playing(False)
        self.set_enabled(False)

    # ---- API ----------------------------------------------------------
    def set_playing(self, playing: bool) -> None:
        self.btn_toggle.set_icon("pause" if playing else "play")

    def set_enabled(self, enabled: bool) -> None:
        self.btn_toggle.set_enabled(enabled)
        self.btn_stop.set_enabled(enabled)
        self.volume.set_enabled(enabled)

    def set_levels(self, left: float, right: float) -> None:
        self.meter.set_levels([left, right])

    def reset_meter(self) -> None:
        self.meter.reset()

    def update_time(self, position: float, duration: float) -> None:
        self.time_label.configure(text=f"{_fmt(position)} / {_fmt(duration)}")
        self.seek.set(position, duration)
