"""菜单栏：文件 / 编辑 / 视图 / 渲染 / 帮助。

Tk 的原生菜单条在 Windows 上永远是浅色的，和 VS 的深色界面冲突，
所以这里用一排 Menubutton 自绘菜单栏，下拉菜单仍是 tk.Menu（可自由上色）。

**Tk 9.0 的坑**：``tk::MbPost`` 会检查菜单窗口是不是 Menubutton 的后代，
不是就直接拒绝弹出（"can't post ... it isn't a descendant of ..."）。
所以每个下拉菜单必须**以对应的 Menubutton 为 master** 创建；早先用菜单栏
Frame 当 master，5 个按钮点了都没有反应。此外 Tk 9 的 Menubutton 类绑定
只在 ``tk::Priv(inMenubutton)`` 已设置时才 post，因此这里用实例级
``<ButtonPress-1>`` 绑定显式接管点击，不依赖鼠标 Enter 状态。
"""

from __future__ import annotations

import re
import tkinter as tk

from . import theme

T = theme.PALETTE


def _mnemonic(label: str) -> str | None:
    """从「文件(F)」里取出 Alt 快捷键字母；中文首字不是合法 keysym。"""
    match = re.search(r"\(([A-Za-z])\)", str(label))
    return match.group(1).lower() if match else None


