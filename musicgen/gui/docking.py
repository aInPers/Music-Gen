"""面板停靠：左侧 / 右侧 / 底部三个停靠区，可移动、关闭、重新打开。

Tk 的控件不能换父容器，所以「停靠」的实现是**销毁并在新容器里重建**同一个面板，
重建后由 ``on_created`` 回调把状态（当前音轨、日志内容等）灌回去。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme

T = theme.PALETTE

AREAS: tuple[tuple[str, str], ...] = (("left", "左侧"), ("right", "右侧"), ("bottom", "底部"))

#: 各停靠区重新出现时的默认尺寸（像素）
AREA_SIZE = {"left": (180, 282), "right": (210, 268), "bottom": (80, 150)}


class DockManager:
    def __init__(
        self,
        containers: dict[str, tk.Frame],
        paned: dict[str, tk.PanedWindow],
        factories: dict[str, Callable[[tk.Misc], tk.Widget]],
        titles: dict[str, str],
        defaults: dict[str, str],
        on_created: Callable[[str, tk.Widget], None],
        center: tk.Widget,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        self.containers = containers
        self.paned = paned
        self.factories = factories
        self.titles = titles
        self.defaults = defaults
        self.on_created = on_created
        self.center = center
        self.on_layout_changed = on_layout_changed

        self.panels: dict[str, tk.Widget] = {}
        self.areas: dict[str, str] = {}
        self.sizes: dict[str, int] = {area: AREA_SIZE[area][1] for area, _ in AREAS}

    # ---- 查询 ---------------------------------------------------------
    def panel(self, key: str) -> tk.Widget | None:
        return self.panels.get(key)

    def visible(self, key: str) -> bool:
        return key in self.panels

    def area_of(self, key: str) -> str | None:
        return self.areas.get(key)

    def state(self) -> dict[str, str | None]:
        return {key: self.areas.get(key) for key in self.factories}

    # ---- 停靠操作 -----------------------------------------------------
    def dock(self, key: str, area: str) -> None:
        if area not in self.containers:
            raise ValueError(f"未知停靠区：{area}")
        if key in self.panels:
            if self.areas.get(key) == area:
                return
            self._destroy(key)
        self._create(key, area)
        self._notify()

    def close(self, key: str) -> None:
        if key in self.panels:
            area = self.areas[key]
            self._destroy(key)
            self._collapse(area)
            self._notify()

    def toggle(self, key: str) -> None:
        if key in self.panels:
            self.close(key)
        else:
            self.dock(key, self.areas.get(key) or self.defaults[key])

    def reset(self) -> None:
        for key in list(self.panels):
            self.close(key)
        for key in self.factories:
            self.dock(key, self.defaults[key])

    # ---- 初始状态 -----------------------------------------------------
    def apply_state(self, docks: dict | None, sizes: dict | None = None) -> None:
        self.sizes.update({k: int(v) for k, v in (sizes or {}).items() if k in self.sizes})
        wanted = dict(self.defaults)
        if docks:
            for key, area in docks.items():
                if key in self.factories:
                    wanted[key] = area
        for key, area in wanted.items():
            if area in self.containers:
                self._create(key, area)

    # ---- 内部 ---------------------------------------------------------
    def _create(self, key: str, area: str) -> None:
        container = self.containers[area]
        widget = self.factories[key](container)
        if isinstance(container, tk.PanedWindow):
            # 面板自己声明 PREFERRED_HEIGHT，入栏时先按它给高度，
            # 再由最高的那个面板吃掉窗口变高多出来的空间。
            wanted = int(getattr(widget, "PREFERRED_HEIGHT", 200))
            container.add(widget, minsize=80, height=wanted, stretch="never")
            self._rebalance(container)
        else:
            widget.pack(fill="both", expand=True)
        self.panels[key] = widget
        self.areas[key] = area
        self._ensure_pane(area)
        self._attach_menu(key, widget)
        self.on_created(key, widget)

    def _destroy(self, key: str) -> None:
        widget = self.panels.pop(key, None)
        self.areas.pop(key, None)
        if widget is not None:
            area = self._area_from_widget(widget)
            container = self.containers.get(area) if area else None
            if isinstance(container, tk.PanedWindow) and self.has_pane(container, widget):
                container.forget(widget)
            widget.destroy()
            if area:
                container.update_idletasks() if container is not None else None
                if self._area_is_empty(area):
                    self._collapse(area)

    @staticmethod
    def _rebalance(container: tk.PanedWindow) -> None:
        """同一停靠区里，最高的面板拉伸，其余保持自然高度。"""
        panes = [container.nametowidget(str(item)) for item in container.panes()]
        if not panes:
            return
        heights = {pane: int(getattr(pane, "PREFERRED_HEIGHT", 200)) for pane in panes}
        tallest = max(panes, key=lambda pane: heights[pane])
        for pane in panes:
            container.paneconfigure(pane, stretch="always" if pane is tallest else "never")

    def _area_is_empty(self, area: str) -> bool:
        container = self.containers[area]
        if isinstance(container, tk.PanedWindow):
            return len(container.panes()) == 0
        return not container.winfo_children()

    def _area_from_widget(self, widget: tk.Widget) -> str | None:
        for area, container in self.containers.items():
            if widget.winfo_parent() == str(container):
                return area
        return None

    @staticmethod
    def has_pane(paned: tk.PanedWindow, container: tk.Widget) -> bool:
        """paned.panes() 返回的是 window 对象，必须逐项转字符串比较。"""
        return any(str(item) == str(container) for item in paned.panes())

    def _ensure_pane(self, area: str) -> None:
        container = self.containers[area]
        paned = self.paned[area]
        if self.has_pane(paned, container):
            return
        minsize, default_size = AREA_SIZE[area]
        stored = self.sizes.get(area) or default_size
        if area == "bottom":
            paned.add(container, minsize=minsize, height=stored, stretch="never")
        elif area == "left":
            paned.add(container, minsize=minsize, width=stored, stretch="never",
                      before=self.center)
        else:
            paned.add(container, minsize=minsize, width=stored, stretch="never")

    def _collapse(self, area: str) -> None:
        container = self.containers[area]
        paned = self.paned[area]
        if self.has_pane(paned, container):
            # 记住当前尺寸，下次停靠回来时还原
            measured = (container.winfo_height() if area == "bottom" else container.winfo_width())
            if measured > AREA_SIZE[area][0]:
                self.sizes[area] = measured
            paned.forget(container)

    def _attach_menu(self, key: str, widget: tk.Widget) -> None:
        header = getattr(widget, "header", None)
        if header is None or not hasattr(header, "add_menu_button"):
            return
        menu = theme.make_menu(widget)
        current = self.areas.get(key)
        for area, label in AREAS:
            menu.add_command(
                label=f"停靠到{label}",
                state="disabled" if area == current else "normal",
                command=lambda a=area: self.dock(key, a),
            )
        menu.add_separator()
        menu.add_command(label="关闭", command=lambda: self.close(key))
        header.add_menu_button(menu, "停靠 / 关闭")

    def apply_measured_sizes(self, sashes) -> None:
        """恢复分栏位置。新格式 {"main": [[x, y]...], "areas": {area: [[x, y]...]}}，
        也兼容旧格式 [[x, y], ...]。"""
        try:
            if isinstance(sashes, list):
                if sashes and len(self.paned["left"].panes()) >= 2:
                    self.paned["left"].sash_place(0, int(sashes[0][0]), 0)
                if len(sashes) > 1 and len(self.paned["bottom"].panes()) >= 2:
                    self.paned["bottom"].sash_place(0, 0, int(sashes[1][1]))
                return
            main = (sashes or {}).get("main") or []
            hsplit = self.paned["left"]
            if main and len(hsplit.panes()) >= 2:
                hsplit.sash_place(0, int(main[0][0]), 0)
            center = self.paned["bottom"]
            if len(main) > 1 and len(center.panes()) >= 2:
                center.sash_place(0, 0, int(main[1][1]))
            for area, coords in ((sashes or {}).get("areas") or {}).items():
                container = self.containers.get(area)
                if container is None:
                    continue
                for index, (_x, y) in enumerate(coords):
                    if index < len(container.panes()) - 1:
                        container.sash_place(index, 0, int(y))
        except Exception:  # noqa: BLE001 - 位置恢复失败不影响使用
            pass

    def _notify(self) -> None:
        if self.on_layout_changed:
            self.on_layout_changed()
