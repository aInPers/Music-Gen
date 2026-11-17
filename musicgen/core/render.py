"""工程渲染：音符展开循环 → 采样合成 → 轨道处理（增益/声像/混响）→ 母带。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from .audioclip import AudioClipError, load_clip
from .automation import Curve, apply_gain_curve, apply_pan_curve
from .effects import create_effect
from .eventbank import EventSampleBank
from .instruments import InstrumentRegistry
from .project import Project, Track

ProgressFn = Callable[[float, str], None]

#: 母带目标峰值（-1dBFS）
TARGET_PEAK = 10 ** (-1.0 / 20.0)
#: 结尾防爆音淡出（秒）
END_FADE = 0.01
#: 力度 / 时长量化，便于复用在循环里重复出现的音符
_VEL_Q = 0.05
_DUR_Q = 0.005


@dataclass
class TrackStat:
    name: str
    instrument: str
    notes: int
    peak: float
    seconds: float
    effects: str = ""
    kind: str = "notes"  # notes | audio
    rms: float = 0.0
    #: 50ms 一帧的电平包络（0–1 幅度），供电平表按播放位置取值
    envelope: np.ndarray | None = None


@dataclass
class RenderResult:
    audio: np.ndarray  # (2, N) float32
    sample_rate: int
    duration: float
    peak: float
    rms: float
    notes_rendered: int
    track_stats: list[TrackStat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def peak_db(self) -> float:
        return 20 * math.log10(max(self.peak, 1e-12))

    @property
    def rms_db(self) -> float:
        return 20 * math.log10(max(self.rms, 1e-12))


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _next_pow2(value: int) -> int:
    return 1 << max(1, int(value - 1).bit_length())


_ir_cache: dict[tuple[int, int], np.ndarray] = {}


def _make_impulse_response(sample_rate: int, amount: float) -> np.ndarray:
    """合成一段指数衰减的噪声脉冲响应（立体声去相关）。"""
    key = (sample_rate, int(round(amount * 20)))
    if key in _ir_cache:
        return _ir_cache[key]
    amount = max(0.0, min(1.0, amount))
    decay_s = 0.55 + 1.9 * amount
    length = int(decay_s * sample_rate)
    t = np.arange(length, dtype=np.float32) / sample_rate
    envelope = np.exp(-t * (5.2 / decay_s)).astype(np.float32)
    rng = np.random.default_rng(20240501)
    ir = np.empty((2, length), dtype=np.float32)
    for ch in range(2):
        noise = rng.standard_normal(length).astype(np.float32)
        # 简易低通（滑动平均），让残响更暗
        window = int(max(2, 0.0035 * sample_rate * (0.4 + amount)))
        kernel = np.ones(window, dtype=np.float32) / window
        noise = np.convolve(noise, kernel, mode="same")
        ir[ch] = noise * envelope
    # 预延迟
    pre = int(0.015 * sample_rate)
    ir = np.hstack([np.zeros((2, pre), dtype=np.float32), ir])
    energy = float(np.sqrt(np.sum(ir ** 2)))
    if energy > 0:
        ir /= energy
    _ir_cache[key] = ir
    return ir


def _apply_reverb(audio: np.ndarray, amount: float, sample_rate: int) -> np.ndarray:
    if amount <= 1e-3 or audio.shape[1] == 0:
        return audio
    ir = _make_impulse_response(sample_rate, amount)
    total = audio.shape[1] + ir.shape[1] - 1
    n = _next_pow2(total)
    out = np.empty_like(audio)
    for ch in range(2):
        spec = np.fft.rfft(audio[ch], n)
        resp = np.fft.rfft(ir[ch], n)
        out[ch] = np.fft.irfft(spec * resp, n)[: audio.shape[1]].astype(np.float32)
    # 卷积后的能量与干声大致相当，这里按 send 量混合
    return audio + out * min(1.0, amount) * 0.9


def _pan_gains(pan: float) -> tuple[float, float]:
    """等功率声像。"""
    theta = (max(-1.0, min(1.0, pan)) + 1.0) * math.pi / 4.0
    return math.cos(theta), math.sin(theta)


def _effect_label(spec) -> str:
    """效果名；注册表里没有该类型时退回注册键，保证渲染不因显示失败而中断。"""
    try:
        return spec.label()
    except Exception:  # noqa: BLE001 - 旧工程里可能留有已删除的效果
        return getattr(spec, "type", "?")


def _find_curve(automation: Sequence[Curve], target: str) -> Curve | None:
    """按目标名找曲线（大小写不敏感）；后写的覆盖先写的。"""
    wanted = target.strip().lower()
    found = None
    for curve in automation:
        if curve.target.strip().lower() == wanted:
            found = curve
    return found


def _effect_curves(automation: Sequence[Curve], index: int) -> dict[str, Curve]:
    """取出针对第 ``index`` 个效果的参数曲线：``effect.2.mix`` → ``{"mix": Curve}``。"""
    prefix = f"effect.{index}."
    found: dict[str, Curve] = {}
    for curve in automation:
        target = curve.target.strip().lower()
        if target.startswith(prefix):
            key = target[len(prefix):].strip()
            if key:
                found[key] = curve
    return found


def _apply_effect_automated(dry: np.ndarray, spec, curves: dict[str, Curve],
                            sr: int, bpm: float, block: int = 4096) -> np.ndarray:
    """按参数曲线分块重处理，重叠 50% 用汉宁窗加权拼接。

    效果本身有状态（回声、混响的尾巴），分块不可能完全无缝，所以块取 93ms、
    重叠一半再加窗；参数自动化用在滤波、失真、干湿这类地方听不出接缝。
    """
    frames = dry.shape[1]
    if frames == 0 or not curves:
        return create_effect(spec.type, spec.params).process(dry, sr)
    hop = max(1, block // 2)
    out = np.zeros_like(dry)
    weight = np.zeros(frames, dtype=np.float32)
    window = np.hanning(block).astype(np.float32)
    sec_per_beat = 60.0 / max(1e-9, bpm)
    for start in range(0, frames, hop):
        end = min(frames, start + block)
        chunk = dry[:, start:end]
        if chunk.shape[1] < 16:
            break
        centre_beat = (start + chunk.shape[1] / 2.0) / sr / sec_per_beat
        params = dict(spec.params)
        for key, curve in curves.items():
            if spec.cls.param(key) is not None:
                params[key] = curve.value_at(centre_beat)
        wet = create_effect(spec.type, params).process(chunk, sr)
        w = window[: chunk.shape[1]]
        out[:, start:end] += wet * w
        weight[start:end] += w
    return out / np.maximum(weight, 1e-6)


def _blend_range(dry: np.ndarray, wet: np.ndarray, start_beat: float | None,
                 end_beat: float | None, bpm: float, sr: int) -> np.ndarray:
    """只在 [start, end) 拍内用湿声，边界 20ms 淡入淡出。"""
    frames = dry.shape[1]
    if frames == 0:
        return wet
    sec_per_beat = 60.0 / max(1e-9, bpm)
    a = 0 if start_beat is None else int(max(0.0, start_beat * sec_per_beat * sr))
    b = frames if end_beat is None else int(min(float(frames), end_beat * sec_per_beat * sr))
    if b <= a:
        return dry
    mask = np.zeros(frames, dtype=np.float32)
    mask[a:b] = 1.0
    fade = min(int(0.02 * sr), max(1, (b - a) // 2))
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        mask[a:a + fade] *= ramp
        mask[b - fade:b] *= ramp[::-1]
    return (dry * (1.0 - mask) + wet * mask).astype(np.float32)


def _run_effects(buffer: np.ndarray, specs: Sequence, automation: Sequence[Curve],
                 sr: int, bpm: float, label: str,
                 warn: Callable[[str], None],
                 report_fn: Callable[[float, str], None],
                 progress: float) -> np.ndarray:
    """按顺序跑效果链：支持参数曲线与时间区间。"""
    for index, spec in enumerate(specs):
        name = _effect_label(spec)
        report_fn(progress, f"音效：{name}（{label}）")
        try:
            curves = _effect_curves(automation, index)
            if curves:
                wet = _apply_effect_automated(buffer, spec, curves, sr, bpm)
            else:
                wet = create_effect(spec.type, spec.params).process(buffer, sr)
            if spec.ranged:
                wet = _blend_range(buffer, wet, spec.start, spec.end, bpm, sr)
            buffer = wet
        except Exception as exc:  # noqa: BLE001 - 单个效果出错不该让整首渲染失败
            warn(
                f"音效 {name}（{spec.type}）在「{label}」上处理失败，"
                f"该效果已跳过：{type(exc).__name__}: {exc}"
            )
    return buffer


def _note_offsets(track: Track, project: Project, length_s: float) -> list[float]:
    """把循环展开成每次重复的起始偏移（秒）。"""
    bpm = track.resolve_bpm(project.bpm)
    sec_per_beat = 60.0 / bpm
    if not track.loop:
        return [0.0]
    period_s = track.period_beats(project.beats_per_bar) * sec_per_beat
    if period_s <= 1e-6:
        return [0.0]
    step_s = period_s + max(0.0, track.loop_gap_beats) * sec_per_beat
    if track.loop_count > 0:
        reps = track.loop_count
    else:
        reps = int(math.ceil(length_s / step_s - 1e-9)) + 1
    reps = max(1, reps)
    return [i * step_s for i in range(reps)]


#: 电平表包络的帧长（秒）
METER_HOP = 0.05


def _envelope(mono: np.ndarray, sample_rate: int, hop: float = METER_HOP) -> np.ndarray:
    """逐帧 RMS 包络（0–1 幅度），电平表按播放位置直接取值。"""
    step = max(1, int(round(hop * sample_rate)))
    count = mono.size // step
    if count <= 0:
        return np.zeros(1, dtype=np.float32)
    frames = np.asarray(mono[: count * step], dtype=np.float32).reshape(count, step)
    return np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1)).astype(np.float32)


def render_project(
    project: Project,
    cache_root: Path | None = None,
    progress: ProgressFn | None = None,
    normalize: bool | None = None,
    only: Sequence[int] | None = None,
) -> RenderResult:
    """渲染整个工程，返回立体声 float32 数据。

    ``normalize`` 为 None 时使用工程里的设置。
    """
    sr = int(project.sample_rate)
    length_s = project.compute_length_seconds()
    frames = max(1, int(round(length_s * sr)))
    warnings: list[str] = []
    event_root = (Path(project.path).parent / "samples") if project.path else None
    # 工程自带音源优先：<工程目录>/instruments/<乐器>/*.wav，搬走也能渲染
    if cache_root is None and project.path is not None:
        local = Path(project.path).parent / "instruments"
        if local.is_dir():
            cache_root = local
    registry = InstrumentRegistry(
        cache_root=cache_root, sample_rate=sr,
        warn=lambda m: warnings.append(m) if m not in warnings else None,
        base_dir=project.path.parent if project.path else None,
    )

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, fraction)), message)

    candidates = list(project.tracks)
    if only is not None:
        # 指定轨道渲染：按工程里的下标选（0 起），越界的忽略
        wanted = {int(index) for index in only}
        candidates = [track for index, track in enumerate(project.tracks) if index in wanted]
        if not candidates:
            warnings.append("指定的轨道都不存在，已改为渲染全部")
            candidates = list(project.tracks)
    active = [t for t in candidates if not t.mute]
    if any(t.solo for t in active):
        active = [t for t in active if t.solo]
    if not active:
        warnings.append("没有可渲染的音轨（全部静音或为空）")

    master = np.zeros((2, frames), dtype=np.float32)
    #: 总线输入累加器（键为小写名），轨道归属与发送都汇到这里
    bus_inputs: dict[str, np.ndarray] = {
        bus.name.strip().lower(): np.zeros((2, frames), dtype=np.float32)
        for bus in project.buses
    }
    #: 组输入累加器；组在总线之前汇总，组内轨道共享组的处理
    group_inputs: dict[str, np.ndarray] = {
        group.name.strip().lower(): np.zeros((2, frames), dtype=np.float32)
        for group in project.groups
    }
    note_cache: dict[tuple, np.ndarray] = {}
    # 时间轴补全：同一分轨拆成多条工程轨时，同一起音的多个音符会挂同一个
    # 立体声块。只播一次，否则该区间被叠 N 遍（响 +6dB 以上）。
    played_events: set[tuple[str, int]] = set()
    stats: list[TrackStat] = []
    notes_rendered = 0

    total_notes = sum(len(t.notes) for t in active) or 1
    done = 0

    for track in active:
        instrument = registry.get_for_track(track) if track.notes else None
        buffer = np.zeros((2, frames), dtype=np.float32)
        is_audio = track.audio_path is not None
        if is_audio:
            try:
                buffer += load_clip(
                    track.audio_path,
                    sr,
                    frames,
                    offset=track.audio_offset,
                    gain_db=track.audio_gain_db,
                )
            except AudioClipError as exc:
                warnings.append(f"音频轨「{track.name}」读取失败，已跳过：{exc}")
            if track.audio_offset + track.audio_duration > length_s + 0.01:
                warnings.append(
                    f"音频轨「{track.name}」超出工程长度，尾部已被截断"
                    f"（{track.audio_offset + track.audio_duration:.2f}s > {length_s:.2f}s）"
                )
        offsets = _note_offsets(track, project, length_s)
        events = EventSampleBank(event_root, sr)
        bpm = track.resolve_bpm(project.bpm)
        sec_per_beat = 60.0 / bpm
        track_notes = 0

        for note in track.notes:
            dur_s = note.dur * sec_per_beat
            key = (
                getattr(instrument, "name", track.instrument),
                track.patch_path,
                tuple(note.pitches),
                tuple(note.bend) if note.bend else None,
                note.sample,
                round(note.velocity / _VEL_Q),
                round(dur_s / _DUR_Q),
            )
            seg = note_cache.get(key)
            if seg is None and note.sample and events.has(note.sample):
                # 逐事件采样：原样播放，变速比 0——音高与起音都不经过任何判断
                seg = events.render(note.sample, dur_s)
            if seg is None:
                pieces = []
                for midi in note.pitches:
                    piece = instrument.render_note(
                        midi, note.velocity, dur_s, bend=note.bend
                    )
                    if piece is None:
                        continue
                    pieces.append(piece)
                if not pieces:
                    # 采样缺失时用合成器顶上
                    synth = registry.synth()
                    pieces = [synth.render_note(m, note.velocity, dur_s) for m in note.pitches]
                seg = np.zeros((2, max(p.shape[1] for p in pieces)), dtype=np.float32)
                for piece in pieces:
                    seg[:, : piece.shape[1]] += piece
                note_cache[key] = seg

            from_bank = bool(note.sample and events.has(note.sample))
            # 精确回放：按原分轨起音秒定位，绕开「秒→拍→秒」的 BPM 往返误差。
            # 若 time 被手动改过（与 src_time 相差 >5ms），则尊重编辑后的位置。
            use_src = (note.src_time is not None
                       and abs(note.time * sec_per_beat - note.src_time) <= 0.005)
            for offset in offsets:
                base = note.src_time if use_src else (note.time + track.start_offset_beats) * sec_per_beat
                start = int(round((base + offset) * sr))
                if start >= frames:
                    continue
                if from_bank and (note.sample, start) in played_events:
                    continue
                end = min(frames, start + seg.shape[1])
                if end <= start:
                    continue
                buffer[:, start:end] += seg[:, : end - start]
                if from_bank:
                    played_events.add((note.sample, start))
                track_notes += 1
            done += 1

        if track_notes == 0 and not is_audio:
            stats.append(TrackStat(track.name, track.instrument, 0, 0.0, 0.0))
            continue

        bpm_track = track.resolve_bpm(project.bpm)
        gain_curve = _find_curve(track.automation, "gain")
        if gain_curve is not None:
            buffer = apply_gain_curve(buffer, gain_curve, sr, bpm_track, track.gain_db)
        else:
            buffer *= db_to_gain(track.gain_db)
        if track.reverb > 0:
            report(done / total_notes * 0.9, f"混响：{track.name}")
            buffer = _apply_reverb(buffer, track.reverb, sr)
        pan_curve = _find_curve(track.automation, "pan")
        if pan_curve is not None:
            buffer = apply_pan_curve(buffer, pan_curve, sr, bpm_track, track.pan)
            if is_audio:
                buffer *= math.sqrt(2.0)
        else:
            left, right = _pan_gains(track.pan)
            if is_audio:
                # 音频轨是立体声素材，pan=0 必须原样通过；等功率声像会给两声道各 -3dB
                left *= math.sqrt(2.0)
                right *= math.sqrt(2.0)
            buffer[0] *= left
            buffer[1] *= right

        # 效果链：按 XML 里出现的顺序串联，位置在声像之后、汇入组/总线之前
        buffer = _run_effects(
            buffer, track.effects, track.automation, sr, bpm_track,
            f"音轨「{track.name}」", warnings.append, report, done / total_notes * 0.9,
        )

        # 混音路由：归属组 → 该组；否则归属总线 → 该总线；发送 → 目标总线
        group = project.find_group(track.group)
        if track.group and group is None:
            warnings.append(
                f"音轨「{track.name}」归属的组「{track.group}」不存在，已按无组处理"
            )
        if group is not None:
            if track.bus:
                warnings.append(
                    f"音轨「{track.name}」已归入组「{group.name}」，"
                    f"自身的总线归属不再生效（改由组的 bus 决定去向）"
                )
            group_inputs[group.name.strip().lower()] += buffer
            target = None
        else:
            target = project.find_bus(track.bus)
            if track.bus and target is None:
                warnings.append(
                    f"音轨「{track.name}」归属的总线「{track.bus}」不存在，已直接并入母带"
                )
            if target is not None:
                bus_inputs[target.name.strip().lower()] += buffer
            if target is None:
                master += buffer
        # 发送独立于归属：进组也照样能往混响/延迟总线上送
        for sent in track.sends:
            if sent.amount <= 1e-4:
                continue
            destination = project.find_bus(sent.bus)
            if destination is None:
                warnings.append(
                    f"音轨「{track.name}」发送的目标总线「{sent.bus}」不存在，该发送已忽略"
                )
                continue
            bus_inputs[destination.name.strip().lower()] += buffer * float(sent.amount)

        peak = float(np.max(np.abs(buffer)))
        sounding = float(np.count_nonzero(np.any(buffer != 0, axis=0))) / sr
        mono = np.mean(buffer, axis=0, dtype=np.float32)
        track_rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) if mono.size else 0.0
        stats.append(
            TrackStat(
                track.name, track.instrument, track_notes, peak, sounding,
                " → ".join(_effect_label(spec) for spec in track.effects),
                "audio" if is_audio else "notes",
                rms=track_rms,
                envelope=_envelope(mono, sr),
            )
        )
        notes_rendered += track_notes
        report(done / total_notes * 0.9, f"渲染音轨：{track.name}")

    # ---- 组：汇总 → 效果链 → 自动化 → 推子 → 归属总线 / 母带 ----
    for group in project.groups:
        buffer = group_inputs.get(group.name.strip().lower())
        if buffer is None:
            continue
        if group.mute:
            warnings.append(f"组「{group.name}」已静音，其输出未进入母带")
            continue
        if not np.any(buffer):
            continue
        report(0.91, f"组处理：{group.name}")
        buffer = _run_effects(
            buffer, group.effects, group.automation, sr, project.bpm,
            f"组「{group.name}」", warnings.append, report, 0.91,
        )
        g_gain = _find_curve(group.automation, "gain")
        if g_gain is not None:
            buffer = apply_gain_curve(buffer, g_gain, sr, project.bpm, group.gain_db)
        else:
            buffer *= db_to_gain(group.gain_db)
        g_pan = _find_curve(group.automation, "pan")
        if g_pan is not None:
            buffer = apply_pan_curve(buffer, g_pan, sr, project.bpm, group.pan)
        else:
            left, right = _pan_gains(group.pan)
            buffer[0] *= left
            buffer[1] *= right

        target = project.find_bus(group.bus)
        if group.bus and target is None:
            warnings.append(
                f"组「{group.name}」归属的总线「{group.bus}」不存在，已直接并入母带"
            )
        if target is not None:
            bus_inputs[target.name.strip().lower()] += buffer
        else:
            master += buffer
        peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
        mono = np.mean(buffer, axis=0, dtype=np.float32)
        stats.append(
            TrackStat(
                group.name, "", 0, peak, float(np.count_nonzero(np.any(buffer != 0, axis=0))) / sr,
                " → ".join(_effect_label(spec) for spec in group.effects),
                "group",
                rms=float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) if mono.size else 0.0,
                envelope=_envelope(mono, sr),
            )
        )

    # ---- 总线：汇总 → 效果链 → 推子 → 母带 ----
    for bus in project.buses:
        buffer = bus_inputs.get(bus.name.strip().lower())
        if buffer is None:
            continue
        buffer = _run_effects(
            buffer, bus.effects, bus.automation, sr, project.bpm,
            f"总线「{bus.name}」", warnings.append, report, 0.90,
        )
        if bus.mute:
            warnings.append(f"总线「{bus.name}」已静音，其输出未进入母带")
            continue
        b_gain = _find_curve(bus.automation, "gain")
        if b_gain is not None:
            buffer = apply_gain_curve(buffer, b_gain, sr, project.bpm, bus.gain_db)
        else:
            buffer *= db_to_gain(bus.gain_db)
        master += buffer
        peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
        sounding = float(np.count_nonzero(np.any(buffer != 0, axis=0))) / sr
        mono = np.mean(buffer, axis=0, dtype=np.float32)
        bus_rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) if mono.size else 0.0
        stats.append(
            TrackStat(
                bus.name, "", 0, peak, sounding,
                " → ".join(_effect_label(spec) for spec in bus.effects),
                "bus", rms=bus_rms, envelope=_envelope(mono, sr),
            )
        )

    report(0.92, "母带处理")
    master *= db_to_gain(project.master_gain_db)
    for spec in project.master_effects:
        label = _effect_label(spec)
        report(0.93, f"母带音效：{label}")
        try:
            master = create_effect(spec.type, spec.params).process(master, sr)
        except Exception as exc:  # noqa: BLE001 - 单个效果出错不该让整首渲染失败
            warnings.append(
                f"母带音效 {label}（{spec.type}）处理失败，已跳过："
                f"{type(exc).__name__}: {exc}"
            )

    end_fade = min(int(END_FADE * sr), frames)
    if end_fade > 1:
        master[:, -end_fade:] *= np.linspace(1.0, 0.0, end_fade, dtype=np.float32)

    peak = float(np.max(np.abs(master))) if master.size else 0.0
    do_normalize = project.normalize if normalize is None else normalize
    if do_normalize and peak > 0:
        master *= TARGET_PEAK / peak
    elif peak > 1.0:
        warnings.append(f"峰值 {20 * math.log10(peak):.1f}dBFS 已超出 0dB，已做削波保护")
        master = np.clip(master, -1.0, 1.0)

    peak = float(np.max(np.abs(master))) if master.size else 0.0
    rms = float(np.sqrt(np.mean(master ** 2))) if master.size else 0.0
    report(1.0, "完成")

    return RenderResult(
        audio=master,
        sample_rate=sr,
        duration=frames / sr,
        peak=peak,
        rms=rms,
        notes_rendered=notes_rendered,
        track_stats=stats,
        warnings=warnings,
    )