class MenuBar(tk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["chrome"], height=28)
        self.pack_propagate(False)
        self.buttons: list[tk.Menubutton] = []
        self._bound: dict[tk.Menubutton, tk.Menu] = {}
        self._open: tk.Menu | None = None

    # ---- 构建 ---------------------------------------------------------
    def add_button(self, label: str) -> tk.Menubutton:
        """先建按钮；菜单要以它为 master 创建，再交给 :meth:`attach`。"""
        button = tk.Menubutton(
            self, text=label, bg=T["chrome"], fg=T["text"],
            activebackground=T["chrome_hi"], activeforeground=T["text"],
            relief="flat", bd=0, highlightthickness=0, font=theme.font(9),
            padx=10, pady=2, cursor="arrow",
        )
        button.pack(side="left", padx=1, pady=3)
        self.buttons.append(button)
        return button

    def attach(self, button: tk.Menubutton, menu: tk.Menu) -> None:
        """把下拉菜单挂到按钮上，并接管点击 / Alt 快捷键。"""
        # Tk 9 的 tk::MbPost 要求菜单是按钮的后代，开发期直接挡住错误写法
        if not str(menu).startswith(str(button) + "."):
            raise ValueError(f"菜单 {menu} 必须以按钮 {button} 为 master 创建")
        button.configure(menu=menu)
        self._bound[button] = menu

        # 实例级绑定先于类绑定执行，返回 "break" 阻止 Tk 9 那套
        # 依赖 inMenubutton 状态的内置逻辑（也避免重复 post）
        button.bind(
            "<ButtonPress-1>",
            lambda _e, b=button, m=menu: self._toggle(b, m),
        )
        key = _mnemonic(button.cget("text"))
        if key:
            try:
                self.winfo_toplevel().bind(
                    f"<Alt-Key-{key}>",
                    lambda _e, b=button, m=menu: self._open_menu(b, m),
                )
            except tk.TclError:  # pragma: no cover - 平台不支持该 keysym
                pass

    # ---- 弹出 / 收起 --------------------------------------------------
    def _toggle(self, button: tk.Menubutton, menu: tk.Menu) -> str:
        # 总是「先收起再弹出」。winfo_ismapped() 对 tk.Menu 不可靠（菜单实际
        # 显示时也可能返回 0），拿它或 _open 做开关判断会出现「点第二次打不开」。
        self._open_menu(button, menu)
        return "break"

    def _open_menu(self, button: tk.Menubutton, menu: tk.Menu) -> str:
        self.close_menu()
        menu.post(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        self._open = menu
        return "break"

    def close_menu(self) -> None:
        """收起当前打开的菜单（菜单项被点后 Tk 会自己 unpost，这里只是同步状态）。"""
        if self._open is None:
            return
        try:
            self._open.unpost()
        except tk.TclError:  # pragma: no cover - 菜单已销毁
            pass
        self._open = None

    def menu_of(self, button: tk.Menubutton) -> tk.Menu | None:
        return self._bound.get(button)


def build(root: tk.Misc, app) -> MenuBar:
    """构建菜单栏，返回可 pack 的控件。"""
    bar = MenuBar(root)
    labels = ("文件(F)", "编辑(E)", "视图(V)", "渲染(R)", "帮助(H)")
    buttons = {label: bar.add_button(label) for label in labels}
    b_file, b_edit, b_view, b_render, b_help = (buttons[label] for label in labels)

    # 关键：每个下拉菜单的 master 是它自己的 Menubutton，不是 bar
    file_menu = theme.make_menu(b_file)
    file_menu.add_command(label="新建工程…", accelerator="Ctrl+N", command=app.on_new_project)
    file_menu.add_command(label="新建轨道…", accelerator="Ctrl+Shift+T", command=app.on_new_track)
    file_menu.add_command(label="打开工程…", accelerator="Ctrl+O", command=app.on_open)
    file_menu.add_separator()
    file_menu.add_command(label="应用音轨参数", accelerator="Ctrl+Enter", command=app.on_apply)
    file_menu.add_command(label="导出 WAV…", accelerator="Ctrl+S", command=app.on_export)
    file_menu.add_separator()
    file_menu.add_command(label="退出", command=app.on_quit)

    edit_menu = theme.make_menu(b_edit)
    edit_menu.add_command(label="静音选中音轨", accelerator="M",
                          command=lambda: app.on_toggle_selected("mute"))
    edit_menu.add_command(label="独奏选中音轨", accelerator="S",
                          command=lambda: app.on_toggle_selected("solo"))
    edit_menu.add_separator()
    edit_menu.add_command(label="复制选中音符", accelerator="Ctrl+D",
                          command=app.on_note_duplicate)
    edit_menu.add_command(label="删除选中音符", accelerator="Delete",
                          command=app.on_note_delete)
    edit_menu.add_command(label="上一个音符", accelerator="Ctrl+←",
                          command=lambda: app.on_note_step(-1))
    edit_menu.add_command(label="下一个音符", accelerator="Ctrl+→",
                          command=lambda: app.on_note_step(1))
    edit_menu.add_separator()
    edit_menu.add_command(label="清空输出", command=app.clear_output)

    # 视图：文档页 + 工具窗口（勾选式）+ 布局
    view_menu = theme.make_menu(b_view)
    view_menu.add_command(label="钢琴卷帘", accelerator="Ctrl+1",
                          command=lambda: app.goto_page("roll"))
    view_menu.add_command(label="波形", accelerator="Ctrl+2",
                          command=lambda: app.goto_page("wave"))
    view_menu.add_command(label="工程信息", accelerator="Ctrl+3",
                          command=lambda: app.goto_page("info"))
    view_menu.add_separator()

    view_vars: dict[str, tk.BooleanVar] = {}
    for key, label, accelerator in (
        ("explorer", "解决方案资源管理器", "Ctrl+Shift+E"),
        ("properties", "属性窗口", "F4"),
        ("note", "音符窗口", ""),
        ("output", "输出窗口", "Ctrl+L"),
    ):
        var = tk.BooleanVar(value=True)
        view_vars[key] = var
        view_menu.add_checkbutton(
            label=label, variable=var, accelerator=accelerator,
            command=lambda k=key: app.toggle_panel(k),
        )
    app._view_vars = view_vars

    view_menu.add_separator()
    dock_menu = theme.make_menu(view_menu)
    for key, label in (("explorer", "解决方案资源管理器"), ("properties", "属性"),
                       ("note", "音符"), ("output", "输出")):
        sub = theme.make_menu(dock_menu)
        for area, area_label in (("left", "左侧"), ("right", "右侧"), ("bottom", "底部")):
            sub.add_command(label=area_label,
                            command=lambda k=key, a=area: app.dock_panel(k, a))
        dock_menu.add_cascade(label=label, menu=sub)
    view_menu.add_cascade(label="停靠到", menu=dock_menu)
    view_menu.add_command(label="重置布局", command=app.reset_layout)
    view_menu.add_command(label="复位卷帘缩放", command=app.reset_zoom)

    render_menu = theme.make_menu(b_render)
    render_menu.add_command(label="渲染工程", accelerator="Ctrl+R", command=app.on_render)
    render_menu.add_command(label="预览（播放/暂停）", accelerator="Space", command=app.on_play)
    render_menu.add_command(label="停止预览", accelerator="Esc", command=app.on_stop_playback)
    render_menu.add_separator()
    render_menu.add_command(label="下载 / 更新音源", command=app.on_samples)
    render_menu.add_command(label="重新载入工程", accelerator="F5", command=app.on_refresh)

    help_menu = theme.make_menu(b_help)
    help_menu.add_command(label="工程格式说明", command=app.show_format_help)
    help_menu.add_command(label="关于 MusicGen", command=app.on_about)

    for button, menu in zip((b_file, b_edit, b_view, b_render, b_help),
                            (file_menu, edit_menu, view_menu, render_menu, help_menu)):
        bar.attach(button, menu)

    app.menus = {
        "file": file_menu,
        "edit": edit_menu,
        "view": view_menu,
        "render": render_menu,
        "help": help_menu,
    }
    return bar
