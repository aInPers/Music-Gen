"""CLI 的 ``instrument`` 子命令：增删查改用户乐器注册表。

注册表是 ``~/.musicgen/instruments.json``（可用 ``MUSICGEN_INSTRUMENTS`` 覆盖），
GUI 的「乐器注册表」对话框读写的是同一个文件。
"""

from __future__ import annotations

import argparse
import sys

from .samples import fetch, registry

RegistryError = registry.RegistryError


def _entries() -> list[dict]:
    return registry.load()


def _reload() -> None:
    """写操作后让同进程内的 fetch 四张表立刻生效（测试/连续调用需要）。"""
    fetch.reload_registry()


def _aliases(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        for part in raw.split(","):
            part = part.strip()
            if part and part not in result:
                result.append(part)
    return result


def cmd_list(args: argparse.Namespace) -> int:
    entries = _entries()
    print(f"注册表：{registry.path()}（{len(entries)} 件）")
    for entry in entries:
        key = entry["key"]
        kind = entry.get("kind", "sample")
        if kind == "builtin":
            detail = entry.get("description") or "内置乐器"
            state = "无需下载"
        else:
            have, want = fetch.instrument_status(key, args.cache_dir)
            state = "就绪" if want and have >= want else ("部分" if have else "未下载")
            low = entry.get("midi_low", 21)
            high = entry.get("midi_high", 108)
            rng = entry.get("range_label") or f"MIDI {low}–{high}"
            detail = f"{rng}  采样 {have}/{want} [{state}]  增益 {float(entry.get('gain', 10.0)):g}"
            source = fetch.SOURCES.get(key)
            if source:
                detail += f"  {source.title}"
        aliases = "、".join(entry.get("aliases") or []) or "—"
        print(f"  {key:<12} {entry['label']:<8} {kind:<8} {detail}")
        print(f"               别名 {aliases}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    print(registry.path())
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    entries = _entries()
    spec = {
        "key": args.key,
        "label": args.name,
        "aliases": _aliases(args.alias),
        "kind": "sample",
        "gm": args.gm or "",
        "urls": _aliases(args.url) if args.url else [],
        "title": args.title or (
            f"FluidR3 GM — {args.gm.replace('_', ' ').title()}" if args.gm else ""
        ),
        "midi_low": args.low,
        "midi_high": args.high,
        "range_label": args.range_label or "",
        "gain": args.gain,
    }
    entry = registry.add(entries, spec, force=args.force)
    _reload()
    have, want = fetch.instrument_status(entry["key"])
    origin = entry.get("gm") or f"{len(entry.get('urls') or [])} 个自定义地址"
    print(f"已注册乐器 {entry['key']}（{entry['label']}）")
    print(f"  类型：采样 · {origin} · MIDI {entry['midi_low']}–{entry['midi_high']} "
          f"· 增益 {entry['gain']:g}")
    print(f"  别名：{'、'.join(entry['aliases']) or '—'}")
    print(f"  采样 {have}/{want} · 注册表 {registry.path()}")
    print(f"  下一步：python -m musicgen samples fetch --instrument {entry['key']}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    entries = _entries()
    entry = registry.remove(entries, args.key)
    _reload()
    print(f"已删除注册表中的 {entry['key']}（{entry['label']}）")
    print("  采样缓存未删除；工程里再写这个名字会回退到内置合成器。")
    print("  恢复默认：python -m musicgen instrument reset")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    entries = registry.reset()
    _reload()
    print(f"已恢复内置默认乐器（{len(entries)} 件）："
          + "、".join(f"{e['label']}({e['key']})" for e in entries))
    print(f"  注册表：{registry.path()}")
    return 0


def register_instrument_commands(sub) -> None:
    """把 ``instrument`` 命令组挂到主 parser 的 subparsers 上。"""
    p = sub.add_parser("instrument", help="管理用户乐器注册表（添加 / 删除 / 恢复）")
    inst = p.add_subparsers(dest="action", required=True)

    p_list = inst.add_parser("list", help="列出注册表里的乐器与采样状态")
    p_list.add_argument("--cache-dir", default=None, help="音源缓存目录")
    p_list.set_defaults(func=cmd_list)

    p_path = inst.add_parser("path", help="打印注册表文件路径")
    p_path.set_defaults(func=cmd_path)

    p_add = inst.add_parser("add", help="注册一件新乐器（FluidR3 GM 包名或自定义地址）")
    p_add.add_argument("key", help="注册键，小写字母/数字/下划线，如 flute")
    p_add.add_argument("--name", required=True, help="界面显示的中文名，如 长笛")
    p_add.add_argument("--gm", default=None,
                       help="FluidR3 GM 音色包名，如 flute / acoustic_guitar_nylon；"
                            "会自动生成 4 个下载镜像")
    p_add.add_argument("--url", action="append", default=None,
                       help="自定义下载地址（可重复或逗号分隔，需含 {name} 与 {ext} 占位符）")
    p_add.add_argument("--alias", action="append", default=None,
                       help="别名，可重复或逗号分隔（中文名会自动成为别名）")
    p_add.add_argument("--low", type=int, default=21, help="最低 MIDI 音高（默认 21 = A0）")
    p_add.add_argument("--high", type=int, default=108, help="最高 MIDI 音高（默认 108 = C8）")
    p_add.add_argument("--gain", type=float, default=10.0,
                       help="采样电平补偿倍数（默认 10.0，声音偏小就调大）")
    p_add.add_argument("--range-label", default="", help="音域说明，如 C4–C7")
    p_add.add_argument("--title", default="", help="音源名称（显示在列表里）")
    p_add.add_argument("--force", action="store_true", help="已存在时覆盖")
    p_add.set_defaults(func=cmd_add)

    p_remove = inst.add_parser("remove", help="删除注册表里的乐器（不删采样缓存）")
    p_remove.add_argument("key")
    p_remove.set_defaults(func=cmd_remove)

    p_reset = inst.add_parser("reset", help="恢复内置默认乐器列表")
    p_reset.set_defaults(func=cmd_reset)
