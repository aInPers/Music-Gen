"""从分轨音频里估计「原曲用了什么效果」，给大模型一份可复核的依据。

这里只做**可解释的粗估**，不是参数反演：

* 峰值因数（crest factor）→ 动态被压扁了多少；
* 起音后能量衰减斜率 → 混响时间 RT60；
* 起音包络自相关 → 是否有等间隔回声（延迟时间）；
* 低/高频相对中频的能量密度 → 频谱倾斜（只作信息，EQ 建议由「渲染 vs 原分轨」另算）；
* 侧/中能量比 → 立体声宽度，以及是否需要声像展开。

估计结果只作为建议（``suggested``），最终是否写进工程由决策文件决定——
这正是留给大模型判断的地方。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import find_peaks

from . import spectrum

EPS = 1e-9


@dataclass
class EffectHints:
    crest_db: float = 0.0
    rt60_s: float = 0.0
    echo_ms: float = 0.0
    width: float = 0.0
    low_db: float = 0.0
    mid_db: float = 0.0
    high_db: float = 0.0
    flatness: float = 0.0
    centroid_hz: float = 0.0
    suggested: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "crest_db": round(self.crest_db, 1),
            "rt60_s": round(self.rt60_s, 2),
            "echo_ms": round(self.echo_ms, 1),
            "width": round(self.width, 2),
            "bands_db": {"low": round(self.low_db, 1), "mid": round(self.mid_db, 1),
                         "high": round(self.high_db, 1)},
            "flatness": round(self.flatness, 3),
            "centroid_hz": round(self.centroid_hz, 0),
            "suggested": self.suggested,
        }


def _band_density_db(mag: np.ndarray, freqs: np.ndarray, low: float, high: float) -> float:
    """频带能量**密度**（每 Hz 的能量）的 dB 值。

    用密度而不是总能量，否则宽频带天然占优；低频段窄、能量又集中，
    直接比总量会让每条分轨都显示「低频过重」。
    """
    mask = (freqs >= low) & (freqs < high)
    if not np.any(mask):
        return -120.0
    energy = float(np.sum(np.square(mag[mask], dtype=np.float64)))
    bandwidth = float(max(1.0, freqs[mask][-1] - freqs[mask][0]))
    return 10.0 * np.log10(max(energy / bandwidth, EPS))


def _estimate_rt60(envelope: np.ndarray, fps: float) -> float:
    """按起音后的能量衰减斜率估 RT60（秒）。"""
    if envelope.size < int(fps * 0.8):
        return 0.0
    level = 20.0 * np.log10(envelope + EPS)
    peaks, _ = find_peaks(level, height=float(np.percentile(level, 85)))
    slopes: list[float] = []
    window = max(4, int(fps * 0.5))
    for peak in peaks:
        tail = level[peak: peak + window]
        if tail.size < window:
            continue
        span = tail[-1] - tail[0]
        if span < -6.0:  # 至少衰减 6dB 才算有意义的尾部
            slopes.append(span / (tail.size / fps))
    if not slopes:
        return 0.0
    # 用中位数，避免个别长音把 RT60 拉爆
    slope = float(np.median(slopes))
    if slope >= -1.0:
        return 0.0
    return float(np.clip(60.0 / abs(slope), 0.05, 8.0))


def _estimate_echo(envelope: np.ndarray, fps: float) -> float:
    """起音包络自相关，找 0.12–1.2s 的等间隔重复（毫秒）。"""
    if envelope.size < int(fps * 1.6):
        return 0.0
    centered = envelope - float(envelope.mean())
    energy = float(np.dot(centered, centered))
    if energy <= EPS:
        return 0.0
    size = 1 << (2 * centered.size - 1).bit_length()
    spec = np.fft.rfft(centered, size)
    acf = np.fft.irfft(spec * np.conj(spec), size)[: centered.size]
    acf /= acf[0] + EPS
    low = max(1, int(0.12 * fps))
    high = min(centered.size - 1, int(1.2 * fps))
    if high <= low:
        return 0.0
    window = acf[low:high]
    position = int(np.argmax(window)) + low
    if acf[position] < 0.28:
        return 0.0
    return float(position / fps * 1000.0)


def analyse_stem(stereo: np.ndarray, sample_rate: int,
                 bpm: float | None = None) -> EffectHints:
    """估计一条分轨的效果特征；输入 ``(2, N)`` 或单声道一维数组。

    ``bpm`` 已知时会排除与节拍同周期的「伪回声」——鼓点本身按拍重复，
    自相关必然在第 1/2/4 拍上出峰，那不是延迟效果。
    """
    data = np.asarray(stereo, dtype=np.float32)
    mono = data.mean(axis=0) if data.ndim == 2 else data.reshape(-1)
    hints = EffectHints()
    if mono.size < 2048:
        return hints

    mag = spectrum.magnitude(spectrum.stft(mono, n_fft=2048, hop=512))
    freqs = spectrum.frequencies(2048, sample_rate)
    feats = spectrum.spectral_features(mag, freqs, 2048)
    hints.centroid_hz = float(np.median(feats.centroid)) if feats.centroid.size else 0.0
    hints.flatness = float(np.median(feats.flatness)) if feats.flatness.size else 0.0

    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    hints.crest_db = 20.0 * np.log10(max(peak, EPS) / max(rms, EPS))

    # 以中频密度为 0dB 参考，记录低/高频的相对倾斜
    mid_density = _band_density_db(mag, freqs, 250.0, 2000.0)
    hints.low_db = _band_density_db(mag, freqs, 20.0, 250.0) - mid_density
    hints.mid_db = 0.0
    hints.high_db = _band_density_db(mag, freqs, 2000.0, 9000.0) - mid_density

    envelope = spectrum.onset_envelope(mag, sample_rate / 512.0, smooth_seconds=0.25)
    hints.rt60_s = _estimate_rt60(envelope, sample_rate / 512.0)
    hints.echo_ms = _estimate_echo(envelope, sample_rate / 512.0)
    if bpm and hints.echo_ms:
        beat_ms = 60000.0 / max(1.0, float(bpm))
        ratio = hints.echo_ms / beat_ms
        # 常见节奏型按 8 分 / 16 分音符重复，所以对齐到半拍网格再判断
        nearest = round(ratio * 2.0) / 2.0
        if abs(ratio - nearest) < 0.12:
            hints.echo_ms = 0.0  # 与律动重合，不当成延迟效果

    if data.ndim == 2 and data.shape[1] > 1:
        left, right = data[0], data[1]
        mid = float(np.sqrt(np.mean(((left + right) * 0.5) ** 2) + EPS))
        side = float(np.sqrt(np.mean(((left - right) * 0.5) ** 2) + EPS))
        hints.width = side / max(mid, EPS)

    hints.suggested = _suggest(hints)
    return hints


def _suggest(hints: EffectHints) -> list[dict]:
    """除 EQ 之外的效果建议。

    EQ 不在这里判断：只拿原曲分轨对比「标准曲线」必然人人都要加 EQ，
    真正有意义的是「渲染结果 vs 原分轨」的频谱差，那一步在 pipeline 里做。
    """
    suggestions: list[dict] = []
    if hints.crest_db < 9.0:
        suggestions.append({"type": "compressor", "threshold": -22.0, "ratio": 4.0,
                            "reason": f"峰值因数只有 {hints.crest_db:.1f}dB，动态已被压扁"})
    if hints.flatness > 0.25 and hints.crest_db < 14.0:
        suggestions.append({"type": "drive", "amount": 0.2, "tone": 0.5,
                            "reason": f"噪声成分偏高（flatness {hints.flatness:.2f}），"
                                      "可能用了饱和/失真"})
    if hints.echo_ms >= 120.0:
        suggestions.append({"type": "echo", "time": int(round(hints.echo_ms)),
                            "feedback": 0.3, "mix": 0.2,
                            "reason": f"起音包络在 {hints.echo_ms:.0f}ms 有周期性重复"})
    if 0.0 < hints.width < 0.2:
        suggestions.append({"type": "stereo", "width": 1.5,
                            "reason": f"侧/中能量比只有 {hints.width:.2f}，声场偏窄"})
    return suggestions
