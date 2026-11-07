"""工程渲染：音符展开循环 → 采样合成 → 轨道处理（增益/声像/混响）→ 母带。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .instruments import InstrumentRegistry
from .project import Project, Track

ProgressFn = Callable[[float, str], None]

#: 母带目标峰值（-1dBFS）
TARGET_PEAK = 10 ** (-1.0 / 20.0)
#: 结尾防爆音淡出（秒）
END_FADE = 0.01
#: 力度 / 时长量化，便于复用在循环里重复出现的音符
_VEL_Q = 0.05
_DUR_Q = 0.005


@dataclass
class TrackStat:
    name: str
    instrument: str
    notes: int
    peak: float
    seconds: float


@dataclass
class RenderResult:
    audio: np.ndarray  # (2, N) float32
    sample_rate: int
    duration: float
    peak: float
    rms: float
    notes_rendered: int
    track_stats: list[TrackStat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def peak_db(self) -> float:
        return 20 * math.log10(max(self.peak, 1e-12))

    @property
    def rms_db(self) -> float:
        return 20 * math.log10(max(self.rms, 1e-12))


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _next_pow2(value: int) -> int:
    return 1 << max(1, int(value - 1).bit_length())


_ir_cache: dict[tuple[int, int], np.ndarray] = {}


def _make_impulse_response(sample_rate: int, amount: float) -> np.ndarray:
    """合成一段指数衰减的噪声脉冲响应（立体声去相关）。"""
    key = (sample_rate, int(round(amount * 20)))
    if key in _ir_cache:
        return _ir_cache[key]
    amount = max(0.0, min(1.0, amount))
    decay_s = 0.55 + 1.9 * amount
    length = int(decay_s * sample_rate)
    t = np.arange(length, dtype=np.float32) / sample_rate
    envelope = np.exp(-t * (5.2 / decay_s)).astype(np.float32)
    rng = np.random.default_rng(20240501)
    ir = np.empty((2, length), dtype=np.float32)
    for ch in range(2):
        noise = rng.standard_normal(length).astype(np.float32)
        # 简易低通（滑动平均），让残响更暗
        window = int(max(2, 0.0035 * sample_rate * (0.4 + amount)))
        kernel = np.ones(window, dtype=np.float32) / window
        noise = np.convolve(noise, kernel, mode="same")
        ir[ch] = noise * envelope
    # 预延迟
    pre = int(0.015 * sample_rate)
    ir = np.hstack([np.zeros((2, pre), dtype=np.float32), ir])
    energy = float(np.sqrt(np.sum(ir ** 2)))
    if energy > 0:
        ir /= energy
    _ir_cache[key] = ir
    return ir


def _apply_reverb(audio: np.ndarray, amount: float, sample_rate: int) -> np.ndarray:
    if amount <= 1e-3 or audio.shape[1] == 0:
        return audio
    ir = _make_impulse_response(sample_rate, amount)
    total = audio.shape[1] + ir.shape[1] - 1
    n = _next_pow2(total)
    out = np.empty_like(audio)
    for ch in range(2):
        spec = np.fft.rfft(audio[ch], n)
        resp = np.fft.rfft(ir[ch], n)
        out[ch] = np.fft.irfft(spec * resp, n)[: audio.shape[1]].astype(np.float32)
    # 卷积后的能量与干声大致相当，这里按 send 量混合
    return audio + out * min(1.0, amount) * 0.9


def _pan_gains(pan: float) -> tuple[float, float]:
    """等功率声像。"""
    theta = (max(-1.0, min(1.0, pan)) + 1.0) * math.pi / 4.0
    return math.cos(theta), math.sin(theta)


def _note_offsets(track: Track, project: Project, length_s: float) -> list[float]:
    """把循环展开成每次重复的起始偏移（秒）。"""
    bpm = track.resolve_bpm(project.bpm)
    sec_per_beat = 60.0 / bpm
    if not track.loop:
        return [0.0]
    period_s = track.period_beats(project.beats_per_bar) * sec_per_beat
    if period_s <= 1e-6:
        return [0.0]
    if track.loop_count > 0:
        reps = track.loop_count
    else:
        reps = int(math.ceil(length_s / period_s - 1e-9)) + 1
    reps = max(1, reps)
    return [i * period_s for i in range(reps)]


def render_project(
    project: Project,
    cache_root: Path | None = None,
    progress: ProgressFn | None = None,
    normalize: bool | None = None,
) -> RenderResult:
    """渲染整个工程，返回立体声 float32 数据。

    ``normalize`` 为 None 时使用工程里的设置。
    """
    sr = int(project.sample_rate)
    length_s = project.compute_length_seconds()
    frames = max(1, int(round(length_s * sr)))
    warnings: list[str] = []
    registry = InstrumentRegistry(
        cache_root=cache_root, sample_rate=sr, warn=lambda m: warnings.append(m) if m not in warnings else None
    )

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, fraction)), message)

    active = [t for t in project.tracks if not t.mute]
    if any(t.solo for t in active):
        active = [t for t in active if t.solo]
    if not active:
        warnings.append("没有可渲染的音轨（全部静音或为空）")

    master = np.zeros((2, frames), dtype=np.float32)
    note_cache: dict[tuple, np.ndarray] = {}
    stats: list[TrackStat] = []
    notes_rendered = 0

    total_notes = sum(len(t.notes) for t in active) or 1
    done = 0

    for track in active:
        instrument = registry.get(track.instrument)
        buffer = np.zeros((2, frames), dtype=np.float32)
        offsets = _note_offsets(track, project, length_s)
        bpm = track.resolve_bpm(project.bpm)
        sec_per_beat = 60.0 / bpm
        track_notes = 0

        for note in track.notes:
            dur_s = note.dur * sec_per_beat
            key = (
                track.instrument,
                tuple(note.pitches),
                round(note.velocity / _VEL_Q),
                round(dur_s / _DUR_Q),
            )
            seg = note_cache.get(key)
            if seg is None:
                pieces = []
                for midi in note.pitches:
                    piece = instrument.render_note(midi, note.velocity, dur_s)
                    if piece is None:
                        continue
                    pieces.append(piece)
                if not pieces:
                    # 采样缺失时用合成器顶上
                    synth = registry.synth()
                    pieces = [synth.render_note(m, note.velocity, dur_s) for m in note.pitches]
                seg = np.zeros((2, max(p.shape[1] for p in pieces)), dtype=np.float32)
                for piece in pieces:
                    seg[:, : piece.shape[1]] += piece
                note_cache[key] = seg

            for offset in offsets:
                start = int(round((note.time * sec_per_beat + offset) * sr))
                if start >= frames:
                    continue
                end = min(frames, start + seg.shape[1])
                if end <= start:
                    continue
                buffer[:, start:end] += seg[:, : end - start]
                track_notes += 1
            done += 1

        if track_notes == 0:
            stats.append(TrackStat(track.name, track.instrument, 0, 0.0, 0.0))
            continue

        buffer *= db_to_gain(track.gain_db)
        if track.reverb > 0:
            report(done / total_notes * 0.9, f"混响：{track.name}")
            buffer = _apply_reverb(buffer, track.reverb, sr)
        left, right = _pan_gains(track.pan)
        buffer[0] *= left
        buffer[1] *= right
        master += buffer

        peak = float(np.max(np.abs(buffer)))
        sounding = float(np.count_nonzero(np.any(buffer != 0, axis=0))) / sr
        stats.append(TrackStat(track.name, track.instrument, track_notes, peak, sounding))
        notes_rendered += track_notes
        report(done / total_notes * 0.9, f"渲染音轨：{track.name}")

    report(0.92, "母带处理")
    master *= db_to_gain(project.master_gain_db)

    end_fade = min(int(END_FADE * sr), frames)
    if end_fade > 1:
        master[:, -end_fade:] *= np.linspace(1.0, 0.0, end_fade, dtype=np.float32)

    peak = float(np.max(np.abs(master))) if master.size else 0.0
    do_normalize = project.normalize if normalize is None else normalize
    if do_normalize and peak > 0:
        master *= TARGET_PEAK / peak
    elif peak > 1.0:
        warnings.append(f"峰值 {20 * math.log10(peak):.1f}dBFS 已超出 0dB，已做削波保护")
        master = np.clip(master, -1.0, 1.0)

    peak = float(np.max(np.abs(master))) if master.size else 0.0
    rms = float(np.sqrt(np.mean(master ** 2))) if master.size else 0.0
    report(1.0, "完成")

    return RenderResult(
        audio=master,
        sample_rate=sr,
        duration=frames / sr,
        peak=peak,
        rms=rms,
        notes_rendered=notes_rendered,
        track_stats=stats,
        warnings=warnings,
    )
