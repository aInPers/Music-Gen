"""频谱分析：STFT、梅尔滤波器组、MFCC、谐波/打击分离与调性估计。

这一层只做「时频表示」，不含任何音乐语义判断；上层 :mod:`pitch` /
:mod:`rhythm` / :mod:`timbre` 都建立在这里。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import get_window

#: 默认分析参数：46ms 窗 / 11.6ms 步进（44.1kHz），兼顾音高分辨率与时间分辨率
DEFAULT_N_FFT = 2048
DEFAULT_HOP = 512
EPS = 1e-10


def stft(
    samples: np.ndarray,
    n_fft: int = DEFAULT_N_FFT,
    hop: int = DEFAULT_HOP,
    window: str = "hann",
    center: bool = True,
) -> np.ndarray:
    """短时傅里叶变换，返回 ``(n_fft//2+1, 帧数)`` 的 complex64。"""
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    if signal.size == 0:
        return np.zeros((n_fft // 2 + 1, 0), dtype=np.complex64)
    if signal.size < n_fft:
        signal = np.pad(signal, (0, n_fft - signal.size))

    win = np.asarray(get_window(window, n_fft), dtype=np.float32)
    pad = n_fft // 2 if center else 0
    if pad:
        mode = "reflect" if signal.size > pad else "constant"
        padded = np.pad(signal, (pad, pad), mode=mode)
    else:
        padded = signal

    n_frames = 1 + max(0, (padded.size - n_fft) // hop)
    out = np.empty((n_fft // 2 + 1, n_frames), dtype=np.complex64)
    view = np.lib.stride_tricks.sliding_window_view(padded, n_fft)
    block = 256
    for start in range(0, n_frames, block):
        stop = min(n_frames, start + block)
        index = np.arange(start, stop) * hop
        frames = view[index] * win
        out[:, start:stop] = np.fft.rfft(frames, axis=1).T.astype(np.complex64)
    return out


def magnitude(spectrum: np.ndarray) -> np.ndarray:
    return np.abs(spectrum).astype(np.float32)


def frame_times(n_frames: int, hop: int = DEFAULT_HOP, sample_rate: int = 44100) -> np.ndarray:
    """每一帧中心的秒数（center=True 时第 0 帧在 0 秒）。"""
    return np.arange(n_frames, dtype=np.float64) * hop / float(sample_rate)


def frequencies(n_fft: int = DEFAULT_N_FFT, sample_rate: int = 44100) -> np.ndarray:
    return np.fft.rfftfreq(n_fft, 1.0 / float(sample_rate)).astype(np.float64)


def frame_rms(mag: np.ndarray, n_fft: int = DEFAULT_N_FFT) -> np.ndarray:
    """由幅度谱近似每帧 RMS（Parseval，单边谱乘 2）。"""
    energy = np.sum(np.square(mag, dtype=np.float64), axis=0) * 2.0
    return np.sqrt(energy) / float(n_fft)


def hz_to_mel(freq):
    return 2595.0 * np.log10(1.0 + np.asarray(freq, dtype=np.float64) / 700.0)


def mel_to_hz(mel):
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def mel_filterbank(
    sample_rate: int = 44100,
    n_fft: int = DEFAULT_N_FFT,
    n_mels: int = 40,
    fmin: float = 30.0,
    fmax: float | None = None,
) -> np.ndarray:
    """三角梅尔滤波器组，形状 ``(n_mels, n_fft//2+1)``。"""
    fmax = float(fmax if fmax else sample_rate / 2.0)
    fmin = max(1.0, float(fmin))
    mels = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz = mel_to_hz(mels)
    bins = np.floor((n_fft + 1) * hz / sample_rate).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)

    bank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for index in range(n_mels):
        left, center, right = bins[index], bins[index + 1], bins[index + 2]
        if center == left:
            center = min(left + 1, n_fft // 2)
        if right == center:
            right = min(center + 1, n_fft // 2)
        if center > left:
            ramp = np.linspace(0.0, 1.0, center - left, endpoint=False)
            bank[index, left:center] = ramp
        if right > center:
            ramp = np.linspace(1.0, 0.0, right - center, endpoint=False)
            bank[index, center:right] = ramp
    return bank


def apply_mel(mag: np.ndarray, bank: np.ndarray) -> np.ndarray:
    return bank @ mag


def log_mel(mag: np.ndarray, bank: np.ndarray) -> np.ndarray:
    return np.log1p(apply_mel(mag, bank) * 10.0).astype(np.float32)


def mfcc(logmel: np.ndarray, n_mfcc: int = 13) -> np.ndarray:
    """倒谱系数（DCT-II，正交归一）；第 0 维是整体电平，通常丢掉。"""
    from scipy.fft import dct

    coeffs = dct(logmel, type=2, axis=0, norm="ortho")
    return coeffs[:n_mfcc].astype(np.float32)


@dataclass
class SpectralFeatures:
    """每帧的谱形描述，长度都等于帧数。"""

    centroid: np.ndarray
    spread: np.ndarray
    rolloff: np.ndarray
    flatness: np.ndarray
    rms: np.ndarray


def spectral_features(
    mag: np.ndarray,
    freqs: np.ndarray,
    n_fft: int = DEFAULT_N_FFT,
    rolloff: float = 0.85,
) -> SpectralFeatures:
    total = np.sum(mag, axis=0, dtype=np.float64) + EPS
    weighted = (freqs[:, None] * mag).sum(axis=0, dtype=np.float64)
    centroid = weighted / total
    spread = np.sqrt(
        (np.square(freqs[:, None] - centroid[None, :]) * mag).sum(axis=0, dtype=np.float64) / total
    )
    cumulative = np.cumsum(mag, axis=0, dtype=np.float64)
    threshold = cumulative[-1] * rolloff
    index = np.argmax(cumulative >= threshold[None, :], axis=0)
    flatness = np.exp(np.mean(np.log(mag + EPS), axis=0)) / (np.mean(mag, axis=0) + EPS)
    return SpectralFeatures(
        centroid=centroid.astype(np.float32),
        spread=spread.astype(np.float32),
        rolloff=freqs[np.clip(index, 0, freqs.size - 1)].astype(np.float32),
        flatness=flatness.astype(np.float32),
        rms=frame_rms(mag, n_fft),
    )


def harmonic_percussive(mag: np.ndarray, kernel: int = 17) -> tuple[np.ndarray, np.ndarray]:
    """中值滤波谐波/打击分离（HPSS），返回 ``(谐波分量, 打击分量)``。"""
    kernel = max(3, int(kernel) | 1)  # 必须是奇数
    harmonic = median_filter(mag, size=(1, kernel), mode="nearest")
    percussive = median_filter(mag, size=(kernel, 1), mode="nearest")
    power = 2.0
    mask = (harmonic ** power) / (harmonic ** power + percussive ** power + EPS)
    return mag * mask, mag * (1.0 - mask)


def whiten(mag: np.ndarray, width: int = 31) -> np.ndarray:
    """按频率方向的中值做谱白化，压掉宽带噪声与长时谐波泄漏。"""
    floor = median_filter(mag, size=(max(3, int(width) | 1), 1), mode="nearest")
    return mag / (floor + EPS)


def whiten_log(mag: np.ndarray, width: int = 31) -> np.ndarray:
    """对数域白化：先取对数再减局部中值，兼顾低频与高频的对比度。

    直接对幅度做除法会让稀疏的高频谐波被过度放大（低音区的中值里
    混着好几根谐波，高音区的中值却只是噪声底），表现为「高音虚检」。
    """
    logmag = np.log1p(mag)
    floor = median_filter(logmag, size=(max(3, int(width) | 1), 1), mode="nearest")
    return np.maximum(0.0, logmag - floor).astype(np.float32)


def band_energy(mag: np.ndarray, freqs: np.ndarray, low: float, high: float) -> np.ndarray:
    """频带能量（每帧一个值）。"""
    mask = (freqs >= low) & (freqs < high)
    if not np.any(mask):
        return np.zeros(mag.shape[1], dtype=np.float32)
    return np.sum(mag[mask], axis=0, dtype=np.float64).astype(np.float32)


def chroma(mag: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """12 维色度向量（按帧累加后归一化）。"""
    usable = (freqs >= 55.0) & (freqs <= 5000.0) & (mag.sum(axis=1) > 0)
    if not np.any(usable):
        return np.zeros(12, dtype=np.float64)
    pitch = 69.0 + 12.0 * np.log2(np.maximum(freqs[usable], 1e-9) / 440.0)
    classes = np.mod(np.round(pitch).astype(int), 12)
    weights = mag[usable].sum(axis=1, dtype=np.float64)
    out = np.zeros(12, dtype=np.float64)
    np.add.at(out, classes, weights)
    total = float(out.sum())
    return out / total if total > 0 else out


#: Krumhansl-Schmuckler 调性模板（大调 / 小调）
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def estimate_key(chroma_vector: np.ndarray) -> tuple[str, float]:
    """用色度向量与调性模板做相关，返回 ``("A minor", 相关系数)``。"""
    vector = np.asarray(chroma_vector, dtype=np.float64).reshape(-1)
    if vector.size != 12 or float(np.sum(np.abs(vector))) <= 0:
        return "", 0.0
    vector = vector - vector.mean()
    norm = float(np.linalg.norm(vector)) or 1.0

    best_name, best_score = "", -2.0
    for root in range(12):
        for profile, mode in ((_MAJOR_PROFILE, "major"), (_MINOR_PROFILE, "minor")):
            rotated = np.roll(profile - profile.mean(), root)
            score = float(np.dot(vector, rotated) / (norm * (np.linalg.norm(rotated) or 1.0)))
            if score > best_score:
                best_score = score
                best_name = f"{_PITCH_NAMES[root]} {mode}"
    return best_name, best_score


def onset_envelope(mag: np.ndarray, fps: float, smooth_seconds: float = 0.35) -> np.ndarray:
    """谱通量（对数幅度正差分之和），再减局部均值做成自适应包络。"""
    if mag.shape[1] < 2:
        return np.zeros(mag.shape[1], dtype=np.float32)
    logmag = np.log1p(mag * 10.0)
    flux = np.zeros(mag.shape[1], dtype=np.float32)
    flux[1:] = np.sum(np.maximum(0.0, np.diff(logmag, axis=1)), axis=0)
    window = max(3, int(round(smooth_seconds * fps)) | 1)
    local = uniform_filter1d(flux, size=window, mode="nearest")
    env = np.maximum(0.0, flux - local)
    peak = float(np.max(env))
    return env / peak if peak > 0 else env
