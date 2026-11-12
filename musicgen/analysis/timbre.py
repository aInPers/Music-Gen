"""音色特征与乐器识别。

思路是「用项目自己的音源当模板库」：把注册表里已下载的每件乐器在若干音高上
渲染一遍，提取同一套音色特征（对数梅尔谱形、MFCC、谱质心/带宽/滚降/平坦度、
起音与衰减、周期性），再对每个检测到的音符做最近邻匹配。这样不需要任何
机器学习依赖，音色空间也与最终渲染音源一致。

局限：模板是「单音、干声」，混音里叠了别的乐器、失真或强混响时会失准；
置信度低的音符会在报告里标注，并统一落到兜底乐器轨。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.notes import midi_to_freq
from ..samples import fetch
from . import spectrum

EPS = 1e-9

#: 模板音高：覆盖常见写作音域，分类时优先比对最接近的音高
TEMPLATE_PITCHES = (36, 40, 43, 48, 52, 55, 60, 64, 67, 72, 76, 79, 84)

#: 高音/低音先验：超出乐器标称音域太远要扣分（半音）
RANGE_PENALTY = 0.35

#: 合成音在最近邻比较里的「先手」：与最好的声学模板差在这个距离内就算打平
SYNTH_PRIOR = 0.08


def _zcr(signal: np.ndarray, sample_rate: int) -> float:
    if signal.size < 2:
        return 0.0
    crossings = np.count_nonzero(np.diff(np.signbit(signal)))
    return float(crossings) / (signal.size / float(sample_rate))


def _envelope(signal: np.ndarray, sample_rate: int, step: float = 0.005) -> np.ndarray:
    frame = max(1, int(step * sample_rate))
    count = signal.size // frame
    if count <= 0:
        return np.zeros(0)
    trimmed = signal[: count * frame].reshape(count, frame)
    return np.sqrt(np.mean(np.square(trimmed, dtype=np.float64), axis=1))


def _attack_decay(signal: np.ndarray, sample_rate: int) -> tuple[float, float, float]:
    env = _envelope(signal, sample_rate)
    if env.size == 0:
        return 0.0, 0.0, 0.0
    peak_index = int(np.argmax(env))
    peak = float(env[peak_index])
    if peak <= EPS:
        return 0.0, 0.0, 0.0
    above = np.where(env[: peak_index + 1] > peak * 0.1)[0]
    onset = int(above[0]) if above.size else 0
    below = np.where(env[peak_index:] < peak * 0.1)[0]
    release = int(below[0]) if below.size else env.size - peak_index
    step = 0.005
    return (peak_index - onset) * step, release * step, peak


def _periodicity(signal: np.ndarray, sample_rate: int) -> float:
    """短时自相关的最高峰（不含 0 延迟），反映谐波程度。"""
    segment = signal[: int(0.12 * sample_rate)] if signal.size > 0.12 * sample_rate else signal
    if segment.size < 256:
        return 0.0
    centered = segment - segment.mean()
    energy = float(np.dot(centered, centered))
    if energy <= EPS:
        return 0.0
    size = 1 << (2 * centered.size - 1).bit_length()
    spectrum_ = np.fft.rfft(centered, size)
    acf = np.fft.irfft(spectrum_ * np.conj(spectrum_), size)[: centered.size]
    acf = acf / (acf[0] + EPS)
    low = int(0.002 * sample_rate)
    high = min(centered.size - 1, int(0.02 * sample_rate))
    if high <= low:
        return 0.0
    return float(np.clip(np.max(acf[low:high]), 0.0, 1.0))


def segment_features(
    samples: np.ndarray,
    sample_rate: int,
    start: float = 0.0,
    duration: float = 0.32,
    n_fft: int = 1024,
    hop: int = 256,
    n_mels: int = 32,
) -> np.ndarray:
    """提取一段音频的音色特征向量（维度固定 55）。"""
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    begin = max(0, int(start * sample_rate))
    end = begin + max(n_fft, int(duration * sample_rate))
    segment = signal[begin:end]
    if segment.size < n_fft:
        segment = np.pad(segment, (0, n_fft - segment.size))
    segment = segment[: max(n_fft, int(duration * sample_rate))]

    bank = spectrum.mel_filterbank(sample_rate, n_fft, n_mels=n_mels, fmin=40.0)
    mag = spectrum.magnitude(spectrum.stft(segment, n_fft=n_fft, hop=hop))
    freqs = spectrum.frequencies(n_fft, sample_rate)
    logmel = spectrum.log_mel(mag, bank)

    shape = logmel.mean(axis=1)
    shape = shape - float(shape.mean())  # 去掉整体电平，只留谱形
    coeffs = spectrum.mfcc(logmel.mean(axis=1)[:, None], n_mfcc=13)[1:13, 0]

    feats = spectrum.spectral_features(mag, freqs, n_fft)
    spectral = np.array(
        [
            float(np.mean(feats.centroid)) / (sample_rate / 2.0),
            float(np.std(feats.centroid)) / (sample_rate / 2.0),
            float(np.mean(feats.spread)) / (sample_rate / 2.0),
            float(np.mean(feats.rolloff)) / (sample_rate / 2.0),
            float(np.mean(feats.flatness)),
            float(np.std(feats.flatness)),
        ],
        dtype=np.float64,
    )

    attack, decay, peak = _attack_decay(segment, sample_rate)
    temporal = np.array(
        [
            min(attack, 0.4) / 0.4,
            min(decay, 2.0) / 2.0,
            _periodicity(segment, sample_rate),
            min(_zcr(segment, sample_rate) / 4000.0, 1.0),
            min(peak * 4.0, 1.0),
        ],
        dtype=np.float64,
    )
    return np.concatenate([shape.astype(np.float64), coeffs.astype(np.float64),
                           spectral, temporal])


def describe_segment(signal: np.ndarray, sample_rate: int,
                     duration: float = 0.4) -> dict[str, float]:
    """给一段音频算「人能看懂」的音色指标（简报与大模型判断用）。

    与 :func:`segment_features` 的关系：那个是喂给最近邻的 55 维向量，
    这个把其中可解释的几维还原成物理量，便于写进 JSON、也便于大模型比较。
    """
    mono = np.asarray(signal, dtype=np.float32).reshape(-1)
    total = max(1024, int(duration * sample_rate))
    segment = mono[:total]
    if segment.size < 1024:
        segment = np.pad(segment, (0, 1024 - segment.size))
    mag = spectrum.magnitude(spectrum.stft(segment, n_fft=2048, hop=512))
    freqs = spectrum.frequencies(2048, sample_rate)
    feats = spectrum.spectral_features(mag, freqs, 2048)
    attack, decay, peak = _attack_decay(segment, sample_rate)
    return {
        "centroid_hz": float(np.median(feats.centroid)) if feats.centroid.size else 0.0,
        "spread_hz": float(np.median(feats.spread)) if feats.spread.size else 0.0,
        "rolloff_hz": float(np.median(feats.rolloff)) if feats.rolloff.size else 0.0,
        "flatness": float(np.median(feats.flatness)) if feats.flatness.size else 0.0,
        "attack_s": round(attack, 3),
        "decay_s": round(decay, 3),
        "harmonicity": round(_periodicity(segment, sample_rate), 3),
        "zcr": round(_zcr(segment, sample_rate), 1),
        "peak": round(peak, 4),
    }


@dataclass
class Template:
    instrument: str
    midi: int
    vector: np.ndarray


@dataclass
class Classification:
    instrument: str
    confidence: float
    ranking: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class TimbreBank:
    """按需构建的乐器模板库。"""

    cache_root: Path | None = None
    sample_rate: int = 44100
    instruments: list[str] | None = None
    warn: object | None = None
    templates: list[Template] = field(default_factory=list)
    _mean: np.ndarray | None = None
    _std: np.ndarray | None = None

    def build(self) -> int:
        """渲染模板并计算标准化参数，返回模板数量。"""
        from ..core.instruments import InstrumentRegistry

        registry = InstrumentRegistry(
            cache_root=self.cache_root,
            sample_rate=self.sample_rate,
            warn=self.warn if callable(self.warn) else None,
        )
        keys = self.instruments or [
            key for key in list(fetch.SOURCES) + list(fetch.SYNTHS)
            if fetch.is_synth(key) or fetch.available_notes(key, self.cache_root)
        ]
        templates: list[Template] = []
        for key in keys:
            source = fetch.source_for(key)
            if source is None:
                continue
            if not fetch.is_synth(key) and not fetch.available_notes(key, self.cache_root):
                continue
            instrument = registry.get(key)
            low = max(0, int(source.midi_low) + 1)
            high = min(127, int(source.midi_high) - 1)
            for midi in TEMPLATE_PITCHES:
                if not low <= midi <= high:
                    continue
                piece = instrument.render_note(midi, 0.8, 0.9)
                if piece is None or piece.shape[1] < 2048:
                    continue
                mono = np.mean(piece, axis=0).astype(np.float32)
                templates.append(
                    Template(instrument=key, midi=midi,
                             vector=segment_features(mono, self.sample_rate))
                )
        self.templates = templates
        if templates:
            matrix = np.stack([item.vector for item in templates])
            self._mean = matrix.mean(axis=0)
            std = matrix.std(axis=0)
            self._std = np.where(std < 1e-6, 1.0, std)
        return len(templates)

    @property
    def ready(self) -> bool:
        return bool(self.templates) and self._mean is not None

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        return (vector - self._mean) / self._std  # type: ignore[operator]

    def classify(self, features: np.ndarray, midi: int) -> Classification:
        """最近邻分类；库为空时按音高给一个兜底乐器。"""
        if not self.ready:
            return Classification(_fallback_instrument(midi), 0.1, [])
        vector = self._normalize(features)
        best: dict[str, float] = {}
        for template in self.templates:
            distance = float(np.linalg.norm(vector - self._normalize(template.vector)))
            distance /= np.sqrt(vector.size) or 1.0
            distance += RANGE_PENALTY * max(0, abs(template.midi - midi) - 3) / 12.0
            source = fetch.source_for(template.instrument)
            if source and not (source.midi_low - 1 <= midi <= source.midi_high + 1):
                # 音符落在乐器标称音域外，音色再像也不能选它：
                # 否则低音区的钢琴/贝斯会被判成小提琴，渲染时还会警告。
                distance += 1.8
            if fetch.is_synth(template.instrument):
                # 电子音先手：流行歌的「其他」分轨绝大多数是合成器，采样模板
                # （钢琴/弦乐）常常只险胜一点点就把整条琶音判成钢琴——听起来
                # 完全是另一首曲子，电平也会差十几 dB。这里做在距离上而不是
                # 排序后调换，概率才和选择一致，不会被「低置信」再丢回钢琴。
                distance -= SYNTH_PRIOR
            if template.instrument not in best or distance < best[template.instrument]:
                best[template.instrument] = distance

        ordered = sorted(best.items(), key=lambda item: item[1])
        distances = np.array([distance for _, distance in ordered])
        weights = np.exp(-(distances - distances.min()) / 0.45)
        probabilities = weights / weights.sum()
        ranking = [(name, float(probability)) for (name, _), probability in zip(ordered, probabilities)]
        return Classification(ordered[0][0], float(probabilities[0]), ranking[:3])


def _fallback_instrument(midi: int) -> str:
    if midi <= 43:
        return "cello"
    if midi >= 76:
        return "violin"
    return "piano"


def classify_notes(
    notes,
    mono: np.ndarray,
    sample_rate: int,
    bank: TimbreBank,
    minimum_confidence: float = 0.34,
) -> str:
    """给音符打上乐器标签，返回兜底乐器键。"""
    for note in notes:
        features = segment_features(
            mono,
            sample_rate,
            start=note.start,
            duration=max(0.18, min(0.45, note.duration + 0.05)),
        )
        result = bank.classify(features, note.midi)
        note.instrument = result.instrument
        note.detail["timbre_confidence"] = result.confidence
        note.confidence = float(np.clip(0.55 * note.confidence + 0.45 * result.confidence, 0.05, 0.99))
        note.detail["ranking"] = result.ranking
        if result.confidence < minimum_confidence:
            note.detail["low_confidence"] = True
    return _fallback_instrument(int(np.median([n.midi for n in notes]))) if notes else "piano"


def midi_range_for(key: str) -> tuple[float, float]:
    """乐器标称音域对应的 Hz 区间（分析范围用）。"""
    source = fetch.SOURCES.get(fetch.resolve(key))
    if source is None:
        return 55.0, 2000.0
    low = max(1, int(source.midi_low)) - 2
    high = min(127, int(source.midi_high)) + 2
    return float(midi_to_freq(low)), float(midi_to_freq(high))
