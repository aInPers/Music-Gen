"""CLI 的 ``effect`` 子命令：查看音效类型、管理音轨效果链。

写操作走 :mod:`musicgen.core.trackedit`（保留 XML 注释的原地编辑），
与界面共用同一套逻辑；效果类型与参数由 :mod:`musicgen.core.effects` 的注册表提供。
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

from .core import trackedit as te
from .core.effectscript import EffectScript, EffectScriptError
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


def cmd_effect_save(args: argparse.Namespace) -> int:
    project = load_project(_project_path(args))
    index, track = te.find_track(project, args.track)
    script = EffectScript.from_specs(
        track.effects,
        name=args.name or f"{track.name}-chain",
        description=args.description or "",
    )
    target = Path(args.output).expanduser()
    script.save(target)
    print(f"已保存音轨 {index + 1}. {track.name} 的效果链 → {target}")
    print(f"  {len(script.specs)} 个效果：{script.summary()}")
    print(f"  复用到别的音轨：python -m musicgen effect apply <工程> --track <轨> --script \"{target}\"")
    return 0


def cmd_effect_apply(args: argparse.Namespace) -> int:
    try:
        script = EffectScript.load(args.script)
    except EffectScriptError as exc:
        raise TrackEditError(str(exc)) from exc
    project_path = _project_path(args)
    project = load_project(project_path)
    index, track = te.find_track(project, args.track)
    specs = list(track.effects) + list(script.specs) if args.append else list(script.specs)
    if len(specs) > 12:
        raise TrackEditError(f"效果链过长（{len(specs)} 个，上限 12）")
    te.set_effects(project_path, args.track, specs)
    print(f"已把脚本「{script.name}」应用到音轨 {index + 1}. {track.name}"
          f"（{'追加' if args.append else '替换'}）")
    print(f"  当前效果链：{' → '.join(script.labels()) if not args.append else ' → '.join(s.label() for s in specs)}")
    return 0


def cmd_effect_check(args: argparse.Namespace) -> int:
    try:
        script = EffectScript.load(args.script)
    except EffectScriptError as exc:
        print(f"脚本有问题：{exc}")
        return 1
    print(f"脚本正常：{script.name}（{len(script.specs)} 个效果）")
    if script.description:
        print(f"  说明：{script.description}")
    for order, spec in enumerate(script.specs, 1):
        print(f"  {order}. {spec.label()} {spec.type}  {spec.summary()}")
    print("\n应用：python -m musicgen effect apply <工程> --track <轨> "
          f"--script \"{args.script}\"")
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

    p_save = effect_sub.add_parser("save", help="把音轨效果链存成 .fx.json 脚本")
    _add_selector(p_save)
    p_save.add_argument("-o", "--output", required=True, help="输出脚本路径，如 fx/guitar.fx.json")
    p_save.add_argument("--name", default="", help="脚本名（默认 <音轨名>-chain）")
    p_save.add_argument("--description", default="", help="用途说明")
    p_save.set_defaults(func=cmd_effect_save)

    p_apply = effect_sub.add_parser("apply", help="把 .fx.json 脚本应用到音轨（整链替换或追加）")
    _add_selector(p_apply)
    p_apply.add_argument("--script", required=True, help="音效脚本路径")
    p_apply.add_argument("--append", action="store_true",
                         help="追加到现有链尾（默认整链替换）")
    p_apply.set_defaults(func=cmd_effect_apply)

    p_check = effect_sub.add_parser("check", help="校验音效脚本（不写工程）")
    p_check.add_argument("script", help="音效脚本路径")
    p_check.set_defaults(func=cmd_effect_check)

    p_remove = effect_sub.add_parser("remove", help="删除第 N 个音效")
    _add_selector(p_remove)
    p_remove.add_argument("--index", type=int, required=True, help="1-based 音效序号")
    p_remove.set_defaults(func=cmd_effect_remove)
