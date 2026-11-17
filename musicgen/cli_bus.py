"""CLI 的 ``bus`` 子命令：查看与编辑编组 / 返回总线。

总线是混音层的路由单元：多条音轨汇总到一条总线，统一走效果链与推子，再进母带。
轨道还可以把输出按比例「发送」到另一条总线（典型用法是混响 / 延迟返回）。
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

from .core import trackedit as te
from .core.project import load_project

TrackEditError = te.TrackEditError


def _pad(text: str, width: int) -> str:
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _project_path(args: argparse.Namespace) -> Path:
    return Path(args.project).expanduser().resolve()


def cmd_bus_list(args: argparse.Namespace) -> int:
    project = load_project(_project_path(args))
    if not project.buses:
        print("工程里没有总线。加一条：python -m musicgen bus add <工程> --name Drums")
        print("未归属的轨道直接进母带。")
        return 0
    print(f"工程 {project.name} · {len(project.buses)} 条总线")
    for index, bus in enumerate(project.buses, 1):
        owners = [t.name for t in project.tracks
                  if (t.bus or "").strip().lower() == bus.name.strip().lower()]
        senders = [t.name for t in project.tracks
                   if any(s.bus.strip().lower() == bus.name.strip().lower() for s in t.sends)]
        flags = ["静音"] if bus.mute else []
        chain = " → ".join(spec.label() for spec in bus.effects)
        print(
            f"  {index:>2}. {bus.name:<14} {bus.gain_db:+g}dB  "
            + (f"效果 {chain}" if chain else "无效果")
            + (f"  [{'、'.join(flags)}]" if flags else "")
        )
        if owners:
            print(f"      归属：{'、'.join(owners)}")
        if senders:
            pairs = []
            for track in project.tracks:
                for sent in track.sends:
                    if sent.bus.strip().lower() == bus.name.strip().lower():
                        pairs.append(f"{track.name} {sent.amount:g}")
            print(f"      发送：{'、'.join(pairs)}")
        if not owners and not senders:
            print("      （没有音轨接入，这条总线目前是空的）")
    loose = [t.name for t in project.tracks if not t.bus]
    if loose:
        print(f"  直接进母带：{'、'.join(loose)}")
    return 0


def cmd_bus_add(args: argparse.Namespace) -> int:
    gain = te.parse_gain(args.gain) if args.gain else None
    name = te.add_bus(_project_path(args), args.name, gain_db=gain, mute=args.mute)
    print(f"已新增总线「{name}」" + (f"（{args.gain}）" if args.gain else ""))
    print(f"  把音轨接进去：python -m musicgen track edit <工程> --track 1 --bus {name}")
    return 0


def cmd_bus_set(args: argparse.Namespace) -> int:
    if args.gain is None and args.mute is None and args.rename is None:
        raise TrackEditError("没有指定要修改的字段（--gain / --mute / --rename）")
    applied = te.set_bus(
        _project_path(args),
        args.name,
        gain_db=te.parse_gain(args.gain) if args.gain is not None else None,
        mute=args.mute,
        rename=args.rename,
    )
    print(f"已更新总线「{args.name}」")
    for field, old, new in applied:
        print(f"  {field:<8} {old} → {new}")
    return 0


def cmd_bus_remove(args: argparse.Namespace) -> int:
    cleared = te.remove_bus(_project_path(args), args.name, force=args.force)
    print(f"已删除总线「{args.name}」")
    if cleared:
        print(f"  已清理引用：{'、'.join(cleared)}")
    return 0


def cmd_bus_send(args: argparse.Namespace) -> int:
    try:
        value = float(str(args.amount).rstrip("x").rstrip("%"))
    except ValueError:
        raise TrackEditError(f"发送量不是有效数字：{args.amount!r}") from None
    if str(args.amount).strip().endswith("%"):
        value /= 100.0
    if not 0.0 <= value <= 1.0:
        raise TrackEditError(f"发送量应在 0–1（或 0–100%）之间：{args.amount}")
    message = te.set_send(_project_path(args), args.track, args.to, value)
    print(f"已设置发送：{message}")
    return 0


def cmd_bus_unsend(args: argparse.Namespace) -> int:
    message = te.remove_send(_project_path(args), args.track, args.to)
    print(message)
    return 0


def register_bus_commands(sub) -> None:
    parser = sub.add_parser("bus", help="编组 / 返回总线：查看、增删、发送")
    bus_sub = parser.add_subparsers(dest="action", required=True)

    p_list = bus_sub.add_parser("list", help="查看总线、归属与发送")
    p_list.add_argument("project", help="*.musicproj 工程文件")
    p_list.set_defaults(func=cmd_bus_list)

    p_add = bus_sub.add_parser("add", help="新增一条总线")
    p_add.add_argument("project", help="*.musicproj 工程文件")
    p_add.add_argument("--name", required=True, help="总线名（建议用英文，如 Drums / Reverb）")
    p_add.add_argument("--gain", default=None, help="推子增益，如 -3dB")
    p_add.add_argument("--mute", action="store_true", help="新建时就静音")
    p_add.set_defaults(func=cmd_bus_add)

    p_set = bus_sub.add_parser("set", help="改推子 / 静音 / 名称")
    p_set.add_argument("project", help="*.musicproj 工程文件")
    p_set.add_argument("--name", required=True, help="要修改的总线名")
    p_set.add_argument("--gain", default=None, help="推子增益，如 -6dB")
    p_set.add_argument("--mute", action=argparse.BooleanOptionalAction, default=None,
                       help="静音 / 取消静音")
    p_set.add_argument("--rename", default=None, help="改名，所有引用一起改")
    p_set.set_defaults(func=cmd_bus_set)

    p_remove = bus_sub.add_parser("remove", help="删除总线")
    p_remove.add_argument("project", help="*.musicproj 工程文件")
    p_remove.add_argument("--name", required=True, help="要删除的总线名")
    p_remove.add_argument("--force", action="store_true", help="仍被引用时强制删除并清理引用")
    p_remove.set_defaults(func=cmd_bus_remove)

    p_send = bus_sub.add_parser("send", help="设置轨道到总线的发送量")
    p_send.add_argument("project", help="*.musicproj 工程文件")
    p_send.add_argument("--track", required=True, help="1-based 序号 / 名称 / 文件名")
    p_send.add_argument("--to", required=True, help="目标总线名")
    p_send.add_argument("--amount", required=True, help="发送量 0–1 或百分比，如 0.35 / 35%")
    p_send.set_defaults(func=cmd_bus_send)

    p_unsend = bus_sub.add_parser("unsend", help="取消一条发送")
    p_unsend.add_argument("project", help="*.musicproj 工程文件")
    p_unsend.add_argument("--track", required=True, help="1-based 序号 / 名称 / 文件名")
    p_unsend.add_argument("--to", required=True, help="目标总线名")
    p_unsend.set_defaults(func=cmd_bus_unsend)
