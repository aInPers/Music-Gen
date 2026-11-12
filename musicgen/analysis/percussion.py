"""鼓点检测与鼓件分类。

Demucs 的 ``drums`` 分轨已经把打击乐单独拎出来，这里只需要：
1. 在谱通量包络上找起音点（带 50ms 最小间隔，防止一个鼓打两次）；
2. 对每个起音取 600ms 窗口，算低频/中频/高频能量比、谱质心、衰减时间与谱平坦度；
3. 用一组手工权重给 ``kick / snare / hat / openhat / crash / tom / ride`` 打分，
   取最高分作为鼓件，软最大化得到置信度。

鼓是宽带瞬态，纯 FFT 判别足够用；真正难的是同时敲响的多个鼓件，这里按
一次一个处理（流行鼓组里绝大多数起音点确实只对应一个鼓件）。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from ..core.notes import PERCUSSION_ALIASES
from . import spectrum
from .pitch import NoteEvent

#: 分类标签 -> 工程里写的鼓件名（对应 GM 打击乐编号）
DRUM_ALIASES = {
    "kick": "kick",
    "snare": "snare",
    "hat": "hat",
    "openhat": "openhat",
    "crash": "crash",
    "tom": "tom2",
    "ride": "ride",
}

EPS = 1e-9


def _envelope(signal: np.ndarray, sample_rate: int, frame: int = 128) -> np.ndarray:
    count = signal.size // frame
    if count <= 0:
        return np.zeros(0)
    trimmed = signal[: count * frame].reshape(count, frame)
    return np.sqrt(np.mean(np.square(trimmed, dtype=np.float64), axis=1))


def _decay_seconds(signal: np.ndarray, sample_rate: int) -> float:
    env = _envelope(signal, sample_rate)
    if env.size == 0:
        return 0.0
    peak_index = int(np.argmax(env))
    peak = float(env[peak_index])
    if peak <= EPS:
        return 0.0
    threshold = peak * 10 ** (-18.0 / 20.0)
    after = np.where(env[peak_index:] < threshold)[0]
    if after.size == 0:
        return signal.size / sample_rate
    return float(after[0]) * 128 / sample_rate


def _attack_time(signal: np.ndarray, sample_rate: int) -> float:
    env = _envelope(signal, sample_rate)
    if env.size == 0:
        return 0.0
    peak_index = int(np.argmax(env))
    return peak_index * 128 / sample_rate


def _features(segment: np.ndarray, sample_rate: int) -> dict[str, float]:
    n_fft = 1024
    hop = 256
    spec = spectrum.stft(segment, n_fft=n_fft, hop=hop)
    mag = spectrum.magnitude(spec)
    freqs = spectrum.frequencies(n_fft, sample_rate)
    if mag.shape[1] == 0:
        return dict(low=0.0, body=0.0, mid=0.0, high=0.0, air=0.0, centroid=0.0,
                    flatness=0.0, decay=0.0, attack=0.0)

    attack_frames = max(1, int(0.075 * sample_rate / hop))
    attack = mag[:, :attack_frames]

    # 各频带按带宽归一化成「能量密度」，否则 6–14kHz 这种宽频带会天然占优
    bands = {
        "low": (20.0, 120.0),
        "body": (120.0, 400.0),
        "mid": (400.0, 2500.0),
        "high": (2500.0, 6000.0),
        "air": (6000.0, 12000.0),
    }
    density = {
        name: float(spectrum.band_energy(attack, freqs, low, high).sum()) / max(1.0, high - low)
        for name, (low, high) in bands.items()
    }
    total = sum(density.values()) + EPS
    ratios = {name: value / total for name, value in density.items()}

    centroid = float(
        (freqs[:, None] * attack).sum() / (attack.sum() + EPS)
    )
    flat = float(np.mean(np.exp(np.mean(np.log(attack + EPS), axis=0))
                         / (np.mean(attack, axis=0) + EPS)))
    return {
        **ratios,
        "centroid": centroid,
        "flatness": flat,
        "decay": _decay_seconds(segment, sample_rate),
        "attack": _attack_time(segment, sample_rate),
    }


def _scores(f: dict[str, float]) -> dict[str, float]:
    short = float(np.clip(1.0 - f["decay"] / 0.22, 0.0, 1.0))
    medium = float(np.clip(1.0 - abs(f["decay"] - 0.32) / 0.32, 0.0, 1.0))
    long = float(np.clip((f["decay"] - 0.45) / 0.6, 0.0, 1.0))
    noise = float(np.clip(f["flatness"] * 2.5, 0.0, 1.0))
    tonal = float(np.clip(1.0 - f["flatness"] * 4.0, 0.0, 1.0))
    low_centroid = float(np.clip(1.0 - f["centroid"] / 500.0, 0.0, 1.0))

    # 频带密度比才是可靠的判别依据：底鼓低频、军鼓中频+噪声、踩镲空气感。
    # 谱质心只给底鼓做辅助，宽带噪声会把质心拉到 10kHz 以上，不能当主特征。
    return {
        "kick": 3.0 * f["low"] + 1.2 * f["body"] + 1.0 * low_centroid,
        "snare": 2.6 * f["body"] + 1.8 * f["mid"] + 1.2 * f["high"] + 2.0 * noise,
        "hat": 3.0 * f["air"] + 1.5 * f["high"] + 1.2 * short,
        "openhat": 2.4 * f["air"] + 1.2 * f["high"] + 1.0 * medium,
        "crash": 2.0 * f["air"] + 1.5 * f["high"] + 1.6 * long + 0.8 * noise,
        "tom": 2.6 * f["body"] + 2.0 * f["low"] + 1.0 * medium + 0.8 * tonal,
        "ride": 2.0 * f["high"] + 1.5 * f["air"] + 1.0 * tonal + 1.2 * long,
    }


def _band_envelope(
    mag: np.ndarray, freqs: np.ndarray, low: float, high: float, fps: float,
    smooth: float = 0.15,
) -> np.ndarray:
    mask = (freqs >= low) & (freqs < high)
    if not np.any(mask):
        return np.zeros(mag.shape[1], dtype=np.float32)
    return spectrum.onset_envelope(mag[mask], fps, smooth_seconds=smooth)


def _pick(env: np.ndarray, fps: float, height: float, min_gap: float) -> np.ndarray:
    if env.size == 0:
        return np.zeros(0, dtype=int)
    floor = max(height, float(np.percentile(env, 65)) * 0.7)
    peaks, _ = find_peaks(
        env,
        height=floor,
        distance=max(1, int(min_gap * fps)),
        prominence=max(0.015, floor * 0.3),
    )
    return peaks


def _high_tail_ratio(segment: np.ndarray, sample_rate: int) -> float:
    """起音后 30–100ms 的高频能量 / 峰值，用来区分闭镲、开镲与吊镲。

    密集段落里下一击（16 分音符，约 116ms）会把「衰减到 -18dB」的判据撑爆，
    所有踩镲都会被误判成吊镲；改用固定时间窗的尾音比就不受后续鼓点影响。
    """
    hop = 128
    mag = spectrum.magnitude(spectrum.stft(segment, n_fft=1024, hop=hop))
    freqs = spectrum.frequencies(1024, sample_rate)
    mask = freqs >= 5000.0
    if not np.any(mask):
        return 0.0
    env = np.sqrt(np.sum(np.square(mag[mask], dtype=np.float64), axis=0))
    if env.size == 0:
        return 0.0
    peak = int(np.argmax(env))
    if env[peak] <= EPS:
        return 0.0
    start = peak + int(0.03 * sample_rate / hop)
    end = peak + int(0.10 * sample_rate / hop)
    tail = env[start:min(end, env.size)]
    if tail.size == 0:
        return 0.0
    return float(np.mean(tail) / env[peak])


def detect_hits(
    samples: np.ndarray,
    sample_rate: int,
    n_fft: int = 1024,
    hop: int = 256,
    min_gap: float = 0.05,
    threshold: float = 0.08,
) -> list[NoteEvent]:
    """从鼓分轨里检测鼓点，返回带 ``detail["drum"]`` 的音符事件。

    低频链（20–5kHz）判底鼓 / 军鼓 / 嗵鼓，高频链（5–16kHz）单独判踩镲 / 开镲 / 吊镲。
    两条链独立，所以「底鼓 + 踩镲」同时响时能各留一个，而不是只剩得分最高的那个。
    """
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    if signal.size < n_fft:
        return []
    # 前面垫 30ms 静音：谱通量靠「相邻帧差分」，否则 0 秒处的第一下鼓永远检测不到
    pad = int(0.03 * sample_rate)
    padded = np.concatenate([np.zeros(pad, dtype=np.float32), signal])
    spec = spectrum.stft(padded, n_fft=n_fft, hop=hop)
    mag = spectrum.magnitude(spec)
    freqs = spectrum.frequencies(n_fft, sample_rate)
    fps = sample_rate / float(hop)
    if mag.shape[1] < 3:
        return []

    low_env = _band_envelope(mag, freqs, 20.0, 5000.0, fps, smooth=0.18)
    high_env = _band_envelope(mag, freqs, 5000.0, 16000.0, fps, smooth=0.12)
    low_peaks = _pick(low_env, fps, threshold, min_gap)
    high_peaks = _pick(high_env, fps, threshold * 0.7, max(0.04, min_gap * 0.8))

    hits: list[NoteEvent] = []

    def frame_start(peak: int) -> float:
        return max(0.0, float(peak * hop / sample_rate) - pad / sample_rate)

    high_times = [frame_start(int(peak)) for peak in high_peaks]

    for peak in low_peaks:
        start = frame_start(int(peak))
        if start >= signal.size / sample_rate:
            continue
        segment = signal[int(start * sample_rate): int(start * sample_rate) + int(0.6 * sample_rate)]
        if segment.size < int(0.03 * sample_rate):
            continue
        features = _features(segment, sample_rate)
        # 只有极低频占绝对主导的才是底鼓；长衰减的 808 常被误判成嗵鼓
        if features["low"] > 0.55:
            label, confidence = "kick", 0.75
        elif features["low"] + features["body"] < 0.18:
            continue  # 高频串过来的踩镲，交给高频链
        else:
            scores = _scores(features)
            candidates = ("kick", "snare", "tom")
            values = np.array([scores[name] for name in candidates], dtype=np.float64)
            values -= values.max()
            probability = np.exp(values / 0.18)
            probability /= probability.sum()
            best = int(np.argmax(probability))
            label = candidates[best]
            confidence = float(np.clip(probability[best], 0.05, 0.99))
        strength = float(low_env[peak])
        hits.append(
            NoteEvent(
                start=start,
                duration=0.2,
                midi=PERCUSSION_ALIASES[DRUM_ALIASES[label]],
                velocity=float(np.clip(0.4 + 0.55 * strength, 0.3, 0.95)),
                confidence=confidence,
                instrument="drums",
                detail={"drum": DRUM_ALIASES[label], "chain": "low", "features": features},
            )
        )

    high_reference = float(np.max(high_env)) if high_env.size else 0.0
    for index, peak in enumerate(high_peaks):
        start = frame_start(int(peak))
        if start >= signal.size / sample_rate:
            continue
        segment = signal[int(start * sample_rate): int(start * sample_rate) + int(0.4 * sample_rate)]
        if segment.size < int(0.03 * sample_rate):
            continue
        ratio = _high_tail_ratio(segment, sample_rate)
        gap = (high_times[index + 1] - start) if index + 1 < len(high_times) else 1.0
        if gap < 0.3:
            # 密集段落（16 分踩镲）：只分闭镲/开镲，不要判吊镲
            label, confidence = ("hat", 0.8) if ratio < 0.5 else ("openhat", 0.6)
        elif ratio < 0.35:
            label, confidence = "hat", 0.8
        elif ratio < 0.65:
            label, confidence = "openhat", 0.62
        else:
            label, confidence = "crash", 0.6
        strength = float(high_env[peak] / high_reference) if high_reference > EPS else 0.5
        hits.append(
            NoteEvent(
                start=start,
                duration=0.2,
                midi=PERCUSSION_ALIASES[DRUM_ALIASES[label]],
                velocity=float(np.clip(0.35 + 0.55 * strength, 0.28, 0.95)),
                confidence=confidence,
                instrument="drums",
                detail={"drum": DRUM_ALIASES[label], "chain": "high", "tail": ratio},
            )
        )

    hits.sort(key=lambda hit: (hit.start, hit.midi))
    return hits
