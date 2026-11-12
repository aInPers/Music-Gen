"""逆向简报：把分析结果整理成一份**给大模型看的**结构化 JSON。

自动分析只能回答「这里大概是什么音、几几拍」，很多判断是上下文相关的：

* 这层是 pad 还是被 Demucs 漏过来的钢琴残响？
* 原曲的宽度是编曲（双轨吉他）还是效果（合唱/立体）造成的？
* 高音区的碎音值得单独成轨，还是并回主旋律？
* 这段该用采样音色，还是干脆用合成音脚本更接近？

这些没有唯一解。简报把「可量化的事实」摊开（音域、时值、频谱、候选音色概率、
效果估计），再由大模型按 :mod:`musicgen.core.decisions` 的决策格式给结论。
"""

from __future__ import annotations

import time

import numpy as np

from .. import __version__
from ..core.patch import builtin_patches
from ..samples import fetch
from . import effects_estimate, timbre
from .pitch import NoteEvent

BRIEF_VERSION = 1

#: 简报里每个候选列表最多列几个
MAX_CANDIDATES = 4


def _segments_for_notes(mono: np.ndarray, sample_rate: int,
                        notes: list[NoteEvent], limit: int = 8) -> list[np.ndarray]:
    segments = []
    for note in sorted(notes, key=lambda item: -item.velocity)[:limit]:
        begin = max(0, int(note.start * sample_rate))
        span = max(0.25, min(0.6, note.duration + 0.08))
        end = begin + int(span * sample_rate)
        if begin >= mono.size:
            continue
        piece = mono[begin:end]
        if piece.size >= 2048:
            segments.append(piece)
    return segments


def _features_for(mono: np.ndarray, sample_rate: int,
                  notes: list[NoteEvent]) -> tuple[np.ndarray, dict[str, float]]:
    """返回 ``(55 维向量均值, 可解释指标中位数)``。"""
    segments = _segments_for_notes(mono, sample_rate, notes)
    if not segments:
        return np.zeros(55, dtype=np.float64), {}
    vectors = [timbre.segment_features(piece, sample_rate) for piece in segments]
    matrix = np.stack(vectors)
    descriptions = [timbre.describe_segment(piece, sample_rate) for piece in segments[:4]]
    readable = {
        key: float(np.median([item[key] for item in descriptions]))
        for key in descriptions[0]
    }
    for key in ("centroid_hz", "spread_hz", "rolloff_hz", "zcr"):
        readable[key] = round(readable[key], 1)
    for key in ("flatness", "harmonicity"):
        readable[key] = round(readable[key], 3)
    for key in ("attack_s", "decay_s", "peak"):
        readable[key] = round(readable[key], 3)
    return matrix.mean(axis=0), readable


def _candidates(bank: timbre.TimbreBank, features: np.ndarray, midi: int) -> list[dict]:
    if not bank.ready:
        return []
    result = bank.classify(features, midi)
    entries = []
    for instrument, probability in result.ranking[:MAX_CANDIDATES]:
        entries.append({
            "instrument": instrument,
            "label": fetch.label(instrument),
            "kind": "synth" if fetch.is_synth(instrument) else "sample",
            "score": round(float(probability), 3),
        })
    return entries


