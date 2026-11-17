"""musicproj → 标准 MIDI 文件（SMF type 1）。

导出的是**音符与演奏信息**，不是音频：多轨各占一个 MIDI 轨，带轨道名、
GM 音色号（program change）、力度、弯音与循环展开后的完整时间轴。这样工程
可以进 DAW 继续编曲，也能交给别的音源或记谱软件。

设计取舍：

* 时间轴用拍（工程里本来就是拍），tick 分辨率 480；
* 通道从 0 开始分配，**跳过 9**（GM 打击乐通道），鼓轨固定走 9；
* 弯音按「半音偏离 / 弯音范围」换算成 14 位 pitch wheel 值，默认范围 ±2 半音；
* 循环轨按渲染时的同一套规则展开，导出结果与 WAV 的时长一致；
* 音色号只是建议值：找不到对应 GM 音色时退到 0（钢琴），不会因此失败。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import mido

from .core.project import Project, Track
from .samples import fetch

#: 内置乐器的 GM 音色号（0 基）。注册表里带 ``gm`` 的条目会优先用它的编号。
_BUILTIN_PROGRAMS: dict[str, int] = {
    "piano": 0, "violin": 40, "cello": 42, "trumpet": 56, "recorder": 74,
    "guitar": 25, "bass": 33, "vocal": 53,
    "synth_lead": 80, "synth_pad": 88, "synth_bass": 38, "synth_pluck": 84,
    "synth": 81, "drums": 0,
}

#: GM 打击乐通道（0 基）
DRUM_CHANNEL = 9
TICKS_PER_BEAT = 480


@dataclass
class MidiExportResult:
    path: Path
    tracks: int
    notes: int
    beats: float
    channels: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _program_for(instrument: str) -> int:
    """乐器键 → GM 音色号。优先查注册表的 gm 包名，再落到内置表。"""
    key = fetch.resolve(instrument)
    gm_name = None
    try:
        from .samples import registry as _registry

        for entry in _registry.load():
            if entry.get("key") == key:
                gm_name = entry.get("gm")
                break
    except Exception:  # noqa: BLE001 - 注册表读不出来只影响音色建议
        gm_name = None
    if gm_name:
        try:
            from .samples.gm_catalog import lookup as _gm_lookup

            found = _gm_lookup(gm_name)
            if found is not None:
                return int(found.program)
        except Exception:  # noqa: BLE001 - 旧版本没有 GM 全目录，退回内置表
            pass
    return _BUILTIN_PROGRAMS.get(key, 0)


def _is_drums(instrument: str) -> bool:
    return fetch.resolve(instrument) in ("drums", "drum", "kit")


def _loop_offsets(track: Track, project: Project, length_beats: float,
                  expand: bool) -> list[float]:
    """循环展开的起始偏移（拍），与渲染端同一套规则。"""
    if not expand or not track.loop:
        return [0.0]
    period = track.period_beats(project.beats_per_bar)
    if period <= 1e-9:
        return [0.0]
    if track.loop_count > 0:
        reps = track.loop_count
    else:
        reps = int(math.ceil(length_beats / period - 1e-9)) + 1
    return [i * period for i in range(max(1, reps))]


def _assign_channels(tracks: list[Track], warnings: list[str]) -> dict[int, int]:
    """给每个轨道下标分配 MIDI 通道（跳过 9）。"""
    mapping: dict[int, int] = {}
    cursor = 0
    for index, track in enumerate(tracks):
        if _is_drums(track.instrument):
            mapping[index] = DRUM_CHANNEL
            continue
        while cursor == DRUM_CHANNEL:
            cursor += 1
        if cursor > 15:
            warnings.append(f"轨道超过 15 条，音轨「{track.name}」与其它轨共用通道 15")
            mapping[index] = 15
            continue
        mapping[index] = cursor
        cursor += 1
    return mapping


def _track_events(
    track: Track,
    project: Project,
    channel: int,
    program: int,
    length_beats: float,
    expand_loop: bool,
    bend_range: float,
) -> tuple[list[tuple[int, int, mido.Message]], int]:
    """生成 (绝对 tick, 排序权重, 消息) 列表。权重让同 tick 的 note_off 先于 on。"""
    events: list[tuple[int, int, mido.Message]] = [(0, 0, mido.Message(
        "program_change", channel=channel, program=program, time=0
    ))]
    offsets = _loop_offsets(track, project, length_beats, expand_loop)
    count = 0
    for note in track.notes:
        start_beat = note.time
        duration = max(1e-4, note.dur)
        for offset in offsets:
            start_tick = int(round((start_beat + offset) * TICKS_PER_BEAT))
            end_tick = int(round((start_beat + offset + duration) * TICKS_PER_BEAT))
            if start_tick >= int(round(length_beats * TICKS_PER_BEAT)):
                continue
            velocity = max(1, min(127, int(round(note.velocity * 127))))
            for pitch in note.pitches:
                events.append((start_tick, 2, mido.Message(
                    "note_on", channel=channel, note=max(0, min(127, pitch)),
                    velocity=velocity, time=0
                )))
                events.append((end_tick, 0, mido.Message(
                    "note_off", channel=channel, note=max(0, min(127, pitch)),
                    velocity=0, time=0
                )))
                count += 1
            if note.bend:
                for position, semitone in note.bend:
                    tick = int(round((start_beat + offset + position * duration) * TICKS_PER_BEAT))
                    value = int(round(8192 + (semitone / bend_range) * 8191))
                    events.append((tick, 1, mido.Message(
                        "pitchwheel", channel=channel,
                        pitch=max(-8192, min(8191, value - 8192)), time=0
                    )))
                events.append((end_tick, 1, mido.Message(
                    "pitchwheel", channel=channel, pitch=0, time=0
                )))
    # 同一 tick 内先 note_off、再弯音、最后 note_on
    events.sort(key=lambda item: (item[0], item[1]))
    return events, count


def _build_project_track(project: Project, name: str) -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.name = name
    track.append(mido.MetaMessage(
        "time_signature", numerator=project.beats_per_bar, denominator=4, time=0
    ))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(project.bpm), time=0))
    return track


def _fill(track: mido.MidiTrack, events: list[tuple[int, int, mido.Message]]) -> None:
    previous = 0
    for tick, _weight, message in events:
        message.time = max(0, tick - previous)
        previous = tick
        track.append(message)
    track.append(mido.MetaMessage("end_of_track", time=0))


def export_midi(
    project: Project,
    output: str | Path,
    *,
    only: list[int] | None = None,
    expand_loop: bool = True,
    bend_range: float = 2.0,
) -> MidiExportResult:
    """把工程写成单个多轨 MIDI 文件（type 1）。"""
    warnings: list[str] = []
    if bend_range <= 0:
        raise ValueError("bend_range 必须大于 0")

    indices = list(range(len(project.tracks)))
    if only is not None:
        wanted = set(only)  # 0 基下标，与 render --tracks 一致
        chosen = [i for i in indices if i in wanted]
        if not chosen:
            raise ValueError("--only 指定的轨道都不存在")
        indices = chosen
    selected = [project.tracks[i] for i in indices]
    if not selected:
        raise ValueError("工程里没有可导出的音轨")

    length_beats = project.compute_length_seconds() * project.bpm / 60.0
    channels = _assign_channels(selected, warnings)

    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    conductor = _build_project_track(project, project.name)
    conductor.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(conductor)

    total_notes = 0
    channel_report: dict[str, int] = {}
    for position, track in enumerate(selected):
        channel = channels[position]
        program = 0 if _is_drums(track.instrument) else _program_for(track.instrument)
        events, count = _track_events(
            track, project, channel, program, length_beats, expand_loop, bend_range
        )
        midi_track = mido.MidiTrack()
        midi_track.name = track.name
        _fill(midi_track, events)
        midi.tracks.append(midi_track)
        total_notes += count
        channel_report[track.name] = channel

    target = Path(output).expanduser()
    if target.suffix.lower() not in (".mid", ".midi"):
        target = target.with_suffix(".mid")
    target.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(target))

    return MidiExportResult(
        path=target,
        tracks=len(selected),
        notes=total_notes,
        beats=length_beats,
        channels=channel_report,
        warnings=warnings,
    )


def export_midi_split(
    project: Project,
    directory: str | Path,
    *,
    only: list[int] | None = None,
    expand_loop: bool = True,
    bend_range: float = 2.0,
) -> list[MidiExportResult]:
    """每条轨道单独导出一个 type 0 文件，便于逐轨导入 DAW。"""
    root = Path(directory).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    indices = list(range(len(project.tracks)))
    if only is not None:
        wanted = set(only)
        indices = [i for i in indices if i in wanted]
    results: list[MidiExportResult] = []
    length_beats = project.compute_length_seconds() * project.bpm / 60.0
    for index in indices:
        track = project.tracks[index]
        channel = DRUM_CHANNEL if _is_drums(track.instrument) else 0
        program = 0 if _is_drums(track.instrument) else _program_for(track.instrument)
        events, count = _track_events(
            track, project, channel, program, length_beats, expand_loop, bend_range
        )
        midi = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT)
        midi_track = _build_project_track(project, track.name)
        _fill(midi_track, events)
        midi.tracks.append(midi_track)
        # 文件名用轨道名，去掉路径分隔符
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in track.name).strip()
        target = root / f"{index + 1:02d}_{safe or 'track'}.mid"
        midi.save(str(target))
        results.append(MidiExportResult(
            path=target, tracks=1, notes=count, beats=length_beats,
            channels={track.name: channel},
        ))
    if not results:
        raise ValueError("没有可导出的音轨")
    return results
