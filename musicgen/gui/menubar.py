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
            self.winfo_toplevel().bind(
                f"<Alt-Key-{key}>", lambda _e, m=menu, b=button: self._post(m, b)
            )
        except Exception:  # noqa: BLE001 - 绑定失败不影响鼠标操作
            pass
        return button

    @staticmethod
    def _post(menu: tk.Menu, button: tk.Menubutton) -> str:
        menu.post(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        return "break"


def build(root: tk.Misc, app) -> MenuBar:
    """构建菜单栏，返回可 pack 的控件。"""
    bar = MenuBar(root)

    file_menu = theme.make_menu(bar)
    file_menu.add_command(label="新建工程…", accelerator="Ctrl+N", command=app.on_new_project)
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
    edit_menu.add_command(label="清空输出", command=app.clear_output)

    view_menu = theme.make_menu(bar)
    view_menu.add_command(label="钢琴卷帘", accelerator="Ctrl+1",
                          command=lambda: app.goto_page("roll"))
    view_menu.add_command(label="波形", accelerator="Ctrl+2",
                          command=lambda: app.goto_page("wave"))
    view_menu.add_command(label="工程信息", accelerator="Ctrl+3",
                          command=lambda: app.goto_page("info"))
    view_menu.add_separator()
    view_menu.add_command(label="显示/隐藏输出面板", accelerator="Ctrl+L",
                          command=app.toggle_output)
    view_menu.add_command(label="复位卷帘缩放", command=app.reset_zoom)

    render_menu = theme.make_menu(bar)
    render_menu.add_command(label="渲染工程", accelerator="Ctrl+R", command=app.on_render)
    render_menu.add_command(label="播放", accelerator="Space", command=app.on_play)
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
