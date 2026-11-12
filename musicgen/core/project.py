"""``*.musicproj`` 工程与音轨文件的解析 / 校验 / 回写。

工程结构::

    demo.musicproj                  <musicproj bpm="120" ...>
    demo/tracks/lead.track.xml      <track instrument="piano" ...>
    demo/tracks/bass.track.xml          <note .../>

设计原则：
* 时值统一用「拍」表示（支持 ``1/4`` 与 ``0.5`` 两种写法），渲染时按所属 BPM 换算成秒；
* 解析失败时一次性收集全部问题，带音轨文件名与笔记序号，方便定位；
* 解析结果是一个可回写的纯数据模型，供 GUI 修改参数后另存。
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .effects import (
    EffectError,
    EffectSpec,
    effect_types_text,
    normalize_effect_params,
    resolve_effect,
)
from .notes import MIDI_MAX, MIDI_MIN, PitchError, midi_to_name, parse_pitches

PROJECT_SUFFIX = ".musicproj"
DEFAULT_BPM = 120.0
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_BEATS_PER_BAR = 4
VALID_SAMPLE_RATES = (22050, 32000, 44100, 48000, 88200, 96000)


class ProjectError(Exception):
    """工程文件存在无法继续渲染的错误。"""

    def __init__(self, issues: Iterable["Issue"]):
        self.issues = list(issues)
        super().__init__("\n".join(f"[{i.level}] {i.where}: {i.message}" for i in self.issues))


@dataclass
class Issue:
    level: str  # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        tag = "错误" if self.level == "error" else "警告"
        return f"{tag} | {self.where}: {self.message}"


@dataclass
class Note:
    """一个音符事件。``time``/``dur`` 单位为拍。"""

    time: float
    dur: float
    pitches: list[int]
    velocity: float = 0.8
    #: 弯音曲线 ``[(0..1 位置, 半音偏离), …]``。人声、弦乐、推弦不是一个音高按到底，
    #: 靠它把滑音与颤音写进工程；单音轨也可为空。
    bend: list[tuple[float, float]] | None = None
    #: 逐事件采样名（``samples/<name>.wav``）。有它就原样播放这个音自己的片段，
    #: 不经过任何音高判断——音高、起音、音色都是原的。
    sample: str | None = None
    #: 精确回放：片段在原分轨里的起音秒。有它时渲染端按秒定位，不经过
    #: 「秒→拍→秒」的 BPM 往返（XML 的 bpm 只有 6 位有效数字，长曲目会累积漂移）。
    src_time: float | None = None


@dataclass
class Track:
    name: str
    instrument: str = "piano"
    path: Path | None = None
    bpm: float | None = None          # None = 跟随工程 BPM
    gain_db: float = 0.0
    pan: float = 0.0
    reverb: float = 0.0
    loop: bool = False
    loop_len: float | None = None     # 循环周期（拍）
    loop_count: int = 0               # 0 = 一直循环到工程结束
    mute: bool = False
    solo: bool = False
    transpose: int = 0
    effects: list[EffectSpec] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    # 音频轨：直接引用磁盘上的分轨，渲染时原样混音（不做转谱）
    audio_path: Path | None = None
    audio_offset: float = 0.0       # 片段起点（秒）
    audio_gain_db: float = 0.0
    audio_duration: float = 0.0     # 片段时长（秒），解析时读头部填入
    # 合成音脚本：instrument="synth" 时由它提供音色；相对路径相对音轨文件
    patch_path: Path | None = None

    # ---- 派生量 -------------------------------------------------------
    @property
    def content_end_beats(self) -> float:
        if not self.notes:
            return 0.0
        return max(n.time + n.dur for n in self.notes)

    def period_beats(self, beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> float:
        """循环周期（拍）。未显式给出时向上取整到整小节。"""
        if not self.loop:
            return max(self.content_end_beats, 0.0)
        if self.loop_len:
            return float(self.loop_len)
        bars = max(1, math.ceil(self.content_end_beats / beats_per_bar))
        return float(bars * beats_per_bar)

    def resolve_bpm(self, project_bpm: float) -> float:
        return float(self.bpm if self.bpm else project_bpm)

    def note_times_seconds(self, project_bpm: float) -> list[tuple[float, float, list[int], float]]:
        """返回 [(起始秒, 时长秒, 音高列表, 力度)]，已展开循环。"""
        bpm = self.resolve_bpm(project_bpm)
        sec_per_beat = 60.0 / bpm
        return [
            (n.time * sec_per_beat, n.dur * sec_per_beat, n.pitches, n.velocity)
            for n in self.notes
        ]


@dataclass
class Project:
    name: str
    path: Path
    bpm: float = DEFAULT_BPM
    sample_rate: int = DEFAULT_SAMPLE_RATE
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR
    length_spec: str = "auto"
    tail_seconds: float = 2.0          # length="auto" 时在最后一个音符后留的余韵
    master_gain_db: float = 0.0
    normalize: bool = True
    #: 母带效果链（``<master>`` 下的 ``<effect>``，在总线上、归一化之前生效）
    master_effects: list[EffectSpec] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    # ---- 时长计算 -----------------------------------------------------
    def compute_length_seconds(self) -> float:
        spec = (self.length_spec or "auto").strip().lower()
        if spec and spec != "auto":
            if spec.endswith("ms"):
                return max(0.05, _parse_number(spec[:-2], "length") / 1000.0)
            if spec.endswith("s"):
                return max(0.05, _parse_number(spec[:-1], "length"))
            if spec.endswith("bar") or spec.endswith("bars"):
                bars = _parse_number(spec.replace("bar", "").replace("s", ""), "length")
                return max(0.05, bars * self.beats_per_bar * 60.0 / self.bpm)
            beats = _parse_beats(spec, "length")
            return max(0.05, beats * 60.0 / self.bpm)

        longest = 0.0
        for track in self.tracks:
            if track.audio_path is not None:
                longest = max(longest, track.audio_offset + max(0.0, track.audio_duration))
            if track.loop and track.loop_count <= 0:
                continue
            bpm = track.resolve_bpm(self.bpm)
            beats = track.period_beats(self.beats_per_bar)
            if track.loop and track.loop_count > 0:
                beats *= track.loop_count
            longest = max(longest, beats * 60.0 / bpm)
        if longest <= 0.0:
            # 全部是无限循环轨：默认取 4 小节长度
            longest = self.beats_per_bar * 4 * 60.0 / self.bpm
        return longest + max(0.0, self.tail_seconds)


# ---------------------------------------------------------------------------
# 解析工具
# ---------------------------------------------------------------------------


def _parse_number(text: str, what: str) -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{what} 不是有效数字: {text!r}") from None


def _parse_beats(text: str | None, what: str = "时值", default: float | None = None) -> float:
    """解析以「拍」为单位的数值，支持 ``1/4``、``0.5``、``3``、``2*4``。"""
    if text is None or str(text).strip() == "":
        if default is None:
            raise ValueError(f"{what} 缺失")
        return default
    raw = str(text).strip().lower().replace(" ", "")
    if "*" in raw:
        total = 1.0
        for part in raw.split("*"):
            total *= _parse_beats(part, what)
        return total
    if "/" in raw:
        left, _, right = raw.partition("/")
        num = _parse_number(left, what)
        den = _parse_number(right, what)
        if den == 0:
            raise ValueError(f"{what} 分母不能为 0: {text!r}")
        return num / den
    value = _parse_number(raw, what)
    if value < 0:
        raise ValueError(f"{what} 不能为负: {text!r}")
    return value


def _parse_seconds(text: str | None, what: str = "时间", default: float = 0.0) -> float:
    """解析秒数：``1.5`` / ``1500ms`` / ``1.5s``。"""
    if text is None or str(text).strip() == "":
        return default
    raw = str(text).strip().lower()
    if raw.endswith("ms"):
        return _parse_number(raw[:-2], what) / 1000.0
    if raw.endswith("s"):
        return _parse_number(raw[:-1], what)
    return _parse_number(raw, what)


def _parse_gain_db(text: str | None, default: float = 0.0) -> float:
    """解析增益：``-3dB`` / ``+2dB`` / ``-6`` / ``0``。"""
    if text is None or str(text).strip() == "":
        return default
    raw = str(text).strip().lower()
    if raw.endswith("db"):
        raw = raw[:-2]
    return _parse_number(raw, "增益")


def _parse_bool(text: str | None, default: bool = False) -> bool:
    if text is None:
        return default
    raw = str(text).strip().lower()
    if raw in ("1", "true", "yes", "on", "是", "循环"):
        return True
    if raw in ("0", "false", "no", "off", "否", ""):
        return False
    raise ValueError(f"布尔值无法识别: {text!r}")


def _parse_velocity(text: str | None, default: float = 0.8) -> float:
    """力度：0-1 小数，或 MIDI 风格 1-127 整数。"""
    if text is None or str(text).strip() == "":
        return default
    value = _parse_number(text, "力度")
    if value > 1.0:
        value = value / 127.0
    return min(1.0, max(0.0, value))


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------


def _parse_effect_element(el, label: str, issues: list[Issue]) -> "EffectSpec | None":
    """解析一个 ``<effect>`` 节点；参数越界只警告，类型未知直接跳过。"""
    raw_type = el.get("type")
    if not raw_type:
        issues.append(Issue("warning", label, "缺少 type 属性，已忽略"))
        return None
    key = resolve_effect(raw_type)
    if key is None:
        issues.append(
            Issue(
                "warning",
                label,
                f"未知音效 {raw_type!r}，已忽略（可用：{effect_types_text()}）",
            )
        )
        return None
    raw = {k: v for k, v in el.attrib.items() if k != "type"}
    try:
        params, problems = normalize_effect_params(key, raw)
    except EffectError as exc:
        issues.append(Issue("warning", label, str(exc)))
        return None
    for problem in problems:
        issues.append(Issue("warning", label, problem))
    return EffectSpec(key, params)


def _parse_track_file(path: Path, issues: list[Issue], defaults_bpm: float) -> Track:
    where = path.name
    try:
        root = ET.fromstring(path.read_bytes())
    except ET.ParseError as exc:
        issues.append(Issue("error", where, f"XML 解析失败: {exc}"))
        return Track(name=path.stem, path=path)
    if root.tag != "track":
        issues.append(Issue("error", where, f"根节点必须是 <track>，实际是 <{root.tag}>"))

    track = Track(
        name=root.get("name") or path.stem,
        instrument=(root.get("instrument") or "piano").strip().lower(),
        path=path,
    )
    for attr, setter in (
        ("gain", lambda v: setattr(track, "gain_db", v)),
        ("reverb", lambda v: setattr(track, "reverb", v)),
        ("pan", lambda v: setattr(track, "pan", v)),
        ("transpose", lambda v: setattr(track, "transpose", v)),
        ("loopCount", lambda v: setattr(track, "loop_count", v)),
    ):
        if root.get(attr) is not None:
            try:
                if attr == "gain":
                    setter(_parse_gain_db(root.get(attr)))
                elif attr == "loopCount":
                    setter(int(_parse_number(root.get(attr), attr)))
                elif attr == "pan":
                    setter(max(-1.0, min(1.0, _parse_number(root.get(attr), attr))))
                elif attr == "transpose":
                    setter(int(_parse_number(root.get(attr), attr)))
                else:
                    setter(max(0.0, min(1.0, _parse_number(root.get(attr), attr))))
            except ValueError as exc:
                issues.append(Issue("warning", where, f"属性 {attr} 无效，已用默认值: {exc}"))

    if root.get("patch"):
        from .patch import PatchError, load_patch

        try:
            track.patch_path = (path.parent / root.get("patch")).resolve()
            load_patch(track.patch_path)  # 载入一次，脚本坏了在打开工程时就报错
        except PatchError as exc:
            issues.append(Issue("error", where, f"合成音脚本无效: {exc}"))

    if root.get("bpm"):
        try:
            track.bpm = _parse_number(root.get("bpm"), "bpm")
            if track.bpm <= 0:
                raise ValueError("bpm 必须大于 0")
        except ValueError as exc:
            issues.append(Issue("warning", where, f"bpm 无效: {exc}"))
    if root.get("loop") is not None:
        try:
            track.loop = _parse_bool(root.get("loop"))
        except ValueError as exc:
            issues.append(Issue("warning", where, str(exc)))
    if root.get("loopLen") is not None:
        try:
            track.loop_len = _parse_beats(root.get("loopLen"), "loopLen")
        except ValueError as exc:
            issues.append(Issue("warning", where, str(exc)))
    for attr, fieldname in (("mute", "mute"), ("solo", "solo")):
        if root.get(attr) is not None:
            try:
                setattr(track, fieldname, _parse_bool(root.get(attr)))
            except ValueError as exc:
                issues.append(Issue("warning", where, str(exc)))

    # ---- 音符 ----
    pending_time = 0.0
    for index, el in enumerate(root):
        if el.tag == "audio":
            label = f"{where} 第 {index + 1} 个 <audio>"
            if track.audio_path is not None:
                issues.append(Issue("warning", label, "一个音轨只能有一个 <audio>，后者已忽略"))
                continue
            file_attr = el.get("file")
            if not file_attr:
                issues.append(Issue("error", label, "缺少 file 属性"))
                continue
            audio_path = (path.parent / file_attr).resolve()
            if not audio_path.is_file():
                issues.append(Issue("error", label, f"音频文件不存在: {file_attr}"))
                continue
            try:
                from .audioclip import AudioClipError, duration_seconds

                track.audio_duration = duration_seconds(audio_path)
                track.audio_offset = max(0.0, _parse_seconds(el.get("offset"), "offset"))
                track.audio_gain_db = _parse_gain_db(el.get("gain"))
                track.audio_path = audio_path
            except (AudioClipError, ValueError) as exc:
                issues.append(Issue("error", label, str(exc)))
            continue
        if el.tag == "effect":
            spec = _parse_effect_element(el, f"{where} 第 {index + 1} 个 <effect>", issues)
            if spec is not None:
                track.effects.append(spec)
            continue
        if el.tag != "note":
            if el.tag not in ("comment", "meta"):
                issues.append(Issue("warning", where, f"忽略未知节点 <{el.tag}>"))
            continue
        label = f"{where} 第 {index + 1} 个 <note>"
        try:
            if el.get("time") is not None:
                start = _parse_beats(el.get("time"), "time")
            else:
                start = pending_time
            dur = _parse_beats(el.get("dur"), f"{label} 的 dur")
            if dur <= 0:
                raise ValueError("dur 必须大于 0")
            pitch_text = el.get("pitch") or el.get("notes")
            if not pitch_text:
                raise ValueError("缺少 pitch 属性")
            pitches = parse_pitches(pitch_text)
            if track.transpose:
                pitches = [p + track.transpose for p in pitches]
            bad = [p for p in pitches if not MIDI_MIN <= p <= MIDI_MAX]
            if bad:
                raise ValueError(f"音高超出 0-127: {bad}")
            velocity = _parse_velocity(el.get("vel"), 0.8)
        except (ValueError, PitchError) as exc:
            issues.append(Issue("error", label, str(exc)))
            continue
        bend = _parse_bend(el.get("bend"), label, issues)
        try:
            src_time = float(el.get("srcTime")) if el.get("srcTime") is not None else None
        except ValueError:
            src_time = None
        track.notes.append(
            Note(time=start, dur=dur, pitches=pitches, velocity=velocity, bend=bend,
                 sample=(el.get("sample") or None), src_time=src_time)
        )
        pending_time = start + dur

    if track.loop:
        period = track.period_beats(DEFAULT_BEATS_PER_BAR) if track.loop_len else None
        if period:
            over = [n for n in track.notes if n.time >= period - 1e-9]
            if over:
                issues.append(
                    Issue(
                        "warning",
                        where,
                        f"{len(over)} 个音符起点超出循环周期 {period:g} 拍，不会被播放",
                    )
                )
    return track


def load_project(path: str | Path) -> Project:
    """载入工程。致命问题抛 :class:`ProjectError`。"""
    project_path = Path(path).expanduser().resolve()
    issues: list[Issue] = []
    if not project_path.is_file():
        raise ProjectError([Issue("error", str(project_path), "文件不存在")])
    if project_path.suffix.lower() != PROJECT_SUFFIX:
        issues.append(
            Issue("warning", project_path.name, f"扩展名不是 {PROJECT_SUFFIX}，仍按工程文件解析")
        )

    try:
        root = ET.fromstring(project_path.read_bytes())
    except ET.ParseError as exc:
        raise ProjectError([Issue("error", project_path.name, f"XML 解析失败: {exc}")]) from None

    if root.tag != "musicproj":
        issues.append(Issue("error", project_path.name, f"根节点必须是 <musicproj>，实际是 <{root.tag}>"))

    project = Project(
        name=root.get("name") or project_path.stem,
        path=project_path,
    )
    try:
        if root.get("bpm") is not None:
            project.bpm = _parse_number(root.get("bpm"), "bpm")
            if not 5 <= project.bpm <= 600:
                raise ValueError(f"bpm 超出合理范围 5-600: {project.bpm:g}")
        if root.get("sampleRate") is not None:
            rate = int(_parse_number(root.get("sampleRate"), "sampleRate"))
            if rate not in VALID_SAMPLE_RATES:
                issues.append(
                    Issue(
                        "warning",
                        project_path.name,
                        f"非常用采样率 {rate}，仍会使用（推荐 {'/'.join(map(str, VALID_SAMPLE_RATES))}）",
                    )
                )
            project.sample_rate = rate
        if root.get("beatsPerBar") is not None:
            project.beats_per_bar = max(1, int(_parse_number(root.get("beatsPerBar"), "beatsPerBar")))
        if root.get("masterGain") is not None:
            project.master_gain_db = _parse_gain_db(root.get("masterGain"))
        if root.get("normalize") is not None:
            project.normalize = _parse_bool(root.get("normalize"), True)
        master_el = root.find("master")
        if master_el is not None:
            for index, el in enumerate(master_el.findall("effect")):
                spec = _parse_effect_element(
                    el, f"{project_path.name} <master> 第 {index + 1} 个 <effect>", issues
                )
                if spec is not None:
                    project.master_effects.append(spec)
        if root.get("length") is not None:
            project.length_spec = root.get("length").strip()
        if root.get("tail") is not None:
            raw_tail = root.get("tail").strip().lower()
            if raw_tail.endswith("ms"):
                project.tail_seconds = _parse_number(raw_tail[:-2], "tail") / 1000.0
            elif raw_tail.endswith("s"):
                project.tail_seconds = _parse_number(raw_tail[:-1], "tail")
            else:
                project.tail_seconds = _parse_number(raw_tail, "tail")
            project.tail_seconds = max(0.0, project.tail_seconds)
    except ValueError as exc:
        issues.append(Issue("error", project_path.name, str(exc)))

    track_elements = [el for el in root if el.tag == "track"]
    if not track_elements:
        issues.append(Issue("error", project_path.name, "工程里没有任何 <track>"))

    for index, el in enumerate(track_elements):
        file_attr = el.get("file")
        if not file_attr:
            issues.append(
                Issue("error", f"{project_path.name} 第 {index + 1} 个 <track>", "缺少 file 属性")
            )
            continue
        track_path = (project.base_dir / file_attr).resolve()
        if not track_path.is_file():
            issues.append(
                Issue("error", project_path.name, f"音轨文件不存在: {file_attr}")
            )
            continue
        track = _parse_track_file(track_path, issues, project.bpm)
        # 工程层的覆盖属性
        if el.get("name"):
            track.name = el.get("name")
        if el.get("gain") is not None:
            try:
                track.gain_db = _parse_gain_db(el.get("gain"))
            except ValueError as exc:
                issues.append(Issue("warning", file_attr, f"gain 无效: {exc}"))
        if el.get("pan") is not None:
            try:
                track.pan = max(-1.0, min(1.0, _parse_number(el.get("pan"), "pan")))
            except ValueError as exc:
                issues.append(Issue("warning", file_attr, f"pan 无效: {exc}"))
        for attr in ("mute", "solo"):
            if el.get(attr) is not None:
                try:
                    setattr(track, attr, _parse_bool(el.get(attr)))
                except ValueError as exc:
                    issues.append(Issue("warning", file_attr, str(exc)))
        if el.get("loop") is not None:
            try:
                track.loop = _parse_bool(el.get("loop"))
            except ValueError as exc:
                issues.append(Issue("warning", file_attr, str(exc)))
        project.tracks.append(track)

    errors = [i for i in issues if i.level == "error"]
    project.warnings = [i for i in issues if i.level != "error"]
    if errors:
        raise ProjectError(issues)
    return project


# ---------------------------------------------------------------------------
# 回写（GUI 修改参数后另存）
# ---------------------------------------------------------------------------


def _format_bend(curve: list[tuple[float, float]]) -> str:
    """弯音曲线 → ``"0:0;0.5:-0.35;1:0"``。"""
    return ";".join(f"{float(pos):g}:{float(semitone):g}" for pos, semitone in curve)


def _parse_bend(text: str | None, label: str, issues: list[Issue]):
    """``"0:0;0.5:-0.35;1:0"`` → ``[(0.0, 0.0), (0.5, -0.35), (1.0, 0.0)]``。"""
    if not text:
        return None
    curve: list[tuple[float, float]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 2:
            issues.append(Issue("warning", label, f"弯音 {chunk!r} 格式不对，已忽略该点"))
            continue
        try:
            position = float(parts[0])
            semitone = float(parts[1])
        except ValueError:
            issues.append(Issue("warning", label, f"弯音 {chunk!r} 不是数字，已忽略该点"))
            continue
        curve.append((max(0.0, min(1.0, position)), semitone))
    if len(curve) < 2:
        return None
    return sorted(curve)


def save_track(track: Track, path: Path | None = None) -> Path:
    """把音轨模型写回 *track.xml*（格式会规范化）。"""
    target = Path(path or track.path or f"{track.name}.track.xml")
    root = ET.Element(
        "track",
        {
            "name": track.name,
            "instrument": track.instrument,
            "gain": f"{track.gain_db:+g}dB",
            "pan": f"{track.pan:g}",
            "reverb": f"{track.reverb:g}",
        },
    )
    if track.bpm:
        root.set("bpm", f"{track.bpm:g}")
    if track.loop:
        root.set("loop", "true")
        if track.loop_len:
            root.set("loopLen", f"{track.loop_len:g}")
        if track.loop_count:
            root.set("loopCount", str(track.loop_count))
    if track.mute:
        root.set("mute", "true")
    if track.solo:
        root.set("solo", "true")
    if track.transpose:
        root.set("transpose", str(track.transpose))
    if track.patch_path is not None:
        import os

        try:
            relative = os.path.relpath(track.patch_path, target.parent).replace("\\", "/")
        except ValueError:
            relative = str(track.patch_path)
        root.set("patch", relative)
    for spec in track.effects:
        ET.SubElement(root, "effect", spec.to_attrs())
    if track.audio_path is not None:
        import os

        try:
            relative = os.path.relpath(track.audio_path, target.parent).replace("\\", "/")
        except ValueError:
            relative = str(track.audio_path)
        ET.SubElement(
            root,
            "audio",
            {
                "file": relative,
                "offset": f"{track.audio_offset:.12g}",
                "gain": f"{track.audio_gain_db:+g}dB",
            },
        )
    for note in track.notes:
        pitches = ",".join(midi_to_name(p) for p in note.pitches)
        ET.SubElement(
            root,
            "note",
            {
                # 12 位有效数字：1/3 拍的三连音不能被 %g 截成 0.333333
                "time": f"{note.time:.12g}",
                "dur": f"{note.dur:.12g}",
                "pitch": pitches,
                "vel": f"{note.velocity:g}",
                **({"bend": _format_bend(note.bend)} if note.bend else {}),
                **({"sample": note.sample} if note.sample else {}),
                **({"srcTime": f"{note.src_time:.9f}"}
                   if note.src_time is not None else {}),
            },
        )
    ET.indent(root, space="  ")
    target.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n",
        encoding="utf-8",
    )
    return target


def describe(project: Project) -> str:
    """生成工程概要文本（CLI ``info`` 与 GUI 共用）。"""
    length = project.compute_length_seconds()
    lines = [
        f"工程: {project.name}",
        f"文件: {project.path}",
        f"BPM: {project.bpm:g} | 采样率: {project.sample_rate} | "
        f"拍号: {project.beats_per_bar}/4 | 时长: {length:.2f}s | "
        f"母带: {project.master_gain_db:+g}dB | 归一化: {'开' if project.normalize else '关'}",
        f"音轨: {len(project.tracks)}",
    ]
    if project.master_effects:
        lines.append("  母带效果: " + " → ".join(spec.label() for spec in project.master_effects))
    for i, track in enumerate(project.tracks, 1):
        bpm = track.resolve_bpm(project.bpm)
        if track.audio_path is not None:
            lines.append(
                f"  {i}. {track.name} [音频] {track.audio_duration:.2f}s | "
                f"{track.gain_db:+g}dB | pan {track.pan:+.2f} | "
                f"文件 {track.audio_path.name}"
            )
            continue
        flags = []
        if track.mute:
            flags.append("静音")
        if track.solo:
            flags.append("独奏")
        if track.effects:
            flags.append("音效 " + " → ".join(spec.label() for spec in track.effects))
        if track.loop:
            period = track.period_beats(project.beats_per_bar)
            times = "无限" if track.loop_count <= 0 else f"{track.loop_count} 次"
            flags.append(f"循环 {period:g}拍×{times}")
        lines.append(
            f"  {i}. {track.name} [{track.instrument}] 音符 {len(track.notes)} "
            f"| {bpm:g}BPM | {track.gain_db:+g}dB | pan {track.pan:+.2f} | "
            f"混响 {track.reverb:.2f}" + (f" | {'、'.join(flags)}" if flags else "")
        )
    for warning in project.warnings:
        lines.append(f"  ! {warning}")
    return "\n".join(lines)


def clear_track_overrides(project_path: Path, track_path: Path | None,
                          *, include_name: bool = False) -> bool:
    """删除工程文件里针对某条音轨的参数覆盖，返回是否有改动。

    GUI 保存音轨后调用：否则下次载入会被工程层的旧 gain/pan 覆盖回去。
    用保留注释的解析器读写，避免破坏工程的注释。
    ``include_name=True`` 时连工程层的 ``name`` 覆盖一起清掉（CLI 改音轨名时用）。
    """
    if track_path is None or not Path(project_path).is_file():
        return False
    project_path = Path(project_path)
    track_path = Path(track_path)
    try:
        relative = track_path.relative_to(project_path.parent).as_posix()
    except ValueError:
        import os

        relative = os.path.relpath(track_path, project_path.parent).replace("\\", "/")

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        tree = ET.parse(str(project_path), parser=parser)
    except ET.ParseError:
        return False

    changed = False
    for element in tree.getroot():
        if element.tag != "track":
            continue
        file_attr = (element.get("file") or "").replace("\\", "/")
        if file_attr == relative:
            attrs = ["gain", "pan", "mute", "solo", "loop"]
            if include_name:
                attrs.append("name")
            for attr in attrs:
                if element.get(attr) is not None:
                    del element.attrib[attr]
                    changed = True
    if changed:
        tree.write(str(project_path), encoding="utf-8", xml_declaration=True)
    return changed


TEMPLATE_PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<musicproj name="{name}" bpm="{bpm}" sampleRate="{sample_rate}" beatsPerBar="{beats_per_bar}"
           length="auto" masterGain="-1dB" normalize="true">
  <!-- 每条音轨对应 tracks/ 下的一个文件 -->
  <track file="tracks/lead.track.xml"/>
  <track file="tracks/pad.track.xml" gain="-3dB" pan="0.15"/>
</musicproj>
"""

