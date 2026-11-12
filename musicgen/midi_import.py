"""MIDI → musicproj：把转谱模型（如 MuScriptor）导出的 MIDI 变成可渲染工程。

映射规则（GM 音色号 → 本项目的乐器键）保持简单可解释：

* 通道 10（0 基的 9）一律当鼓组，音高按 GM 打击乐编号原样保留；
* 0–7 钢琴、24–31 吉他、32–39 贝斯、56–63 铜管、72–79 管乐、52–55 人声；
* 40–47 弦乐按音域分：中位音高 < 52 走大提琴，其余走小提琴；
* 80–95 合成音：80–87 合成主音、88–95 合成垫；
* 其余按中位音高落到钢琴/大提琴/小提琴。

默认把用到的采样一起拷进 ``<工程>/instruments/``，渲染时优先用工程内的音源，
所以工程搬到没有全局采样缓存的机器上也能直接渲染。
"""

from __future__ import annotations

import math
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import mido

from .core.notes import midi_to_name
from .core.project import Note, Project, Track, save_track
from .samples import fetch

#: GM 音色号 → 乐器键（按 GM 家族的起始号归类）
_GM_FAMILIES = (
    (0, 8, "piano"),
    (24, 8, "guitar"),
    (32, 8, "bass"),
    (56, 8, "trumpet"),
    (72, 8, "recorder"),
    (52, 4, "vocal"),
    (80, 8, "synth_lead"),
    (88, 8, "synth_pad"),
)


@dataclass
class MidiNote:
    start: float          # 拍
    duration: float       # 拍
    pitch: int
    velocity: float


@dataclass
class MidiPart:
    name: str
    instrument: str
    channel: int
    program: int
    notes: list[MidiNote] = field(default_factory=list)


@dataclass
class MidiPiece:
    bpm: float
    beats_per_bar: int
    parts: list[MidiPart]
    length_beats: float


def instrument_for(channel: int, program: int, median_pitch: float) -> str:
    if channel == 9:
        return "drums"
    for start, span, key in _GM_FAMILIES:
        if start <= program < start + span:
            if key == "piano":
                return "cello" if median_pitch < 40 else "piano"
            return key
    if program < 24:
        return "piano"
    if program < 32:
        return "guitar"
    if program < 40:
        return "bass"
    if program < 48:
        return "cello" if median_pitch < 52 else "violin"
    if program < 56:
        return "synth_pad"
    if program < 64:
        return "trumpet"
    if program < 72:
        return "recorder"
    if program < 80:
        return "recorder"
    if program < 88:
        return "synth_lead"
    if program < 96:
        return "synth_pad"
    if median_pitch < 52:
        return "cello"
    return "piano"


