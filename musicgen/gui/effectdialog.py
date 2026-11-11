"""「音效」对话框：选类型 + 按注册表动态生成参数控件。

参数控件完全由 :mod:`musicgen.core.effects` 的 ``Param`` 元数据驱动：
小数 / 整数用滑杆，开关用复选框，枚举用下拉框。新增一个效果只需要在
注册表里声明 ``PARAMS``，这里不用改。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ..core.effects import (
    EffectError,
    Param,
    create_effect,
    describe_effects,
    effect_class,
)
from . import theme, widgets

T = theme.PALETTE


class EffectDialog(tk.Toplevel):
    """模态对话框。``result`` 为 ``(注册键, 参数 dict)``，取消时为 ``None``。"""

    def __init__(
        self,
        master: tk.Misc,
        effect_type: str = "stereo",
        params: dict | None = None,
        mode: str = "add",
    ) -> None:
        super().__init__(master)
        self.title("添加音效" if mode == "add" else "编辑音效")
        self.configure(bg=T["panel"])
        self.resizable(False, False)
        self.transient(master)
        self.result: tuple[str, dict] | None = None
        self._editors: dict[str, tk.Widget] = {}

        self._build(mode, effect_type, params or {})
        self.update_idletasks()
        self._center_on(master)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

    # ---- 构建 ---------------------------------------------------------
    def _build(self, mode: str, effect_type: str, params: dict) -> None:
        header = tk.Frame(self, bg=T["chrome"], height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="添加音效" if mode == "add" else "编辑音效",
                 bg=T["chrome"], fg=T["text"], font=theme.font(12, bold=True)).pack(
            side="left", padx=16)
        tk.Label(header, text="效果按列表顺序串联", bg=T["chrome"],
                 fg=T["text_faint"], font=theme.font(8)).pack(side="left")
        widgets.Separator(self).pack(fill="x")

        body = tk.Frame(self, bg=T["panel"])
        body.pack(fill="x", padx=18, pady=(12, 6))
        body.columnconfigure(1, weight=1)
        tk.Label(body, text="类型", bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 width=8, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        holder = tk.Frame(body, bg=T["panel"], height=23, width=300)
        holder.grid(row=0, column=1, sticky="ew", pady=4)
        holder.pack_propagate(False)
        self.type_dropdown = widgets.Dropdown(
            holder, self._type_items(), effect_type, self._on_type_change
        )
        self.type_dropdown.pack(fill="x")

        self.description = tk.Label(
            body, text="", bg=T["panel"], fg=T["text_faint"], font=theme.font(8),
            anchor="w", justify="left", wraplength=360,
        )
        self.description.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        widgets.Separator(self).pack(fill="x", pady=(6, 0))
        self.params_frame = tk.Frame(self, bg=T["panel"])
        self.params_frame.pack(fill="x", padx=18, pady=(8, 10))

        widgets.Separator(self).pack(fill="x")
        footer = tk.Frame(self, bg=T["panel"], height=54)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        tk.Button(footer, text="确定", command=self._ok, bg=T["accent_dim"], fg="#FFFFFF",
                  activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
                  font=theme.font(9), padx=22, pady=6, cursor="hand2").pack(
            side="right", padx=(6, 16), pady=12)
        tk.Button(footer, text="取消", command=self._cancel, bg=T["chrome_hi"], fg=T["text"],
                  activebackground=T["chrome_lo"], activeforeground=T["text"], relief="flat",
                  bd=0, font=theme.font(9), padx=18, pady=6, cursor="hand2").pack(
            side="right", pady=12)

        self._fill_params(effect_type, params)

    @staticmethod
    def _type_items() -> list[tuple[str, str]]:
        return [
            (key, label if label.lower() == key else f"{label}（{key}）")
            for key, label, _desc, _params in describe_effects()
        ]

    def _fill_params(self, effect_type: str, params: dict) -> None:
        for child in self.params_frame.winfo_children():
            child.destroy()
        self._editors.clear()
        cls = effect_class(effect_type)
        self.description.configure(text=cls.DESCRIPTION)
        usable = {k: v for k, v in params.items() if cls.param(k) is not None}
        values = create_effect(effect_type, usable).values()
        for param in cls.PARAMS:
            self._add_param_row(param, values[param.key])

    def _add_param_row(self, param: Param, value) -> None:
        row = tk.Frame(self.params_frame, bg=T["panel"], height=26)
        row.pack(fill="x")
        row.pack_propagate(False)
        tk.Label(row, text=param.label, bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 width=8, anchor="w").pack(side="left")
        holder = tk.Frame(row, bg=T["panel"])
        holder.pack(side="left", fill="both", expand=True)

        if param.kind == "bool":
            editor: tk.Widget = widgets.CheckBox(holder, "", bool(value))
        elif param.kind == "choice":
            editor = widgets.Dropdown(
                holder, [(choice, choice) for choice in param.choices], str(value)
            )
        else:
            low = param.minimum if param.minimum is not None else 0.0
            high = param.maximum if param.maximum is not None else 1.0
            editor = widgets.MiniSlider(
                holder, low, high, float(value), param.step or 0.01,
                lambda v, p=param: p.display(v),
            )
        editor.pack(fill="both", expand=True)
        self._editors[param.key] = editor

    def _on_type_change(self, effect_type: str) -> None:
        if self.type_dropdown.get() != effect_type:
            self.type_dropdown.set(effect_type)
        self._fill_params(effect_type, {})

    def _center_on(self, master: tk.Misc) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - width) // 2
            y = master.winfo_rooty() + (master.winfo_height() - height) // 3
        except tk.TclError:
            x = y = 200
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ---- 动作 ---------------------------------------------------------
    def values(self) -> tuple[str, dict]:
        """收集参数；只保留与注册表默认值不同的项，XML 保持简短。"""
        key = self.type_dropdown.get()
        cls = effect_class(key)
        params: dict = {}
        for param in cls.PARAMS:
            editor = self._editors.get(param.key)
            if editor is None:
                continue
            value = editor.get()  # type: ignore[attr-defined]
            if param.kind == "int":
                value = int(round(float(value)))
            if param.format(value) != param.format(param.default):
                params[param.key] = value
        return key, params

    def _ok(self) -> None:
        key, params = self.values()
        try:
            create_effect(key, params)
        except EffectError as exc:
            messagebox.showerror("参数不合法", str(exc), parent=self)
            return
        self.result = (key, params)
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()
