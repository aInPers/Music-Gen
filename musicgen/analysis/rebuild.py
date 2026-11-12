"""把分析结果写成 ``*.musicproj`` 工程。

输出结构与 ``musicgen init`` 生成的工程一致：一个工程文件 + ``tracks/`` 下
每条音轨一个 XML，随时可以用 CLI / 界面打开、改音符、渲染。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from ..core.project import Note, Track, save_track
from .pitch import NoteEvent


@dataclass
class TrackDraft:
    """一条待写出的音轨（音符时间单位仍是秒）。"""

    name: str
    instrument: str
    notes: list[NoteEvent] = field(default_factory=list)
    gain_db: float = 0.0
    pan: float = 0.0
    reverb: float = 0.0
    stem: str = ""
    confidence: float = 0.0
    level: float = 0.0          # 该轨在分轨里的 RMS
    peak: float = 0.0           # 该轨在分轨里的峰值（响度对齐优先按峰值）
    # 音频轨：直接引用分轨文件，保留原始音色
    audio_path: Path | None = None
    audio_offset: float = 0.0
    audio_gain_db: float = 0.0
    # 合成音脚本（instrument="synth" 时必填）与效果链
    patch_path: Path | None = None
    effects: list = field(default_factory=list)
    # 分层信息：同一条分轨拆出的第几层 / 共几层
    layer: int = 0
    layer_count: int = 0
    #: 稳定编号：分层完成后分配，供逆向简报与决策文件引用（不受排序影响）
    id: int = 0


@dataclass
class WrittenProject:
    project_path: Path
    track_paths: list[Path]
    track_count: int
    note_count: int


def _slug(instrument: str, index: int) -> str:
    safe = "".join(ch for ch in instrument.lower() if ch.isalnum() or ch in "-_") or "track"
    return f"{index:02d}-{safe}.track.xml"


def write_project(
    directory: str | Path,
    name: str,
    bpm: float,
    sample_rate: int,
    duration: float,
    tracks: list[TrackDraft],
    *,
    beats_per_bar: int = 4,
    master_gain_db: float = -1.0,
    master_effects: list | None = None,
    force: bool = False,
    report: dict | None = None,
) -> WrittenProject:
    """写出工程、音轨与 ``analysis.json``。已存在时需 ``force=True``。"""
    from ..core.project import VALID_SAMPLE_RATES

    target = Path(directory)
    slug = target.name or "analysis"
    project_path = target / f"{slug}.musicproj"
    if project_path.exists() and not force:
        raise FileExistsError(f"目标已存在：{project_path}（加 --force 覆盖）")
    if int(sample_rate) not in VALID_SAMPLE_RATES:
        sample_rate = 44100
    target.mkdir(parents=True, exist_ok=True)
    tracks_dir = target / "tracks"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    if force and tracks_dir.is_dir():
        # 只清理本工具生成的「01-xxx.track.xml」；用户自己加的文件不动
        for stale in tracks_dir.glob("[0-9][0-9]-*.track.xml"):
            try:
                stale.unlink()
            except OSError:
                pass

    written: list[Path] = []
    note_count = 0
    for index, draft in enumerate(tracks, start=1):
        track = Track(
            name=draft.name,
            instrument=draft.instrument,
            path=tracks_dir / _slug(draft.instrument, index),
            gain_db=float(draft.gain_db),
            pan=float(max(-1.0, min(1.0, draft.pan))),
            reverb=float(max(0.0, min(1.0, draft.reverb))),
            audio_path=draft.audio_path,
            audio_offset=float(draft.audio_offset),
            audio_gain_db=float(draft.audio_gain_db),
            patch_path=draft.patch_path,
            effects=list(draft.effects),
        )
        for note in sorted(draft.notes, key=lambda item: (item.start, item.midi)):
            beats = note.start * bpm / 60.0
            # 下限从 1/32 拍放宽到 1/256 拍：精确回放的立体声块可能只有几毫秒，
            # 拉长会让渲染端误判「时值被改过」，从而给片段加上不该有的淡出。
            length = max(note.duration * bpm / 60.0, 1.0 / 256.0)
            track.notes.append(
                Note(
                    time=round(beats, 6),
                    dur=round(length, 6),
                    pitches=[int(note.midi)],
                    velocity=round(float(max(0.05, min(1.0, note.velocity))), 3),
                    bend=note.detail.get("bend") or None,
                    sample=note.detail.get("sample") or None,
                    src_time=note.detail.get("src_time"),
                )
            )
        written.append(save_track(track, track.path))
        note_count += len(track.notes)

    root = ET.Element(
        "musicproj",
        {
            "name": name,
            "bpm": f"{bpm:g}",
            "sampleRate": str(int(sample_rate)),
            "beatsPerBar": str(int(beats_per_bar)),
            "length": f"{max(1.0, duration):.3f}s",
            "masterGain": f"{master_gain_db:+g}dB",
            "normalize": "true",
        },
    )
    root.append(ET.Comment(
        f" 由音频分析重建：{escape(name)} · {len(tracks)} 条音轨 · "
        f"{note_count} 个音符。参数与置信度见 analysis.json。 "
    ))
    for path in written:
        ET.SubElement(root, "track", {"file": f"tracks/{path.name}"})
    if master_effects:
        master = ET.SubElement(root, "master")
        for spec in master_effects:
            ET.SubElement(master, "effect", spec.to_attrs())
    ET.indent(root, space="  ")
    project_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n",
        encoding="utf-8",
    )

    if report is not None:
        payload = dict(report)
        payload.setdefault("project", str(project_path))
        (target / "analysis.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return WrittenProject(
        project_path=project_path,
        track_paths=written,
        track_count=len(tracks),
        note_count=note_count,
    )