TEMPLATE_EMPTY_PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<musicproj name="{name}" bpm="{bpm}" sampleRate="{sample_rate}" beatsPerBar="{beats_per_bar}"
           length="auto" masterGain="-1dB" normalize="true">
  <track file="tracks/track1.track.xml"/>
</musicproj>
"""

TEMPLATE_EMPTY_TRACK = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Track 1" instrument="piano" gain="0dB" pan="0" reverb="0.2">
  <!-- 在这里写音符：time 起点、dur 时值（单位都是“拍”），pitch 用音名，
       多个音名用逗号分隔就是和弦，vel 是 0-1 的力度。例如：
  <note time="0" dur="1" pitch="C4,E4,G4" vel="0.7"/>
  <note time="1" dur="1" pitch="D4" vel="0.6"/>
  -->
</track>
"""

TEMPLATE_LEAD = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Lead" instrument="piano" gain="-2dB" pan="0" reverb="0.18">
  <!-- 时值单位是“拍”，支持 1/4 这种分数写法 -->
  <note time="0"  dur="0.5" pitch="E5"      vel="0.78"/>
  <note time="0.5" dur="0.5" pitch="G5"     vel="0.70"/>
  <note time="1"  dur="1"   pitch="C6,E5,G5" vel="0.72"/>
  <note time="2"  dur="0.5" pitch="B5"      vel="0.66"/>
  <note time="2.5" dur="0.5" pitch="G5"     vel="0.62"/>
  <note time="3"  dur="1"   pitch="A5,E5,C5" vel="0.70"/>
