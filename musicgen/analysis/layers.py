"""分层（layer）：把一条分轨里的音按音区分成几层。

Demucs 的 ``other`` 常常同时装着铺底 pad、主旋律与高音点缀；只把整条分轨当成
一件乐器，逆向出来必然「糊」。这一层按**音区**把音符切成 1–3 组：

* 只在音符之间的音高间隙足够大（默认 ≥7 个半音）处切；
* 切完两侧都要有足够多的音（默认 ≥6 个），避免切出孤零零的一两个音；
* 分层数上限默认 3，按间隙从大到小依次切。

这是**可解释的确定性规则**，不训练模型；比 k-means 更稳定，也方便大模型复核：
简报里会给出每层的音域、平均时值、持续音占比，由大模型决定「这层是 pad 还是
被误检的低音残响」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .pitch import NoteEvent


@dataclass
class Layer:
    """一组音区相近的音符。``index`` 从 1 开始，低音区在前。"""

    index: int
    notes: list[NoteEvent] = field(default_factory=list)
    low: int = 0
    high: int = 0
    median: float = 0.0
    sustain_ratio: float = 0.0   # 时值 ≥ 0.4s 的音符占比，pad/lead 的粗略区分
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "layer": self.index,
            "notes": len(self.notes),
            "pitch_low": self.low,
            "pitch_high": self.high,
            "pitch_median": round(self.median, 1),
            "sustain_ratio": round(self.sustain_ratio, 2),
        }


def _make_layer(index: int, notes: list[NoteEvent]) -> Layer:
    pitches = [int(note.midi) for note in notes]
    durations = [float(note.duration) for note in notes]
    sustained = sum(1 for value in durations if value >= 0.4) / max(1, len(durations))
    return Layer(
        index=index,
        notes=sorted(notes, key=lambda note: (note.start, note.midi)),
        low=min(pitches), high=max(pitches),
        median=float(np.median(pitches)),
        sustain_ratio=float(sustained),
        confidence=float(np.mean([note.confidence for note in notes])) if notes else 0.0,
    )


def split_layers(
    notes: list[NoteEvent],
    *,
    max_layers: int = 3,
    min_notes: int = 6,
    min_gap: int = 7,
) -> list[Layer]:
    """按音区把音符分层；不满足条件时返回单层。"""
    ordered = sorted(notes, key=lambda note: note.midi)
    if not ordered:
        return []
    if max_layers <= 1 or len(ordered) < min_notes * 2:
        return [_make_layer(1, ordered)]

    groups: list[list[NoteEvent]] = [ordered]
    for _ in range(max_layers - 1):
        best_index = -1
        best_gap = 0
        for position, group in enumerate(groups):
            if len(group) < min_notes * 2:
                continue
            gap, split_at = _largest_gap([note.midi for note in group], min_notes, min_gap)
            if gap > best_gap:
                best_gap, best_index = gap, position
        if best_index < 0:
            break
        group = groups[best_index]
        _, split_at = _largest_gap([note.midi for note in group], min_notes, min_gap)
        groups = groups[:best_index] + [group[:split_at], group[split_at:]] + groups[best_index + 1:]

    groups.sort(key=lambda group: float(np.median([note.midi for note in group])))
    return [_make_layer(index, group) for index, group in enumerate(groups, 1)]


def _largest_gap(pitches: list[int], min_notes: int, min_gap: int) -> tuple[int, int]:
    """返回 ``(间隙, 分割位置)``；分割位置是下半组的音符数。"""
    ordered = sorted(pitches)
    best_gap = 0
    best_split = 0
    for position in range(min_notes, len(ordered) - min_notes + 1):
        gap = ordered[position] - ordered[position - 1]
        if gap > best_gap:
            best_gap = gap
            best_split = position
    return (best_gap if best_gap >= min_gap else 0), best_split