def build_brief(
    *,
    drafts: list,
    regions: list,
    stems: dict[str, np.ndarray],
    sample_rate: int,
    bpm: float,
    key: str,
    duration: float,
    source: str,
    cache_root=None,
    bank: timbre.TimbreBank | None = None,
) -> dict:
    """生成 ``reverse_brief.json`` 的内容。"""
    if bank is None:
        bank = timbre.TimbreBank(
            cache_root=cache_root, sample_rate=sample_rate,
            instruments=[
                key for key in list(fetch.SOURCES) + list(fetch.SYNTHS)
                if fetch.is_synth(key) or fetch.available_notes(key, cache_root)
            ],
        )
        bank.build()

    stem_hints: dict[str, dict] = {}
    for name, data in stems.items():
        stem_hints[name] = effects_estimate.analyse_stem(
            data, sample_rate, bpm=bpm
        ).to_dict()

    tracks = []
    for order, draft in enumerate(drafts, 1):
        data = stems.get(draft.stem)
        mono = np.mean(data, axis=0, dtype=np.float32) if data is not None else np.zeros(0, np.float32)
        features, readable = _features_for(mono, sample_rate, draft.notes)
        median_midi = int(round(float(np.median([note.midi for note in draft.notes])))) \
            if draft.notes else 60
        entry = {
            "id": getattr(draft, "id", 0) or order,
            "name": draft.name,
            "stem": draft.stem,
            "layer": getattr(draft, "layer", 0) or 1,
            "layer_count": getattr(draft, "layer_count", 0) or 1,
            "instrument": draft.instrument,
            "notes": len(draft.notes),
            "pitch_low": min((note.midi for note in draft.notes), default=0),
            "pitch_high": max((note.midi for note in draft.notes), default=0),
            "pitch_median": median_midi,
            "sustain_ratio": _sustain_ratio(draft.notes),
            "gain_db": draft.gain_db,
            "pan": round(float(draft.pan), 2),
            "confidence": round(float(draft.confidence), 2),
            "features": readable,
            "candidates": ([] if draft.instrument == "drums" or draft.audio_path is not None
                           else _candidates(bank, features, median_midi)),
            "effects": stem_hints.get(draft.stem, {}),
            "eq_suggestion": getattr(draft, "eq_suggestion", None),
            "current_effects": [spec.to_attrs() for spec in getattr(draft, "effects", [])],
        }
        if draft.instrument == "drums":
            entry["note"] = "鼓组是打击乐，不需要音色匹配；判断重点是鼓件归属与漏检"
        if draft.audio_path is not None:
            entry["audio"] = f"{draft.audio_path.parent.name}/{draft.audio_path.name}"
            entry["note"] = "音频轨：原样引用分轨 WAV，不转谱、不做音色匹配；可静音/调音量/挪声像"
        tracks.append(entry)

    presets = [
        {
            "patch": name,
            "label": patch.label or name,
            "hint": _preset_hint(name, patch),
        }
        for name, patch in builtin_patches().items()
    ]
    instruments = [_instrument_entry(key, cache_root) for key in fetch.instrument_keys()]

    return {
        "version": __version__,
        "brief_version": BRIEF_VERSION,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "global": {
            "bpm": round(float(bpm), 2),
            "key": key,
            "duration_s": round(float(duration), 2),
            "sample_rate": int(sample_rate),
            "regions": [region.to_dict() for region in regions],
            "instruments": instruments,
            "synth_presets": presets,
        },
        "stems": stem_hints,
        "tracks": tracks,
        "instructions": {
            "step_1": "逐条读 tracks[]：features 是音色事实，candidates 是最近邻给出的"
                      "候选（score 为概率），effects.suggested 是效果估计与理由。",
            "step_2": "按 Skill 里 references/逆向判断.md 的判据决定每条轨：保留采样音色 / 换乐器 / "
                      "换合成音色（instrument=synth + patch）/ 删掉误检 / 拆得更细。",
            "step_3": "把结论写成 decisions.json（结构见 decisions_schema），再运行 "
                      "`musicgen analyze <音频> -o <目录> --decisions decisions.json --force`。",
            "note": "regions 只有边界与统计，分区名称（前奏/主歌/副歌）由你判断后在 "
                    "decisions.regions[].label 里给出。",
            "decisions_schema": {
                "version": 1,
                "regions": [{"index": 1, "label": "intro"}],
                "synth_patches": {"my_pad": {"name": "my_pad", "oscillators": ["saw"]}},
                "tracks": [{
                    "track": "轨名或 id",
                    "action": "keep | retimbre | delete",
                    "instrument": "synth",
                    "patch": "warm_pad 或 my_pad 或 synths/x.synth.json",
                    "name": "新轨名",
                    "gain_db": -4.0,
                    "pan": 0.2,
                    "effects": [{"type": "eq", "low": -2}],
                    "reason": "为什么这样判断",
                }],
            },
        },
    }


def _instrument_entry(key: str, cache_root) -> dict:
    info = fetch.source_for(key)
    return {
        "key": key,
        "label": fetch.label(key),
        "kind": "synth" if fetch.is_synth(key) else "sample",
        "midi_low": int(info.midi_low) if info else 0,
        "midi_high": int(info.midi_high) if info else 0,
        "ready": bool(fetch.is_synth(key) or fetch.available_notes(key, cache_root)),
    }


def _sustain_ratio(notes: list[NoteEvent]) -> float:
    if not notes:
        return 0.0
    return round(sum(1 for note in notes if note.duration >= 0.4) / len(notes), 2)


def _preset_hint(name: str, patch) -> str:
    """一句话说明这个音色适合什么声部，方便大模型挑。"""
    hints = {
        "warm_pad": "慢起音、宽声场，适合铺底和声；也可顶替被误检的弦乐群",
        "glass_bell": "FM 金属音，适合高音点缀、前奏单音动机",
        "analog_lead": "带颤音的锯齿主音，适合副歌旋律线",
        "deep_bass": "正弦 sub + 锯齿，适合 808 / 电子贝斯",
        "pluck_string": "短衰减拨弦，适合琶音、分解和弦",
        "soft_keys": "柔键盘，适合被采样钢琴误判的电子琴层",
        "air_texture": "噪声铺底，适合 riser、氛围层、无人声段落",
    }
    base = hints.get(name, "")
    waves = "、".join(sorted({osc.wave for osc in patch.oscillators}))
    return f"{base}（波形 {waves}，增益 {patch.gain:g}）" if base else f"波形 {waves}"
