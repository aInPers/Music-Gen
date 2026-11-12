"""复音音高检测与音符跟踪。

做法是「谱白化 + 谐波求和 + 迭代消去」：

1. 对每帧幅度谱按频率方向做中值白化，压掉宽带噪声与长时泄漏；
2. 在 1/4 半音网格上算谐波和（第 h 次谐波权重 ``1/sqrt(h)``）；
3. 取最高峰作为一个音，再把它各次谐波附近的谱线清零，重复取下一个，
   直到达到复音上限或信噪比不够——这样和弦里的多个音能被逐个拆出来；
4. 把逐帧音高按 ±0.8 半音的容差串成音符，补上力度与置信度。

这是轻量级转谱，不是神经网络：单音/稀疏织体准确度高，密集和弦与强混响
会多检或漏检，重建后仍需人工微调。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import spectrum

EPS = 1e-9


@dataclass
class NoteEvent:
    """一个检测到的音符（时间单位秒，音高为整数 MIDI）。"""

    start: float
    duration: float
    midi: int
    velocity: float = 0.75
    confidence: float = 0.6
    instrument: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class _Voice:
    start: float
    last: float
    pitches: list[float]
    strengths: list[float]

    @property
    def pitch(self) -> float:
        return float(np.median(self.pitches))


def _midi_grid(fmin: float, fmax: float, step: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    low = 69.0 + 12.0 * np.log2(max(fmin, 1e-6) / 440.0)
    high = 69.0 + 12.0 * np.log2(max(fmax, fmin + 1e-6) / 440.0)
    low = max(0.0, low)
    start = np.floor(low / step) * step
    values = np.arange(start, high + step * 0.5, step)
    return values.astype(np.float64), 440.0 * 2.0 ** ((values - 69.0) / 12.0)


def _harmonic_index(
    candidate_hz: np.ndarray,
    freqs: np.ndarray,
    max_harmonics: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """预计算每个候选音高各次谐波落在哪些频点上。"""
    harmonics = np.arange(1, max_harmonics + 1, dtype=np.float64)
    targets = candidate_hz[:, None] * harmonics[None, :]
    index = np.searchsorted(freqs, targets)
    index = np.clip(index, 1, freqs.size - 2)
    neighbours = np.stack([index - 1, index, index + 1], axis=2)  # (n, H, 3)
    valid = targets < freqs[-1] * 0.98
    return neighbours, valid, harmonics


def _harmonic_support(
    column: np.ndarray, freqs: np.ndarray, f0: float, harmonics: int = 4
) -> bool:
    """检查候选基频的前几次谐波是否真有峰，用来排除「把谐波当基频」的伪音。"""
    fundamental = 0.0
    present = 0
    checked = 0
    for harmonic in range(1, harmonics + 1):
        target = f0 * harmonic
        if target >= freqs[-1] * 0.95:
            break
        index = int(np.searchsorted(freqs, target))
        low = max(0, index - 2)
        high = min(column.size, index + 3)
        if high <= low:
            continue
        value = float(np.max(column[low:high]))
        if harmonic == 1:
            fundamental = value
        checked += 1
        if value > 0.7:
            present += 1
    if fundamental < 0.9:
        return False
    return present >= 2 and present >= min(2, checked)


class _FrameAnalyzer:
    """把一帧的谱线变成音高候选。"""

    def __init__(
        self,
        freqs: np.ndarray,
        fmin: float,
        fmax: float,
        step: float = 0.25,
        max_harmonics: int = 10,
    ) -> None:
        self.grid, self.candidate_hz = _midi_grid(fmin, fmax, step)
        self.step = step
        self.freqs = freqs
        self.neighbours, self.valid, harmonics = _harmonic_index(
            self.candidate_hz, freqs, max_harmonics
        )
        self.harmonics = harmonics
        self.targets = self.candidate_hz[:, None] * harmonics[None, :]
        weights = 1.0 / np.sqrt(harmonics)
        self.weights = weights[None, :]
        self.normalizer = np.maximum((self.weights * self.valid).sum(axis=1), EPS)
        self.size = self.grid.size

    def salience(self, column: np.ndarray) -> np.ndarray:
        # 在精确的谐波频率上做线性插值，而不是「附近 ±1 个频点取最大」：
        # 后者在低频会让相邻候选共享同一频点，形成平台，峰值位置随机偏一格。
        values = np.interp(self.targets, self.freqs, column, left=0.0, right=0.0)
        return (values * self.weights * self.valid).sum(axis=1) / self.normalizer


def _bend_curve(pitches: list[float], base: int, points: int = 12,
                limit: float = 2.0) -> list[tuple[float, float]]:
    """把逐帧音高压成 ``[(0..1 位置, 半音偏离), …]``，最多 ``points`` 个点。"""
    if len(pitches) < 3:
        return []
    values = np.asarray(pitches, dtype=np.float64) - float(base)
    values = np.clip(values, -limit, limit)
    count = min(points, values.size)
    if count < 2:
        return []
    # 等间隔重采样到固定点数，首尾保留
    grid = np.linspace(0.0, 1.0, count)
    source = np.linspace(0.0, 1.0, values.size)
    sampled = np.interp(grid, source, values)
    # 起音阻尼：前 15% 的偏离多半是追踪器还没抓住稳定音高，不是真的滑音。
    # 不压掉的话每个音都会带一下「滑入」，听感会显得油。
    ramp = np.clip(grid / 0.15, 0.0, 1.0)
    sampled = sampled * ramp
    # 全部贴着 0 就不写，省工程体积
    if float(np.max(np.abs(sampled))) < 0.06:
        return []
    return [(round(float(g), 3), round(float(v), 2)) for g, v in zip(grid, sampled)]


def detect_notes(
    samples: np.ndarray,
    sample_rate: int,
    fmin: float = 55.0,
    fmax: float = 2000.0,
    max_polyphony: int = 1,
    n_fft: int = spectrum.DEFAULT_N_FFT,
    hop: int = spectrum.DEFAULT_HOP,
    min_duration: float = 0.06,
    min_salience: float = 0.55,
    relative_threshold: float = 0.24,
    flatness_gate: float = 0.62,
    pitch_tolerance: float = 1.2,
    max_gap: float = 0.12,
    keep_bend: bool = False,
) -> list[NoteEvent]:
    """从单声道信号里检测音符。

    ``max_polyphony``：同时最多几个音（人声/贝斯 1，钢琴 6，吉他 4）。
    ``min_salience``：谐波和的绝对下限，白噪声大约在 1.0–1.5，用来挡噪声。
    """
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    if signal.size < n_fft:
        return []

    spec = spectrum.stft(signal, n_fft=n_fft, hop=hop)
    mag = spectrum.magnitude(spec)
    if mag.shape[1] == 0:
        return []
    freqs = spectrum.frequencies(n_fft, sample_rate)
    white = spectrum.whiten_log(mag, width=31)
    feats = spectrum.spectral_features(mag, freqs, n_fft)
    fps = sample_rate / float(hop)
    times = spectrum.frame_times(mag.shape[1], hop, sample_rate)

    level = feats.rms
    reference = float(np.percentile(level, 90)) if level.size else 0.0
    gate = level > max(reference * 0.018, 1e-4)
    clean = feats.flatness < flatness_gate

    analyzer = _FrameAnalyzer(freqs, fmin, fmax, step=0.25)
    voices: list[_Voice] = []
    finished: list[NoteEvent] = []

    def close(voice: _Voice, end_time: float) -> None:
        duration = end_time - voice.start
        if duration + EPS < min_duration:
            return
        strength = float(np.mean(voice.strengths)) if voice.strengths else 0.0
        # 对数域白化后的谐波和大概在 0.5–3 之间，用指数映射到 0–1，
        # 直接除 4 会让正常的和弦内声部全都显示成 0.15 的「低置信」。
        confidence = 1.0 - float(np.exp(-max(strength, 0.0) / 0.9))
        note = NoteEvent(
            start=float(voice.start),
            duration=float(duration),
            midi=int(round(voice.pitch)),
            confidence=float(np.clip(confidence, 0.05, 0.99)),
        )
        if keep_bend:
            curve = _bend_curve(voice.pitches, note.midi)
            if curve:
                # 人声、弦乐、推弦吉他都不是「一个音高按到底」：
                # 逐帧音高相对音符音高的偏离就是滑音与颤音，丢掉就只剩台阶。
                note.detail["bend"] = curve
        finished.append(note)

    for frame in range(mag.shape[1]):
        now = float(times[frame])
        if not gate[frame] or not clean[frame]:
            still: list[_Voice] = []
            for voice in voices:
                if now - voice.last > max_gap:
                    close(voice, voice.last + hop / sample_rate)
                else:
                    still.append(voice)
            voices = still
            continue

        column = white[:, frame].copy()
        detected: list[tuple[float, float]] = []
        scale = 0.0
        for index in range(max_polyphony):
            salience = analyzer.salience(column)
            position = int(np.argmax(salience))
            peak = float(salience[position])
            if position == 0 or position == analyzer.size - 1:
                break
            if index == 0:
                scale = peak
                if peak < min_salience:
                    break
            elif peak < scale * relative_threshold or peak < min_salience * 0.55:
                break
            # 必须是局部极大，避免取到谐波峰的肩部
            if salience[position] < salience[position - 1] or salience[position] < salience[position + 1]:
                column[analyzer.neighbours[position]] = 0.0
                continue

            offset = 0.0
            base = float(analyzer.candidate_hz[position])
            if not _harmonic_support(column, freqs, base):
                column[analyzer.neighbours[position]] = 0.0
                continue
            left, mid, right = (
                float(salience[position - 1]),
                float(salience[position]),
                float(salience[position + 1]),
            )
            denominator = left - 2.0 * mid + right
            if abs(denominator) > EPS:
                offset = 0.5 * (left - right) / denominator
                offset = float(np.clip(offset, -0.5, 0.5))
            pitch = float(analyzer.grid[position] + offset * analyzer.step)
            if not np.isfinite(pitch):
                break
            detected.append((pitch, peak))
            column[analyzer.neighbours[position]] = 0.0

        if not detected:
            for voice in voices:
                if now - voice.last > max_gap:
                    close(voice, voice.last + hop / sample_rate)
            voices = [v for v in voices if now - v.last <= max_gap]
            continue

        # 一一匹配：先配最接近的，容差 pitch_tolerance 个半音
        pairs: list[tuple[float, int, int]] = []
        for di, (pitch, _strength) in enumerate(detected):
            for vi, voice in enumerate(voices):
                distance = abs(pitch - voice.pitch)
                if distance <= pitch_tolerance:
                    pairs.append((distance, di, vi))
        pairs.sort(key=lambda item: item[0])
        used_d: set[int] = set()
        used_v: set[int] = set()
        for _distance, di, vi in pairs:
            if di in used_d or vi in used_v:
                continue
            used_d.add(di)
            used_v.add(vi)
            voices[vi].pitches.append(detected[di][0])
            voices[vi].strengths.append(detected[di][1])
            voices[vi].last = now
            if len(voices[vi].pitches) > 64:  # 长音只留最近一段，够求中位数
                del voices[vi].pitches[:-64]
                del voices[vi].strengths[:-64]

        for di, (pitch, strength) in enumerate(detected):
            if di in used_d:
                continue
            voices.append(_Voice(start=now, last=now, pitches=[pitch], strengths=[strength]))

        keep: list[_Voice] = []
        for voice in voices:
            if now - voice.last > max_gap:
                close(voice, voice.last + hop / sample_rate)
            else:
                keep.append(voice)
        voices = keep

    end_time = float(times[-1]) + hop / sample_rate if times.size else 0.0
    for voice in voices:
        close(voice, max(end_time, voice.last + hop / sample_rate))

    notes = _merge_notes([n for n in finished if n.duration >= min_duration], max_gap)
    if max_polyphony <= 1:
        notes = _enforce_monophony(notes, min_duration)
    else:
        notes = _prune_harmonic_echoes(notes)
    notes = _prune_weak_duplicates(notes)
    _assign_velocity(notes, signal, sample_rate)
    notes.sort(key=lambda note: (note.start, note.midi))
    return notes


def _merge_notes(notes: list[NoteEvent], gap: float) -> list[NoteEvent]:
    """同音高、间隔很小的相邻音符合并，减少碎片。"""
    notes = sorted(notes, key=lambda note: (note.midi, note.start))
    merged: list[NoteEvent] = []
    for note in notes:
        if (
            merged
            and merged[-1].midi == note.midi
            and note.start - merged[-1].end <= gap
        ):
            previous = merged[-1]
            previous.duration = max(previous.end, note.end) - previous.start
            previous.confidence = max(previous.confidence, note.confidence)
            continue
        merged.append(note)
    return merged


def _prune_harmonic_echoes(notes: list[NoteEvent]) -> list[NoteEvent]:
    """同一时刻、恰好相差 2–6 次谐波且明显更弱的音，多半是谐波误检，删掉。"""
    ordered = sorted(notes, key=lambda note: (note.start, -_score(note)))
    kept: list[NoteEvent] = []
    for note in ordered:
        echo = False
        for other in kept[-16:]:
            if abs(note.start - other.start) > 0.08:
                continue
            if note.midi - other.midi in (12, 19, 24, 28, 31, 34, 36) \
                    and _score(other) > _score(note) * 1.4:
                echo = True
                break
        if not echo:
            kept.append(note)
    kept.sort(key=lambda note: (note.start, note.midi))
    return kept


def _score(note: NoteEvent) -> float:
    return note.duration * max(note.confidence, 0.05)


def _enforce_monophony(notes: list[NoteEvent], min_duration: float) -> list[NoteEvent]:
    """单声部材料只保留一条音高线：同一次起音只留最可信的一个音。"""
    ordered = sorted(notes, key=lambda note: (note.start, -note.duration))
    kept: list[NoteEvent] = []
    for note in ordered:
        if kept and note.start - kept[-1].start < 0.06:
            if _score(note) > _score(kept[-1]):
                kept[-1] = note
            continue
        if kept and note.start < kept[-1].end - 1e-6:
            previous = kept[-1]
            overlap = previous.end - note.start
            if overlap > 0.55 * min(previous.duration, note.duration):
                if _score(note) > _score(previous):
                    kept[-1] = note
                continue
            previous.duration = max(min_duration, note.start - previous.start)
        kept.append(note)
    return [note for note in kept if note.duration >= min_duration]


def _prune_weak_duplicates(notes: list[NoteEvent]) -> list[NoteEvent]:
    """同一时刻两个相邻音高、强弱悬殊时，弱的多半是基频估计抖动，删掉。"""
    ordered = sorted(notes, key=lambda note: (note.start, -_score(note)))
    kept: list[NoteEvent] = []
    for note in ordered:
        duplicate = False
        for index in range(len(kept) - 1, max(-1, len(kept) - 5), -1):
            other = kept[index]
            if abs(note.start - other.start) > 0.05:
                break
            if abs(note.midi - other.midi) <= 2 and _score(other) > _score(note) * 1.8:
                duplicate = True
                break
        if not duplicate:
            kept.append(note)
    kept.sort(key=lambda note: (note.start, note.midi))
    return kept


def _assign_velocity(notes: list[NoteEvent], signal: np.ndarray, sample_rate: int) -> None:
    """用音符片段的 RMS 映射力度，最响的音符约 0.92。"""
    if not notes:
        return
    levels: list[float] = []
    for note in notes:
        begin = max(0, int(note.start * sample_rate))
        end = min(signal.size, int(note.end * sample_rate) + int(0.03 * sample_rate))
        segment = signal[begin:end]
        levels.append(float(np.sqrt(np.mean(np.square(segment, dtype=np.float64)))) if segment.size else 0.0)
    reference = float(np.percentile(levels, 90)) if levels else 0.0
    for note, level in zip(notes, levels):
        ratio = level / reference if reference > EPS else 0.0
        note.velocity = float(np.clip(0.32 + 0.62 * ratio ** 0.6, 0.25, 0.95))


def monophonic_pitch_track(
    samples: np.ndarray,
    sample_rate: int,
    fmin: float = 70.0,
    fmax: float = 1100.0,
    **kwargs,
) -> list[NoteEvent]:
    """人声 / 贝斯这类单声部材料的便捷入口。"""
    kwargs.setdefault("max_polyphony", 1)
    return detect_notes(samples, sample_rate, fmin=fmin, fmax=fmax, **kwargs)
