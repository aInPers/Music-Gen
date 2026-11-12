"""音频片段：把磁盘上的 WAV/MP3 读成工程采样率下的立体声缓冲。

音频轨（``<audio file="..."/>``）用这里加载分轨。与采样乐器不同，音频轨
不做变调、不做包络，只重采样 + 对齐 + 增益，目标是「原样混音」。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class AudioClipError(RuntimeError):
    """音频片段读不出来。"""


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or samples.size == 0:
        return samples
    try:
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(int(source_rate), int(target_rate))
        up = int(target_rate) // divisor
        down = int(source_rate) // divisor
        out = np.empty(
            (samples.shape[0], max(1, int(round(samples.shape[1] * up / down)))),
            dtype=np.float32,
        )
        for channel in range(samples.shape[0]):
            out[channel] = resample_poly(samples[channel], up, down).astype(np.float32)
        return out
    except Exception:  # noqa: BLE001 - 没有 scipy 时退回线性插值
        channels, frames = samples.shape
        out_frames = max(1, int(round(frames * target_rate / source_rate)))
        position = np.arange(out_frames, dtype=np.float64) * (frames - 1) / max(1, out_frames - 1)
        index = np.floor(position).astype(np.int64)
        frac = (position - index).astype(np.float32)
        nxt = np.minimum(index + 1, frames - 1)
        out = np.empty((channels, out_frames), dtype=np.float32)
        for channel in range(channels):
            out[channel] = samples[channel, index] * (1.0 - frac) + samples[channel, nxt] * frac
        return out


def read_audio(path: str | Path, sample_rate: int | None = None) -> tuple[np.ndarray, int]:
    """读成 ``(声道, 帧)`` float32；``sample_rate=None`` 时保留原采样率。"""
    file = Path(path)
    if not file.is_file():
        raise AudioClipError(f"音频文件不存在：{file}")
    import soundfile as sf

    try:
        data, rate = sf.read(str(file), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise AudioClipError(f"解码失败（{file.name}）：{type(exc).__name__}: {exc}") from exc
    if data.size == 0:
        raise AudioClipError(f"音频是空的：{file}")
    samples = np.ascontiguousarray(data.T)
    if samples.shape[0] == 1:
        samples = np.vstack([samples, samples])
    elif samples.shape[0] > 2:
        samples = samples[:2]
    if sample_rate and int(sample_rate) != int(rate):
        samples = _resample(samples, int(rate), int(sample_rate))
        rate = int(sample_rate)
    return samples, int(rate)


def load_clip(
    path: str | Path,
    sample_rate: int,
    frames: int,
    offset: float = 0.0,
    gain_db: float = 0.0,
) -> np.ndarray:
    """把音频片段铺到 ``(2, frames)`` 缓冲上，越界部分截掉。"""
    samples, rate = read_audio(path, sample_rate)
    if rate != int(sample_rate):
        samples = _resample(samples, rate, int(sample_rate))
    out = np.zeros((2, max(0, int(frames))), dtype=np.float32)
    if out.shape[1] == 0 or samples.size == 0:
        return out
    start = max(0, int(round(float(offset) * sample_rate)))
    if start >= out.shape[1]:
        return out
    end = min(out.shape[1], start + samples.shape[1])
    out[:, start:end] = samples[:, : end - start]
    if gain_db:
        out *= 10.0 ** (float(gain_db) / 20.0)
    return out


def duration_seconds(path: str | Path) -> float:
    """只读头部，拿时长（解析音频轨时用）。"""
    file = Path(path)
    if not file.is_file():
        raise AudioClipError(f"音频文件不存在：{file}")
    import soundfile as sf

    try:
        info = sf.info(str(file))
    except Exception as exc:  # noqa: BLE001
        raise AudioClipError(f"无法读取音频信息（{file.name}）：{type(exc).__name__}: {exc}") from exc
    if not info.samplerate:
        raise AudioClipError(f"音频采样率无效：{file}")
    return float(info.frames) / float(info.samplerate)