</track>
"""

TEMPLATE_PAD = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Pad" instrument="piano" loop="true" loopLen="4" gain="-6dB"
       pan="-0.2" reverb="0.35">
  <!-- loop=true 的音轨会在工程时长内不断重复，每 4 拍一轮 -->
  <note time="0"    dur="3.5" pitch="C3,G3"  vel="0.42"/>
  <note time="0"    dur="0.4" pitch="C4"     vel="0.34"/>
  <note time="0.95" dur="0.4" pitch="C4"     vel="0.30"/>
  <note time="2.5"  dur="0.4" pitch="C4"     vel="0.32"/>
  <note time="3.45" dur="0.4" pitch="D4"     vel="0.28"/>
</track>
"""




def create_project(
    directory: Path,
    name: str | None = None,
    force: bool = False,
    bpm: float = 100.0,
    sample_rate: int = 44100,
    beats_per_bar: int = 4,
    template: str = "demo",
) -> Path:
    """在 *directory* 下生成一个可直接渲染的工程，返回工程文件路径。

    ``template``：``demo``＝两轨示例（旋律 + 循环音型），``empty``＝单条空音轨。
    """
    if template not in ("demo", "empty"):
        raise ValueError(f"未知模板：{template}")
    if not 5 <= float(bpm) <= 600:
        raise ValueError(f"速度超出范围 5-600：{bpm}")
    if int(sample_rate) not in VALID_SAMPLE_RATES:
        raise ValueError(f"不支持的采样率：{sample_rate}")
    if int(beats_per_bar) not in (2, 3, 4, 6):
        raise ValueError(f"不支持的拍号：{beats_per_bar}/4")

    target = Path(directory)
    project_name = name or target.name or "新工程"
    slug = target.name or "demo"
    project_file = target / f"{slug}.musicproj"
    if project_file.exists() and not force:
        raise FileExistsError(f"目标已存在：{project_file}")
    (target / "tracks").mkdir(parents=True, exist_ok=True)

    fmt = {
        "name": project_name,
        "bpm": f"{float(bpm):g}",
        "sample_rate": int(sample_rate),
        "beats_per_bar": int(beats_per_bar),
    }
    if template == "empty":
        project_file.write_text(TEMPLATE_EMPTY_PROJECT.format(**fmt), encoding="utf-8")
        (target / "tracks" / "track1.track.xml").write_text(TEMPLATE_EMPTY_TRACK, encoding="utf-8")
    else:
        project_file.write_text(TEMPLATE_PROJECT.format(**fmt), encoding="utf-8")
        (target / "tracks" / "lead.track.xml").write_text(TEMPLATE_LEAD, encoding="utf-8")
        (target / "tracks" / "pad.track.xml").write_text(TEMPLATE_PAD, encoding="utf-8")
    return project_file
