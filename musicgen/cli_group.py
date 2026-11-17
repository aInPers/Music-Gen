"""CLI 的 ``group`` 与 ``curve`` 子命令。

``group`` 是编曲层的容器：几条轨道先汇总成一条信号，再统一走效果链、推子与自动化，
输出继续交给归属总线。``curve`` 编辑自动化曲线，可以挂在轨道、组或总线上。
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


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------


def cmd_group_list(args: argparse.Namespace) -> int:
    project = load_project(_project_path(args))
    if not project.groups:
        print("工程里没有轨道组。新建一个：python -m musicgen group add <工程> --name Drums")
        print("组可以带效果链与曲线，组内轨道自己的处理照旧生效（叠加，不覆盖）。")
        return 0
    print(f"工程 {project.name} · {len(project.groups)} 个组")
    for index, group in enumerate(project.groups, 1):
        members = [t.name for t in project.tracks
                   if (t.group or "").strip().lower() == group.name.strip().lower()]
        flags = ["静音"] if group.mute else []
        chain = " → ".join(spec.label() for spec in group.effects)
        curves = len(group.automation)
        print(
            f"  {index:>2}. {_pad(group.name, 12)} {group.gain_db:+g}dB  "
            f"pan {group.pan:+g}  " + (f"效果 {chain}" if chain else "无效果")
            + (f" · {curves} 条曲线" if curves else "")
            + (f"  [{'、'.join(flags)}]" if flags else "")
        )
        if group.bus:
            print(f"      输出到总线：{group.bus}")
        if members:
            print(f"      组内轨道：{'、'.join(members)}")
        else:
            print("      （空组，用 group assign 把轨道放进来）")
    loose = [t.name for t in project.tracks if not t.group]
    if loose:
        print(f"  不在任何组：{'、'.join(loose)}")
    return 0


def cmd_group_add(args: argparse.Namespace) -> int:
    gain = te.parse_gain(args.gain) if args.gain else None
    name = te.add_group(_project_path(args), args.name, gain_db=gain,
                        pan=args.pan, bus=args.bus)
    print(f"已新增组「{name}」")
    print(f"  放入轨道：python -m musicgen group assign <工程> --track 1 --group {name}")
    return 0


def cmd_group_set(args: argparse.Namespace) -> int:
    if args.gain is None and args.pan is None and args.bus is None and args.mute is None:
        raise TrackEditError("没有指定要修改的字段（--gain / --pan / --bus / --mute）")
    applied = te.set_group(
        _project_path(args),
        args.name,
        gain_db=te.parse_gain(args.gain) if args.gain is not None else None,
        pan=args.pan,
        bus=args.bus,
        mute=args.mute,
    )
    print(f"已更新组「{args.name}」：")
    for field, old, new in applied:
        print(f"  {field}: {old} → {new}")
    return 0


def cmd_group_remove(args: argparse.Namespace) -> int:
    count = te.remove_group(_project_path(args), args.name, force=args.force)
    if count:
        print(f"已删除组「{args.name}」，{count} 条轨道已移出")
    else:
        print(f"已删除组「{args.name}」")
    return 0


def cmd_group_assign(args: argparse.Namespace) -> int:
    ref = te.assign_group(_project_path(args), args.track, args.group)
    if args.group:
        print(f"已把「{ref.name}」放进组「{args.group}」")
        print("  提示：进组后该轨自身的总线归属不再生效（去向由组决定），发送仍然有效。")
    else:
        print(f"已把「{ref.name}」移出组")
    return 0


# ---------------------------------------------------------------------------
# curve
# ---------------------------------------------------------------------------


def cmd_curve_list(args: argparse.Namespace) -> int:
    project_path = _project_path(args)
    scope = args.scope
    selector = args.track or args.group or args.bus
    if args.track:
        scope = "track"
    elif args.group:
        scope = "group"
    elif args.bus:
        scope = "bus"
    rows = te.list_curves(project_path, scope=scope, selector=selector)
    if not rows:
        print("没有找到曲线。加一条：")
        print('  python -m musicgen curve set <工程> --track 1 --target gain --points "0:0; 8:-6; 16:0"')
        return 0
    print(f"共 {len(rows)} 条曲线")
    for scope_name, owner, curve in rows:
        label = {"track": "轨道", "group": "组", "bus": "总线"}.get(scope_name, scope_name)
        pts = "  ".join(f"{p.beat:g}拍={p.value:g}" for p in curve.points[:6])
        if len(curve.points) > 6:
            pts += f" …（共 {len(curve.points)} 点）"
        mode = {"bezier": "贝塞尔", "linear": "线性", "step": "阶跃"}.get(curve.mode, curve.mode)
        print(f"  [{label}] {_pad(owner, 12)} {_pad(curve.target, 22)} {mode}  {pts}")
    return 0


def cmd_curve_set(args: argparse.Namespace) -> int:
    scope = args.scope
    selector = args.track or args.group or args.bus
    if args.track:
        scope, selector = "track", args.track
    elif args.group:
        scope, selector = "group", args.group
    elif args.bus:
        scope, selector = "bus", args.bus
    if scope == "track" and not selector:
        raise TrackEditError("轨道曲线要指定 --track（名字或序号）")
    target = te.set_curve(
        _project_path(args), scope, selector, args.target, args.points,
        mode=args.mode, tension=args.tension,
    )
    where = {"track": "轨道", "group": "组", "bus": "总线"}.get(scope, scope)
    print(f"已写入曲线：{where}「{selector}」的 {target}")
    print(f"  点：{args.points}   模式：{args.mode}")
    return 0


def cmd_curve_remove(args: argparse.Namespace) -> int:
    scope = args.scope
    selector = args.track or args.group or args.bus
    if args.track:
        scope = "track"
    elif args.group:
        scope = "group"
    elif args.bus:
        scope = "bus"
    count = te.remove_curve(_project_path(args), scope, selector, args.target)
    print(f"已删除 {count} 条目标为 {args.target} 的曲线")
    return 0


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


def register_group_commands(sub) -> None:
    parser = sub.add_parser("group", help="轨道组：把几条轨道捏成一条信号统一处理")
    group_sub = parser.add_subparsers(dest="group_action", required=True)

    p_list = group_sub.add_parser("list", help="列出组与组内轨道")
    p_list.add_argument("project")
    p_list.set_defaults(func=cmd_group_list)

    p_add = group_sub.add_parser("add", help="新建组")
    p_add.add_argument("project")
    p_add.add_argument("--name", required=True, help="组名")
    p_add.add_argument("--gain", help="组推子，如 -2dB")
    p_add.add_argument("--pan", type=float, default=None, help="组声像 -1…1")
    p_add.add_argument("--bus", help="组输出接到的总线")
    p_add.set_defaults(func=cmd_group_add)

    p_set = group_sub.add_parser("set", help="改组参数")
    p_set.add_argument("project")
    p_set.add_argument("--name", required=True)
    p_set.add_argument("--gain", default=None)
    p_set.add_argument("--pan", type=float, default=None)
    p_set.add_argument("--bus", default=None)
    p_set.add_argument("--mute", action="store_true", default=None)
    p_set.set_defaults(func=cmd_group_set)

    p_rm = group_sub.add_parser("remove", help="删除组")
    p_rm.add_argument("project")
    p_rm.add_argument("--name", required=True)
    p_rm.add_argument("--force", action="store_true", help="组内还有轨道也删（会清掉引用）")
    p_rm.set_defaults(func=cmd_group_remove)

    p_assign = group_sub.add_parser("assign", help="把轨道放进组 / 移出组")
    p_assign.add_argument("project")
    p_assign.add_argument("--track", required=True, help="轨道名或序号")
    p_assign.add_argument("--group", default=None, help="组名；不给表示移出组")
    p_assign.set_defaults(func=cmd_group_assign)


def register_curve_commands(sub) -> None:
    parser = sub.add_parser("curve", help="自动化曲线：增益、声像、效果参数随时间变化")
    curve_sub = parser.add_subparsers(dest="curve_action", required=True)

    p_list = curve_sub.add_parser("list", help="列出曲线")
    p_list.add_argument("project")
    p_list.add_argument("--scope", default="all", choices=("all", "track", "group", "bus"))
    p_list.add_argument("--track", default=None)
    p_list.add_argument("--group", default=None)
    p_list.add_argument("--bus", default=None)
    p_list.set_defaults(func=cmd_curve_list)

    p_set = curve_sub.add_parser("set", help="写入或替换一条曲线")
    p_set.add_argument("project")
    p_set.add_argument("--target", required=True,
                       help="gain / pan / reverb / effect.<序号>.<参数>，序号从 0 起")
    p_set.add_argument("--points", required=True, help='控制点，如 "0:0; 8:-6; 16:0"')
    p_set.add_argument("--mode", default="bezier", choices=("bezier", "linear", "step"))
    p_set.add_argument("--tension", type=float, default=0.5,
                       help="贝塞尔张力 0–1.5（0 等于折线，0.5 为标准平滑）")
    p_set.add_argument("--scope", default="track", choices=("track", "group", "bus"))
    p_set.add_argument("--track", default=None)
    p_set.add_argument("--group", default=None)
    p_set.add_argument("--bus", default=None)
    p_set.set_defaults(func=cmd_curve_set)

    p_rm = curve_sub.add_parser("remove", help="删除曲线")
    p_rm.add_argument("project")
    p_rm.add_argument("--target", required=True)
    p_rm.add_argument("--scope", default="track", choices=("track", "group", "bus"))
    p_rm.add_argument("--track", default=None)
    p_rm.add_argument("--group", default=None)
    p_rm.add_argument("--bus", default=None)
    p_rm.set_defaults(func=cmd_curve_remove)