def parse_midi(path: str | Path) -> MidiPiece:
    """读 SMF，返回按「轨 × 通道 × 音色」拆好的音符。"""
    midi = mido.MidiFile(str(path))
    ticks_per_beat = max(1, midi.ticks_per_beat)

    tempo = 500000  # 默认 120BPM
    beats_per_bar = 4
    tempo_seen = False
    signature_seen = False
    for track in midi.tracks:
        for message in track:
            if message.type == "set_tempo" and not tempo_seen:
                tempo = message.tempo
                tempo_seen = True
            elif message.type == "time_signature" and not signature_seen:
                beats_per_bar = max(1, int(message.numerator))
                signature_seen = True
            if tempo_seen and signature_seen:
                break
        if tempo_seen and signature_seen:
            break

    parts: dict[tuple[int, int, int], MidiPart] = {}
    last_tick = 0
    for index, track in enumerate(midi.tracks):
        tick = 0
        program_by_channel: dict[int, int] = {}
        active: dict[tuple[int, int], tuple[int, float, int]] = {}
        label = track.name.strip()
        for message in track:
            tick += message.time
            if message.type == "program_change":
                program_by_channel[message.channel] = message.program
            elif message.type == "note_on" and message.velocity > 0:
                active[(message.channel, message.note)] = (
                    tick, message.velocity / 127.0,
                    program_by_channel.get(message.channel, 0),
                )
            elif message.type == "note_off" or (
                    message.type == "note_on" and message.velocity == 0):
                key = (message.channel, message.note)
                held = active.pop(key, None)
                if held is None:
                    continue
                start_tick, velocity, program = held
                span = max(1, tick - start_tick)
                part_key = (index, message.channel, program)
                part = parts.get(part_key)
                if part is None:
                    part = MidiPart(name=label, instrument="", channel=message.channel,
                                    program=program)
                    parts[part_key] = part
                part.notes.append(MidiNote(
                    start=start_tick / ticks_per_beat,
                    duration=span / ticks_per_beat,
                    pitch=int(message.note),
                    velocity=velocity,
                ))
        last_tick = max(last_tick, tick)

    resolved: list[MidiPart] = []
    for part in parts.values():
        if not part.notes:
            continue
        median = float(sorted(note.pitch for note in part.notes)[len(part.notes) // 2])
        part.instrument = instrument_for(part.channel, part.program, median)
        if not part.name:
            part.name = fetch.label(part.instrument)
        resolved.append(part)
    resolved.sort(key=lambda item: (min(note.start for note in item.notes), item.name))

    return MidiPiece(
        bpm=round(60_000_000.0 / max(1, tempo), 3),
        beats_per_bar=beats_per_bar,
        parts=resolved,
        length_beats=max((note.start + note.duration for part in resolved
                          for note in part.notes), default=0.0),
    )


def write_project(piece: MidiPiece, directory: str | Path, name: str | None = None,
                  bundle: bool = True) -> Path:
    """写出工程并（可选）把采样拷进工程目录。"""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    tracks_dir = target / "tracks"
    tracks_dir.mkdir(exist_ok=True)
    slug = target.name or "midi"
    title = name or slug
    length_beats = max(piece.beats_per_bar, math.ceil(piece.length_beats + 1))

    project = Project(
        name=title,
        path=target / f"{slug}.musicproj",
        bpm=piece.bpm,
        sample_rate=44100,
        beats_per_bar=piece.beats_per_bar,
        length_spec=f"{length_beats}",
        tail_seconds=2.0,
    )

    written: list[Path] = []
    for index, part in enumerate(piece.parts, 1):
        track = Track(
            name=part.name,
            instrument=part.instrument,
            path=tracks_dir / f"{index:02d}-{part.instrument}.track.xml",
        )
        for note in sorted(part.notes, key=lambda item: (item.start, item.pitch)):
            track.notes.append(Note(
                time=round(note.start, 6),
                dur=round(max(note.duration, 0.05), 6),
                pitches=[note.pitch],
                velocity=round(max(0.05, min(1.0, note.velocity)), 3),
            ))
        save_track(track, track.path)
        project.tracks.append(track)
        written.append(track.path)

    root = ET.Element("musicproj", {
        "name": title,
        "bpm": f"{piece.bpm:g}",
        "sampleRate": "44100",
        "beatsPerBar": str(piece.beats_per_bar),
        "length": f"{length_beats}",
        "masterGain": "-1dB",
        "normalize": "true",
    })
    root.append(ET.Comment(
        f" 由 MIDI 重建：{len(project.tracks)} 条音轨 · "
        f"{sum(len(track.notes) for track in project.tracks)} 个音符。"
    ))
    for path in written:
        ET.SubElement(root, "track", {"file": f"tracks/{path.name}"})
    ET.indent(root, space="  ")
    project.path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode") + "\n",
        encoding="utf-8",
    )

    if bundle:
        bundle_instruments(project, target)
    return project.path


def bundle_instruments(project: Project, directory: str | Path) -> list[str]:
    """把工程用到的采样音源拷进 ``<工程>/instruments/``，并复制工程外的合成音脚本。"""
    target = Path(directory)
    local = target / "instruments"
    copied: list[str] = []
    for track in project.tracks:
        key = fetch.resolve(track.instrument)
        if key in fetch.SYNTHS or key in fetch.BUILTIN:
            continue
        source = fetch.cache_dir_for(key)
        if not source.is_dir():
            continue
        destination = local / key
        destination.mkdir(parents=True, exist_ok=True)
        for sample in source.glob("*"):
            if sample.is_file():
                target_file = destination / sample.name
                if not target_file.exists() or target_file.stat().st_size != sample.stat().st_size:
                    shutil.copy2(sample, target_file)
        copied.append(key)

        patch = getattr(track, "patch_path", None)
        if patch is not None:
            patch = Path(patch)
            if patch.is_file() and target not in patch.parents:
                synths = target / "synths"
                synths.mkdir(exist_ok=True)
                shutil.copy2(patch, synths / patch.name)
                track.patch_path = synths / patch.name
                save_track(track, track.path)   # 把新的相对路径写回音轨 XML
    return sorted(set(copied))


def describe_piece(piece: MidiPiece) -> str:
    lines = [f"{piece.bpm:g}BPM · {piece.beats_per_bar}/4 · "
             f"{len(piece.parts)} 个声部 · {piece.length_beats:.0f} 拍"]
    for part in piece.parts:
        pitches = [note.pitch for note in part.notes]
        lines.append(
            f"  {part.name} [{part.instrument}] 音符 {len(part.notes)} "
            f"音域 {midi_to_name(min(pitches))}–{midi_to_name(max(pitches))}"
        )
    return "\n".join(lines)
