"""VS 风格的通用小组件。

这里只放“长得像 Visual Studio”的基础控件，不含任何业务逻辑：
工具按钮、面板标题栏、文档标签页、滑块/复选框/属性网格、输出日志视图。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import icons, theme

T = theme.PALETTE


# ---------------------------------------------------------------- 提示气泡


class Tooltip:
    """悬停 0.4 秒后显示的黑色小提示。"""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self._after: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(400, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        if self._window is not None:
            return
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        window.configure(bg=T["border"])
        tk.Label(
            window, text=self.text, bg=T["chrome_lo"], fg=T["text"],
            font=theme.font(8), padx=8, pady=3, bd=0,
        ).pack(padx=1, pady=1)
        self._window = window

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None


# ---------------------------------------------------------------- 工具按钮


class ToolButton(tk.Canvas):
    """扁平工具按钮：图标（或文字）+ 悬停高亮 + 禁用态。"""

    def __init__(
        self,
        master: tk.Misc,
        icon: str | None = None,
        text: str = "",
        command: Callable[[], None] | None = None,
        tooltip: str = "",
        accent: bool = False,
        width: int = 30,
        height: int = 30,
    ) -> None:
        super().__init__(master, width=width, height=height, bg=T["chrome"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._icon = icon
        self._text = text
        self._command = command
        self._accent = accent
        self._enabled = True
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._tooltip = Tooltip(self, tooltip) if tooltip else None
        self._render()

    # ---- 状态 ---------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def icon(self) -> str | None:
        return self._icon

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._render()

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self._render()

    def set_command(self, command: Callable[[], None]) -> None:
        self._command = command

    def set_tooltip(self, text: str) -> None:
        if self._tooltip is not None:
            self._tooltip.set_text(text)

    def configure_state(self, enabled: bool) -> None:
        self.set_enabled(enabled)

    # ---- 交互 ---------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._render()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._render()

    def _on_click(self, _event=None) -> None:
        self.invoke()

    def invoke(self) -> None:
        """走一遍点击逻辑（脚本与测试也能触发）。"""
        if self._enabled and self._command:
            self._command()

    # ---- 绘制 ---------------------------------------------------------
    def _render(self) -> None:
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        if not self._enabled:
            background = T["chrome"]
        elif self._hover:
            background = T["accent_dim"] if self._accent else T["chrome_hi"]
        else:
            background = T["accent"] if self._accent else T["chrome"]
        self.configure(bg=background)
        if self._icon:
            color = T["text_faint"] if not self._enabled else "#FFFFFF"
            icons.draw(self, self._icon, w / 2, h / 2, 15, color)
        elif self._text:
            color = T["text_faint"] if not self._enabled else T["text"]
            self.create_text(w / 2, h / 2, text=self._text, fill=color, font=theme.font(9))


# ---------------------------------------------------------------- 结构件


class PanelHeader(tk.Frame):
    """工具窗口顶部的标题条（含可选操作按钮）。"""

    def __init__(self, master: tk.Misc, title: str, background: str | None = None) -> None:
        super().__init__(master, bg=background or T["chrome"], height=24)
        self.pack_propagate(False)
        self._title = tk.Label(
            self, text=title, bg=background or T["chrome"], fg=T["text_dim"],
            font=theme.font(9), anchor="w", padx=10,
        )
        self._title.pack(side="left", fill="y")
        self._actions = tk.Frame(self, bg=background or T["chrome"])
        self._actions.pack(side="right", fill="y", padx=(0, 4))

    def add_action(self, icon: str, command: Callable[[], None], tooltip: str = "") -> ToolButton:
        button = ToolButton(self._actions, icon=icon, command=command, tooltip=tooltip,
                            width=24, height=22)
        button.pack(side="right", padx=1, pady=1)
        return button

    def add_menu_button(self, menu: tk.Menu, tooltip: str = "") -> "ToolButton":
        """右侧加一个按钮，点击弹出菜单（用于停靠/关闭）。"""
        button = ToolButton(self._actions, icon="dock", tooltip=tooltip, width=24, height=22)
        button.set_command(
            lambda: menu.tk_popup(button.winfo_rootx(),
                                  button.winfo_rooty() + button.winfo_height())
        )
        button.pack(side="right", padx=1, pady=1)
        return button

    def set_title(self, text: str) -> None:
        self._title.configure(text=text)

    def drag_targets(self) -> list[tk.Widget]:
        """可以作为拖动把手的控件：整条标题栏 + 标题文字（右侧按钮区不算）。"""
        return [self, self._title]


class ScrollArea(tk.Frame):
    """可滚动容器：内容放进 ``self.body``，超出高度时右侧出现细滚动条。

    工具窗口里的属性行一多就会超出面板高度，包一层这个就不用担心被裁掉。
    """

    def __init__(self, master: tk.Misc, background: str | None = None) -> None:
        bg = background or T["panel"]
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=1, height=1)
        self.bar = ttk.Scrollbar(
            self, orient="vertical", style="VS.Vertical.TScrollbar", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._sync_region)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

    # ---- 尺寸 ---------------------------------------------------------
    def _sync_region(self, _event=None) -> None:
        self.canvas.configure(
            scrollregion=(0, 0, self.canvas.winfo_width(), max(1, self.body.winfo_reqheight()))
        )
        self._toggle_bar()

    def _sync_width(self, _event=None) -> None:
        self.canvas.itemconfigure(self._window, width=self.canvas.winfo_width())
        self._sync_region()

    def _toggle_bar(self) -> None:
        needed = self.body.winfo_reqheight() > self.canvas.winfo_height() + 1
        shown = bool(self.bar.winfo_manager())
        if needed and not shown:
            self.bar.pack(side="right", fill="y", before=self.canvas)
        elif not needed and shown:
            self.bar.pack_forget()

    # ---- 滚动 ---------------------------------------------------------
    def _on_wheel(self, event: tk.Event) -> str:
        if not self.bar.winfo_manager():
            return "break"
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def bind_wheels(self) -> None:
        """把滚轮事件绑到所有子控件上（子控件不会自动往上报给 canvas）。"""

        def walk(widget: tk.Misc) -> None:
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
            for child in widget.winfo_children():
                walk(child)

        walk(self.body)


class Separator(tk.Frame):
    """1px 分隔线。"""

    def __init__(self, master: tk.Misc, orient: str = "horizontal", color: str | None = None) -> None:
        if orient == "horizontal":
            super().__init__(master, bg=color or T["border"], height=1)
        else:
            super().__init__(master, bg=color or T["border"], width=1)


# ---------------------------------------------------------------- 文档标签页


class TabStrip(tk.Canvas):
    """文档区顶部的标签条：图标 + 标题，选中项带蓝色下划线。"""

    HEIGHT = 28
    TAB_PAD = 14

    def __init__(self, master: tk.Misc, on_select: Callable[[str], None],
                 on_close: Callable[[str], None] | None = None) -> None:
        super().__init__(master, bg=T["tab_idle"], height=self.HEIGHT,
                         highlightthickness=0, bd=0)
        self.on_select = on_select
        self.on_close = on_close
        self._tabs: list[tuple[str, str, str]] = []   # (key, title, icon)
        self._active = ""
        self._hitboxes: list[tuple[int, int, str, str]] = []  # x1, x2, key, part
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_tabs(self, tabs: list[tuple[str, str, str]], active: str) -> None:
        self._tabs = tabs
        self._active = active
        self.redraw()

    def set_active(self, key: str) -> None:
        self._active = key
        self.redraw()

    def _on_click(self, event: tk.Event) -> None:
        for x1, x2, key, part in self._hitboxes:
            if x1 <= event.x <= x2:
                if part == "close" and self.on_close:
                    self.on_close(key)
                else:
                    self.on_select(key)
                return

    def _on_motion(self, event: tk.Event) -> None:
        over = any(x1 <= event.x <= x2 and part == "close" for x1, x2, _k, part in self._hitboxes)
        self.configure(cursor="hand2" if over else "arrow")

    def redraw(self) -> None:
        self.delete("all")
        x = 0
        self._hitboxes = []
        for key, title, icon in self._tabs:
            active = key == self._active
            width = 0
            # 先量文字宽度
            probe = self.create_text(-1000, -1000, text=title, font=theme.font(9), anchor="w")
            text_w = self.bbox(probe)[2] - self.bbox(probe)[0]
            self.delete(probe)
            width = int(text_w) + self.TAB_PAD * 2 + 18 + (18 if self.on_close else 0)
            bg = T["tab_active"] if active else T["tab_idle"]
            self.create_rectangle(x, 0, x + width, self.HEIGHT, fill=bg, outline="")
            if active:
                self.create_rectangle(x, 0, x + width, 2, fill=T["accent"], outline="")
            icons.draw(self, icon, x + 14, self.HEIGHT / 2, 13,
                       T["accent"] if active else T["text_dim"])
            self.create_text(x + 26, self.HEIGHT / 2, text=title, anchor="w",
                             fill=T["text"] if active else T["text_dim"], font=theme.font(9))
            self._hitboxes.append((x, x + width, key, "tab"))
            if self.on_close:
                cx = x + width - 13
                self._hitboxes.append((cx - 9, cx + 9, key, "close"))
                icons.draw(self, "close", cx, self.HEIGHT / 2, 10,
                           T["text_dim"] if not active else T["text"])
            self.create_line(x + width, 0, x + width, self.HEIGHT, fill=T["border_soft"])
            x += width
        # 标签右侧空白
        self.create_rectangle(x, self.HEIGHT - 1, max(self.winfo_width(), x + 1),
                              self.HEIGHT, fill=T["border"], outline="")


# ---------------------------------------------------------------- 属性编辑器


class MiniSlider(tk.Canvas):
    """属性网格里的细滑杆：左侧轨道、右侧数值。"""

    def __init__(
        self,
        master: tk.Misc,
        from_: float,
        to: float,
        value: float,
        resolution: float = 0.1,
        formatter: Callable[[float], str] | None = None,
        command: Callable[[float], None] | None = None,
        background: str | None = None,
        suffix: str = "",
    ) -> None:
        super().__init__(master, height=20, bg=background or T["panel"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.from_ = float(from_)
        self.to = float(to)
        self.resolution = resolution
        self._value = float(value)
        self._formatter = formatter or (lambda v: f"{v:g}{suffix}")
        self._command = command
        self._enabled = True
        self._dragging = False
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._render()

    # ---- API ----------------------------------------------------------
    def get(self) -> float:
        return self._value

    def set(self, value: float, notify: bool = False) -> None:
        value = max(self.from_, min(self.to, float(value)))
        self._value = value
        self._render()
        if notify and self._command:
            self._command(self._value)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._render()

    # ---- 交互 ---------------------------------------------------------
    def _track_bounds(self) -> tuple[float, float]:
        return 2.0, max(12.0, self.winfo_width() - 52.0)

    def _value_from_x(self, x: float) -> float:
        left, right = self._track_bounds()
        ratio = min(1.0, max(0.0, (x - left) / max(1.0, right - left)))
        value = self.from_ + ratio * (self.to - self.from_)
        if self.resolution > 0:
            value = round(value / self.resolution) * self.resolution
        return min(self.to, max(self.from_, value))

    def _on_press(self, event: tk.Event) -> None:
        if not self._enabled:
            return
        self._dragging = True
        self.set(self._value_from_x(event.x), notify=True)

    def _on_drag(self, event: tk.Event) -> None:
        if self._dragging and self._enabled:
            self.set(self._value_from_x(event.x), notify=True)

    def _on_release(self, _event=None) -> None:
        self._dragging = False

    # ---- 绘制 ---------------------------------------------------------
    def _render(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w < 12:
            return
        left, right = self._track_bounds()
        mid = 10
        span = self.to - self.from_
        ratio = 0.0 if span <= 0 else (self._value - self.from_) / span
        knob_x = left + ratio * (right - left)
        track_color = T["border"] if self._enabled else T["border_soft"]
        fill_color = T["accent"] if self._enabled else T["muted"]
        self.create_line(left, mid, right, mid, fill=track_color, width=3, capstyle="round")
        self.create_line(left, mid, knob_x, mid, fill=fill_color, width=3, capstyle="round")
        self.create_oval(knob_x - 4, mid - 4, knob_x + 4, mid + 4,
                         fill="#FFFFFF" if self._enabled else T["text_faint"], outline="")
        self.create_text(w - 2, mid, text=self._formatter(self._value), anchor="e",
                         fill=T["text"] if self._enabled else T["text_faint"],
                         font=theme.font(8))


class Dropdown(tk.Canvas):
    """扁平下拉框：左边当前值、右边小三角，点开是深色菜单。"""

    def __init__(
        self,
        master: tk.Misc,
        items: list[tuple[str, str]] | None = None,
        value: str = "",
        command: Callable[[str], None] | None = None,
        background: str | None = None,
        height: int = 21,
    ) -> None:
        bg = background or T["panel"]
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self._items: list[tuple[str, str]] = list(items or [])  # (值, 显示文本)
        self._value = value
        self._command = command
        self._enabled = True
        self._hover = False
        self._menu: tk.Menu | None = None
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._render()

    # ---- API ----------------------------------------------------------
    def get(self) -> str:
        return self._value

    def set(self, value: str, notify: bool = False) -> None:
        self._value = value
        self._render()
        if notify and self._command:
            self._command(value)

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self._items = list(items)
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._render()

    # ---- 交互 ---------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._render()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._render()

    def _label(self, value: str) -> str:
        for key, text in self._items:
            if key == value:
                return text
        return value or "—"

    def _open(self, _event=None) -> None:
        if not self._enabled or not self._items:
            return
        menu = theme.make_menu(self)
        for key, text in self._items:
            menu.add_command(
                label=("● " if key == self._value else "   ") + text,
                command=lambda k=key: self.set(k, notify=True),
            )
        self._menu = menu
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            menu.grab_release()

    # ---- 绘制 ---------------------------------------------------------
    def _render(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 12 or height < 8:
            return
        self.configure(bg=T["panel"])
        fill = T["editor"] if self._enabled else T["panel_alt"]
        border = T["accent"] if (self._hover and self._enabled) else T["border"]
        self.create_rectangle(1, 1, width - 1, height - 1, fill=fill, outline=border)
        self.create_text(7, height / 2, anchor="w", text=self._label(self._value),
                         fill=T["text"] if self._enabled else T["text_faint"],
                         font=theme.font(9))
        cx = width - 10
        self.create_polygon(cx - 4, height / 2 - 2, cx + 4, height / 2 - 2, cx, height / 2 + 3,
                            fill=T["text_dim"] if self._enabled else T["text_faint"],
                            outline="")


class CheckBox(tk.Canvas):
    """VS 风格复选框。"""

    def __init__(self, master: tk.Misc, text: str, value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 background: str | None = None) -> None:
        bg = background or T["panel"]
        super().__init__(master, height=20, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._value = bool(value)
        self._command = command
        self._enabled = True
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", lambda _e: self._render())
        self._render()

    def get(self) -> bool:
        return self._value

    def set(self, value: bool, notify: bool = False) -> None:
        self._value = bool(value)
        self._render()
        if notify and self._command:
            self._command(self._value)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._render()

    def _on_click(self, _event=None) -> None:
        if not self._enabled:
            return
        self.set(not self._value, notify=True)

    def _render(self) -> None:
        self.delete("all")
        box = 13
        y0 = (20 - box) / 2
        outline = T["border"] if self._enabled else T["border_soft"]
        if self._value:
            self.create_rectangle(1, y0, 1 + box, y0 + box, fill=T["accent"],
                                  outline=T["accent"])
            self.create_line(4, y0 + 6.5, 6.5, y0 + 9.5, 10, y0 + 3.5,
                             fill="#FFFFFF", width=2, capstyle="round", joinstyle="round")
        else:
            self.create_rectangle(1, y0, 1 + box, y0 + box, fill=T["editor"], outline=outline)
        self.create_text(box + 8, 10, text=self._text, anchor="w",
                         fill=T["text"] if self._enabled else T["text_faint"],
                         font=theme.font(9))


class PropertyGrid(tk.Frame):
    """两列属性表：左侧标签、右侧编辑器，可插入分类标题。"""

    def __init__(self, master: tk.Misc, label_width: int = 84) -> None:
        super().__init__(master, bg=T["panel"])
        self.label_width = label_width

    def clear(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def add_category(self, title: str) -> None:
        row = tk.Frame(self, bg=T["panel_alt"], height=22)
        row.pack(fill="x", pady=(0, 1))
        row.pack_propagate(False)
        tk.Label(row, text=title, bg=T["panel_alt"], fg=T["text_dim"], font=theme.font(8, bold=True),
                 anchor="w", padx=8).pack(fill="both", expand=True)

    def add_row(self, label: str, builder, height: int = 24) -> tk.Widget:
        """插入一行属性。``builder(holder)`` 负责在 holder 里创建编辑器。"""
        row = tk.Frame(self, bg=T["panel"], height=height)
        row.pack(fill="x")
        row.grid_propagate(False)  # 行高由 height 决定，不被子控件撑开
        row.columnconfigure(0, minsize=self.label_width)
        row.columnconfigure(1, weight=1)
        tk.Label(row, text=label, bg=T["panel"], fg=T["text_dim"], anchor="w",
                 font=theme.font(9), padx=10).grid(row=0, column=0, sticky="nsew")
        holder = tk.Frame(row, bg=T["panel"])
        holder.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        widget = builder(holder)
        if not widget.winfo_manager():
            widget.pack(fill="both", expand=True)
        SepRow(row).place(x=0, y=height - 1, relwidth=1, height=1)
        return widget

    def add_text(self, label: str, value: str = "—", color: str | None = None) -> tk.Label:
        def build(holder: tk.Misc) -> tk.Label:
            text = tk.Label(holder, text=value, bg=T["panel"], fg=color or T["text"],
                            anchor="w", font=theme.font(9))
            text.pack(fill="x", pady=3)
            return text

        return self.add_row(label, build)  # type: ignore[return-value]


class SepRow(tk.Frame):
    """属性行底部的浅色分隔线。"""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["border_soft"])


# ---------------------------------------------------------------- 输出面板


class LogView(tk.Frame):
    """带时间戳与等级着色的输出视图（只读）。"""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["editor"])
        self.text = tk.Text(
            self, bg=T["editor"], fg=T["text"], insertbackground=T["text"],
            bd=0, highlightthickness=0, wrap="word", font=theme.mono(9),
            padx=10, pady=6, spacing1=1, spacing3=1, state="disabled",
        )
        self.scroll = tk.Scrollbar(
            self, orient="vertical", command=self.text.yview,
            bg=T["panel"], troughcolor=T["panel"], bd=0, width=10,
            activebackground=T["border"], highlightthickness=0,
        )
        self.text.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("time", foreground=T["text_faint"])
        self.text.tag_configure("info", foreground=T["text"])
        self.text.tag_configure("ok", foreground=T["ok"])
        self.text.tag_configure("warn", foreground=T["warn"])
        self.text.tag_configure("error", foreground=T["error"])

    def log(self, message: str, level: str = "info", stamp: bool = True) -> None:
        import time as _time

        self.text.configure(state="normal")
        if stamp:
            self.text.insert("end", f"[{_time.strftime('%H:%M:%S')}] ", "time")
        self.text.insert("end", message + "\n", level)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
