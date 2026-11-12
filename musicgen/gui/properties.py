"""右侧「属性」工具窗口：编辑选中音轨的混音与播放参数。

内容放在可滚动容器里：窗口压矮时不会把行裁掉，滚轮即可看全。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..core.effects import EffectSpec
from ..samples import fetch
from . import theme, widgets
from .effectdialog import EffectDialog

T = theme.PALETTE


class PropertiesPanel(tk.Frame):
    PREFERRED_HEIGHT = 420

    def __init__(self, master: tk.Misc, on_apply: Callable[[], None]) -> None:
        super().__init__(master, bg=T["panel"])
        self.on_apply = on_apply
        self.track = None

        self.header = widgets.PanelHeader(self, "属性")
        self.header.pack(fill="x")

        footer = tk.Frame(self, bg=T["panel"])
        footer.pack(side="bottom", fill="x", pady=(8, 8), padx=10)
        self.btn_apply = tk.Button(
            footer, text="应用并保存", command=self._apply, bg=T["accent_dim"], fg="#FFFFFF",
            activebackground=T["accent"], activeforeground="#FFFFFF", relief="flat", bd=0,
            font=theme.font(9), cursor="hand2", pady=5,
        )
        self.btn_apply.pack(fill="x")
        self.hint = tk.Label(footer, text="修改后写入音轨 XML", bg=T["panel"], fg=T["text_faint"],
                             font=theme.font(8))
        self.hint.pack(anchor="w", pady=(4, 0))

        self.scroll = widgets.ScrollArea(self, background=T["panel"])
        self.scroll.pack(fill="both", expand=True)
        self.grid = widgets.PropertyGrid(self.scroll.body, label_width=76)
        self.grid.pack(fill="x")

        self.labels: dict[str, tk.Label] = {}
        self.vars: dict[str, object] = {}
        self._effect_sel: int | None = None

        self.grid.add_category("常规")
        self.labels["name"] = self.grid.add_text("名称")
        self.instrument = self.grid.add_row(
            "乐器",
            lambda holder: widgets.Dropdown(holder, instrument_items(), "", self._on_instrument),
        )
        self.labels["samples"] = self.grid.add_text("采样", color=T["text_dim"])
        self.labels["notes"] = self.grid.add_text("音符数")
        self.labels["file"] = self.grid.add_text("文件", color=T["text_dim"])

        self.grid.add_category("混音")
        self.vars["gain_db"] = self._slider("音量", -24.0, 12.0, 0.0, 0.5,
                                            lambda v: f"{v:+.1f} dB")
        self.vars["pan"] = self._slider("声像", -1.0, 1.0, 0.0, 0.05, self._format_pan)
        self.vars["reverb"] = self._slider("混响", 0.0, 1.0, 0.0, 0.01, lambda v: f"{v:.2f}")

        self.grid.add_category("效果")
        self.effect_box = tk.Frame(self.grid, bg=T["panel"])
        self.effect_box.pack(fill="x", padx=10, pady=(6, 4))
        self.effect_list = tk.Frame(self.effect_box, bg=T["panel"])
        self.effect_list.pack(fill="x")
        tk.Label(self.effect_box, text="按从上到下的顺序串联", bg=T["panel"],
                 fg=T["text_faint"], font=theme.font(8)).pack(anchor="w", pady=(3, 0))
        buttons = tk.Frame(self.effect_box, bg=T["panel"])
        buttons.pack(fill="x", pady=(6, 2))
        self.btn_effect_add = self._tiny_button(buttons, "添加…", self._add_effect)
        self.btn_effect_edit = self._tiny_button(buttons, "编辑…", self._edit_effect)
        self.btn_effect_del = self._tiny_button(buttons, "删除", self._remove_effect)

        self.grid.add_category("播放")
        self.vars["loop"] = self._check("循环")
        self.vars["mute"] = self._check("静音")
        self.vars["solo"] = self._check("独奏")

        row = tk.Frame(self.grid, bg=T["panel"])
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text="循环长度", bg=T["panel"], fg=T["text_dim"], font=theme.font(9),
                 padx=10).pack(side="left")
        self.vars["loop_len"] = tk.StringVar(value="")
        self.entry_loop = ttk.Entry(row, textvariable=self.vars["loop_len"], width=8,
                                    style="TEntry", font=theme.font(9))
        self.entry_loop.pack(side="left", padx=(0, 6))
        tk.Label(row, text="拍", bg=T["panel"], fg=T["text_faint"], font=theme.font(8)).pack(side="left")

        self.scroll.bind_wheels()
        self.set_track(None)

    # ---- 构建小工具 ---------------------------------------------------
    def _slider(self, label: str, low: float, high: float, value: float,
                resolution: float, formatter) -> widgets.MiniSlider:
        return self.grid.add_row(  # type: ignore[return-value]
            label,
            lambda holder: widgets.MiniSlider(holder, low, high, value, resolution, formatter),
        )

    def _check(self, label: str) -> widgets.CheckBox:
        return self.grid.add_row(  # type: ignore[return-value]
            "", lambda holder: widgets.CheckBox(holder, label)
        )

    def _tiny_button(self, parent: tk.Misc, text: str, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, bg=T["chrome_hi"], fg=T["text"],
            activebackground=T["chrome_lo"], activeforeground=T["text"], relief="flat",
            bd=0, font=theme.font(8), padx=8, pady=2, cursor="hand2", state="disabled",
        )
        button.pack(side="left", padx=(0, 4))
        return button

    # ---- 效果链 -------------------------------------------------------
    def _rebuild_effects(self) -> None:
        for child in self.effect_list.winfo_children():
            child.destroy()
        effects = self.track.effects if self.track is not None else []
        if self._effect_sel is not None and not 0 <= self._effect_sel < len(effects):
            self._effect_sel = None
        if not effects:
            tk.Label(
                self.effect_list, text="（无音效）" if self.track is not None else "—",
                bg=T["panel"], fg=T["text_faint"], font=theme.font(8),
            ).pack(anchor="w", pady=2)
        for index, spec in enumerate(effects):
            selected = index == self._effect_sel
            row = tk.Frame(self.effect_list, cursor="hand2",
                           bg=T["selection"] if selected else T["panel"])
            row.pack(fill="x")
            name = tk.Label(
                row, text=f"{index + 1}. {spec.label()}", bg=row["bg"],
                fg=T["text"] if selected else T["text_dim"], font=theme.font(9), anchor="w",
            )
            name.pack(side="left", padx=(4, 6), pady=2)
            summary = tk.Label(
                row, text=spec.summary(), bg=row["bg"], fg=T["text_faint"],
                font=theme.font(8), anchor="w",
            )
            summary.pack(side="left", fill="x", expand=True, padx=(0, 4))
            for widget in (row, name, summary):
                widget.bind("<Button-1>", lambda _e, i=index: self._select_effect(i))

        editable = self.track is not None
        self.btn_effect_add.configure(state="normal" if editable else "disabled")
        has_selection = editable and self._effect_sel is not None
        for button in (self.btn_effect_edit, self.btn_effect_del):
            button.configure(state="normal" if has_selection else "disabled")

    def _select_effect(self, index: int) -> None:
        self._effect_sel = index
        self._rebuild_effects()

    def _add_effect(self) -> None:
        if self.track is None:
            return
        dialog = EffectDialog(self, mode="add")
        self.wait_window(dialog)
        if dialog.result is None:
            return
        key, params = dialog.result
        self.track.effects.append(EffectSpec(key, params))
        self._effect_sel = len(self.track.effects) - 1
        self._rebuild_effects()

    def _edit_effect(self) -> None:
        if self.track is None or self._effect_sel is None:
            return
        index = self._effect_sel
        spec = self.track.effects[index]
        dialog = EffectDialog(self, effect_type=spec.type, params=spec.params, mode="edit")
        self.wait_window(dialog)
        if dialog.result is None:
            return
        key, params = dialog.result
        self.track.effects[index] = EffectSpec(key, params)
        self._rebuild_effects()

    def _remove_effect(self) -> None:
        if self.track is None or self._effect_sel is None:
            return
        del self.track.effects[self._effect_sel]
        self._effect_sel = (
            min(self._effect_sel, len(self.track.effects) - 1) if self.track.effects else None
        )
        self._rebuild_effects()

    @staticmethod
    def _format_pan(value: float) -> str:
        if abs(value) < 0.02:
            return "居中"
        return f"{'右' if value > 0 else '左'} {abs(value):.2f}"

    # ---- 数据 ---------------------------------------------------------
    def _on_instrument(self, _value: str) -> None:
        """换乐器后立刻刷新采样状态（保存仍由「应用并保存」负责）。"""
        self._refresh_sample_state()

    def _refresh_sample_state(self, instrument_note: str = "") -> None:
        key = fetch.resolve(self.instrument.get())
        if key in fetch.BUILTIN:
            self.labels["samples"].configure(text="内置合成 · 无需采样", fg=T["ok"])
            return
        if key not in fetch.SOURCES:
            self.labels["samples"].configure(text="未注册 · 用合成器", fg=T["warn"])
            return
        have, want = fetch.instrument_status(key)
        if have >= want:
            self.labels["samples"].configure(text=f"{have}/{want} 个采样", fg=T["ok"])
        else:
            self.labels["samples"].configure(
                text=f"{have}/{want} 个采样 · 缺，用合成器{instrument_note}", fg=T["warn"]
            )

    def set_track(self, track, instrument_note: str = "") -> None:
        self.track = track
        editable = track is not None
        for widget in (self.btn_apply,):
            widget.configure(state="normal" if editable else "disabled")
        for key in ("gain_db", "pan", "reverb", "loop", "mute", "solo"):
            self.vars[key].set_enabled(editable)  # type: ignore[attr-defined]
        self.entry_loop.configure(state="normal" if editable else "disabled")
        is_audio = track is not None and track.audio_path is not None
        is_patch = track is not None and getattr(track, "patch_path", None) is not None
        # 音频轨与脚本轨的“乐器”不由下拉框决定，锁住避免误以为能换
        self.instrument.set_enabled(editable and not (is_audio or is_patch))  # type: ignore[attr-defined]

        if track is None:
            self.labels["name"].configure(text="未选中音轨")
            self.instrument.set("")
            self.labels["samples"].configure(text="—", fg=T["text_dim"])
            self.labels["notes"].configure(text="—")
            self.labels["file"].configure(text="—")
            self._effect_sel = None
            self._rebuild_effects()
            return

        key = fetch.resolve(track.instrument)
        items = instrument_items()
        if is_audio:
            items.insert(0, ("audio", "音频轨（原样混音）"))
        elif is_patch:
            items.insert(0, ("synth", "合成音脚本（patch）"))
        elif key not in dict(items):  # 未注册的乐器也让它显示出来
            items.append((key, f"{key}（未注册）"))
        self.instrument.set_items(items)
        self.instrument.set("audio" if is_audio else ("synth" if is_patch else key))
        if is_audio:
            self.labels["samples"].configure(
                text=f"音频 {track.audio_duration:.1f}s · {track.audio_path.name}", fg=T["ok"]
            )
        elif is_patch:
            self.labels["samples"].configure(
                text=f"合成音脚本 · {track.patch_path.name}", fg=T["ok"]
            )
        else:
            self._refresh_sample_state(instrument_note)

        self.labels["name"].configure(text=track.name)
        self.labels["notes"].configure(
            text=f"音频 {track.audio_duration:.1f}s" if is_audio else str(len(track.notes))
        )
        self.labels["file"].configure(text=track.path.name if track.path else "—")
        self.vars["gain_db"].set(track.gain_db)      # type: ignore[attr-defined]
        self.vars["pan"].set(track.pan)              # type: ignore[attr-defined]
        self.vars["reverb"].set(track.reverb)        # type: ignore[attr-defined]
        self.vars["loop"].set(track.loop)            # type: ignore[attr-defined]
        self.vars["mute"].set(track.mute)            # type: ignore[attr-defined]
        self.vars["solo"].set(track.solo)            # type: ignore[attr-defined]
        self.vars["loop_len"].set("" if not track.loop_len else f"{track.loop_len:g}")
        self._effect_sel = None
        self._rebuild_effects()

    def sync_flags(self) -> None:
        """外部（右键菜单等）改了静音/独奏后，同步复选框。"""
        if self.track is None:
            return
        self.vars["mute"].set(self.track.mute)  # type: ignore[attr-defined]
        self.vars["solo"].set(self.track.solo)  # type: ignore[attr-defined]

    def values(self) -> dict:
        return {
            "instrument": self.instrument.get(),
            "gain_db": self.vars["gain_db"].get(),
            "pan": self.vars["pan"].get(),
            "reverb": self.vars["reverb"].get(),
            "loop": self.vars["loop"].get(),
            "mute": self.vars["mute"].get(),
            "solo": self.vars["solo"].get(),
            "loop_len": self.vars["loop_len"].get().strip(),
        }

    def _apply(self) -> None:
        self.on_apply()


def instrument_items() -> list[tuple[str, str]]:
    """下拉框选项：已注册乐器（中文名 + 注册键）。"""
    return [(key, fetch.display(key)) for key in fetch.instrument_keys()]
