"""音频解码、声道处理与重采样。

分析管线统一把输入变成 44100Hz 的 ``(声道, 帧)`` float32 数组：
多声道只保留前两路（其余丢弃），单声道复制成立体声，方便后续同时做
单声道频谱分析与左右声像估计。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: 分析统一采样率。Demucs 内部也是 44.1kHz，避免二次重采样。
ANALYSIS_SR = 44100

#: soundfile 能解码的扩展名（mp3/flac/ogg 取决于 libsndfile 版本）
AUDIO_SUFFIXES = (".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif", ".m4a")


class AudioError(RuntimeError):
    """音频读不出来 / 格式不支持。"""


@dataclass
class Audio:
    """一段已解码音频。``samples`` 形状为 ``(声道数, 帧数)``。"""

    samples: np.ndarray
    sample_rate: int
    path: Path | None = None

    @property
    def channels(self) -> int:
        return int(self.samples.shape[0])

    @property
    def frames(self) -> int:
        return int(self.samples.shape[1])

    @property
    def duration(self) -> float:
        return self.frames / float(self.sample_rate) if self.sample_rate else 0.0

    def channel(self, index: int) -> np.ndarray:
        index = max(0, min(self.channels - 1, index))
        return self.samples[index]

    def mono(self) -> np.ndarray:
        """单声道混合（多声道取平均，避免相位相消）。"""
        if self.channels == 1:
            return self.samples[0]
        return np.mean(self.samples, axis=0, dtype=np.float32)

    def resampled(self, sample_rate: int) -> "Audio":
        if int(sample_rate) == self.sample_rate:
            return self
        return Audio(
            samples=resample(self.samples, self.sample_rate, int(sample_rate)),
            sample_rate=int(sample_rate),
            path=self.path,
        )

    def slice_seconds(self, start: float, duration: float) -> np.ndarray:
        """取一段 ``(声道, N)``；越界部分用零补齐。"""
        begin = max(0, int(round(start * self.sample_rate)))
        count = max(0, int(round(duration * self.sample_rate)))
        out = np.zeros((self.channels, count), dtype=np.float32)
        if count == 0 or begin >= self.frames:
            return out
        end = min(self.frames, begin + count)
        out[:, : end - begin] = self.samples[:, begin:end]
        return out


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """多相重采样（scipy.signal.resample_poly），保持声道数不变。"""
    if source_rate == target_rate or samples.size == 0:
        return np.ascontiguousarray(samples, dtype=np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    divisor = gcd(int(source_rate), int(target_rate))
    up = int(target_rate) // divisor
    down = int(source_rate) // divisor
    # 不要按 round(n*up/down) 预分配：resample_poly 的实际输出是
    # ceil/floor 路径，长音频上常与 round 差 1 个样本，直接赋值会抛
    # 「could not broadcast input array from shape (N,) into shape (N-1,)」。
    pieces = [
        resample_poly(samples[channel], up, down).astype(np.float32)
        for channel in range(samples.shape[0])
    ]
    length = max(piece.size for piece in pieces)
    out = np.zeros((samples.shape[0], length), dtype=np.float32)
    for channel, piece in enumerate(pieces):
        out[channel, : piece.size] = piece
    return out


def load(path: str | Path, sample_rate: int | None = ANALYSIS_SR) -> Audio:
    """读取音频文件。``sample_rate`` 为 None 时保留原始采样率。"""
    file = Path(path).expanduser()
    if not file.is_file():
        raise AudioError(f"音频文件不存在：{file}")
    if file.suffix.lower() not in AUDIO_SUFFIXES:
        raise AudioError(
            f"不支持的音频格式 {file.suffix or '(无扩展名)'}，"
            f"可用：{'、'.join(AUDIO_SUFFIXES)}"
        )

    import soundfile as sf  # 延迟导入：只跑渲染时不需要它

    try:
        data, rate = sf.read(str(file), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 - 把底层异常转成一条可读信息
        raise AudioError(f"解码失败：{type(exc).__name__}: {exc}") from exc
    if data.size == 0:
        raise AudioError("音频里没有任何采样")

    samples = np.ascontiguousarray(data.T)  # (frames, channels) -> (channels, frames)
    if samples.shape[0] == 1:
        samples = np.vstack([samples, samples])
    elif samples.shape[0] > 2:
        samples = samples[:2]
    audio = Audio(samples=samples, sample_rate=int(rate), path=file)
    if sample_rate and int(sample_rate) != int(rate):
        audio = audio.resampled(int(sample_rate))
    return audio


def rms(samples: np.ndarray) -> float:
    """均方根电平。"""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def peak(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.max(np.abs(samples)))


def db(value: float, floor: float = 1e-9) -> float:
    """线性幅度 → dB。"""
    return 20.0 * math.log10(max(float(value), floor))


def stereo_balance(samples: np.ndarray) -> tuple[float, float]:
    """返回 ``(左右能量比, 声像 -1..1)``；单声道恒为 0。"""
    if samples.shape[0] < 2:
        return 0.0, 0.0
    left = rms(samples[0])
    right = rms(samples[1])
    total = left + right
    if total <= 1e-9:
        return 0.0, 0.0
    return (right - left) / total, max(-1.0, min(1.0, (right - left) / total))


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int,
              subtype: str = "PCM_24") -> Path:
    """写 WAV（分轨导出用）。``samples`` 为 ``(声道, 帧)``。"""
    import soundfile as sf

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = np.ascontiguousarray(samples.T, dtype=np.float32)
    sf.write(str(target), data, int(sample_rate), subtype=subtype)
    return target
