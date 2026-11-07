"""面板拖拽停靠：按住标题栏拖动，松手时按指针靠近的那条边停靠。

只做三件事：拖动时显示一个「拿着面板」的小浮窗、把指针位置换算成左/右/底部停靠区、
松手后回调 ``on_dock(key, area)``。真正的重建交给 ``DockManager``。

Tk 的按钮按下会建立隐式抓取，所以指针拖出标题栏甚至拖出窗口，``<B1-Motion>`` 仍然
会送到发起拖动的控件上——这也是不用 ``bind_all`` 的原因。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme

T = theme.PALETTE

#: 移动超过这个距离（像素）才算拖动，否则当成普通点击
THRESHOLD = 6
#: 指针进入窗口这条比例以内才吸附到对应边
EDGE = 0.22
#: 停靠目标区的高亮块（占窗口的比例）
ZONE_FRACTION = {"left": 0.20, "right": 0.20, "bottom": 0.26}

AREA_NAMES = {"left": "左侧", "right": "右侧", "bottom": "底部"}


class DockDrag:
    def __init__(
        self,
        app: tk.Misc,
        manager: Callable[[], object],
        on_dock: Callable[[str, str], None],
    ) -> None:
        self.app = app
        self._manager = manager
        self._on_dock = on_dock
        self._key: str | None = None
        self._title = ""
        self._press = (0, 0)
        self._active = False
        self._area: str | None = None
        self._ghost: tk.Toplevel | None = None
        self._zone: tk.Toplevel | None = None

    # ---- 绑定 ---------------------------------------------------------
    def bind(self, header: tk.Widget, key: str, title: str) -> None:
        targets = header.drag_targets() if hasattr(header, "drag_targets") else [header]
        for widget in targets:
            widget.bind(
                "<ButtonPress-1>",
                lambda event, k=key, t=title: self.start(k, t, event.x_root, event.y_root),
                add="+",
            )
            widget.bind("<B1-Motion>", lambda event: self.move(event.x_root, event.y_root), add="+")
            widget.bind(
                "<ButtonRelease-1>",
                lambda event: self.finish(event.x_root, event.y_root),
                add="+",
            )
            try:
                widget.configure(cursor="fleur")
            except tk.TclError:  # pragma: no cover - 某些 ttk 控件不支持 cursor
                pass

    # ---- 拖动流程 -----------------------------------------------------
    def start(self, key: str, title: str, x: int, y: int) -> None:
        if self._active:
            return
        self._key = key
        self._title = title
        self._press = (x, y)

    def move(self, x: int, y: int) -> None:
        if self._key is None:
            return
        if not self._active:
            if abs(x - self._press[0]) < THRESHOLD and abs(y - self._press[1]) < THRESHOLD:
                return
            self._active = True
            self._make_ghost()
        self._place_ghost(x, y)
        self._show_zone(self.area_at(x, y))

    def finish(self, x: int, y: int) -> str | None:
        """松手：能算出停靠区就停靠，返回停靠区名字（供测试用）。"""
        key = self._key
        area = self.area_at(x, y) if self._active else None
        self._reset()
        if key and area:
            self._on_dock(key, area)
            return area
        return None

    def area_at(self, x: int, y: int) -> str | None:
        """把屏幕坐标换算成停靠区：靠近哪条边就停哪边；同一区或中间区域返回 None。"""
        host = getattr(self.app, "hsplit", None)
        if host is None:
            return None
        hx, hy = host.winfo_rootx(), host.winfo_rooty()
        width = max(host.winfo_width(), 1)
        height = max(host.winfo_height(), 1)
        if not (hx <= x <= hx + width and hy <= y <= hy + height):
            return None
        distances = (
            ((x - hx) / width, "left"),
            ((hx + width - x) / width, "right"),
            ((hy + height - y) / height, "bottom"),
        )
        edge, area = min(distances)
        if edge > EDGE:
            return None
        manager = self._manager()
        if self._key is not None and manager is not None and manager.area_of(self._key) == area:
            return None
        return area

    # ---- 浮窗 ---------------------------------------------------------
    def _make_ghost(self) -> None:
        ghost = tk.Toplevel(self.app)
        ghost.overrideredirect(True)
        ghost.attributes("-topmost", True)
        try:
            ghost.attributes("-alpha", 0.94)
        except tk.TclError:  # pragma: no cover - 少数平台不支持透明
            pass
        frame = tk.Frame(ghost, bg=T["chrome"], highlightthickness=1,
                         highlightbackground=T["accent"])
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame, text=self._title, bg=T["chrome"], fg=T["text"],
            font=theme.font(9), anchor="w", padx=10,
        ).pack(fill="both", expand=True)
        ghost.withdraw()
        self._ghost = ghost

        zone = tk.Toplevel(self.app)
        zone.overrideredirect(True)
        zone.attributes("-topmost", True)
        try:
            zone.attributes("-alpha", 0.28)
        except tk.TclError:  # pragma: no cover
            pass
        zone.configure(bg=T["accent"])
        zone.withdraw()
        self._zone = zone

    def _place_ghost(self, x: int, y: int) -> None:
        if self._ghost is not None:
            self._ghost.geometry(f"180x28+{x + 14}+{y + 12}")

    def _show_zone(self, area: str | None) -> None:
        self._area = area
        if self._zone is None:
            return
        if area is None:
            self._zone.withdraw()
            return
        x, y, width, height = self._zone_rect(area)
        self._zone.geometry(f"{width}x{height}+{x}+{y}")
        self._zone.deiconify()
        self._zone.lift()

    def _zone_rect(self, area: str) -> tuple[int, int, int, int]:
        host = self.app.hsplit
        hx, hy = host.winfo_rootx(), host.winfo_rooty()
        width, height = host.winfo_width(), host.winfo_height()
        fraction = ZONE_FRACTION.get(area, 0.2)
        if area == "left":
            return hx, hy, int(width * fraction), height
        if area == "right":
            return hx + width - int(width * fraction), hy, int(width * fraction), height
        return hx, hy + height - int(height * fraction), width, int(height * fraction)

    def _reset(self) -> None:
        for window in (self._ghost, self._zone):
            if window is not None:
                try:
                    window.destroy()
                except tk.TclError:  # pragma: no cover - 窗口已被销毁
                    pass
        self._ghost = self._zone = None
        self._key = None
        self._title = ""
        self._active = False
        self._area = None
