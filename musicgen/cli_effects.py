"""CLI 的 ``effect`` 子命令：查看音效类型、管理音轨效果链。

写操作走 :mod:`musicgen.core.trackedit`（保留 XML 注释的原地编辑），
与界面共用同一套逻辑；效果类型与参数由 :mod:`musicgen.core.effects` 的注册表提供。
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

from .core import trackedit as te
from .core.effects import describe_effects, effect_class, resolve_effect
from .core.project import load_project

TrackEditError = te.TrackEditError


def _pad(text: str, width: int) -> str:
    """按终端显示宽度补空格（中文算 2 格），表格才对得齐。"""
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _project_path(args: argparse.Namespace) -> Path:
    return Path(args.project).expanduser().resolve()


def _parse_pairs(items: list[str] | None) -> dict[str, str]:
    """``-p time=450 -p mix=0.3`` → ``{"time": "450", "mix": "0.3"}``。"""
    pairs: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise TrackEditError(f"-p 需要 键=值 的形式：{item!r}（例如 -p mix=0.3）")
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise TrackEditError(f"-p 缺少参数名：{item!r}")
        pairs[key] = value.strip()
    return pairs


def _parse_pairs_with_none(items: list[str] | None) -> dict[str, str | None]:
    """``set`` 用：值写 ``none`` 表示删除该参数、回到默认值。"""
    pairs: dict[str, str | None] = {}
    for key, value in _parse_pairs(items).items():
        pairs[key] = None if value.lower() in ("none", "null", "-", "") else value
    return pairs


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_effect_types(_args: argparse.Namespace) -> int:
    entries = describe_effects()
    print(f"已注册音效 {len(entries)} 个（工程里写 <effect type=\"echo\" .../>，按出现顺序串联）")
    for index, (key, label, description, params) in enumerate(entries, 1):
        print(f"\n  {index}. {label}  {key}")
        print(f"     {description}")
        for param in params:
            default = param.display(param.default)
            head = f"     · {_pad(param.key, 10)}{_pad(param.label, 10)}"
            print(f"{head}{_pad(param.range_text(), 16)}默认 {default}")
    print("\n提示：参数写进 XML 属性，例如 "
          '<effect type="echo" time="450" feedback="0.35" mix="0.28"/>')
    return 0


def cmd_effect_list(args: argparse.Namespace) -> int:
    project = load_project(_project_path(args))
    if args.track:
        index, track = te.find_track(project, args.track)
        rows = [(index + 1, track)]
    else:
        rows = list(enumerate(project.tracks, 1))

    print(f"工程 {project.name} · 音效链")
    empty = True
    for number, track in rows:
        print(f"  {number}. {track.name} [{track.instrument}]")
        if not track.effects:
            print("      （无音效）")
            continue
        empty = False
        for order, spec in enumerate(track.effects, 1):
            print(f"      {order}. {spec.label()} {spec.type}  {spec.summary()}")
    if empty:
        print("  整首都没有音效；用 `musicgen effect add …` 添加，类型见 `effect types`。")
    return 0


def cmd_effect_add(args: argparse.Namespace) -> int:
    params = _parse_pairs(args.param)
    ref, spec = te.add_effect(_project_path(args), args.track, args.type, params)
    print(f"已为音轨 {ref.index}. {ref.name} 添加音效：{spec.label()}（{spec.type}）")
    print(f"  参数：{spec.summary()}")
    if not params:
        print("  未指定参数，全部使用注册表默认值（`musicgen effect types` 可查看）")
    _, track = te.find_track(load_project(_project_path(args)), args.track)
    print(f"  当前效果链：{' → '.join(s.label() for s in track.effects)}")
    return 0


def cmd_effect_set(args: argparse.Namespace) -> int:
    params = _parse_pairs_with_none(args.param)
    if not params:
        raise TrackEditError("没有指定要修改的参数（用 -p 键=值，例如 -p feedback=0.5）")
    applied = te.set_effect(_project_path(args), args.track, args.index, **params)
    print(f"已更新音轨 {args.track} 的第 {args.index} 个音效")
    for name, old, new in applied:
        print(f"  {name:<12} {old} → {new}")
    return 0


def cmd_effect_remove(args: argparse.Namespace) -> int:
    attributes = te.remove_effect(_project_path(args), args.track, args.index)
    key = resolve_effect(attributes.get("type"))
    label = effect_class(key).LABEL if key else attributes.get("type", "?")
    detail = "  ".join(f"{k}={v}" for k, v in attributes.items())
    print(f"已删除音轨 {args.track} 的第 {args.index} 个音效（{label}  {detail}）")
    return 0


# ---------------------------------------------------------------------------
# 参数注册
# ---------------------------------------------------------------------------


def _add_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", help="*.musicproj 工程文件")
    parser.add_argument("--track", required=True,
                        help="定位音轨：1-based 序号 / 名称 / 文件名（支持部分匹配）")


def _add_params(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument("-p", "--param", action="append", default=None, metavar="KEY=VALUE",
                        help=help_text)


def register_effect_commands(sub) -> None:
    """把 ``effect`` 命令组挂到主 parser 的 subparsers 上。"""
    p_effect = sub.add_parser("effect", help="查看音效类型 / 管理音轨效果链")
    effect_sub = p_effect.add_subparsers(dest="action", required=True)

    p_types = effect_sub.add_parser("types", help="列出已注册音效及其参数")
    p_types.set_defaults(func=cmd_effect_types)

    p_list = effect_sub.add_parser("list", help="查看工程或某条音轨的效果链")
    p_list.add_argument("project", help="*.musicproj 工程文件")
    p_list.add_argument("--track", default=None,
                        help="只看某条音轨（序号 / 名称 / 文件名）；不给则列出全部")
    p_list.set_defaults(func=cmd_effect_list)

    p_add = effect_sub.add_parser("add", help="给音轨追加一个音效")
    _add_selector(p_add)
    p_add.add_argument("--type", required=True,
                       help="音效类型：stereo / echo / fade / electro，也认中文名")
    _add_params(p_add, "参数，可重复，如 -p time=450 -p mix=0.3；不给则全用默认值")
    p_add.set_defaults(func=cmd_effect_add)

    p_set = effect_sub.add_parser("set", help="修改第 N 个音效的参数")
    _add_selector(p_set)
    p_set.add_argument("--index", type=int, required=True, help="1-based 音效序号")
    _add_params(p_set, "参数，可重复，如 -p feedback=0.5；值写 none 可删除该参数")
    p_set.set_defaults(func=cmd_effect_set)

    p_remove = effect_sub.add_parser("remove", help="删除第 N 个音效")
    _add_selector(p_remove)
    p_remove.add_argument("--index", type=int, required=True, help="1-based 音效序号")
    p_remove.set_defaults(func=cmd_effect_remove)
