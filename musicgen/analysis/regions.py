"""段落（region）切分：把整首歌按「听感变化」切成前奏 / 主歌 / 副歌这类区段。

做法是经典的自底向上思路，不依赖外部模型：

1. 逐帧取对数梅尔谱，按帧做均值方差归一化，消掉整体音量差异；
2. 相邻帧的余弦距离 = 新颖度（novelty），再平滑约 1 秒；
3. 在 novelty 上找峰，峰之间至少间隔 ``min_seconds``，最多 ``max_regions`` 个；
4. 峰值位置即区段边界，每段再统计电平、频谱质心、起音密度与立体声宽度。

这一层只负责「哪里变了」，**不给区段起名字**——名字（前奏/主歌/副歌）交给
大模型根据 :mod:`musicgen.analysis.brief` 的简报判断，或人工在决策文件里填。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

from . import spectrum

EPS = 1e-9


@dataclass
class Region:
    """一个时间区段（秒）。"""

    index: int
    start: float
    end: float
    label: str = ""
    rms_db: float = -60.0
    centroid: float = 0.0
    onsets_per_second: float = 0.0
    width: float = 0.0
    stems: dict[str, float] = field(default_factory=dict)  # 分轨 -> 该段 RMS(dB)

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "seconds": round(self.seconds, 3),
            "label": self.label,
            "rms_db": round(self.rms_db, 1),
            "centroid_hz": round(self.centroid, 1),
            "onsets_per_second": round(self.onsets_per_second, 2),
            "width": round(self.width, 2),
            "stems": {key: round(value, 1) for key, value in self.stems.items()},
        }


def _frame_features(mag: np.ndarray, sample_rate: int, n_fft: int) -> np.ndarray:
    """逐帧特征：对数梅尔谱，按帧去均值（只留谱形）。"""
    bank = spectrum.mel_filterbank(sample_rate, n_fft, n_mels=32, fmin=40.0)
    logmel = spectrum.log_mel(mag, bank)
    features = logmel.T.astype(np.float64)
    features -= features.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(features, axis=1, keepdims=True)
    return features / np.maximum(norm, EPS)


def novelty_curve(mag: np.ndarray, sample_rate: int, n_fft: int = 2048,
                  hop: int = 1024) -> np.ndarray:
    """相邻帧的余弦距离，平滑 1 秒；长度与帧数一致。"""
    features = _frame_features(mag, sample_rate, n_fft)
    if features.shape[0] < 3:
        return np.zeros(features.shape[0], dtype=np.float64)
    similarity = np.sum(features[1:] * features[:-1], axis=1)
    novelty = np.concatenate([[0.0], 1.0 - similarity])
    fps = sample_rate / float(hop)
    window = max(3, int(round(fps * 1.0)))
    return uniform_filter1d(novelty, size=window, mode="nearest")


def detect_regions(
    mono: np.ndarray,
    sample_rate: int,
    *,
    stereo: np.ndarray | None = None,
    min_seconds: float = 6.0,
    max_regions: int = 16,
    n_fft: int = 2048,
    hop: int = 1024,
) -> list[Region]:
    """切分段落并统计每段特征；至少返回一个覆盖全曲的区段。"""
    signal = np.asarray(mono, dtype=np.float32).reshape(-1)
    duration = signal.size / float(sample_rate) if signal.size else 0.0
    if duration <= 0:
        return []
    if duration < min_seconds * 1.5:
        return [_measure(0, 0.0, duration, signal, sample_rate, stereo)]

    mag = spectrum.magnitude(spectrum.stft(signal, n_fft=n_fft, hop=hop))
    fps = sample_rate / float(hop)
    if mag.shape[1] < 8:
        return [_measure(0, 0.0, duration, signal, sample_rate, stereo)]
    novelty = novelty_curve(mag, sample_rate, n_fft, hop)

    # 稳健 z 分数：音乐里大量帧是「没变化」，直接用分位数做阈值会被
    # 这些静默帧带偏（p80 可能只有 0.002）。中位数 + MAD 与尺度无关。
    # 末尾 0.2s 丢掉：STFT 补零会让最后几帧出现假的新颖度尖峰。
    core = novelty[: max(1, novelty.size - int(fps * 0.2))]
    median = float(np.median(core))
    mad = float(np.median(np.abs(core - median))) + 1e-9
    score = (novelty - median) / (1.4826 * mad)
    peaks, _ = find_peaks(
        score,
        distance=max(1, int(min_seconds * fps)),
        height=2.5,
        prominence=1.0,
    )
    times = [float(peak * hop / sample_rate) for peak in peaks]
    # 去掉离首尾太近的边界（< min_seconds 的段落不单独成段）
    times = [t for t in times if min_seconds <= t <= duration - min_seconds]
    times = sorted(times)[: max(0, max_regions - 1)]

    bounds = [0.0] + times + [duration]
    regions: list[Region] = []
    for index, (start, end) in enumerate(zip(bounds, bounds[1:]), 1):
        region = _measure(index, start, end, signal, sample_rate, stereo)
        region.label = chr(ord("A") + (index - 1) % 26)
        regions.append(region)
    return regions


def _measure(index: int, start: float, end: float, signal: np.ndarray,
             sample_rate: int, stereo: np.ndarray | None) -> Region:
    begin = max(0, int(start * sample_rate))
    finish = min(signal.size, int(end * sample_rate))
    segment = signal[begin:finish] if finish > begin else np.zeros(1, dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(segment, dtype=np.float64)) + EPS))
    mag = spectrum.magnitude(spectrum.stft(segment, n_fft=2048, hop=512))
    freqs = spectrum.frequencies(2048, sample_rate)
    feats = spectrum.spectral_features(mag, freqs, 2048)
    centroid = float(np.median(feats.centroid)) if feats.centroid.size else 0.0
    envelope = spectrum.onset_envelope(mag, sample_rate / 512.0, smooth_seconds=0.2)
    onsets = 0.0
    if envelope.size:
        peaks, _ = find_peaks(envelope, height=max(0.05, float(np.percentile(envelope, 75))))
        onsets = float(peaks.size) / max(1e-6, (finish - begin) / sample_rate)
    width = 0.0
    if stereo is not None and stereo.ndim == 2 and stereo.shape[1] >= finish:
        left = stereo[0, begin:finish]
        right = stereo[1, begin:finish]
        mid = float(np.sqrt(np.mean(((left + right) * 0.5) ** 2) + EPS))
        side = float(np.sqrt(np.mean(((left - right) * 0.5) ** 2) + EPS))
        width = side / max(mid, EPS)
    return Region(
        index=index, start=start, end=end,
        label=chr(ord("A") + max(0, index - 1) % 26),
        rms_db=round(20.0 * np.log10(max(rms, 1e-9)), 1),
        centroid=centroid, onsets_per_second=onsets, width=width,
    )


def attach_stem_levels(regions: Iterable[Region], stems: dict[str, np.ndarray],
                       sample_rate: int) -> None:
    """给每个区段补上「各分轨在该段的电平」，用于判断哪段进了什么乐器。"""
    for region in regions:
        begin = max(0, int(region.start * sample_rate))
        finish = int(region.end * sample_rate)
        levels = {}
        for name, data in stems.items():
            mono = np.mean(data[:, begin:finish], axis=0) if data.ndim == 2 else data[begin:finish]
            if mono.size == 0:
                levels[name] = -60.0
                continue
            rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)) + EPS))
            levels[name] = round(20.0 * np.log10(max(rms, 1e-9)), 1)
        region.stems = levels
