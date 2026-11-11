"""CLI 的 ``track`` / ``note`` 子命令：新增、编辑、删除轨道与音符。

所有写操作都走 :mod:`musicgen.core.trackedit`，因此命令行与界面共用同一套
「保留注释的 XML 原地编辑」逻辑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import trackedit as te
from .core.instruments import note_names
from .core.project import load_project
from .samples import fetch

TrackEditError = te.TrackEditError


def _selector(args: argparse.Namespace) -> str:
    return str(args.track)


def _resolve_instrument(value: str | None) -> str | None:
    if value is None:
        return None
    key = fetch.resolve(value)
    if key not in fetch.instrument_keys():
        print(f"  ! 未注册的乐器 '{value}'，渲染时会回退到内置合成器", file=sys.stderr)
    return key


def _number(text: str | float, name: str, low: float | None = None,
            high: float | None = None) -> float:
    try:
        value = float(text)
    except (ValueError, TypeError):
        raise TrackEditError(f"{name} 必须是数字：{text}") from None
    if low is not None and value < low:
        raise TrackEditError(f"{name} 不能小于 {low}：{value}")
    if high is not None and value > high:
        raise TrackEditError(f"{name} 不能大于 {high}：{value}")
    return value


def _optional_beats(raw: str | None, name: str) -> tuple[bool, float | None]:
    """``none`` / 空串表示删除属性。返回 (是否出现, 值)。"""
    if raw is None:
        return False, None
    if raw.strip().lower() in ("", "none", "null", "-"):
        return True, None
    return True, te.parse_beats(raw, name)


def _changes_from(args: argparse.Namespace) -> dict:
    """把 CLI 选项收集成 ``edit_track`` 的 ``**changes``。"""
    changes: dict = {}
    if args.name is not None:
        changes["name"] = args.name.strip()
    if args.instrument is not None:
        changes["instrument"] = _resolve_instrument(args.instrument)
    if args.gain is not None:
        changes["gain_db"] = te.parse_gain(args.gain)
    if args.pan is not None:
        changes["pan"] = _number(args.pan, "pan", -1.0, 1.0)
    if args.reverb is not None:
        changes["reverb"] = _number(args.reverb, "reverb", 0.0, 1.0)
    if args.bpm is not None:
        changes["bpm"] = _number(args.bpm, "bpm", 5.0, 600.0)
    if args.transpose is not None:
        changes["transpose"] = int(_number(args.transpose, "transpose", -48, 48))
    if args.loop_count is not None:
        changes["loop_count"] = int(_number(args.loop_count, "loopCount", 0, 10000))
    for key in ("loop", "mute", "solo"):
        value = getattr(args, key)
        if value is not None:
            changes[key] = value
    appeared, value = _optional_beats(args.loop_len, "loopLen")
    if appeared:
        changes["loop_len"] = value
    return changes


def _parse_note_spec(text: str) -> tuple[float, float, list[int], float]:
    """``--note "0 1 C4,E4 0.8"``：起点 时值 音高 力度（空格分隔）。"""
    parts = text.split()
    if len(parts) != 4:
        raise TrackEditError(
            f"--note 需要 4 段（起点 时值 音高 力度），实际 {len(parts)} 段：{text!r}"
        )
    return (
        te.parse_beats(parts[0], "time"),
        te.parse_beats(parts[1], "dur"),
        te.parse_pitch_list(parts[2]),
        _number(parts[3], "vel", 0.0, 1.0),
    )


def _project_label(project_path: str) -> Path:
    return Path(project_path).expanduser().resolve()


# ---------------------------------------------------------------------------
# track 子命令
# ---------------------------------------------------------------------------


def cmd_track_list(args: argparse.Namespace) -> int:
    project = load_project(_project_label(args.project))
    print(f"工程 {project.name} · {len(project.tracks)} 条音轨")
    for index, track in enumerate(project.tracks, 1):
        relative = str(track.path.relative_to(project.base_dir).as_posix()) if track.path else "—"
        flags = []
        if track.mute:
            flags.append("静音")
        if track.solo:
            flags.append("独奏")
        if track.loop:
            flags.append("循环")
        if track.effects:
            flags.append("音效 " + " → ".join(spec.label() for spec in track.effects))
        print(
            f"  {index:>2}. {track.name:<16} {track.instrument:<9} "
            f"音符 {len(track.notes):>3}  {track.gain_db:+g}dB  "
            f"pan {track.pan:+.2f}  混响 {track.reverb:.2f}  {relative}"
            + (f"  [{'、'.join(flags)}]" if flags else "")
        )
    return 0


def cmd_track_add(args: argparse.Namespace) -> int:
    project_path = _project_label(args.project)
    notes = [_parse_note_spec(spec) for spec in (args.note or [])]
    loop_len = te.parse_beats(args.loop_len, "loopLen") if args.loop_len else None
    ref = te.add_track(
        project_path,
        name=args.name,
        instrument=_resolve_instrument(args.instrument) or "piano",
        gain_db=te.parse_gain(args.gain),
        pan=_number(args.pan, "pan", -1.0, 1.0),
        reverb=_number(args.reverb, "reverb", 0.0, 1.0),
        loop=bool(args.loop),
        loop_len=loop_len,
        bpm=_number(args.bpm, "bpm", 5.0, 600.0) if args.bpm else None,
        notes=notes,
        file=args.file,
        force=args.force,
    )
    print(f"已新增音轨 {ref.index}. {ref.name}")
    print(f"  文件：{ref.relative}")
    print(f"  乐器：{fetch.display(ref.instrument)} · {args.gain} · pan {args.pan} · 混响 {args.reverb}")
    if not notes:
        print("  这是一条空音轨，用 `musicgen note add` 加音符，或直接编辑 XML。")
    else:
        print(f"  初始音符 {len(notes)} 个")
    print(f"  下一步：python -m musicgen render {args.project}")
    return 0


def cmd_track_edit(args: argparse.Namespace) -> int:
    changes = _changes_from(args)
    if not changes:
        raise TrackEditError("没有指定要修改的字段（--name / --instrument / --gain …）")
    ref, applied = te.edit_track(_project_label(args.project), _selector(args), **changes)
    print(f"已更新音轨 {ref.index}. {ref.name}  ({ref.relative})")
    for field, old, new in applied:
        print(f"  {field:<12} {old} → {new}")
    return 0


def cmd_track_remove(args: argparse.Namespace) -> int:
    name, relative, deleted = te.remove_track(
        _project_label(args.project), _selector(args), delete_file=args.delete_file
    )
    print(f"已移除音轨 {name}（{relative}）")
    print("  音轨文件已删除" if deleted else "  音轨文件保留（要一起删除加 --delete-file）")
    return 0


# ---------------------------------------------------------------------------
# note 子命令
# ---------------------------------------------------------------------------


def _note_line(index: int, time: float, dur: float, pitches: str, vel: float) -> str:
    return f"  {index:>3}. time {time:<7g} dur {dur:<7g} {pitches:<18} vel {vel:g}"


def cmd_note_list(args: argparse.Namespace) -> int:
    project = load_project(_project_label(args.project))
    _, track = te.find_track(project, _selector(args))
    notes = te.list_notes(_project_label(args.project), _selector(args))
    print(f"音轨 {track.name} · {len(notes)} 个音符")
    for index, note in notes:
        pitches = note_names(track.instrument, note.pitches)
        print(_note_line(index, note.time, note.dur, pitches, note.velocity))
    return 0


def cmd_note_add(args: argparse.Namespace) -> int:
    index = te.add_note(
        _project_label(args.project), _selector(args),
        time=te.parse_beats(args.time, "time"),
        dur=te.parse_beats(args.dur, "dur"),
        pitches=te.parse_pitch_list(args.pitch),
        velocity=_number(args.vel, "vel", 0.0, 1.0),
    )
    print(f"已添加音符 #{index}：time {args.time}  dur {args.dur}  "
          f"{args.pitch}  vel {args.vel}（音轨 {_selector(args)}）")
    return 0


def cmd_note_set(args: argparse.Namespace) -> int:
    changes: dict = {}
    if args.time is not None:
        changes["time"] = te.parse_beats(args.time, "time")
    if args.dur is not None:
        changes["dur"] = te.parse_beats(args.dur, "dur")
    if args.pitch is not None:
        changes["pitches"] = te.parse_pitch_list(args.pitch)
    if args.vel is not None:
        changes["velocity"] = _number(args.vel, "vel", 0.0, 1.0)
    if not changes:
        raise TrackEditError("没有指定要修改的字段（--time / --dur / --pitch / --vel）")
    applied = te.set_note(_project_label(args.project), _selector(args), args.index, **changes)
    print(f"已更新音轨 {_selector(args)} 的第 {args.index} 个音符")
    for field, old, new in applied:
        print(f"  {field:<10} {old} → {new}")
    return 0


def cmd_note_del(args: argparse.Namespace) -> int:
    removed = te.delete_note(_project_label(args.project), _selector(args), args.index)
    detail = "  ".join(f"{k} {v}" for k, v in removed.items())
    print(f"已删除音轨 {_selector(args)} 的第 {args.index} 个音符（{detail}）")
    return 0


# ---------------------------------------------------------------------------
# 参数注册
# ---------------------------------------------------------------------------


def _add_track_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", help="*.musicproj 工程文件")
    parser.add_argument("--track", required=True,
                        help="定位音轨：1-based 序号 / 名称 / 文件名（支持部分匹配）")


def _add_track_attrs(parser: argparse.ArgumentParser, *, editing: bool) -> None:
    default = None
    parser.add_argument("--name", default=default, required=not editing, help="音轨名称")
    parser.add_argument("--instrument", default=default,
                        help="乐器注册键或中文名（piano / cello / 大提琴 / drums …）")
    parser.add_argument("--gain", default=None if editing else "0dB",
                        help="音量，-3dB / +2 / 0 均可")
    parser.add_argument("--pan", default=None if editing else "0",
                        help="声像 -1…+1")
    parser.add_argument("--reverb", default=None if editing else "0.2",
                        help="混响 0…1")
    parser.add_argument("--bpm", default=None,
                        help="逐轨速度覆盖（不给则跟随工程）")


def register_track_commands(sub) -> None:
    """把 ``track`` / ``note`` 命令组挂到主 parser 的 subparsers 上。"""
    p_track = sub.add_parser("track", help="新增 / 编辑 / 删除音轨")
    track_sub = p_track.add_subparsers(dest="action", required=True)

    p_list = track_sub.add_parser("list", help="列出工程里的音轨")
    p_list.add_argument("project")
    p_list.set_defaults(func=cmd_track_list)

    p_add = track_sub.add_parser("add", help="新增一条音轨")
    p_add.add_argument("project")
    _add_track_attrs(p_add, editing=False)
    p_add.add_argument("--loop", action="store_true", help="设为循环音轨")
    p_add.add_argument("--loop-len", default=None, help="循环周期（拍），配合 --loop")
    p_add.add_argument("--file", default=None,
                       help="指定音轨文件相对路径（默认 tracks/<名称>.track.xml）")
    p_add.add_argument("--force", action="store_true", help="目标文件已存在时覆盖")
    p_add.add_argument("--note", action="append", default=None, metavar="SPEC",
                       help='初始音符，可重复；格式 "起点 时值 音高 力度"，'
                            '如 "0 1 C4,E4 0.8"；和弦用逗号写在音高里')
    p_add.set_defaults(func=cmd_track_add)

    p_edit = track_sub.add_parser("edit", help="修改音轨参数")
    _add_track_selector(p_edit)
    _add_track_attrs(p_edit, editing=True)
    p_edit.add_argument("--loop", action=argparse.BooleanOptionalAction, default=None,
                        help="开启 / 关闭循环（--loop / --no-loop）")
    p_edit.add_argument("--mute", action=argparse.BooleanOptionalAction, default=None)
    p_edit.add_argument("--solo", action=argparse.BooleanOptionalAction, default=None)
    p_edit.add_argument("--loop-len", default=None,
                        help="循环周期（拍）；写 none 可删除该属性")
    p_edit.add_argument("--loop-count", default=None, help="循环次数，0 = 无限")
    p_edit.add_argument("--transpose", default=None, help="整体移调（半音）")
    p_edit.set_defaults(func=cmd_track_edit)

    p_remove = track_sub.add_parser("remove", help="移除音轨引用")
    _add_track_selector(p_remove)
    p_remove.add_argument("--delete-file", action="store_true",
                          help="连音轨文件一起删除（默认保留）")
    p_remove.set_defaults(func=cmd_track_remove)

    p_note = sub.add_parser("note", help="编辑音轨里的音符")
    note_sub = p_note.add_subparsers(dest="action", required=True)

    n_list = note_sub.add_parser("list", help="列出某条音轨的音符")
    _add_track_selector(n_list)
    n_list.set_defaults(func=cmd_note_list)

    n_add = note_sub.add_parser("add", help="追加一个音符")
    _add_track_selector(n_add)
    n_add.add_argument("--time", required=True, help="起点（拍），支持 1/3")
    n_add.add_argument("--dur", required=True, help="时值（拍），支持 1/4")
    n_add.add_argument("--pitch", required=True,
                       help="音高，逗号分隔即和弦；鼓轨可直接写 kick / snare")
    n_add.add_argument("--vel", default="0.8", help="力度 0…1（默认 0.8）")
    n_add.set_defaults(func=cmd_note_add)

    n_set = note_sub.add_parser("set", help="修改第 N 个音符")
    _add_track_selector(n_set)
    n_set.add_argument("--index", type=int, required=True, help="1-based 音符序号")
    n_set.add_argument("--time", default=None)
    n_set.add_argument("--dur", default=None)
    n_set.add_argument("--pitch", default=None)
    n_set.add_argument("--vel", default=None)
    n_set.set_defaults(func=cmd_note_set)

    n_del = note_sub.add_parser("del", help="删除第 N 个音符")
    _add_track_selector(n_del)
    n_del.add_argument("--index", type=int, required=True, help="1-based 音符序号")
    n_del.set_defaults(func=cmd_note_del)
