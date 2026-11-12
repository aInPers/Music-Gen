"""从原曲分轨里提取采样，做成工程自带的采样乐器。

内置采样库与合成预设只能「像」原曲，不可能「是」原曲——电子音色尤其如此：
supersaw / filtered pluck 的谱形用钢琴、拨弦预设去凑，怎么调都不对。

这里换个思路：既然 Demucs 已经把分轨分出来了，就直接从分轨里切出这些音，
按音高命名存进 ``<工程>/instruments/<键>/``，让渲染器用原曲自己的音色发声。
渲染仍然是「读采样 + 按音高变速」，所以音符照样能改——改音高就是换个采样
或换个变速比，改时值就是截断位置变。

限制来自采样器：``MAX_SAMPLE_SHIFT`` 是 7 个半音，所以每 3 个半音至少要有一个
采样，才能保证任何音高都不会被过度变速（变速过度会同时改变共振峰，听起来像
花栗鼠或巨人的声音）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..core.notes import midi_to_name
from ..core.wavio import write_wav

#: 相邻采样之间最多差几个半音（采样器的变速上限是 7，留一点余量）
SAMPLE_SPACING = 3

#: 一条轨最多存多少个采样，控制工程体积（每个约 100–300KB）
MAX_SAMPLES = 18

#: 采样最短时长：太短的片段听不出音色，变速后还会露出窗口效应
MIN_SAMPLE_SECONDS = 0.22


def _span_rms(mono: np.ndarray, sample_rate: int, start: float, duration: float) -> float:
    begin = max(0, int(start * sample_rate))
    end = min(mono.size, int((start + duration) * sample_rate))
    if end <= begin:
        return 0.0
    seg = mono[begin:end].astype(np.float64)
    return float(np.sqrt(np.mean(np.square(seg))))


def _isolated(notes, index: int, tolerance: float = 0.02) -> bool:
    """这个音在响的时候，同一条轨上没有别的音在响（音色才纯）。"""
    note = notes[index]
    for other_index, other in enumerate(notes):
        if other_index == index:
            continue
        if other.start < note.end - tolerance and other.end > note.start + tolerance:
            return False
    return True


def _attack_near(mono: np.ndarray, sample_rate: int, time: float,
                 window: float = 0.06) -> float:
    """在 ``time`` 附近找真正的起音点（能量上升最陡处）。

    检测到的音符起点常有几十毫秒误差，直接按它切会切掉起音、切进上一个音，
    采样听起来就「错位」。按真实起音切，播放时起音才落在音符起点上。
    """
    begin = max(0, int((time - window) * sample_rate))
    end = min(mono.size, int((time + window) * sample_rate))
    if end - begin < 64:
        return time
    seg = np.abs(mono[begin:end].astype(np.float32))
    hop = max(1, int(0.005 * sample_rate))
    frames = seg[: seg.size // hop * hop].reshape(-1, hop)
    if frames.shape[0] < 3:
        return time
    energy = frames.max(axis=1)
    rise = np.diff(energy)
    index = int(np.argmax(rise))
    return float((begin + index * hop) / sample_rate)


def _cut(mono: np.ndarray, sample_rate: int, start: float, length: float,
         fade_in: float = 0.004, fade_out: float = 0.04,
         normalize: bool = True) -> np.ndarray | None:
    begin = max(0, int(start * sample_rate))
    count = int(length * sample_rate)
    if count < int(0.05 * sample_rate):
        return None
    end = min(mono.size, begin + count)
    seg = np.array(mono[begin:end], dtype=np.float32, copy=True)
    if seg.size < int(0.05 * sample_rate):
        return None
    fade_in_n = min(int(fade_in * sample_rate), seg.size // 4)
    fade_out_n = min(int(fade_out * sample_rate), seg.size // 2)
    if fade_in_n > 1:
        seg[:fade_in_n] *= np.linspace(0.0, 1.0, fade_in_n, dtype=np.float32)
    if fade_out_n > 1:
        seg[-fade_out_n:] *= np.linspace(1.0, 0.0, fade_out_n, dtype=np.float32)
    peak = float(np.max(np.abs(seg)))
    if peak <= 1e-5:
        return None
    if normalize:
        # 按音高建模的采样需要统一电平；逐事件采样不能归一化，
        # 否则每个音的强弱被抹平，整条轨的动态就没了。
        seg *= 0.9 / peak
    # 采样器与写盘都按 (通道, 样本) 处理；单声道复制到两声道，
    # 声像由渲染端的 pan 决定，不在这里预埋立体声宽度。
    return np.vstack([seg, seg])


def _pick_pitches(candidates: list[tuple[int, float, float]], limit: int) -> list[int]:
    """在候选里挑一组覆盖音域的采样音高（相邻至少 SAMPLE_SPACING 个半音）。"""
    if not candidates:
        return []
    # 按音高排序，同一音高只留最长的那个
    best: dict[int, float] = {}
    for midi, duration, _score in candidates:
        best[midi] = max(best.get(midi, 0.0), duration)
    pitches = sorted(best)

    # 先保证覆盖：从低到高每 SAMPLE_SPACING 个半音取一个最近的候选
    chosen: list[int] = []
    low, high = pitches[0], pitches[-1]
    target = low
    while target <= high and len(chosen) < limit:
        nearest = min(pitches, key=lambda value: (abs(value - target), value))
        if not chosen or abs(nearest - chosen[-1]) >= SAMPLE_SPACING:
            chosen.append(nearest)
        target += SAMPLE_SPACING
    if pitches[-1] not in chosen and len(chosen) < limit:
        chosen.append(pitches[-1])
    return chosen


def extract_track_samples(
    mono: np.ndarray,
    sample_rate: int,
    notes,
    directory,
    *,
    max_samples: int = MAX_SAMPLES,
    drums: bool = False,
) -> int:
    """把一条轨的音符对应的音频切成采样，写到 ``directory``，返回写出的采样数。"""
    if not notes or mono.size == 0:
        return 0

    if drums:
        # 鼓件：每个音高取最响的一下，不做任何变速
        by_pitch: dict[int, float] = {}
        for note in notes:
            level = _span_rms(mono, sample_rate, note.start, max(note.duration, 0.05))
            by_pitch[note.midi] = max(by_pitch.get(note.midi, 0.0), level)
        if not by_pitch:
            return 0
        directory.mkdir(parents=True, exist_ok=True)
        written = 0
        for pitch in sorted(by_pitch, key=lambda value: -by_pitch[value])[:max_samples]:
            hits = [note for note in notes if note.midi == pitch]
            strongest = max(hits, key=lambda note: _span_rms(
                mono, sample_rate, note.start, max(note.duration, 0.05)))
            # 鼓是瞬态，切长一点把衰减收进去；吊镲给得更长
            length = 1.2 if pitch in (49, 51, 52, 57) else 0.6
            start = _attack_near(mono, sample_rate, strongest.start, window=0.04)
            seg = _cut(mono, sample_rate, start, length, fade_out=0.08)
            if seg is None:
                continue
            write_wav(directory / f"{midi_to_name(pitch)}.wav", seg, sample_rate)
            written += 1
        return written

    ordered = sorted(notes, key=lambda note: note.start)
    candidates: list[tuple[int, float, float]] = []
    for index, note in enumerate(ordered):
        if note.duration < MIN_SAMPLE_SECONDS:
            continue
        if not _isolated(ordered, index):
            continue
        level = _span_rms(mono, sample_rate, note.start, note.duration)
        if level <= 1e-4:
            continue
        candidates.append((note.midi, note.duration, level))
    if not candidates:
        # 全是密集重叠的织体：退一步，用「最长的那几个」当采样，音色稍脏但仍是原音
        fallback = sorted(ordered, key=lambda note: -note.duration)[:max_samples]
        candidates = [(note.midi, note.duration, 1.0) for note in fallback
                      if note.duration >= 0.12]

    chosen = _pick_pitches(candidates, max_samples)
    if len(chosen) < 4 and len(ordered) >= 4:
        # 密集织体里「完全纯净」的音很少。退一步用最长的那些补足覆盖，
        # 采样里混进一点别的音，也比整条轨没有原音色好。
        extra: list[tuple[int, float, float]] = []
        for note in sorted(ordered, key=lambda item: -item.duration):
            if note.duration < 0.15:
                continue
            level = _span_rms(mono, sample_rate, note.start, note.duration)
            if level <= 1e-4:
                continue
            extra.append((note.midi, note.duration, level))
            if len(extra) >= max_samples * 2:
                break
        merged = {(midi, duration): score for midi, duration, score in candidates}
        merged.update({(midi, duration): score for midi, duration, score in extra})
        candidates = [(midi, duration, score) for (midi, duration), score in merged.items()]
        chosen = _pick_pitches(candidates, max_samples)
    if not chosen:
        return 0

    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for pitch in chosen:
        pool = sorted([note for note in ordered
                       if note.midi == pitch and note.duration >= 0.12],
                      key=lambda item: -item.duration)
        for note in pool[:6]:
            start = _attack_near(mono, sample_rate, note.start)
            length = min(max(note.duration * 1.15, MIN_SAMPLE_SECONDS), 2.5)
            seg = _cut(mono, sample_rate, start, length)
            if seg is None:
                continue
            write_wav(directory / f"{midi_to_name(pitch)}.wav", seg, sample_rate)
            written += 1
            break
    return written


def _fingerprint(seg: np.ndarray, sample_rate: int, bands: int = 24) -> np.ndarray:
    """24 段对数频谱，用来判断两个事件是不是「同一个声音」。"""
    from . import spectrum

    mono = np.mean(seg, axis=0) if seg.ndim > 1 else seg
    if mono.size < 512:
        return np.zeros(bands, dtype=np.float32)
    mag = spectrum.magnitude(spectrum.stft(mono.astype(np.float32), n_fft=1024, hop=512))
    freqs = spectrum.frequencies(1024, sample_rate)
    edges = np.geomspace(60.0, min(12000.0, sample_rate / 2 * 0.95), bands + 1)
    out = np.zeros(bands, dtype=np.float32)
    for i in range(bands):
        sel = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if sel.any():
            out[i] = float(np.log1p(mag[sel].sum()))
    norm = float(np.linalg.norm(out))
    return out / norm if norm > 1e-9 else out


def extract_event_samples(
    mono: np.ndarray,
    sample_rate: int,
    notes,
    directory,
    *,
    max_seconds: float = 1.2,
    keep: float = 0.985,
    prefix: str = "",
) -> dict[int, str]:
    """给每个音符切一个「它自己的」采样，返回 ``{音符下标: 采样名}``。

    **不按绝对音高命名**：文件名只是序号，播放时变速比为 0，所以音高、
    起音、音色全是原样，检测得对不对都不影响发声。

    ``prefix``：不同音轨**必须**给不同前缀。所有轨共用同一个 ``samples/``
    目录，若都从 ``e00001`` 起编号，后写的轨会覆盖先写的轨，音符就会播到
    别的轨、别的时刻的片段（曾经导致整首伴奏听起来是乱的）。

    去重：同一个检测音高、而且 24 段频谱余弦相似度 ≥ ``keep`` 的事件共用一个
    采样——只在非常像的时候才合并，合错了最多多占空间，不会变调。
    去重只在同一条轨内做：不同分轨/不同音轨的声音不该共用片段。
    """
    if not notes or mono.size == 0:
        return {}
    directory.mkdir(parents=True, exist_ok=True)

    order = sorted(range(len(notes)), key=lambda i: notes[i].start)
    bank: dict[tuple[int, tuple], str] = {}
    mapping: dict[int, str] = {}
    written = 0

    for index in order:
        note = notes[index]
        start = _attack_near(mono, sample_rate, note.start)
        length = min(max(note.duration * 1.15, 0.12), max_seconds)
        seg = _cut(mono, sample_rate, start, length, fade_out=0.03, normalize=False)
        if seg is None:
            continue
        mark = _fingerprint(seg, sample_rate)
        key = (int(note.midi), tuple(np.round(mark * 20).astype(int)))
        name = bank.get(key)
        if name is None:
            written += 1
            name = f"{prefix}e{written:05d}"
            write_wav(directory / f"{name}.wav", seg, sample_rate)
            bank[key] = name
        mapping[index] = name
    return mapping


def extract_exact_segments(
    data: np.ndarray,
    sample_rate: int,
    events: list[tuple[float, float]],
    directory,
    *,
    prefix: str = "",
    max_seconds: float = 1.2,
) -> tuple[list[str | None], list[tuple[int, int]]]:
    """按精确起音把**立体声**分轨切成首尾相接的块，返回 ``(采样名, 覆盖区间)``。

    与 :func:`extract_event_samples` 不同：不找邻近起音、不归一化、不淡入淡出、
    保持立体声。每个块与 :func:`write_bed` 生成的补全轨拼起来，逐样本等于原分轨。
    超过 ``max_seconds`` 的块只切前一段，剩下的由补全轨原样保留。
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names: list[str | None] = []
    intervals: list[tuple[int, int]] = []
    frames = int(data.shape[1])
    for index, (start, end) in enumerate(events, 1):
        begin = max(0, min(frames, int(round(start * sample_rate))))
        stop = min(frames, int(round(min(end, start + max_seconds) * sample_rate)))
        if stop - begin < 64:
            names.append(None)
            intervals.append((begin, begin))
            continue
        seg = np.ascontiguousarray(data[:, begin:stop], dtype=np.float32)
        name = f"{prefix}b{index:05d}"
        write_wav(directory / f"{name}.wav", seg, sample_rate)
        names.append(name)
        intervals.append((begin, stop))
    return names, intervals


def write_bed(data: np.ndarray, sample_rate: int,
              intervals: list[tuple[int, int]], path) -> None:
    """把事件块覆盖的区间置零，其余原样保留 —— 这条音频轨负责「补全」。

    块与 bed 相加 = 原分轨，所以尾音、持续层、混响、音符间隙都不会丢。
    """
    bed = np.array(data, dtype=np.float32, copy=True)
    merged: list[list[int]] = []
    for begin, stop in sorted(intervals):
        if stop <= begin:
            continue
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([begin, stop])
    for begin, stop in merged:
        bed[:, begin:min(stop, bed.shape[1])] = 0.0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_wav(path, bed, sample_rate)
