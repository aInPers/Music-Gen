"""菜单栏：文件 / 编辑 / 视图 / 渲染 / 帮助。

Tk 的原生菜单条在 Windows 上永远是浅色的，和 VS 的深色界面冲突，
所以这里用一排 Menubutton 自绘菜单栏，下拉菜单仍是 tk.Menu（可自由上色）。
"""

from __future__ import annotations

import tkinter as tk

from . import theme

T = theme.PALETTE


class MenuBar(tk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["chrome"], height=28)
        self.pack_propagate(False)
        self.buttons: list[tk.Menubutton] = []

    def add_menu(self, label: str, menu: tk.Menu) -> tk.Menubutton:
        button = tk.Menubutton(
            self, text=label, menu=menu, bg=T["chrome"], fg=T["text"],
            activebackground=T["chrome_hi"], activeforeground=T["text"],
            relief="flat", bd=0, highlightthickness=0, font=theme.font(9),
            padx=10, pady=2, cursor="arrow",
        )
        button.pack(side="left", padx=1, pady=3)
        self.buttons.append(button)
        key = label[0].lower()
        try:
            self.winfo_toplevel().bind(f"<Alt-Key-{key}>", self._post_at(menu, button))
        except Exception:  # noqa: BLE001 - 绑定失败不影响鼠标操作
            pass
        return button

    @staticmethod
    def _post_at(menu: tk.Menu, button: tk.Menubutton):
        def post(_event=None) -> str:
            menu.post(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
            return "break"

        return post


def build(root: tk.Misc, app) -> MenuBar:
    """构建菜单栏，返回可 pack 的控件。"""
    bar = MenuBar(root)

    file_menu = theme.make_menu(bar)
    file_menu.add_command(label="新建工程…", accelerator="Ctrl+N", command=app.on_new_project)
    file_menu.add_command(label="新建轨道…", accelerator="Ctrl+Shift+T", command=app.on_new_track)
    file_menu.add_command(label="打开工程…", accelerator="Ctrl+O", command=app.on_open)
    file_menu.add_separator()
    file_menu.add_command(label="应用音轨参数", accelerator="Ctrl+Enter", command=app.on_apply)
    file_menu.add_command(label="导出 WAV…", accelerator="Ctrl+S", command=app.on_export)
    file_menu.add_separator()
    file_menu.add_command(label="退出", command=app.on_quit)

    edit_menu = theme.make_menu(bar)
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
    view_menu = theme.make_menu(bar)
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

    render_menu = theme.make_menu(bar)
    render_menu.add_command(label="渲染工程", accelerator="Ctrl+R", command=app.on_render)
    render_menu.add_command(label="预览（播放/暂停）", accelerator="Space", command=app.on_play)
    render_menu.add_command(label="停止预览", accelerator="Esc", command=app.on_stop_playback)
    render_menu.add_separator()
    render_menu.add_command(label="下载 / 更新音源", command=app.on_samples)
    render_menu.add_command(label="重新载入工程", accelerator="F5", command=app.on_refresh)

    help_menu = theme.make_menu(bar)
    help_menu.add_command(label="工程格式说明", command=app.show_format_help)
    help_menu.add_command(label="关于 MusicGen", command=app.on_about)

    bar.add_menu("文件(F)", file_menu)
    bar.add_menu("编辑(E)", edit_menu)
    bar.add_menu("视图(V)", view_menu)
    bar.add_menu("渲染(R)", render_menu)
    bar.add_menu("帮助(H)", help_menu)

    app.menus = {
        "file": file_menu,
        "edit": edit_menu,
        "view": view_menu,
        "render": render_menu,
        "help": help_menu,
    }
    return bar
