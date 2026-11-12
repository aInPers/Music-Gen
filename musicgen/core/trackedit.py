"""工程与音轨的编辑操作（CLI 与 GUI 共用）。

设计要点：

* **原地编辑、保留注释**：已有工程/音轨文件用 ``insert_comments`` 解析器改写，
  只动指定属性或指定 ``<note>`` 节点，不整体重建 XML。手写工程里的小节注释、
  排版和说明都不会被冲掉（``save_track()`` 会规范化格式，不适合这里）。
* **工程层覆盖**：``edit_track`` 改完音轨文件后会清除工程文件里针对该轨的
  参数覆盖，否则下次载入会被旧的 ``gain``/``pan`` 盖回去。
* 新增轨道会同时写 ``tracks/*.track.xml`` 并向 ``*.musicproj`` 追加一条 ``<track>`` 引用。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from typing import Any, Iterable

from .effects import (
    EffectError,
    EffectSpec,
    effect_class,
    effect_types_text,
    normalize_effect_params,
    resolve_effect,
)
from .notes import midi_to_name, parse_pitches
from .project import (
    Note,
    Project,
    ProjectError,
    Track,
    _parse_beats,  # noqa: PLC2701 - 同包复用，保证时值语义与载入时完全一致
    clear_track_overrides,
    load_project,
)

#: ``**changes`` 里出现这个哨兵表示「不要改这一项」；``None`` 表示「删掉该属性」。
MISSING = object()

TRACK_HINT = (
    " 音符：time 起点、dur 时值（单位都是「拍」，支持 1/3 这类分数），"
    "pitch 用音名，多个音名用逗号分隔就是和弦，vel 是 0-1 的力度。\n"
    '   例如 <note time="0" dur="1" pitch="C4,E4,G4" vel="0.7"/>。\n'
    "   音效写在音符前面，按顺序串联：<effect type=\"echo\" time=\"320\" mix=\"0.3\"/>，"
    "可用类型见 musicgen effect types。\n"
    "   也可以用 musicgen 的 note / effect 命令编辑，用法见 docs/CLI文档.md "
)

#: 模型字段 -> (XML 属性名, 值格式化函数)
_TRACK_ATTRS: dict[str, tuple[str, Any]] = {
    "name": ("name", lambda v: str(v)),
    "instrument": ("instrument", lambda v: str(v).strip().lower()),
    "gain_db": ("gain", lambda v: f"{float(v):+g}dB"),
    "pan": ("pan", lambda v: f"{float(v):g}"),
    "reverb": ("reverb", lambda v: f"{float(v):g}"),
    "bpm": ("bpm", lambda v: f"{float(v):g}"),
    "loop_len": ("loopLen", lambda v: f"{float(v):g}"),
    "loop_count": ("loopCount", lambda v: str(int(v))),
    "transpose": ("transpose", lambda v: str(int(v))),
    "loop": ("loop", lambda v: "true" if v else None),
    "mute": ("mute", lambda v: "true" if v else None),
    "solo": ("solo", lambda v: "true" if v else None),
}


class TrackEditError(Exception):
    """参数不合法、找不到轨道或文件读写失败。"""


@dataclass
class TrackRef:
    """一次新增/edit 操作的结果。"""

    index: int            # 1-based
    name: str
    instrument: str
    path: Path
    project_path: Path
    relative: str

    @property
    def file_label(self) -> str:
        return self.relative


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def parse_beats(text: str | float, what: str = "时值") -> float:
    """公开给 CLI 的时值解析（``1/3``、``0.5``、``2*4`` 都认）。"""
    try:
        return _parse_beats(str(text), what)
    except ValueError as exc:
        raise TrackEditError(str(exc)) from None


def parse_gain(text: str | float) -> float:
    """增益：``-3dB`` / ``+2`` / ``0``。"""
    raw = str(text).strip().lower()
    if raw.endswith("db"):
        raw = raw[:-2]
    try:
        return float(raw)
    except ValueError:
        raise TrackEditError(f"增益不是数字：{text}") from None


def parse_pitch_list(text: str) -> list[int]:
    """音高列表：``C4,E4`` / ``kick,snare`` / ``60``。"""
    try:
        pitches = parse_pitches(text)
    except Exception as exc:  # noqa: BLE001 - PitchError 及解析异常统一转友好错误
        raise TrackEditError(f"音高无法解析：{text}（{exc}）") from None
    if not pitches:
        raise TrackEditError(f"音高为空：{text}")
    return pitches


def _tree(path: Path) -> ET.ElementTree:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        return ET.parse(str(path), parser=parser)
    except (ET.ParseError, OSError) as exc:
        raise TrackEditError(f"无法读取 {path.name}：{exc}") from None


def _write(tree: ET.ElementTree, path: Path) -> None:
    try:
        tree.write(str(path), encoding="utf-8", xml_declaration=True)
    except OSError as exc:
        raise TrackEditError(f"无法写入 {path.name}：{exc}") from None


def _load(path: str | Path) -> Project:
    try:
        return load_project(path)
    except ProjectError as exc:
        detail = "；".join(str(issue) for issue in exc.issues[:6])
        raise TrackEditError(f"工程有问题：{detail}") from None


def _children(root: ET.Element, tag: str) -> list[ET.Element]:
    return [el for el in root if el.tag == tag]


def _indent_of(root: ET.Element, siblings: list[ET.Element], index: int) -> str:
    """从周围节点推断缩进，新插入的节点才不会顶到行首。"""
    text = root.text or ""
    if "\n" in text:
        return text.rsplit("\n", 1)[1]
    for el in siblings[max(0, index - 2):index]:
        tail = el.tail or ""
        if "\n" in tail:
            return tail.rsplit("\n", 1)[1]
    return "  "


def _relative(path: Path, project_path: Path) -> str:
    try:
        return path.resolve().relative_to(project_path.parent.resolve()).as_posix()
    except ValueError:
        import os

        return os.path.relpath(path, project_path.parent).replace("\\", "/")


def _slug(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", str(name).strip())
    cleaned = cleaned.strip("._") or "track"
    return cleaned[:48]


def _note_pitches(pitches: Iterable[int], instrument: str) -> str:
    """鼓轨写鼓件名，其余乐器写音名（``midi_to_name``）。"""
    try:  # 延迟导入，避免与 core 包的其它模块形成导入环
        from ..samples import fetch

        if fetch.resolve(instrument) in fetch.BUILTIN:
            from .drums import pad_label

            return ",".join(pad_label(p) for p in pitches)
    except Exception:  # noqa: BLE001 - 兜底成音名即可
        pass
    return ",".join(midi_to_name(p) for p in pitches)


# ---------------------------------------------------------------------------
# 定位轨道
# ---------------------------------------------------------------------------


def find_track(project: Project, selector: str | int) -> tuple[int, Track]:
    """按 1-based 序号 / 名称 / 文件名定位，返回 ``(0-based index, Track)``。"""
    raw = str(selector).strip()
    tracks = project.tracks
    if not tracks:
        raise TrackEditError("工程里没有音轨")
    if raw.isdigit():
        index = int(raw) - 1
        if not 0 <= index < len(tracks):
            raise TrackEditError(f"音轨序号超出范围 1-{len(tracks)}：{raw}")
        return index, tracks[index]

    lowered = raw.lower()
    for index, track in enumerate(tracks):
        if track.name.lower() == lowered:
            return index, track
    for index, track in enumerate(tracks):
        filename = (track.path.name if track.path else "").lower()
        if filename == lowered or (track.path and track.path.stem.lower() == lowered):
            return index, track
    matched = [i for i, track in enumerate(tracks)
               if lowered in track.name.lower()
               or (track.path and lowered in track.path.name.lower())]
    if len(matched) == 1:
        return matched[0], tracks[matched[0]]
    if len(matched) > 1:
        names = "、".join(f"{i + 1}.{tracks[i].name}" for i in matched[:6])
        raise TrackEditError(f"「{raw}」匹配到多条音轨，请用序号或完整名称：{names}")
    available = "、".join(f"{i + 1}.{track.name}" for i, track in enumerate(tracks))
    raise TrackEditError(f"找不到音轨「{raw}」。现有：{available}")


def _ref(project: Project, index: int, path: Path, project_path: Path) -> TrackRef:
    track = project.tracks[index]
    return TrackRef(
        index=index + 1,
        name=track.name,
        instrument=track.instrument,
        path=path,
        project_path=project_path,
        relative=_relative(path, project_path),
    )


# ---------------------------------------------------------------------------
# 新增 / 编辑 / 删除轨道
# ---------------------------------------------------------------------------


def add_track(
    project_path: str | Path,
    *,
    name: str,
    instrument: str = "piano",
    gain_db: float = 0.0,
    pan: float = 0.0,
    reverb: float = 0.2,
    loop: bool = False,
    loop_len: float | None = None,
    bpm: float | None = None,
    notes: list[tuple[float, float, list[int], float]] | None = None,
    file: str | None = None,
    force: bool = False,
) -> TrackRef:
    """新增一条音轨：写轨道文件 + 在 musicproj 里追加引用，返回新轨道信息。"""
    project_path = Path(project_path).expanduser().resolve()
    project = _load(project_path)
    name = str(name).strip()
    if not name:
        raise TrackEditError("音轨名称不能为空")

    if file:
        target = (project_path.parent / file).resolve()
        if project_path.parent.resolve() not in target.parents:
            raise TrackEditError("音轨文件必须放在工程目录内")
    else:
        tracks_dir = project_path.parent / "tracks"
        stem = _slug(name)
        target = tracks_dir / f"{stem}.track.xml"
        if target.exists() and not force:
            counter = 2
            while target.exists():
                target = tracks_dir / f"{stem}_{counter}.track.xml"
                counter += 1
    if target.exists() and not force:
        raise TrackEditError(f"音轨文件已存在：{target}（加 --force 覆盖）")
    target.parent.mkdir(parents=True, exist_ok=True)

    attrs: dict[str, str] = {
        "name": name,
        "instrument": str(instrument).strip().lower(),
        "gain": f"{float(gain_db):+g}dB",
        "pan": f"{float(pan):g}",
        "reverb": f"{float(reverb):g}",
    }
    if bpm:
        attrs["bpm"] = f"{float(bpm):g}"
    if loop:
        attrs["loop"] = "true"
        if loop_len:
            attrs["loopLen"] = f"{float(loop_len):g}"

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append("<track " + " ".join(f'{k}="{escape(v, quote=True)}"' for k, v in attrs.items()) + ">")
    lines.append(f"  <!--{TRACK_HINT}-->")
    for time, dur, pitches, velocity in notes or []:
        lines.append(
            f'  <note time="{float(time):g}" dur="{float(dur):g}" '
            f'pitch="{escape(_note_pitches(pitches, instrument), quote=True)}" '
            f'vel="{float(velocity):g}"/>'
        )
    lines.append("</track>")
    try:
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise TrackEditError(f"无法写入音轨文件：{exc}") from None

    relative = _relative(target, project_path)
    tree = _tree(project_path)
    root = tree.getroot()
    existing = _children(root, "track")
    element = ET.Element("track", {"file": relative})
    if existing:
        last = existing[-1]
        indent = _indent_of(root, list(root), len(list(root)))
        last.tail = "\n" + indent
        element.tail = "\n"
        root.insert(list(root).index(last) + 1, element)
    else:
        element.tail = "\n"
        root.append(element)
    _write(tree, project_path)

    updated = _load(project_path)
    return _ref(updated, len(updated.tracks) - 1, target, project_path)


def edit_track(
    project_path: str | Path,
    selector: str | int,
    **changes: Any,
) -> tuple[TrackRef, list[tuple[str, str, str]]]:
    """修改音轨根属性，返回 ``(轨道信息, [(字段, 旧值, 新值)...])``。

    ``changes`` 只放要改的字段；值为 ``None`` 表示删除对应 XML 属性。
    """
    project_path = Path(project_path).expanduser().resolve()
    project = _load(project_path)
    index, track = find_track(project, selector)
    if track.path is None:
        raise TrackEditError("这条音轨没有对应文件")
    unknown = set(changes) - set(_TRACK_ATTRS)
    if unknown:
        raise TrackEditError(f"不支持修改的字段：{'、'.join(sorted(unknown))}")

    tree = _tree(track.path)
    root = tree.getroot()
    if root.tag != "track":
        raise TrackEditError(f"{track.path.name} 的根节点不是 <track>")

    applied: list[tuple[str, str, str]] = []
    for field, value in changes.items():
        if value is MISSING:
            continue
        attr_name, formatter = _TRACK_ATTRS[field]
        old = root.get(attr_name) or "（默认）"
        new_value = None if value is None else formatter(value)
        if new_value is None or (field in ("transpose", "loop_count") and str(value) in ("0", "0.0")):
            root.attrib.pop(attr_name, None)
            shown = "（删除）"
        else:
            root.set(attr_name, str(new_value))
            shown = str(new_value)
        applied.append((field, old, shown))
    _write(tree, track.path)
    clear_track_overrides(project_path, track.path, include_name="name" in changes)

    updated = _load(project_path)
    return _ref(updated, index, track.path, project_path), applied


def remove_track(
    project_path: str | Path,
    selector: str | int,
    *,
    delete_file: bool = False,
) -> tuple[str, str, bool]:
    """移除工程里的音轨引用，返回 ``(名称, 相对路径, 是否删了文件)``。默认保留文件。"""
    project_path = Path(project_path).expanduser().resolve()
    project = _load(project_path)
    _, track = find_track(project, selector)
    if len(project.tracks) <= 1:
        raise TrackEditError("至少要保留一条音轨；需要空工程请用 `init --template empty`")
    if track.path is None:
        raise TrackEditError("这条音轨没有对应文件")
    relative = _relative(track.path, project_path)

    tree = _tree(project_path)
    root = tree.getroot()
    children = list(root)
    hit = None
    for el in _children(root, "track"):
        if (el.get("file") or "").replace("\\", "/") == relative:
            hit = el
            break
    if hit is None:
        raise TrackEditError(f"工程里找不到音轨引用：{relative}")
    position = children.index(hit)
    if position > 0 and hit.tail:
        children[position - 1].tail = hit.tail
    root.remove(hit)
    _write(tree, project_path)

    deleted = False
    if delete_file and track.path.is_file():
        try:
            track.path.unlink()
            deleted = True
        except OSError as exc:
            raise TrackEditError(f"音轨引用已移除，但文件删除失败：{exc}") from None
    return track.name, relative, deleted


def isolate_project(project: Project, index: int) -> tuple[int, Project]:
    """构造一个**只含指定音轨**的工程副本，用于分轨试听 / 导出（不写任何文件）。

    ``index`` 是 **0-based**（与 GUI 的 ``selected`` / ``Track`` 下标一致，
    注意与 ``find_track`` 的 1-based 字符串序号不同）。返回 ``(index, 新工程)``。
    副本里该轨的 ``mute`` / ``solo`` 会清零，否则渲染器会把它过滤掉；
    原工程对象与其音轨都不会被修改。
    """
    if not 0 <= index < len(project.tracks):
        raise TrackEditError(f"音轨下标超出范围 0-{len(project.tracks) - 1}：{index}")
    single = replace(project.tracks[index], mute=False, solo=False)
    return index, replace(project, tracks=[single], warnings=[])


# ---------------------------------------------------------------------------
# 效果链（直接改 <effect> 节点，保留注释与排版）
# ---------------------------------------------------------------------------


def _effect_tree(project_path: str | Path, selector: str | int):
    project = _load(project_path)
    _, track = find_track(project, selector)
    if track.path is None:
        raise TrackEditError("这条音轨没有对应文件")
    tree = _tree(track.path)
    root = tree.getroot()
    if root.tag != "track":
        raise TrackEditError(f"{track.path.name} 的根节点不是 <track>")
    return tree, root, track


def _insert_effect(root: ET.Element, element: ET.Element) -> None:
    """把 <effect> 插到第一个 <note> 前面（没有音符就追加到末尾）。"""
    children = list(root)
    notes = [el for el in children if el.tag == "note"]
    if notes:
        target = children.index(notes[0])
        indent = _indent_of(root, children, target)
        element.tail = "\n" + indent
        root.insert(target, element)
        return
    if children:
        children[-1].tail = "\n" + _indent_of(root, children, len(children))
    element.tail = "\n"
    root.append(element)


def add_effect(
    project_path: str | Path,
    selector: str | int,
    type_name: str,
    params: dict[str, Any] | None = None,
) -> tuple[TrackRef, EffectSpec]:
    """给音轨追加一个效果，返回 ``(轨道信息, 效果条目)``。"""
    project_path = Path(project_path).expanduser().resolve()
    index, track = find_track(_load(project_path), selector)
    if track.path is None:
        raise TrackEditError("这条音轨没有对应文件")
    key = resolve_effect(type_name)
    if key is None:
        raise TrackEditError(f"未知音效「{type_name}」。可用：{effect_types_text()}")
    try:
        values, _ = normalize_effect_params(key, params or {}, strict=True)
    except EffectError as exc:
        raise TrackEditError(str(exc)) from None

    spec = EffectSpec(key, values)
    tree = _tree(track.path)
    root = tree.getroot()
    _insert_effect(root, ET.Element("effect", spec.to_attrs()))
    _write(tree, track.path)
    updated = _load(project_path)
    return _ref(updated, index, track.path, project_path), spec


def set_effects(
    project_path: str | Path,
    selector: str | int,
    specs: list,
) -> TrackRef:
    """整条替换音轨的效果链（按给定顺序写入，用于应用音效脚本）。"""
    project_path = Path(project_path).expanduser().resolve()
    index, track = find_track(_load(project_path), selector)
    if track.path is None:
        raise TrackEditError("这条音轨没有对应文件")
    tree = _tree(track.path)
    root = tree.getroot()
    for child in list(root):
        if child.tag == "effect":
            root.remove(child)
    position = len(root)
    for offset, child in enumerate(root):
        if child.tag != "effect":
            position = offset
            break
    for step, spec in enumerate(specs):
        root.insert(position + step, ET.Element("effect", spec.to_attrs()))
    _write(tree, track.path)
    updated = _load(project_path)
    return _ref(updated, index, track.path, project_path)


def set_effect(
    project_path: str | Path,
    selector: str | int,
    position: int,
    **params: Any,
) -> list[tuple[str, str, str]]:
    """改第 *position* 个效果（1-based）的参数，返回 ``[(参数, 旧值, 新值)]``。

    值为 ``None`` 表示删除该属性（回到注册表默认值）。
    """
    tree, root, track = _effect_tree(project_path, selector)
    effects = _children(root, "effect")
    if not 1 <= position <= len(effects):
        raise TrackEditError(f"音效序号超出范围 1-{len(effects)}：{position}")
    element = effects[position - 1]
    key = resolve_effect(element.get("type"))
    if key is None:
        raise TrackEditError(f"第 {position} 个效果的 type 无法识别：{element.get('type')!r}")
    cls = effect_class(key)

    applied: list[tuple[str, str, str]] = []
    for name, value in params.items():
        param = cls.param(name)
        if param is None:
            raise TrackEditError(
                f"音效「{cls.LABEL}」没有参数 {name}（可用：{'、'.join(cls.param_keys())}）"
            )
        old = element.get(name) or "（默认）"
        if value is None:
            element.attrib.pop(name, None)
            applied.append((name, old, "（删除）"))
            continue
        try:
            parsed, clamped = param.coerce(value)
        except EffectError as exc:
            raise TrackEditError(str(exc)) from None
        if clamped:
            raise TrackEditError(f"参数 {name} 超出范围 {param.range_text()}")
        element.set(name, param.format(parsed))
        applied.append((name, old, param.display(parsed)))
    _write(tree, track.path)  # type: ignore[arg-type] - _effect_tree 已保证 path 非空
    return applied


def remove_effect(
    project_path: str | Path, selector: str | int, position: int
) -> dict[str, str]:
    """删掉第 *position* 个效果（1-based），返回它原来的属性。"""
    tree, root, track = _effect_tree(project_path, selector)
    effects = _children(root, "effect")
    if not 1 <= position <= len(effects):
        raise TrackEditError(f"音效序号超出范围 1-{len(effects)}：{position}")
    element = effects[position - 1]
    attributes = dict(element.attrib)
    siblings = list(root)
    place = siblings.index(element)
    if place > 0 and element.tail:
        siblings[place - 1].tail = element.tail
    root.remove(element)
    _write(tree, track.path)  # type: ignore[arg-type] - _effect_tree 已保证 path 非空
    return attributes


# ---------------------------------------------------------------------------
# 音符编辑（直接改 <note> 节点，保留注释与排版）
# ---------------------------------------------------------------------------


def list_notes(project_path: str | Path, selector: str | int) -> list[tuple[int, Note]]:
    """返回 ``[(1-based 序号, Note)]``。"""
    project = _load(project_path)
    _, track = find_track(project, selector)
    return list(enumerate(track.notes, start=1))


def _note_tree(project_path: str | Path, selector: str | int):
    project = _load(project_path)
    _, track = find_track(project, selector)
    if track.path is None:
        raise TrackEditError("这条音轨没有对应文件")
    tree = _tree(track.path)
    root = tree.getroot()
    return tree, root, track


def _note_attribs(
    *, time: float, dur: float, pitches: Iterable[int], velocity: float, instrument: str
) -> dict[str, str]:
    return {
        "time": f"{float(time):g}",
        "dur": f"{float(dur):g}",
        "pitch": _note_pitches(pitches, instrument),
        "vel": f"{float(velocity):g}",
    }


def add_note(
    project_path: str | Path,
    selector: str | int,
    *,
    time: float,
    dur: float,
    pitches: Iterable[int],
    velocity: float = 0.8,
) -> int:
    """追加一个音符，返回它的 1-based 序号。"""
    tree, root, track = _note_tree(project_path, selector)
    element = ET.Element("note", _note_attribs(
        time=time, dur=dur, pitches=pitches, velocity=velocity, instrument=track.instrument
    ))
    existing = _children(root, "note")
    if existing:
        last = existing[-1]
        siblings = list(root)
        indent = _indent_of(root, siblings, siblings.index(last))
        last.tail = "\n" + indent
        element.tail = "\n"
        root.insert(siblings.index(last) + 1, element)
    else:
        element.tail = "\n"
        root.append(element)
    _write(tree, track.path)
    return len(existing) + 1


def set_note(
    project_path: str | Path,
    selector: str | int,
    index: int,
    **changes: Any,
) -> list[tuple[str, str, str]]:
    """改第 *index* 个音符（1-based），返回改动列表。"""
    tree, root, track = _note_tree(project_path, selector)
    notes = _children(root, "note")
    if not 1 <= index <= len(notes):
        raise TrackEditError(f"音符序号超出范围 1-{len(notes)}：{index}")
    element = notes[index - 1]
    applied: list[tuple[str, str, str]] = []
    for field, value in changes.items():
        if value is MISSING:
            continue
        if field == "pitches":
            key = "pitch"
            new_value = _note_pitches(value, track.instrument)
        elif field in ("time", "dur", "velocity"):
            key = "vel" if field == "velocity" else field
            new_value = f"{float(value):g}"
        else:
            raise TrackEditError(f"不支持修改的音符字段：{field}")
        old = element.get(key) or "（默认）"
        element.set(key, new_value)
        applied.append((field, old, new_value))
    _write(tree, track.path)
    return applied


def delete_note(project_path: str | Path, selector: str | int, index: int) -> dict[str, str]:
    """删第 *index* 个音符（1-based），返回被删音符的属性。"""
    tree, root, track = _note_tree(project_path, selector)
    notes = _children(root, "note")
    if not 1 <= index <= len(notes):
        raise TrackEditError(f"音符序号超出范围 1-{len(notes)}：{index}")
    element = notes[index - 1]
    attributes = dict(element.attrib)
    siblings = list(root)
    position = siblings.index(element)
    if position > 0 and element.tail:
        siblings[position - 1].tail = element.tail
    root.remove(element)
    _write(tree, track.path)  # type: ignore[arg-type] - 上面已保证 path 非空
    return attributes
