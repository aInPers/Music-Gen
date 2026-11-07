"""文档页：工程信息（只读文本视图，列出参数、音轨与警告）。"""

from __future__ import annotations

import tkinter as tk

from . import theme

T = theme.PALETTE


class ProjectInfoPage(tk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=T["editor"])
        self.text = tk.Text(
            self, bg=T["editor"], fg=T["text"], bd=0, highlightthickness=0,
            wrap="none", font=theme.mono(9), padx=16, pady=12, spacing1=2, spacing3=2,
            state="disabled", insertbackground=T["text"],
        )
        scroll = tk.Scrollbar(
            self, orient="vertical", command=self.text.yview, bg=T["panel"],
            troughcolor=T["panel"], bd=0, width=10, activebackground=T["border"],
            highlightthickness=0,
        )
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        self.text.tag_configure("h1", foreground="#4FC1FF", font=theme.mono(11), spacing1=6,
                                spacing3=6)
        self.text.tag_configure("h2", foreground="#4EC9B0", font=theme.mono(9), spacing1=10,
                                spacing3=4)
        self.text.tag_configure("key", foreground=T["text_dim"])
        self.text.tag_configure("val", foreground=T["text"])
        self.text.tag_configure("warn", foreground=T["warn"])
        self.text.tag_configure("mute", foreground=T["text_faint"])

    def set_project(self, project) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if project is None:
            self.text.insert("end", "尚未打开工程。\n")
            self.text.configure(state="disabled")
            return

        def line(key: str, value: str, tag: str = "val") -> None:
            self.text.insert("end", f"{key:<14}", "key")
            self.text.insert("end", f"{value}\n", tag)

        self.text.insert("end", f"{project.name}\n", "h1")
        self.text.insert("end", f"{project.path}\n", "key")
        length = project.compute_length_seconds()
        self.text.insert("end", "\n")
        line("速度", f"{project.bpm:g} BPM")
        line("拍号", f"{project.beats_per_bar}/4")
        line("采样率", f"{project.sample_rate} Hz")
        line("时长", f"{length:.2f} 秒（含 {project.tail_seconds:g} 秒余韵）")
        line("母带增益", f"{project.master_gain_db:+g} dB")
        line("归一化", "开" if project.normalize else "关")

        self.text.insert("end", "\n音轨\n", "h2")
        header = f"{'#':<3}{'名称':<16}{'乐器':<8}{'音符':>5}  {'音量':>7}  {'声像':>6}  {'混响':>5}  说明"
        self.text.insert("end", header + "\n", "key")
        for index, track in enumerate(project.tracks, 1):
            flags = []
            if track.mute:
                flags.append("静音")
            if track.solo:
                flags.append("独奏")
            if track.loop:
                period = track.period_beats(project.beats_per_bar)
                times = "无限" if track.loop_count <= 0 else f"{track.loop_count} 次"
                flags.append(f"循环 {period:g} 拍 × {times}")
            if track.bpm:
                flags.append(f"{track.bpm:g} BPM")
            self.text.insert(
                "end",
                f"{index:<3}{track.name:<16}{track.instrument:<8}{len(track.notes):>5}  "
                f"{track.gain_db:>+6.1f}dB  {track.pan:>+6.2f}  {track.reverb:>5.2f}  "
                f"{'、'.join(flags)}\n",
                "mute" if track.mute else "val",
            )

        if project.warnings:
            self.text.insert("end", "\n警告\n", "h2")
            for warning in project.warnings:
                self.text.insert("end", f"• {warning}\n", "warn")

        self.text.configure(state="disabled")
