"""节拍分析：速度估计、网格相位与量化。

速度用「谱通量包络的自相关」估计，再按 55–200BPM 的合理区间选峰；
相位用「把起音点折叠到一个 16 分音符周期里做直方图」估计，这样即使用户
从弱起或第 3 拍开始弹，量化网格也能对齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np

EPS = 1e-9

#: 量化网格写法 -> 每格占几拍
GRID_BEATS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
    "1/32": 0.125,
    "off": 0.0,
    "none": 0.0,
}


@dataclass
class TempoInfo:
    bpm: float
    confidence: float
    phase: float          # 网格相位（秒）
    grid_seconds: float   # 一个 16 分音符的秒数

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.5


def parse_grid(text: str | None) -> float:
    """``"1/16"`` / ``"0.25"`` / ``"off"`` → 拍数。"""
    if text is None:
        return GRID_BEATS["1/16"]
    raw = str(text).strip().lower()
    if raw in GRID_BEATS:
        return GRID_BEATS[raw]
    try:
        if "/" in raw:
            return float(Fraction(raw))
        return float(raw)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"无法识别的量化网格：{text!r}（可用 1/4、1/8、1/16、1/32、off）") from None


def estimate_tempo(
    envelope: np.ndarray,
    fps: float,
    min_bpm: float = 55.0,
    max_bpm: float = 200.0,
) -> tuple[float, float]:
    """自相关法估速，返回 ``(bpm, 置信度 0-1)``。"""
    env = np.asarray(envelope, dtype=np.float64).reshape(-1)
    if env.size < 8 or fps <= 0:
        return 120.0, 0.0
    env = env - env.mean()
    energy = float(np.dot(env, env))
    if energy <= EPS:
        return 120.0, 0.0

    size = 1 << (2 * env.size - 1).bit_length()
    spectrum = np.fft.rfft(env, size)
    acf = np.fft.irfft(spectrum * np.conj(spectrum), size)[: env.size]
    acf = acf / (acf[0] + EPS)

    lag_min = max(1, int(np.floor(fps * 60.0 / max_bpm)))
    lag_max = min(env.size - 2, int(np.ceil(fps * 60.0 / min_bpm)))
    if lag_max <= lag_min:
        return 120.0, 0.0

    # 在 55–200BPM 内按 0.5BPM 采样，叠一个以 115BPM 为中心的八度先验
    candidates = np.arange(min_bpm, max_bpm + 0.25, 0.5)
    lags = fps * 60.0 / candidates
    valid = (lags >= lag_min) & (lags <= lag_max)
    if not np.any(valid):
        return 120.0, 0.0
    values = np.interp(lags[valid], np.arange(env.size), acf)
    prior = np.exp(-0.5 * (np.log2(candidates[valid] / 115.0) / 0.62) ** 2)
    score = np.maximum(values, 0.0) * prior
    best = int(np.argmax(score))
    bpm = float(candidates[valid][best])

    # 抛物线插值细化
    if 0 < best < score.size - 1:
        left, mid, right = score[best - 1], score[best], score[best + 1]
        denominator = left - 2.0 * mid + right
        if abs(denominator) > EPS:
            offset = 0.5 * (left - right) / denominator
            bpm += float(np.clip(offset, -1.0, 1.0)) * 0.5

    # 极端速度做八度折叠，避免 40BPM / 240BPM 这种量化过粗或过细的网格
    while bpm < min_bpm and bpm * 2 <= max_bpm:
        bpm *= 2.0
    while bpm > max_bpm and bpm / 2 >= min_bpm:
        bpm /= 2.0

    peak = float(score[best])
    contrast = peak / (float(np.mean(score)) + EPS)
    confidence = 0.6 * float(np.clip(peak * 2.0, 0.0, 1.0))
    confidence += 0.4 * float(np.clip((contrast - 1.0) / 4.0, 0.0, 1.0))
    return bpm, float(np.clip(confidence, 0.0, 1.0))


def grid_phase(
    envelope: np.ndarray,
    fps: float,
    bpm: float,
    grid_beats: float = 0.25,
    bins: int = 48,
) -> float:
    """估计量化网格的相位（秒）。没有起音点时返回 0。"""
    env = np.asarray(envelope, dtype=np.float64).reshape(-1)
    if env.size == 0 or bpm <= 0 or grid_beats <= 0 or fps <= 0:
        return 0.0
    grid = 60.0 / bpm * grid_beats
    times = np.arange(env.size) / fps
    active = env > max(0.08, float(np.percentile(env, 70)))
    if not np.any(active):
        return 0.0
    phases = np.mod(times[active] / grid, 1.0)
    weights = env[active]
    histogram, edges = np.histogram(phases, bins=bins, range=(0.0, 1.0), weights=weights)
    # 圆滑一次，避免相位落在 bin 边界时抖动
    kernel = np.array([1, 2, 3, 2, 1], dtype=np.float64)
    kernel /= kernel.sum()
    smooth = np.convolve(np.r_[histogram[-2:], histogram, histogram[:2]], kernel, mode="same")[2:-2]
    index = int(np.argmax(smooth))
    center = (edges[index] + edges[index + 1]) / 2.0
    return float(center * grid)


def quantize_time(value: float, grid: float, phase: float = 0.0) -> float:
    if grid <= 0:
        return max(0.0, float(value))
    steps = round((float(value) - phase) / grid)
    return max(0.0, steps * grid + phase)


def quantize_duration(value: float, grid: float, minimum: float | None = None) -> float:
    if grid <= 0:
        return max(minimum or 0.0, float(value))
    floor = minimum if minimum is not None else grid
    steps = max(1, int(round(float(value) / grid)))
    return max(floor, steps * grid)


def beat_info(envelope: np.ndarray, fps: float, bpm: float | None = None,
              grid_beats: float = 0.25) -> TempoInfo:
    """一次性得到速度、置信度、相位与网格长度。"""
    if bpm and bpm > 0:
        confidence = 1.0
        speed = float(bpm)
    else:
        speed, confidence = estimate_tempo(envelope, fps)
    phase = grid_phase(envelope, fps, speed, grid_beats)
    return TempoInfo(
        bpm=speed,
        confidence=confidence,
        phase=phase,
        grid_seconds=60.0 / speed * grid_beats,
    )
