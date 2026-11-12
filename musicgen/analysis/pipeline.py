"""分析管线：音频 → Demucs 分轨 → 逐轨转谱/分类 → 重建 musicproj。

分轨与乐器的对应关系（6 轨模型 ``htdemucs_6s``）::

    vocals -> 人声（整条分轨写成一条 <audio> 音频轨，不转谱、不拆音高）
    bass   -> 贝斯（instrument="bass"）
    drums  -> 鼓组（内置合成鼓组，按 GM 鼓件写音高）
    piano  -> 钢琴
    guitar -> 吉他
    other  -> 音色模板识别，拆成小提琴/大提琴/小号/竖笛/钢琴/吉他若干轨

人声的气声、转音、真假声切换用「音符 + 弯音」都还原不了，所以无论哪种模式
都直接引用 Demucs 分出来的 ``stems/vocals.wav``，渲染时原样混音。

4 轨模型（``htdemucs`` / ``htdemucs_ft``）没有单独的钢琴/吉他分轨，
``other`` 会一并交给音色识别。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .. import __version__
from ..core.effects import EffectSpec, create_effect
from ..core.instruments import InstrumentRegistry
from ..samples import fetch
from . import audio as audio_io
from . import brief as brief_mod
from . import decisions as decisions_mod
from . import layers as layers_mod
from . import regions as regions_mod
from . import rhythm, sample_extract, spectrum, timbre
from .percussion import detect_hits
from .pitch import NoteEvent, detect_notes
from .rebuild import TrackDraft, write_project
from .separate import (
    DEFAULT_MODEL,
    MODEL_STEMS,
    SeparationError,
    StemSet,
    availability,
    cache_stems,
    separate,
    stem_names,
)
from .separate import STEM_LABELS

ProgressFn = Callable[[float, str], None]

EPS = 1e-9


class AnalysisError(RuntimeError):
    """分析无法继续（输入、依赖或输出目录有问题）。"""


@dataclass
class AnalysisOptions:
    source: Path
    output: Path
    name: str | None = None
    bpm: float | None = None
    quantize: str = "1/16"
    min_note_ms: int = 65
    model: str = DEFAULT_MODEL
    device: str = "auto"
    keep_stems: bool = True
    reuse_stems: bool = True
    audio_tracks: bool = False   # 只分离、不转谱：直接写音频轨，保留原始音色
    #: 精确回放（默认开）：按原始起音铺立体声片段，块外由补全轨原样填回，
    #: 关闭自动增益/EQ/母带压缩。关掉则回到「量化 + 逐事件采样 + 响度对齐」的旧行为。
    exact_replay: bool = True
    force: bool = False
    cache_root: Path | None = None
    # 废弃：人声固定是一条音频轨，不再选乐器。保留字段兼容旧脚本与 GUI 结果字典。
    vocal_instrument: str = "vocal"
    max_polyphony: int = 6
    layers: bool = True            # 「其他」分轨按音区再分层
    max_layers: int = 3
    write_brief: bool = True       # 写出 reverse_brief.json 供大模型判断
    auto_eq: bool = True           # 按「渲染 vs 原分轨」的频谱差自动补 EQ
    master_comp: bool = True       # 转谱结果峰值因数明显高于原曲时，在母带补压缩
    sample_instruments: bool = True  # 从原分轨切采样，让音色就是原曲的音色
    decisions: Path | None = None  # 大模型/人工的决策文件


@dataclass
class AnalysisResult:
    project_path: Path
    output_dir: Path
    bpm: float
    key: str
    duration: float
    tracks: list[TrackDraft]
    report: dict
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    regions: list = field(default_factory=list)
    brief_path: Path | None = None

    @property
    def note_count(self) -> int:
        return sum(len(track.notes) for track in self.tracks)


@dataclass(frozen=True)
class StemPlan:
    stem: str
    label: str
    instrument: str | None       # None = 交给音色识别
    fmin: float
    fmax: float
    polyphony: int
    kind: str                    # melodic | drums | classify


def _plans(model: str, max_polyphony: int) -> list[StemPlan]:
    """要转谱的分轨。人声不在其中：它整条作为音频轨（见 _build_audio_drafts）。"""
    melodic = min(6, max(1, int(max_polyphony)))
    common = {
        "drums": StemPlan("drums", "鼓组", "drums", 0.0, 0.0, 1, "drums"),
        "bass": StemPlan("bass", "贝斯", "bass", 28.0, 240.0, 1, "melodic"),
        "piano": StemPlan("piano", "钢琴", "piano", 36.0, 2600.0, melodic, "melodic"),
        "guitar": StemPlan("guitar", "吉他", "guitar", 60.0, 1500.0, min(4, melodic), "melodic"),
        # 「其他」通常只剩弦乐/管乐，复音开太高会把钢琴的谐波也检出来
        "other": StemPlan("other", "其他", None, 50.0, 2600.0, min(3, melodic), "classify"),
    }
    return [common[name] for name in stem_names(model) if name in common]


# ---------------------------------------------------------------------------
# 分轨缓存
# ---------------------------------------------------------------------------


def _cache_meta(path: Path) -> dict:
    stat = path.stat()
    return {"source": str(path.resolve()), "size": stat.st_size, "mtime": stat.st_mtime}


def _stem_files(directory: Path, names: tuple[str, ...]) -> bool:
    return all((directory / f"{name}.wav").is_file() for name in names)


def _load_stems(directory: Path, model: str, source: Path) -> StemSet | None:
    import json

    manifest = directory / "stems.json"
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stat = source.stat()
    if (
        payload.get("model") != model
        or payload.get("source") != str(source.resolve())
        or int(payload.get("size") or -1) != stat.st_size
        or abs(float(payload.get("mtime") or 0) - stat.st_mtime) > 1e-6
    ):
        return None
    names = stem_names(model)
    if not _stem_files(directory, names):
        return None
    stems: dict[str, np.ndarray] = {}
    sample_rate = int(payload.get("sample_rate") or 44100)
    for name in names:
        loaded = audio_io.load(directory / f"{name}.wav", sample_rate=None)
        stems[name] = loaded.samples
        sample_rate = loaded.sample_rate
    return StemSet(stems=stems, sample_rate=sample_rate, model=model)


def _save_stems(stem_set: StemSet, directory: Path, source: Path) -> None:
    import json

    cache_stems(stem_set, directory)
    payload = {
        **_cache_meta(source),
        "model": stem_set.model,
        "sample_rate": stem_set.sample_rate,
    }
    (directory / "stems.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 音符后处理
# ---------------------------------------------------------------------------


def _quantize(notes: list[NoteEvent], bpm: float, grid_beats: float,
              phase: float, duration: float, min_duration: float,
              drums: bool = False, exact: bool = False) -> list[NoteEvent]:
    grid = 60.0 / bpm * grid_beats if grid_beats > 0 else 0.0
    cleaned: list[NoteEvent] = []
    seen: set[tuple[int, int]] = set()
    for note in notes:
        if note.start >= duration - 0.02:
            continue
        # 精确回放要拿原始检测起音去切分轨，量化会把它挪到网格上（最多 ±58ms）。
        if exact:
            note.detail["src_time"] = float(note.start)
        if not exact and grid > 0:
            note.start = rhythm.quantize_time(note.start, grid, phase)
            if not drums:
                note.duration = rhythm.quantize_duration(
                    note.duration, grid, minimum=max(grid * 0.5, min_duration * 0.6)
                )
        note.duration = max(note.duration,
                            0.01 if exact else (min_duration if not drums else 0.05))
        note.start = max(0.0, min(note.start, duration - 0.02))
        if note.duration < min_duration and not drums and not exact:
            continue
        key = (int(note.midi), int(round(note.start * 1000)))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(note)
    cleaned.sort(key=lambda item: (item.start, item.midi))
    return cleaned


def _span_peak(mono: np.ndarray, sample_rate: int, notes: list[NoteEvent]) -> float:
    if not notes or mono.size == 0:
        return 0.0
    mask = np.zeros(mono.size, dtype=bool)
    for note in notes:
        begin = max(0, int(note.start * sample_rate))
        end = min(mono.size, int(note.end * sample_rate))
        if end > begin:
            mask[begin:end] = True
    if not np.any(mask):
        return 0.0
    return float(np.max(np.abs(mono[mask])))


def _span_rms(mono: np.ndarray, sample_rate: int, notes: list[NoteEvent]) -> float:
    if not notes or mono.size == 0:
        return 0.0
    mask = np.zeros(mono.size, dtype=bool)
    for note in notes:
        begin = max(0, int(note.start * sample_rate))
        end = min(mono.size, int(note.end * sample_rate))
        if end > begin:
            mask[begin:end] = True
    if not np.any(mask):
        return 0.0
    return audio_io.rms(mono[mask])


#: 鼓件家族：把 GM 打击乐编号归成可直接分轨的几组。
#: 底鼓/军鼓/踩镲一定要各占一条轨——混在一条轨里既改不了音量，
#: 也没法单独替换音色，这是「不可编辑」最直接的来源。
DRUM_FAMILIES: list[tuple[str, set[int]]] = [
    ("底鼓", {35, 36}),
    ("军鼓", {37, 38, 39, 40}),
    ("踩镲", {42, 44, 46, 82}),
    ("嗵鼓", {41, 43, 45, 47, 48, 50}),
    ("镲片", {49, 51, 52, 53, 55, 57, 59, 54, 56}),
]

#: 少于这么多下的鼓件家族并进「镲片」，避免出来只有三五个音的碎轨
DRUM_FAMILY_MIN = 12


def _drum_family(pitch: int) -> str:
    for name, pitches in DRUM_FAMILIES:
        if pitch in pitches:
            return name
    return "打击乐"


def _group_drums(notes: list[NoteEvent]) -> dict[str, list[NoteEvent]]:
    """鼓点按鼓件家族分桶；太小的家族并进镲片。"""
    buckets: dict[str, list[NoteEvent]] = {}
    for note in notes:
        buckets.setdefault(_drum_family(note.midi), []).append(note)
    if len(buckets) <= 1:
        return buckets
    small = [key for key, items in buckets.items() if len(items) < DRUM_FAMILY_MIN]
    for key in small:
        if key == "镲片":
            continue
        buckets.setdefault("镲片", []).extend(buckets.pop(key))
    return {key: items for key, items in buckets.items() if items}


def _trim_overlaps(notes: list[NoteEvent], minimum: float = 0.05) -> int:
    """把每个音的结束时间收到「下一个更晚的起音」上，返回被裁的音数。

    转谱出来的时值常常比实际发声长（谐波尾、混响），同一条轨里每个音都压着
    下一个音。渲染听不出差别（采样本身有释放），但在钢琴卷帘里就是一团重叠，
    点不中、拖不动——这是「不可编辑」最直接的来源。和弦（同一起音）保持完整：
    它们共享同一个裁剪目标。
    """
    ordered = sorted(notes, key=lambda item: (item.start, item.midi))
    starts = np.array([note.start for note in ordered]) if ordered else np.zeros(0)
    trimmed = 0
    for index, note in enumerate(ordered):
        later = np.flatnonzero(starts > note.start + 1e-3)
        if later.size == 0:
            continue
        limit = float(starts[later[0]])
        if note.end - limit > 1e-3:
            note.duration = max(minimum, limit - note.start)
            trimmed += 1
    return trimmed


def _group_by_instrument(
    notes: list[NoteEvent], fallback: str, minimum: int = 3
) -> dict[str, list[NoteEvent]]:
    buckets: dict[str, list[NoteEvent]] = {}
    for note in notes:
        key = note.instrument or fallback
        if note.detail.get("low_confidence"):
            key = fallback
        buckets.setdefault(key, []).append(note)

    small = [key for key, items in buckets.items() if key != fallback and len(items) < minimum]
    for key in small:
        buckets.setdefault(fallback, []).extend(buckets.pop(key))
    return {key: items for key, items in buckets.items() if items}


#: 分层后音轨名的后缀（按层数从低到高）
_LAYER_SUFFIX = {
    2: ("低音区", "高音区"),
    3: ("低音区", "中音区", "高音区"),
}

#: 处理结果随输入电平变化的效果，算完增益要复测一次才能对齐
_LEVEL_DEPENDENT_EFFECTS = frozenset({
    "compressor", "drive", "electro", "bitcrush", "bit8", "bit4", "bit2",
})


#: 少于这么多音的非鼓轨并回同分轨的主轨。转谱偶尔会把几个音判成别的乐器，
#: 留下一条 11 音的「竖笛」——编辑时只会碍事，听到的也不是它。
TINY_TRACK_NOTES = 24


def _unknown_timbre_fallback(notes: list[NoteEvent]) -> str:
    """音色判不出来时给一个合成音兜底，按音区与长短挑最像的那类。"""
    if not notes:
        return "synth_pad"
    pitches = [note.midi for note in notes]
    durations = [note.duration for note in notes]
    median_pitch = float(np.median(pitches))
    median_duration = float(np.median(durations))
    if median_pitch <= 47:
        return "synth_bass"
    if median_duration <= 0.30:
        return "synth_pluck"
    if median_pitch >= 72:
        return "synth_lead"
    return "synth_pad"


def _merge_tiny_drafts(drafts: list[TrackDraft], mono_of, sample_rate: int) -> list[TrackDraft]:
    """把同分轨里的小碎片轨并进最大的那条。"""
    by_stem: dict[str, list[TrackDraft]] = {}
    for draft in drafts:
        by_stem.setdefault(draft.stem, []).append(draft)
    kept: list[TrackDraft] = []
    for stem, group in by_stem.items():
        if stem in ("drums", "vocals") or len(group) == 1:
            kept.extend(group)
            continue
        host = max(group, key=lambda item: len(item.notes))
        for draft in group:
            if draft is host:
                kept.append(draft)
                continue
            if len(draft.notes) < TINY_TRACK_NOTES:
                host.notes.extend(draft.notes)
                continue
            kept.append(draft)
        mono = mono_of(stem)
        if mono.size:
            host.level = _span_rms(mono, sample_rate, host.notes)
            host.peak = _span_peak(mono, sample_rate, host.notes)
    return kept


def _split_draft_layers(drafts: list[TrackDraft], mono_of, sample_rate: int,
                        options: AnalysisOptions) -> list[TrackDraft]:
    """把「其他」分轨按音区拆成多条轨；其它分轨原样保留。

    只在音高间隙足够大时切（见 :mod:`musicgen.analysis.layers`），切不动就保持一条，
    所以不会平白多出一堆只有三两个音的碎片轨。
    """
    if not options.layers:
        return drafts
    layered: list[TrackDraft] = []
    for draft in drafts:
        if draft.stem != "other" or len(draft.notes) < 12 or draft.audio_path is not None:
            layered.append(draft)
            continue
        parts = layers_mod.split_layers(draft.notes, max_layers=max(1, options.max_layers))
        if len(parts) < 2:
            layered.append(draft)
            continue
        mono = mono_of(draft.stem)
        suffixes = _LAYER_SUFFIX.get(len(parts), tuple(f"层{i}" for i in range(1, len(parts) + 1)))
        for part, suffix in zip(parts, suffixes):
            clone = TrackDraft(
                name=f"{draft.name} {suffix}",
                instrument=draft.instrument,
                notes=list(part.notes),
                pan=draft.pan,
                stem=draft.stem,
                confidence=part.confidence,
                level=_span_rms(mono, sample_rate, part.notes),
                peak=_span_peak(mono, sample_rate, part.notes),
                layer=part.index,
                layer_count=len(parts),
            )
            layered.append(clone)
    return layered


def _is_bleed(note: NoteEvent, reference: list[NoteEvent],
              tolerance: float = 1.0, overlap_ratio: float = 0.5) -> bool:
    """判断一个音是否只是别的分轨漏过来的同一个音（时间重叠 + 音高相近）。"""
    for other in reference:
        if abs(note.midi - other.midi) > tolerance:
            continue
        overlap = min(note.end, other.end) - max(note.start, other.start)
        if overlap > 0 and overlap >= overlap_ratio * min(note.duration, other.duration):
            return True
    return False


def _render_mono(notes: list[NoteEvent], instrument: str, sample_rate: int,
                 registry, duration: float, patch_path=None,
                 event_root=None) -> np.ndarray:
    """把一条轨单独渲染成单声道（不套增益），用于响度对齐与频谱对比。"""
    total = int((duration + 2.0) * sample_rate)
    buffer = np.zeros(total, dtype=np.float32)
    player = (registry.patch_instrument(patch_path, instrument) if patch_path is not None
              else registry.get(instrument))
    bank = None
    if event_root is not None:
        from ..core.eventbank import EventSampleBank

        bank = EventSampleBank(event_root, sample_rate)
    for note in notes:
        name = note.detail.get("sample")
        piece = bank.render(name, note.duration) if (bank is not None and name) else None
        if piece is None:
            piece = player.render_note(note.midi, note.velocity, note.duration)
        if piece is None or piece.size == 0:
            continue
        start = max(0, int(note.start * sample_rate))
        end = min(total, start + piece.shape[1])
        if end > start:
            buffer[start:end] += piece[0, : end - start]
    return buffer


def _apply_effects_mono(signal: np.ndarray, effects: list, sample_rate: int) -> np.ndarray:
    """把效果链套到单声道测量信号上（效果只认 ``(2, N)``，这里复制再取回）。"""
    if not effects or signal.size == 0:
        return signal
    stereo = np.stack([signal, signal])
    for spec in effects:
        stereo = create_effect(spec.type, spec.params).process(stereo, sample_rate)
    return np.asarray(stereo[0], dtype=np.float32)


def _rendered_levels(notes: list[NoteEvent], instrument: str, sample_rate: int,
                     registry, duration: float, patch_path=None) -> tuple[float, float]:
    """渲染后取 ``(RMS, 峰值)``。"""
    buffer = _render_mono(notes, instrument, sample_rate, registry, duration, patch_path)
    if buffer.size == 0 or not np.any(buffer):
        return 0.0, 0.0
    return (
        float(np.sqrt(np.mean(np.square(buffer, dtype=np.float64)))),
        float(np.max(np.abs(buffer))),
    )


def _span_signal(mono: np.ndarray, sample_rate: int, notes: list[NoteEvent]) -> np.ndarray:
    """把音符覆盖的片段拼起来（跳过静音），用于「原分轨 vs 渲染」的频谱对比。"""
    pieces = []
    for note in notes:
        begin = max(0, int(note.start * sample_rate))
        end = min(mono.size, begin + int(max(0.12, note.duration) * sample_rate))
        if end - begin > 512:
            pieces.append(mono[begin:end])
    if not pieces:
        return mono
    return np.concatenate(pieces)


def _band_tilt(signal: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """相对中频的频谱倾斜（dB）：``(低频, 高频)``。"""
    if signal.size < 2048:
        return 0.0, 0.0
    mag = spectrum.magnitude(spectrum.stft(signal, n_fft=2048, hop=512))
    freqs = spectrum.frequencies(2048, sample_rate)

    def density(low: float, high: float) -> float:
        mask = (freqs >= low) & (freqs < high)
        if not np.any(mask):
            return -120.0
        energy = float(np.sum(np.square(mag[mask], dtype=np.float64)))
        bandwidth = float(max(1.0, freqs[mask][-1] - freqs[mask][0]))
        return 10.0 * np.log10(max(energy / bandwidth, EPS))

    mid = density(250.0, 2000.0)
    return density(20.0, 250.0) - mid, density(2000.0, 9000.0) - mid


def _eq_suggestion(original: np.ndarray, rendered: np.ndarray,
                   sample_rate: int) -> dict | None:
    """比较「原分轨」与「渲染结果」的频谱倾斜，给出补差 EQ。

    这是逆向里少数能客观算出来的效果：音色换成采样/合成器后，频谱一定有差，
    差值本身就是要补的 EQ。只有超过 2dB 才建议，避免给每条轨都挂一个摆设。
    """
    low_o, high_o = _band_tilt(original, sample_rate)
    low_r, high_r = _band_tilt(rendered, sample_rate)
    low = float(np.clip(low_o - low_r, -9.0, 9.0))
    high = float(np.clip(high_o - high_r, -9.0, 9.0))
    if abs(low) < 2.0 and abs(high) < 2.0:
        return None
    return {
        "low": round(low, 1), "high": round(high, 1),
        "reason": f"渲染比原分轨：低频差 {low:+.1f}dB、高频差 {high:+.1f}dB",
    }


def _layer_summary(drafts: list[TrackDraft]) -> list[dict]:
    groups: dict[str, dict] = {}
    for draft in drafts:
        if not getattr(draft, "layer_count", 0):
            continue
        entry = groups.setdefault(draft.stem, {"stem": draft.stem, "layers": []})
        entry["layers"].append({
            "layer": draft.layer,
            "name": draft.name,
            "notes": len(draft.notes),
            "pitch_low": min((note.midi for note in draft.notes), default=0),
            "pitch_high": max((note.midi for note in draft.notes), default=0),
        })
    return list(groups.values())


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    index = 2
    while f"{name} {index}" in used:
        index += 1
    final = f"{name} {index}"
    used.add(final)
    return final


#: 6 轨模型的分轨 -> 工程里显示的乐器键
AUDIO_INSTRUMENTS = {
    "vocals": "vocal",
    "drums": "drums",
    "bass": "bass",
    "piano": "piano",
    "guitar": "guitar",
    "other": "audio",
}


def _build_audio_drafts(
    stem_set: "StemSet", stems_dir: Path, duration: float, warnings: list[str],
    only: str | None = None,
) -> tuple[list[TrackDraft], dict[str, dict]]:
    """把分轨写成音频轨：不转谱、不改电平，混音即原曲。

    ``only`` 给定时只写这一条分轨（默认转谱模式用它单独保留人声）。
    """
    drafts: list[TrackDraft] = []
    summary: dict[str, dict] = {}
    for stem, data in stem_set.stems.items():
        if only is not None and stem != only:
            continue
        path = stems_dir / f"{stem}.wav"
        if not path.is_file():
            warnings.append(f"分轨 {stem} 没有写成 WAV，音频轨已跳过")
            continue
        mono = np.mean(data, axis=0, dtype=np.float32)
        _balance, pan = audio_io.stereo_balance(data)
        drafts.append(
            TrackDraft(
                name=STEM_LABELS.get(stem, stem),
                instrument=AUDIO_INSTRUMENTS.get(stem, "audio"),
                stem=stem,
                audio_path=path,
                audio_offset=0.0,
                confidence=1.0,
                level=audio_io.rms(mono),
                peak=float(np.max(np.abs(mono))) if mono.size else 0.0,
            )
        )
        summary[stem] = {
            "notes": 0,
            "rms_db": round(audio_io.db(audio_io.rms(mono)), 1),
            "pan": round(pan, 2),
            "tracks": {AUDIO_INSTRUMENTS.get(stem, "audio"): 0},
            "audio": f"stems/{stem}.wav",
            "seconds": round(duration, 2),
        }
    return drafts, summary


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def analyze(options: AnalysisOptions, progress: ProgressFn | None = None) -> AnalysisResult:
    started = time.time()
    source = Path(options.source).expanduser()
    output = Path(options.output).expanduser()
    warnings: list[str] = []

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, fraction)), message)

    if options.model not in MODEL_STEMS:
        raise AnalysisError(f"未知分离模型 {options.model!r}，可用：{'、'.join(MODEL_STEMS)}")
    if not source.is_file():
        raise AnalysisError(f"音频文件不存在：{source}")
    try:
        rhythm.parse_grid(options.quantize)
    except ValueError as exc:
        raise AnalysisError(str(exc)) from exc
    project_file = output / f"{output.name or 'analysis'}.musicproj"
    if project_file.exists() and not options.force:
        raise AnalysisError(f"目标已存在：{project_file}（加 --force 覆盖）")

    ok, reason = availability()
    if not ok:
        raise AnalysisError(
            "音频分析需要 Demucs（torch + demucs）。\n"
            "  安装：py -3 -m pip install -r requirements.txt\n"
            f"  底层错误：{reason}"
        )

    report(0.01, "读取音频…")
    try:
        source_audio = audio_io.load(source, sample_rate=audio_io.ANALYSIS_SR)
    except audio_io.AudioError as exc:
        raise AnalysisError(str(exc)) from exc
    duration = source_audio.duration
    if duration < 1.0:
        raise AnalysisError(f"音频太短（{duration:.2f}s），至少需要 1 秒")

    # ---- 分离 / 复用分轨 ------------------------------------------------
    stems_dir = output / "stems"
    stem_set: StemSet | None = None
    if options.reuse_stems and stems_dir.is_dir():
        cached = _load_stems(stems_dir, options.model, source)
        if cached is not None:
            report(0.05, "复用上次的分轨缓存…")
            stem_set = cached
    if stem_set is None:
        try:
            stem_set = separate(
                source_audio,
                model=options.model,
                device=options.device,
                progress=report,
                span=(0.03, 0.55),
            )
        except SeparationError as exc:
            raise AnalysisError(str(exc)) from exc
        if options.keep_stems or options.audio_tracks:
            report(0.56, "保存分轨…")
            try:
                _save_stems(stem_set, stems_dir, source)
            except OSError as exc:
                warnings.append(f"分轨写盘失败（不影响重建）：{exc}")
        elif "vocals" in stem_set.stems:
            # 人声是工程里的一条音频轨，--no-stems 时也必须留下 vocals.wav，
            # 否则工程会引用一个不存在的文件。其余分轨仍按参数不落盘。
            try:
                cache_stems(
                    StemSet(
                        stems={"vocals": stem_set.stems["vocals"]},
                        sample_rate=stem_set.sample_rate,
                        model=stem_set.model,
                    ),
                    stems_dir,
                )
            except OSError as exc:
                warnings.append(f"人声分轨写盘失败：{exc}")
    report(0.56, "分析速度与调性…")

    sr = int(stem_set.sample_rate)

    def mono_of(name: str) -> np.ndarray:
        data = stem_set.stems.get(name)
        if data is None:
            return np.zeros(0, dtype=np.float32)
        return np.mean(data, axis=0, dtype=np.float32)

    if stem_set.stems:
        pieces = list(stem_set.stems.values())
        frames = min(int(sr * duration), min(piece.shape[1] for piece in pieces))
        mix = np.zeros((2, frames), dtype=np.float32)
        for piece in pieces:
            mix += piece[:, :frames]
        mix /= len(pieces)
    else:
        mix = source_audio.mono()[: int(sr * duration)]

    # ---- 速度 / 相位 / 调性 --------------------------------------------
    drums_mono = mono_of("drums")
    rhythm_source = drums_mono if drums_mono.size and audio_io.rms(drums_mono) > 1e-4 else mix
    rhythm_mag = spectrum.magnitude(spectrum.stft(rhythm_source, n_fft=1024, hop=256))
    fps = sr / 256.0
    envelope = spectrum.onset_envelope(rhythm_mag, fps, smooth_seconds=0.35)
    grid_beats = rhythm.parse_grid(options.quantize)
    tempo = rhythm.beat_info(envelope, fps, bpm=options.bpm, grid_beats=max(grid_beats, 0.25))
    if options.bpm:
        warnings.append(f"速度按参数固定为 {options.bpm:g}BPM")
    elif tempo.confidence < 0.25:
        warnings.append(
            f"速度检测置信度偏低（{tempo.confidence:.2f}），建议用 --bpm 指定；"
            f"当前按 {tempo.bpm:.1f}BPM 量化"
        )

    harmonic, _percussive = spectrum.harmonic_percussive(
        spectrum.magnitude(spectrum.stft(mix, n_fft=2048, hop=512))
    )
    key_name, key_score = spectrum.estimate_key(
        spectrum.chroma(harmonic, spectrum.frequencies(2048, sr))
    )

    region_list: list[regions_mod.Region] = []
    decision_messages: list[str] = []
    mix_estimate: np.ndarray | None = None
    bed_paths: dict[str, Path] = {}       # 精确回放：每条分轨的补全轨
    if options.audio_tracks:
        drafts, analysis_summary = _build_audio_drafts(
            stem_set, stems_dir, duration, warnings
        )
    else:
        # ---- 逐分轨转谱 -----------------------------------------------------
        plans = _plans(options.model, options.max_polyphony)
        available_keys = set(fetch.instrument_keys())
        if "bass" not in available_keys:
            warnings.append("注册表里没有 bass，贝斯轨改用 cello")

        drafts: list[TrackDraft] = []
        analysis_summary: dict[str, dict] = {}
        bank: timbre.TimbreBank | None = None

        def instrument_for(plan: StemPlan) -> str:
            key = plan.instrument
            if key and key in available_keys:
                return key
            if plan.stem == "bass":
                return "cello" if "cello" in available_keys else "piano"
            return "piano"

        total = max(1, len(plans))
        for index, plan in enumerate(plans):
            report(0.58 + 0.24 * index / total, f"转谱：{plan.label}…")
            data = stem_set.stems.get(plan.stem)
            if data is None:
                continue
            mono = np.mean(data, axis=0, dtype=np.float32)
            if audio_io.rms(mono) < 1e-4:
                analysis_summary[plan.stem] = {"notes": 0, "rms_db": -60.0}
                continue

            if plan.kind == "drums":
                notes = detect_hits(mono, sr)
                instrument = "drums"
            else:
                # 低频需要更长的分析窗：2048 点在 C2 上频点间隔 21.5Hz，基频会落进
                # 相邻谐波的主瓣里，直接检不出来。贝斯用 8192，其余旋律轨 4096。
                if plan.stem == "bass":
                    n_fft, hop = 8192, 1024
                else:
                    n_fft, hop = 4096, 512
                notes = detect_notes(
                    mono,
                    sr,
                    fmin=plan.fmin,
                    fmax=plan.fmax,
                    max_polyphony=plan.polyphony,
                    min_duration=max(0.05, options.min_note_ms / 1000.0),
                    n_fft=n_fft,
                    hop=hop,
                )
                instrument = instrument_for(plan)

            notes = _quantize(
                notes,
                tempo.bpm,
                grid_beats,
                tempo.phase,
                duration,
                max(0.05, options.min_note_ms / 1000.0),
                drums=plan.kind == "drums",
                exact=options.exact_replay,
            )

            buckets: dict[str, list[NoteEvent]]
            if plan.kind == "classify" and notes:
                if bank is None:
                    report(0.83, "构建音色模板库…")
                    bank = timbre.TimbreBank(
                        cache_root=options.cache_root,
                        sample_rate=sr,
                        instruments=[
                            key for key in fetch.SOURCES
                            if key not in ("vocal", "bass") and fetch.available_notes(key, options.cache_root)
                        ],
                    )
                    built = bank.build()
                    if built == 0:
                        warnings.append("没有可用的采样音色，其他分轨按音高归到钢琴/大提琴/小提琴")
                fallback = timbre.classify_notes(notes, mono, sr, bank)
                # 「其他」里判不出音色的音，默认落到合成音而不是钢琴：
                # 这一层绝大多数是合成器，候选分数实测只有 0.10–0.14（基本是猜），
                # 判成钢琴既不像（整条琶音变钢琴练习曲），电平还会差十几 dB。
                fallback = _unknown_timbre_fallback(notes)
                buckets = _group_by_instrument(notes, fallback)
            else:
                for note in notes:
                    note.instrument = instrument
                buckets = ({}
                           if plan.kind == "drums" and not notes
                           else _group_drums(notes) if plan.kind == "drums"
                           else {instrument: notes} if notes
                           else {})

            level = _span_rms(mono, sr, notes)
            level_db = audio_io.db(level) if level > EPS else -60.0
            _loudness, pan = audio_io.stereo_balance(data)
            for instrument_key, items in buckets.items():
                if plan.kind == "drums":
                    draft_name = f"鼓-{instrument_key}"
                elif plan.kind == "classify":
                    draft_name = fetch.label(instrument_key)
                else:
                    draft_name = plan.label
                drafts.append(
                    TrackDraft(
                        name=draft_name,
                        # 鼓件只作轨名，乐器一律是 drums，否则会去找不存在的采样
                        instrument="drums" if plan.kind == "drums" else instrument_key,
                        notes=items,
                        pan=round(pan, 3),
                        stem=plan.stem,
                        confidence=float(np.mean([n.confidence for n in items])) if items else 0.0,
                        level=_span_rms(mono, sr, items),
                        peak=_span_peak(mono, sr, items),
                    )
                )
            analysis_summary[plan.stem] = {
                "notes": len(notes),
                "rms_db": round(level_db, 1),
                "pan": round(pan, 2),
                "tracks": {key: len(items) for key, items in buckets.items()},
            }

        # ---- 人声：整条分轨就是一条音频轨，不拆成各个音高 -------------------
        # 气声、转音、颤音、歌词用任何采样/弯音都还原不了；直接引用分轨 WAV，
        # 渲染时原样混音，工程里仍是一条可静音/调音量/挪声像的独立轨。
        vocal_drafts, vocal_summary = _build_audio_drafts(
            stem_set, stems_dir, duration, warnings, only="vocals"
        )
        if "vocals" in stem_set.stems and not vocal_drafts:
            raise AnalysisError("人声分轨 WAV 缺失，无法生成人声音频轨")
        drafts.extend(vocal_drafts)
        analysis_summary.update(vocal_summary)

        # ---- 去掉「其他」分轨里与直接分轨重复的串音 -------------------------
        # Demucs 的 other 常残留钢琴/吉他；不去掉的话同一段旋律会在两条轨上各出现一次。
        reference = [
            note
            for draft in drafts if draft.stem != "other"
            for note in draft.notes
        ]
        if reference:
            for draft in drafts:
                if draft.stem == "other":
                    draft.notes = [note for note in draft.notes if not _is_bleed(note, reference)]
            drafts = [draft for draft in drafts if draft.notes or draft.audio_path]

        # ---- 合并同一乐器、声像接近的轨 -------------------------------------
        # 「其他」分轨里常混着钢琴/吉他的残响，音色识别会额外分出一条同名轨；
        # 声像接近就并回主轨，避免出现「吉他」「吉他 2」这种碎片。
        # 音频轨没有音符，不参与合并，原样留到最后。
        merged: list[TrackDraft] = []
        audio_drafts = [draft for draft in drafts if draft.audio_path is not None]
        # 先处理直接分轨，再让「其他」往里并：音轨归属与声像都以主分轨为准
        for draft in sorted(
            (item for item in drafts if item.audio_path is None),
            key=lambda item: 1 if item.stem == "other" else 0,
        ):
            for target in merged:
                if (target.instrument == draft.instrument
                        and abs(target.pan - draft.pan) <= 0.35
                        and target.stem != "drums"):
                    target.notes.extend(draft.notes)
                    target.level = float(np.hypot(target.level, draft.level))
                    target.peak = max(target.peak, draft.peak)
                    target.confidence = max(target.confidence, draft.confidence)
                    break
            else:
                merged.append(draft)
        for draft in merged:
            seen: set[tuple[int, int]] = set()
            unique: list[NoteEvent] = []
            for note in sorted(draft.notes, key=lambda item: (item.start, item.midi)):
                key = (int(note.midi), int(round(note.start * 100)))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(note)
            draft.notes = unique
        drafts = merged + audio_drafts
        # 串音过滤只动「其他」分轨；合并过的轨保留了各分轨的能量叠加，不能按单一分轨重算
        for draft in drafts:
            if draft.stem != "other":
                continue
            mono = mono_of(draft.stem)
            draft.level = _span_rms(mono, sr, draft.notes)
            draft.peak = _span_peak(mono, sr, draft.notes)

        # ---- 按音区分层：把「其他」里叠着的 pad / 主旋律 / 高音点缀拆开 -----
        drafts = _split_draft_layers(drafts, mono_of, sr, options)

        drafts = _merge_tiny_drafts(drafts, mono_of, sr)

        # ---- 同轨去重叠：时值收到下一个起音，卷帘里才点得中 -----------------
        # 精确回放模式下时值由「分轨起音边界」决定，不能再裁到下一个音；
        # 电平也不做自动校准，所以这两段都跳过。
        if not options.exact_replay:
            trimmed_total = 0
            for draft in drafts:
                # 鼓件已经按件分轨，同一条轨里同样不该互相压着（踩镲实测重叠 1.04）
                trimmed_total += _trim_overlaps(
                    draft.notes, minimum=max(0.05, options.min_note_ms / 1000.0)
                )
            if trimmed_total:
                warnings.append(f"同轨重叠：裁掉 {trimmed_total} 个音的尾巴，便于逐音编辑")
            # 时值变短后，原先按长音符算的「分轨电平」不再代表这条轨的实际能量，
            # 增益会整体高估（鼓件实测差 10dB 以上），所以裁剪后统一重算一次。
            for draft in drafts:
                if draft.audio_path is not None:
                    continue
                mono = mono_of(draft.stem)
                if mono.size:
                    draft.level = _span_rms(mono, sr, draft.notes)
                    draft.peak = _span_peak(mono, sr, draft.notes)

        # 编号在分层之后一次定死：简报与决策文件都用它引用音轨，
        # 后面按增益排序不会让编号漂移（否则 id 1 会指到另一条轨）。
        for index, draft in enumerate(drafts, 1):
            draft.id = index

        # ---- 段落切分（写进报告，也供简报与决策使用）------------------------
        report(0.86, "切分段落…")
        mono_mix = mix.mean(axis=0) if mix.ndim == 2 else mix
        stereo_mix = np.zeros((2, mono_mix.size), dtype=np.float32)
        stereo_mix[0] = mono_mix
        stereo_mix[1] = mono_mix
        if stem_set.stems:
            for piece in stem_set.stems.values():
                length = min(stereo_mix.shape[1], piece.shape[1])
                stereo_mix[:, :length] += piece[:, :length]
            stereo_mix /= max(1, len(stem_set.stems))
        region_list = regions_mod.detect_regions(
            mono_mix, sr, stereo=stereo_mix,
            min_seconds=max(4.0, 8.0 * 60.0 / max(tempo.bpm, 1.0)),
        )
        regions_mod.attach_stem_levels(region_list, stem_set.stems, sr)

        # ---- 应用大模型 / 人工决策 -------------------------------------------
        if options.decisions is not None:
            report(0.88, "应用决策…")
            try:
                plan = decisions_mod.Decisions.load(options.decisions)
                drafts, decision_messages = decisions_mod.apply_decisions(
                    drafts, plan, output_dir=output, regions=region_list
                )
            except decisions_mod.DecisionError as exc:
                raise AnalysisError(f"决策文件有问题：{exc}") from exc
            warnings.extend(decision_messages)
            if not drafts:
                raise AnalysisError("决策把全部音轨都删掉了，工程为空")

        # ---- 增益与命名 -----------------------------------------------------
        if not drafts:
            raise AnalysisError(
                "没有生成任何音轨。可能原因：整段静音、全是噪声，"
                "或分离模型没有产出有效分轨。"
            )

        # ---- 原曲采样：让音色就是原曲的音色 --------------------------------
        # 内置采样库与合成预设只能「像」，电子音色尤其不像。既然分轨已经分好了，
        # 就直接从分轨里切音做采样，渲染时按音高变速重放——音符仍然可改。
        sample_root = output / "instruments"
        sampled = 0
        if options.sample_instruments:
            report(0.86, "从原曲提取音色采样…")
            for index, draft in enumerate(drafts, 1):
                mono = mono_of(draft.stem)
                if mono.size == 0 or not draft.notes:
                    continue
                key = f"src_{draft.stem}_{index}"
                written = sample_extract.extract_track_samples(
                    mono, sr, draft.notes, sample_root / key,
                    drums=draft.stem == "drums",
                )
                if written:
                    draft.instrument = key
                    sampled += 1
            if sampled:
                warnings.append(
                    f"音色采样：{sampled} 条音轨改用原曲采样（instruments/，"
                    f"工程可脱离内置音色库渲染）"
                )

        # ---- 时间轴补全：精确起音分块 + 补全轨 ---------------------------
        # 精确回放（默认）：按每条分轨的原始起音把时间轴切成首尾相接的立体声块，
        # 块挂在音符上原样播放，块外（间隙/尾音/持续层/混响）由 bed 音频轨原样填回。
        # 两者相加逐样本等于原分轨；用户改某个音符才只影响那一处。
        # 旧模式（--no-exact-replay）仍走「每音一个片段 + 独立前缀」的路径。
        event_root = output / "samples"
        events_written = 0
        if options.sample_instruments and options.exact_replay:
            by_stem: dict[str, list[TrackDraft]] = {}
            for draft in drafts:
                if (draft.audio_path is None and draft.notes
                        and draft.stem in stem_set.stems):
                    by_stem.setdefault(draft.stem, []).append(draft)
            max_block = 1.2
            for stem, group in by_stem.items():
                data = stem_set.stems[stem]
                starts = sorted({
                    round(float(note.detail.get("src_time", note.start)), 4)
                    for draft in group for note in draft.notes
                })
                starts = [t for t in starts if 0.0 <= t < duration]
                if not starts:
                    continue
                bounds = starts + [float(duration)]
                blocks = [
                    (starts[i], min(bounds[i + 1], starts[i] + max_block))
                    for i in range(len(starts))
                ]
                names, intervals = sample_extract.extract_exact_segments(
                    data, sr, blocks, event_root, prefix=f"{stem}_",
                )
                owner = {
                    starts[i]: (names[i], blocks[i][1] - blocks[i][0])
                    for i in range(len(starts)) if names[i]
                }
                for draft in group:
                    for note in draft.notes:
                        key = round(float(note.detail.get("src_time", note.start)), 4)
                        hit = owner.get(key)
                        if hit is None:
                            note.detail.pop("sample", None)
                            continue
                        name, length = hit
                        note.start = key
                        # 时值必须精确等于片段长度：渲染端靠「时值 == 片段长度」
                        # 判断这个音没被改过、应原样输出。给下限会让它误判成
                        # 被编辑过，从而给每个块加上不该有的淡入淡出。
                        note.duration = length
                        note.detail["sample"] = name
                        # 与 bed 的挖空区间用同一个起点，渲染端按秒定位
                        note.detail["src_time"] = key
                bed_path = output / "beds" / f"{stem}.wav"
                sample_extract.write_bed(data, sr, intervals, bed_path)
                bed_paths[stem] = bed_path
                events_written += len(owner)
            # 没检测到音符的分轨（例如很弱的钢琴轨）也不能整条丢掉：
            # 写一条覆盖区间为空的 bed，内容就是完整原分轨。
            for stem, data in stem_set.stems.items():
                if stem == "vocals" or stem in bed_paths:
                    continue
                bed_path = output / "beds" / f"{stem}.wav"
                sample_extract.write_bed(data, sr, [], bed_path)
                bed_paths[stem] = bed_path
            if bed_paths:
                warnings.append(
                    f"时间轴补全：{events_written} 个立体声块 + {len(bed_paths)} 条补全轨"
                    f"（beds/），块与补全轨相加 = 原分轨"
                )
        elif options.sample_instruments:
            # 所有轨共用 samples/ 目录，每条轨必须给独立前缀，否则 e00001 会被
            # 后写的轨覆盖，鼓/贝斯会播到吉他或合成层的片段。
            for track_index, draft in enumerate(drafts, 1):
                if draft.stem == "vocals" or not draft.notes:
                    continue
                mono = mono_of(draft.stem)
                if mono.size == 0:
                    continue
                mapping = sample_extract.extract_event_samples(
                    mono, sr, draft.notes, event_root,
                    prefix=f"t{track_index:02d}_",
                )
                for index, name in mapping.items():
                    draft.notes[index].detail["sample"] = name
                events_written += len(set(mapping.values()))
            if events_written:
                warnings.append(
                    f"逐事件采样：{events_written} 个片段（samples/），"
                    f"每个音原样播放，不依赖音高检测"
                )

        # 响度对齐：把每条轨单独渲染一遍再和原分轨比 RMS / 峰值。
        # 效果链在渲染时位于增益之后，所以测量必须带上效果——否则自动 EQ 削掉的
        # 电平没人补（鼓组低切 6dB 就会整轨轻 6dB）。
        registry = InstrumentRegistry(
            cache_root=(sample_root if sampled else options.cache_root),
            sample_rate=sr,
            base_dir=output,
        )
        mix_estimate = np.zeros(int((duration + 2.0) * sr), dtype=np.float32)
        for draft in drafts:
            if options.exact_replay:
                # 块与补全轨本来就是原分轨的电平与频谱，任何自动增益 / EQ
                # 都会把它推离原曲。保持 gain=0 / effects=空 / reverb=0。
                continue
            if draft.audio_path is not None:
                # 音频轨原样混音：不渲染音符、不自动改增益/EQ，
                # 直接把分轨加进母带估算，压缩与高频修剪才算得准。
                mono = mono_of(draft.stem)
                length = min(mix_estimate.size, mono.size)
                mix_estimate[:length] += mono[:length]
                continue
            rendered = _render_mono(
                draft.notes, draft.instrument, sr, registry, duration,
                draft.patch_path, event_root=event_root,
            )
            if draft.notes and draft.level > EPS:
                suggestion = _eq_suggestion(
                    _span_signal(mono_of(draft.stem), sr, draft.notes), rendered, sr
                )
                if suggestion is not None:
                    draft.eq_suggestion = suggestion
                    if (options.auto_eq and not draft.effects
                            and (abs(suggestion["low"]) >= 3.0
                                 or abs(suggestion["high"]) >= 3.0)):
                        # 自动补偿限制在 ±6dB：差值更大时多半是「音色选错了」，
                        # 该由决策换乐器，而不是靠 EQ 硬掰。
                        draft.effects = [EffectSpec("eq", {
                            "low": float(np.clip(suggestion["low"], -6.0, 6.0)),
                            "high": float(np.clip(suggestion["high"], -6.0, 6.0)),
                        })]

            measured, effects_ok = rendered, True
            if draft.effects:
                try:
                    measured = _apply_effects_mono(rendered, draft.effects, sr)
                except Exception as exc:  # noqa: BLE001 - 效果本身出错不该让分析失败
                    effects_ok = False
                    warnings.append(
                        f"音轨「{draft.name}」的效果链测量失败，按未处理信号对齐电平："
                        f"{type(exc).__name__}: {exc}"
                    )
            rendered_rms = (float(np.sqrt(np.mean(np.square(measured, dtype=np.float64))))
                            if measured.size else 0.0)
            rendered_peak = float(np.max(np.abs(measured))) if measured.size else 0.0

            def _match_gain(rms: float, peak: float) -> float:
                """RMS 对齐与峰值对齐的几何平均（两者各让一步，避免一头过响）。"""
                if min(draft.level, draft.peak, rms, peak) <= EPS:
                    return 0.0
                # 两个幅度比的乘积取对数，正好是两组 dB 差的平均
                return 10.0 * math.log10(
                    (draft.level * draft.peak) / (rms * peak)
                )

            gain = _match_gain(rendered_rms, rendered_peak)
            # 只有电平相关的效果（压缩、失真、位压）才需要按增益复测；均衡/延迟是线性的，
            # 增益不改变它们的形状，复测只会白白多算一遍全长 FFT。
            needs_retest = any(spec.type in _LEVEL_DEPENDENT_EFFECTS
                               for spec in draft.effects)
            if needs_retest and effects_ok and abs(gain) > EPS:
                # 压缩/失真等非线性效果随输入电平变化，按算出的增益复测一次再微调
                try:
                    retest = _apply_effects_mono(
                        rendered * float(10.0 ** (gain / 20.0)), draft.effects, sr
                    )
                    if retest.size:
                        gain += _match_gain(
                            float(np.sqrt(np.mean(np.square(retest, dtype=np.float64)))),
                            float(np.max(np.abs(retest))),
                        )
                except Exception:  # noqa: BLE001 - 复测失败就用第一遍的结果
                    pass
            if not getattr(draft, "gain_locked", False):
                # 采样鼓组比真实鼓组峰值高得多，+9dB 的上限会把整条鼓轨压得过轻
                clamped = float(np.clip(gain, -30.0, 24.0))
                if abs(clamped - gain) > 0.5:
                    warnings.append(
                        f"音轨「{draft.name}」按分轨电平需要 {gain:+.1f}dB，"
                        f"已夹到 {clamped:+.1f}dB"
                    )
                draft.gain_db = round(clamped, 1)
            draft.reverb = 0.0

            # 用同一次渲染累加出整首混音的估算值，供母带动态对齐使用：
            # 这里把声像的下混系数也算进去，估算峰值才和真实渲染对得上。
            if measured.size and mix_estimate is not None and mix_estimate.size == measured.size:
                theta = (max(-1.0, min(1.0, draft.pan)) + 1.0) * math.pi / 4.0
                downmix = (math.cos(theta) + math.sin(theta)) / 2.0
                mix_estimate += measured * float(10.0 ** (draft.gain_db / 20.0)) * downmix

    # ---- 鼓件增益归一到同一个标尺 --------------------------------------------
    # 内置鼓组的镲片比真鼓轻 30dB，按分轨电平硬匹配会把踩镲抬到 +24dB，
    # 整首变刺耳（实测 4–12kHz 占比 19%→46%）。以音符最多的那件（通常是底鼓）
    # 为锚，其余鼓件只在 +6/-9dB 内浮动，保留相对关系又不让单件顶翻混音。
    drum_drafts = [draft for draft in drafts if draft.stem == "drums"]
    if len(drum_drafts) > 1:
        # 底鼓是鼓组的电平基准：它最稳、也最不容易被采样库的响度毛病带偏。
        # 用「音符最多」选锚会选到踩镲（970 下），结果把整套鼓都抬上去。
        kicked = [item for item in drum_drafts if "底鼓" in item.name]
        anchor = (kicked[0] if kicked
                  else sorted(drum_drafts, key=lambda item: item.gain_db)[len(drum_drafts) // 2])
        for draft in drum_drafts:
            if draft is anchor:
                continue
            low, high = anchor.gain_db - 9.0, anchor.gain_db + 6.0
            if draft.gain_db > high or draft.gain_db < low:
                limited = float(np.clip(draft.gain_db, low, high))
                warnings.append(
                    f"音轨「{draft.name}」按分轨电平需要 {draft.gain_db:+.1f}dB，"
                    f"已按鼓组基准（{anchor.name} {anchor.gain_db:+.1f}dB）收到 {limited:+.1f}dB"
                )
                draft.gain_db = round(limited, 1)

    # ---- 母带动态对齐：原曲通常是压过的成品，转谱出来的瞬态更尖 --------------
    master_effects: list[EffectSpec] = []
    if mix_estimate is not None and np.any(mix_estimate):
        source_mono = np.mean(mix, axis=0) if getattr(mix, "ndim", 1) == 2 else mix
        # 采样鼓组与合成音色的高频通常比真乐器多得多（实测 4–12kHz 占比 19%→46%），
        # 听感就是「刺」。按实测占比差补一档高频搁架，比逐轨调 EQ 稳。
        shares_est = _band_shares(mix_estimate, sr)
        shares_src = _band_shares(source_mono, sr)
        excess = shares_est["high"] - shares_src["high"]
        if excess > 4.0:
            trim = round(float(np.clip(-(excess - 2.0) / 2.5, -6.0, -1.0)), 1)
            master_effects.append(EffectSpec("eq", {"high": trim}))
            warnings.append(
                f"母带高频修剪 {trim:+.1f}dB：4–12kHz 占比 {shares_est['high']:.1f}%，"
                f"原曲 {shares_src['high']:.1f}%"
            )

    if options.master_comp and mix_estimate is not None and np.any(mix_estimate):
        estimate_peak = float(np.max(np.abs(mix_estimate)))
        estimate_rms = float(np.sqrt(np.mean(np.square(mix_estimate, dtype=np.float64))))
        source_mono = np.mean(mix, axis=0) if getattr(mix, "ndim", 1) == 2 else mix
        source_peak = float(np.max(np.abs(source_mono))) if source_mono.size else 0.0
        source_rms = float(np.sqrt(np.mean(np.square(source_mono, dtype=np.float64))))
        if min(estimate_peak, estimate_rms, source_peak, source_rms) > EPS:
            crest_est = audio_io.db(estimate_peak / estimate_rms)
            crest_src = audio_io.db(source_peak / source_rms)
            if crest_est - crest_src > 4.0:
                # 在「RMS 电平」处压下峰值：阈值之上的峰值被除以 ratio，
                # 峰值因数大致变成 原峰值因数 / ratio。×1.25 是实测校准：
                # 释放平滑会把阈值以下的信号也稍微带下去，比例要略大才落到原曲响度。
                ratio = float(np.clip(crest_est / max(crest_src, 1.0) * 1.25, 1.2, 4.0))
                threshold = round(audio_io.db(estimate_peak) - crest_est, 1)
                master_effects.append(EffectSpec("compressor", {
                    "threshold": float(np.clip(threshold, -48.0, -4.0)),
                    "ratio": round(ratio, 2),
                    "release": 120,
                }))
                warnings.append(
                    f"母带补压缩：转谱峰值因数 {crest_est:.1f}dB，原曲 {crest_src:.1f}dB，"
                    f"已在 RMS 电平处按 {ratio:.1f}:1 压缩（--no-master-comp 可关闭）"
                )

    # 补全轨：每条分轨一条，与它上面的事件块相加正好是原分轨。
    # 放在最后追加，避免参与分层/合并/增益等「按音符」的处理。
    for stem, path in bed_paths.items():
        drafts.append(TrackDraft(
            name=f"{STEM_LABELS.get(stem, stem)} 补全",
            instrument="audio",
            stem=f"bed_{stem}",
            audio_path=path,
            confidence=1.0,
        ))

    used_names: set[str] = set()
    for draft in drafts:
        draft.name = _unique_name(draft.name, used_names)
    drafts.sort(key=lambda item: (-item.gain_db, item.name))

    # ---- 写工程 ---------------------------------------------------------
    report(0.90, "写出工程文件…")
    project_name = options.name or source.stem
    low_confidence = sum(
        1 for draft in drafts for note in draft.notes if note.detail.get("low_confidence")
    )
    total_notes = sum(len(draft.notes) for draft in drafts)
    if total_notes and low_confidence / total_notes > 0.25:
        warnings.append(
            f"{low_confidence}/{total_notes} 个音符的音色匹配置信度偏低，"
            "乐器归属可能需要人工调整（见 analysis.json 的 ranking）"
        )
    if "vocals" in stem_set.stems and not any(d.stem == "vocals" for d in drafts):
        warnings.append("人声分轨没能写成音频轨，工程里没有人声")

    # ---- 逆向简报（给大模型判断用）---------------------------------------
    brief_path: Path | None = None
    if options.write_brief and not options.audio_tracks:
        report(0.92, "生成逆向简报…")
        try:
            brief_payload = brief_mod.build_brief(
                drafts=drafts,
                regions=region_list,
                stems=stem_set.stems,
                sample_rate=sr,
                bpm=tempo.bpm,
                key=key_name,
                duration=duration,
                source=str(source.resolve()),
                cache_root=options.cache_root,
            )
            brief_path = output / "reverse_brief.json"
            brief_path.write_text(
                json.dumps(brief_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"逆向简报写盘失败（不影响工程）：{exc}")

    # 串音过滤与合并会改变每轨音符数，报告以最终结果为准
    for stem, summary in analysis_summary.items():
        items = [draft for draft in drafts if draft.stem == stem]
        summary["notes"] = sum(len(draft.notes) for draft in items)
        if items and all(draft.audio_path is not None for draft in items):
            # 纯音频轨：保留 _build_audio_drafts 写好的 audio 路径，不覆盖成空音符表
            continue
        summary["tracks"] = {draft.instrument: len(draft.notes) for draft in items}

    payload = {
        "version": __version__,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(source.resolve()),
        "source_duration": round(duration, 3),
        "model": options.model,
        "mode": "audio" if options.audio_tracks else "notes",
        "exact_replay": bool(options.exact_replay and not options.audio_tracks),
        "separated": True,
        "bpm": round(tempo.bpm, 2),
        "bpm_confidence": round(tempo.confidence, 3),
        "bpm_override": options.bpm,
        "phase": round(tempo.phase, 4),
        "key": key_name,
        "key_score": round(key_score, 3),
        "quantize": options.quantize,
        "grid_seconds": round(tempo.grid_seconds, 4),
        "tracks": [
            {
                "name": draft.name,
                "instrument": draft.instrument,
                "stem": draft.stem,
                "notes": len(draft.notes),
                "gain_db": draft.gain_db,
                "pan": draft.pan,
                "confidence": round(draft.confidence, 3),
                "audio": (f"{draft.audio_path.parent.name}/{draft.audio_path.name}"
                          if draft.audio_path else None),
                "editability": _editability(
                    draft.notes, tempo.bpm, duration, tempo.grid_seconds
                ),
            }
            for draft in drafts
        ],
        "stems": analysis_summary,
        "regions": [region.to_dict() for region in region_list],
        "layers": _layer_summary(drafts),
        "master_effects": [
            {"type": spec.type, "params": spec.params} for spec in master_effects
        ],
        "decisions": str(options.decisions) if options.decisions else None,
        "brief": "reverse_brief.json" if brief_path else None,
        "warnings": warnings,
    }

    written = write_project(
        output,
        name=project_name,
        bpm=tempo.bpm,
        sample_rate=sr,
        duration=duration,
        tracks=drafts,
        master_effects=master_effects,
        force=options.force,
        report=payload,
    )
    report(1.0, "完成")
    return AnalysisResult(
        project_path=written.project_path,
        output_dir=output,
        bpm=tempo.bpm,
        key=key_name,
        duration=duration,
        tracks=drafts,
        report=payload,
        warnings=warnings,
        elapsed=time.time() - started,
        regions=region_list,
        brief_path=brief_path,
    )


def _band_shares(mono: np.ndarray, sample_rate: int) -> dict[str, float]:
    """四个频段的能量占比（%）：低 <250、中低 250–1k、中 1k–4k、高 4k–12k。"""
    signal = np.asarray(mono, dtype=np.float32).reshape(-1)
    empty = {"low": 0.0, "midlow": 0.0, "mid": 0.0, "high": 0.0}
    if signal.size < 4096:
        return empty
    step = 2 if sample_rate > 24000 else 1
    signal = signal[::step]
    magnitude = spectrum.magnitude(spectrum.stft(signal, n_fft=4096, hop=2048))
    freqs = spectrum.frequencies(4096, sample_rate / step)
    total = float(magnitude.sum())
    if total <= EPS:
        return empty
    edges = {"low": (20.0, 250.0), "midlow": (250.0, 1000.0),
             "mid": (1000.0, 4000.0), "high": (4000.0, 12000.0)}
    return {
        name: 100.0 * float(magnitude[(freqs >= low) & (freqs < high)].sum()) / total
        for name, (low, high) in edges.items()
    }


def _editability(notes: list[NoteEvent], bpm: float, span: float,
                 grid_seconds: float = 0.0) -> dict:
    """一条轨的「可编辑性」指标——卷帘里点得中、拖得动吗。

    真正让工程没法二次编辑的不是音色像不像，而是这几件事：
    音符互相重叠（点不中）、短到看不见（拖不动）、复音高到看不清（改一个碰三个）。
    """
    if not notes or span <= 0:
        return {"notes": 0}
    beat = 60.0 / max(bpm, 1e-6)
    events: list[tuple[float, int]] = []
    for note in notes:
        events.append((note.start, 1))
        events.append((note.end, -1))
    events.sort()
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    total = sum(note.duration for note in notes)
    durations = np.array([note.duration for note in notes], dtype=np.float64)
    shortest = grid_seconds * 0.6 if grid_seconds > 0 else beat * 0.25 * 0.6
    grid = max(grid_seconds, 1e-6)
    # 量化时带相位（analysis/rhythm.py），直接对 0 取模会全部判成「不在网格上」。
    # 先取中位偏移当作相位，再看残差。
    phase = float(np.median([note.start % grid for note in notes]))
    offsets = np.array([
        min(abs((note.start - phase) % grid), grid - abs((note.start - phase) % grid))
        for note in notes
    ], dtype=np.float64)
    return {
        "notes": len(notes),
        "per_second": round(len(notes) / span, 2),
        "polyphony": int(peak),
        "mean_beats": round(float(np.median(durations)) / beat, 2),
        "overlap": round(float(total) / span, 2),
        "too_short": round(float(np.mean(durations < shortest)), 3),
        "on_grid": round(float(np.mean(offsets <= grid * 0.25)), 3),
    }


def describe_result(result: AnalysisResult) -> str:
    """CLI 用的多行摘要。"""
    audio_count = sum(1 for draft in result.tracks if draft.audio_path is not None)
    detail = (f"{audio_count} 条音频轨（原样混音）" if audio_count == len(result.tracks)
              else f"{result.note_count} 个音符"
              + (f" + {audio_count} 条音频轨" if audio_count else ""))
    lines = [
        f"重建工程：{result.project_path}",
        f"  {len(result.tracks)} 条音轨 | {detail} | "
        f"{result.bpm:.1f}BPM | {result.key or '调性未知'} | "
        f"时长 {result.duration:.1f}s | 耗时 {result.elapsed:.1f}s",
    ]
    for draft in result.tracks:
        if draft.audio_path is not None:
            lines.append(
                f"  · {draft.name:<6} [音频] {draft.stem:<7} "
                f"pan {draft.pan:+.2f}  文件 {draft.audio_path.name}"
            )
            continue
        lines.append(
            f"  · {draft.name:<6} {draft.instrument:<8} 音符 {len(draft.notes):<5} "
            f"{draft.gain_db:+.1f}dB  pan {draft.pan:+.2f}  置信 {draft.confidence:.2f}"
        )
    scored = [
        (draft, _editability(
            draft.notes, result.bpm, result.duration,
            float(result.report.get("grid_seconds") or 0.0),
        ))
        for draft in result.tracks if draft.audio_path is None and draft.notes
    ]
    if scored:
        lines.append("  · 可编辑性（重叠>1 说明音符互相压着；过短>20% 说明碎片太多）")
        for draft, info in scored:
            lines.append(
                f"    {draft.name:<8} 音符 {info['notes']:<5} 复音 {info['polyphony']:<2} "
                f"中位时值 {info['mean_beats']:.2f}拍 重叠 {info['overlap']:.2f} "
                f"过短 {info['too_short']:.0%} 网格 {info['on_grid']:.0%}"
            )
    if result.regions:
        labels = "".join(region.label or "?" for region in result.regions)
        detail = f"{len(result.regions)} 段（{labels}）"
        layered = sum(1 for draft in result.tracks if getattr(draft, "layer_count", 0))
        if layered:
            detail += f" · {layered} 条分层轨"
        lines.append(f"  · 段落 {detail}")
    if result.brief_path is not None:
        lines.append(f"  · 逆向简报 {result.brief_path.name}（可交给大模型判断后重新应用）")
    for warning in result.warnings:
        lines.append(f"  ! {warning}")
    return "\n".join(lines)
