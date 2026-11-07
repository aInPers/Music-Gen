"""把采样文件变成可以直接混进总线的 PCM 片段。

流程：找最近的采样 → 解码 → 变调（重采样）→ 力度（音量 + 亮度）→
包络（起音淡入 / 释放淡出）→ 立体声 float32 片段。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .notes import midi_to_name
from ..samples import fetch

#: 起音淡入长度（秒），消除采样开头的爆音
ATTACK_FADE = 0.002
#: 松键后的衰减尾巴（秒）
RELEASE_TAIL = 0.35
#: 采样被截断时的收尾淡出（秒）
TRUNCATE_FADE = 0.15
#: 力度量化步长，便于复用同一片段
VELOCITY_STEP = 0.05


def _load_audio(path: Path) -> tuple[np.ndarray, int]:
    """读取任意受支持格式，返回 (声道×样本 的 float32 数组, 采样率)。"""
    import soundfile as sf  # 延迟导入：CLI 的纯解析命令不需要它

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = np.ascontiguousarray(data.T)  # (frames, channels) -> (channels, frames)
    if audio.shape[0] == 1:
        audio = np.vstack([audio, audio])
    elif audio.shape[0] > 2:
        audio = audio[:2]
    return audio, int(sr)


def _resample(audio: np.ndarray, ratio: float) -> np.ndarray:
    """线性插值重采样。``ratio`` > 1 表示变快（音高变高）。"""
    if abs(ratio - 1.0) < 1e-6:
        return audio
    channels, frames = audio.shape
    out_frames = max(1, int(round(frames / ratio)))
    src_pos = np.arange(out_frames, dtype=np.float64) * ratio
    src_pos = np.clip(src_pos, 0, frames - 1)
    idx = np.floor(src_pos).astype(np.int64)
    frac = (src_pos - idx).astype(np.float32)
    idx_next = np.minimum(idx + 1, frames - 1)
    out = np.empty((channels, out_frames), dtype=np.float32)
    for ch in range(channels):
        a = audio[ch, idx]
        b = audio[ch, idx_next]
        out[ch] = a + (b - a) * frac
    return out


def _lowpass(audio: np.ndarray, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    """窗函数 sinc 低通（FIR），用于模拟弱奏时更暗的音色。"""
    nyquist = sample_rate / 2.0
    cutoff = min(max(cutoff_hz, 200.0), nyquist * 0.98)
    if cutoff >= nyquist * 0.95:
        return audio
    taps = 25
    n = np.arange(taps) - (taps - 1) / 2.0
    fc = cutoff / sample_rate
    kernel = 2 * fc * np.sinc(2 * fc * n) * np.hanning(taps)
    kernel /= kernel.sum()
    out = np.empty_like(audio)
    for ch in range(audio.shape[0]):
        out[ch] = np.convolve(audio[ch], kernel.astype(np.float32), mode="same")
    return out


@dataclass
class _CachedSample:
    audio: np.ndarray
    sample_rate: int
    source_midi: int


class SampleInstrument:
    """基于采样文件的乐器。当前用于 ``piano``。"""

    #: 采样整体偏小，这里补足到接近满刻度的电平
    instrument_gain = 16.0

    def __init__(
        self,
        name: str,
        cache_root: Path | None = None,
        sample_rate: int = 44100,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.name = name
        self.cache_root = cache_root
        self.sample_rate = sample_rate
        self._warn = warn or (lambda msg: None)
        self._cache: dict[int, _CachedSample | None] = {}
        self._available: list[int] = fetch.available_notes(name, cache_root)
        self._missing_warned = False

    # ---- 元信息 -------------------------------------------------------
    @property
    def ready(self) -> bool:
        return bool(self._available)

    def describe(self) -> str:
        source = fetch.SOURCES.get(self.name)
        title = source.title if source else self.name
        return f"{title}（{len(self._available)} 个采样）"

    # ---- 采样查找 -----------------------------------------------------
    def _nearest(self, midi: int) -> int | None:
        if not self._available:
            return None
        return min(self._available, key=lambda m: (abs(m - midi), m))

    def _get_sample(self, midi: int) -> _CachedSample | None:
        if midi in self._cache:
            return self._cache[midi]
        source_midi = self._nearest(midi)
        if source_midi is None:
            self._cache[midi] = None
            return None
        path = fetch.sample_path(self.name, midi_to_name(source_midi), self.cache_root)
        try:
            audio, sr = _load_audio(path)
        except Exception as exc:  # noqa: BLE001 - 单个采样坏了不该中断渲染
            self._warn(f"采样 {path.name} 读取失败（{exc}），改用合成音色")
            self._cache[midi] = None
            return None
        if sr != self.sample_rate:
            audio = _resample(audio, sr / self.sample_rate)
            sr = self.sample_rate
        cached = _CachedSample(audio=audio, sample_rate=sr, source_midi=source_midi)
        self._cache[midi] = cached
        return cached

    # ---- 渲染单个音 ---------------------------------------------------
    def render_note(self, midi: int, velocity: float, duration: float) -> np.ndarray | None:
        """渲染一个音，返回 (2, N) float32；无采样时返回 None。"""
        cached = self._get_sample(midi)
        if cached is None:
            if not self._missing_warned:
                self._warn(
                    f"乐器 '{self.name}' 缺少采样，已回退到内置合成器。"
                    f"可运行 `python -m musicgen samples fetch --instrument {self.name}` 下载"
                )
                self._missing_warned = True
            return None

        audio = cached.audio
        shift = midi - cached.source_midi
        if shift:
            audio = _resample(audio, 2.0 ** (shift / 12.0))

        gain = self.instrument_gain * (max(0.0, min(1.0, velocity)) ** 1.4)
        audio = audio * gain

        # 弱奏更暗：截止频率随力度从 4kHz 抬到 18kHz
        cutoff = 4000.0 * (4.5 ** max(0.0, min(1.0, velocity)))
        if cutoff < self.sample_rate * 0.45:
            audio = _lowpass(audio, cutoff, self.sample_rate)

        total = audio.shape[1]
        hold = max(1, int(round(duration * self.sample_rate)))
        if hold >= total:
            # 采样不够长：直接用到尾，末尾淡出避免啪声
            seg = audio
            fade_len = min(int(TRUNCATE_FADE * self.sample_rate), seg.shape[1])
        else:
            fade_len = min(int(RELEASE_TAIL * self.sample_rate), total - hold)
            end = min(total, hold + fade_len)
            seg = audio[:, :end].copy()
            if fade_len > 1:
                ramp = np.linspace(1.0, 0.0, fade_len, dtype=np.float32) ** 1.6
                seg[:, hold : hold + fade_len] *= ramp
            fade_len = 0

        if fade_len > 1:
            ramp = np.linspace(1.0, 0.0, fade_len, dtype=np.float32) ** 1.6
            seg[:, -fade_len:] *= ramp

        attack = min(int(ATTACK_FADE * self.sample_rate), seg.shape[1])
        if attack > 1:
            seg[:, :attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
        return seg


class SynthInstrument:
    """兜底乐器：加性合成，保证任何未下载的乐器也能出声。"""

    instrument_gain = 0.32

    def __init__(self, name: str, sample_rate: int = 44100, **_: object) -> None:
        self.name = name
        self.sample_rate = sample_rate

    @property
    def ready(self) -> bool:
        return True

    def describe(self) -> str:
        return f"内置合成器（乐器 '{self.name}' 暂无采样）"

    def render_note(self, midi: int, velocity: float, duration: float) -> np.ndarray:
        sr = self.sample_rate
        hold = max(1, int(round(duration * sr)))
        tail = int(RELEASE_TAIL * sr)
        total = hold + tail
        t = np.arange(total, dtype=np.float32) / sr
        freq = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        signal = np.zeros(total, dtype=np.float32)
        for harmonic in range(1, 9):
            if freq * harmonic > sr * 0.45:
                break
            amp = 1.0 / (harmonic ** 1.7)
            phase = 2.0 * math.pi * freq * harmonic * t
            signal += (amp * np.sin(phase)).astype(np.float32)
        decay = np.exp(-3.0 * t).astype(np.float32)
        env = decay.copy()
        if tail > 1:
            env[hold:] *= np.linspace(1.0, 0.0, tail, dtype=np.float32) ** 1.5
        vel = max(0.0, min(1.0, velocity)) ** 1.3
        signal = signal * env * (self.instrument_gain * vel)
        return np.vstack([signal, signal])
