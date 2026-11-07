"""Visual Studio 2022 深色主题：调色板、字体、ttk 样式、标题栏。

所有界面模块都只从这里取颜色与字体，改主题只需改这一个文件。
"""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- 调色板
# 取值对齐 VS 2022 Dark：菜单/工具栏 #2D2D30、工具窗口 #252526、编辑器 #1E1E1E
PALETTE = {
    "chrome": "#2D2D30",        # 菜单栏 / 工具栏 / 面板标题
    "chrome_hi": "#3E3E40",     # 悬停
    "chrome_lo": "#333337",
    "panel": "#252526",         # 工具窗口背景
    "panel_alt": "#1F1F22",
    "editor": "#1E1E1E",        # 文档区背景
    "tab_active": "#1E1E1E",
    "tab_idle": "#2D2D30",
    "border": "#3F3F46",        # 分隔线 / 边框
    "border_soft": "#333337",
    "text": "#F1F1F1",
    "text_dim": "#A0A0A0",
    "text_faint": "#6D6D6D",
    "accent": "#007ACC",        # VS 蓝
    "accent_hi": "#1C97EA",
    "accent_dim": "#005A9E",
    "purple": "#68217A",        # VS 紫
    "selection": "#04395E",     # 列表选中
    "selection_idle": "#37373D",
    "grid": "#2A2A2C",
    "grid_strong": "#3A3A3D",
    "key_black": "#232326",
    "warn": "#D7BA7D",
    "error": "#F48771",
    "ok": "#6A9955",
    "muted": "#5A5A5C",
}

#: 音轨配色，取自 VS Code 语法高亮，天然“VS 味”
TRACK_COLORS = ["#4FC1FF", "#4EC9B0", "#DCDCAA", "#C586C0", "#CE9178", "#B5CEA8", "#D7BA7D"]

_FAMILY_CANDIDATES = ("Microsoft YaHei UI", "Segoe UI", "Helvetica")
_MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "Courier New")

_font_family = "Helvetica"
_mono_family = "Courier"


def init_fonts(root: tk.Misc) -> None:
    """挑选系统里可用的字体族（VS 用 Segoe UI，中文回退到 YaHei）。"""
    global _font_family, _mono_family
    families = set(tkfont.families(root))
    for name in _FAMILY_CANDIDATES:
        if name in families:
            _font_family = name
            break
    for name in _MONO_CANDIDATES:
        if name in families:
            _mono_family = name
            break


def font(size: int = 9, bold: bool = False, italic: bool = False) -> tuple:
    style = ""
    if bold and italic:
        style = "bold italic"
    elif bold:
        style = "bold"
    elif italic:
        style = "italic"
    return (_font_family, size, style) if style else (_font_family, size)


def mono(size: int = 9) -> tuple:
    return (_mono_family, size)


def color(key: str) -> str:
    return PALETTE[key]


def mix(color_a: str, color_b: str, alpha: float) -> str:
    """把 a 按 alpha 混到 b 上（用于弱化非选中音轨）。"""
    a = [int(color_a[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(round(x * alpha + y * (1 - alpha))):02x}" for x, y in zip(a, b))


def lighten(color_value: str, amount: float) -> str:
    return mix("#FFFFFF", color_value, amount)


# ---------------------------------------------------------------- ttk 样式


def apply_theme(root: tk.Misc) -> None:
    """统一配置 ttk 控件；在创建界面之前调用一次。"""
    init_fonts(root)
    root.configure(bg=PALETTE["panel"])

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(
        ".",
        background=PALETTE["panel"],
        foreground=PALETTE["text"],
        font=font(),
        fieldbackground=PALETTE["editor"],
        bordercolor=PALETTE["border"],
        focuscolor=PALETTE["accent"],
        troughcolor=PALETTE["editor"],
    )
    style.configure("TFrame", background=PALETTE["panel"])
    style.configure("Chrome.TFrame", background=PALETTE["chrome"])
    style.configure("Editor.TFrame", background=PALETTE["editor"])
    style.configure("TLabel", background=PALETTE["panel"], foreground=PALETTE["text"])
    style.configure("Dim.TLabel", background=PALETTE["panel"], foreground=PALETTE["text_dim"])

    # 按钮（工具栏用自绘，这里保留 ttk.Button 供对话框/兜底使用）
    style.configure(
        "TButton",
        background=PALETTE["chrome_hi"],
        foreground=PALETTE["text"],
        borderwidth=0,
        focusthickness=0,
        padding=(10, 5),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", PALETTE["accent_dim"]), ("active", lighten(PALETTE["chrome_hi"], 0.12))],
        foreground=[("disabled", PALETTE["text_faint"])],
    )

    # 输入框
    style.configure(
        "TEntry",
        fieldbackground=PALETTE["editor"],
        foreground=PALETTE["text"],
        insertcolor=PALETTE["text"],
        bordercolor=PALETTE["border"],
        lightcolor=PALETTE["border"],
        darkcolor=PALETTE["border"],
        padding=4,
    )
    style.map("TEntry", bordercolor=[("focus", PALETTE["accent"])])

    # 进度条
    style.configure(
        "TProgressbar",
        background=PALETTE["accent"],
        troughcolor=PALETTE["panel_alt"],
        bordercolor=PALETTE["panel_alt"],
        lightcolor=PALETTE["accent"],
        darkcolor=PALETTE["accent"],
        thickness=4,
    )

    # 树（解决方案资源管理器）
    style.configure(
        "VS.Treeview",
        background=PALETTE["panel"],
        fieldbackground=PALETTE["panel"],
        foreground=PALETTE["text"],
        borderwidth=0,
        rowheight=22,
        font=font(),
    )
    style.map(
        "VS.Treeview",
        background=[("selected", PALETTE["selection"])],
        foreground=[("selected", "#FFFFFF")],
    )
    style.layout(
        "VS.Treeview",
        [("Treeview.treearea", {"sticky": "nswe"})],
    )

    # 细滚动条（去掉箭头）
    for orient, prefix in (("vertical", "Vertical"), ("horizontal", "Horizontal")):
        style.layout(
            f"VS.{prefix}.TScrollbar",
            [
                (
                    f"{prefix}.Scrollbar.trough",
                    {
                        "children": [
                            (f"{prefix}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                        ],
                        "sticky": "ns" if orient == "vertical" else "ew",
                    },
                )
            ],
        )
        style.configure(
            f"VS.{prefix}.TScrollbar",
            background=PALETTE["border"],
            troughcolor=PALETTE["panel"],
            bordercolor=PALETTE["panel"],
            arrowcolor=PALETTE["panel"],
            width=10 if orient == "vertical" else 10,
        )
        style.map(
            f"VS.{prefix}.TScrollbar",
            background=[("active", PALETTE["text_faint"])],
        )

    # 分隔条（ttk.Panedwindow 备用）
    style.configure("Sash", sashthickness=4, gripcount=0, background=PALETTE["border"])


def enable_dark_titlebar(window: tk.Misc) -> None:
    """让 Windows 10/11 的原生标题栏也变深色，和 VS 一致。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        value = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE（Win10 20H1+），19 是更早版本的编号
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:  # noqa: BLE001 - 非 Windows 或旧系统，忽略
        pass


def make_menu(root: tk.Misc) -> tk.Menu:
    """创建与主题一致的菜单控件。"""
    return tk.Menu(
        root,
        tearoff=0,
        bg=PALETTE["chrome"],
        fg=PALETTE["text"],
        activebackground=PALETTE["accent"],
        activeforeground="#FFFFFF",
        disabledforeground=PALETTE["text_faint"],
        relief="flat",
        bd=0,
        font=font(),
    )
