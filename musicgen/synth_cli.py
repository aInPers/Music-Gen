"""CLI 的 ``synth`` 子命令：查看、校验、试听合成音脚本。

合成音脚本是纯 JSON（``*.synth.json``），既可以是内置音色，也可以是项目里
自己写的一份文件。这个命令组的作用是让「写脚本 → 校验 → 试听」形成闭环，
大模型生成脚本后也能用 ``synth check`` 自查，不必先建工程。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .core.patch import Patch, PatchError, PatchInstrument, builtin_patches, load_patch
from .core.wavio import write_wav
from .samples import fetch


def _load(ref: str) -> Patch:
    try:
        return load_patch(ref)
    except PatchError as exc:
        print(f"脚本有问题：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_list(args: argparse.Namespace) -> int:
    builtins = builtin_patches()
    print(f"内置合成音脚本（{len(builtins)} 个，可用名字直接写进 instrument）：")
    for key, patch in builtins.items():
        waves = "、".join(sorted({osc.wave for osc in patch.oscillators}))
        print(f"  {key:<12} {patch.label:<6} 波形 {waves:<12} "
              f"增益 {patch.gain:g}  起音 {patch.amp.attack:g}s  释放 {patch.amp.release:g}s")
    registry_keys = [key for key in fetch.instrument_keys() if fetch.is_synth(key)]
    if registry_keys:
        print("\n注册表里的合成音色：")
        for key in registry_keys:
            source = fetch.SYNTHS.get(key)
            print(f"  {key:<12} {fetch.label(key):<6} → {source.patch if source else '?'}")
    print("\n自定义脚本：写一份 JSON 后 `synth check 文件` 校验，"
          "工程里用 <track instrument=\"synth\" patch=\"synths/x.synth.json\">")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    patch = _load(args.patch)
    print(json.dumps(patch.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    patch = _load(args.patch)
    instrument = PatchInstrument(patch)
    audio = instrument.render_note(args.midi, args.velocity, args.duration)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.0
    print(f"脚本正常：{patch.name}（{patch.label or '无标签'}）")
    print(f"  {instrument.describe()}")
    print(f"  试渲染 MIDI {args.midi} / {args.duration:g}s / 力度 {args.velocity:g}："
          f"{audio.shape[1]} 帧（{audio.shape[1] / 44100:.2f}s）")
    print(f"  峰值 {peak:.3f}（{20 * np.log10(max(peak, 1e-9)):.1f}dBFS） RMS {rms:.4f}")
    if peak < 0.02:
        print("  ! 电平偏低：检查 gain，或把 filter.cutoff 调高")
    if peak >= 1.0:
        print("  ! 电平过高：把 gain 调到 0.3 以下")
    if not np.any(np.abs(audio) > 1e-6):
        print("  ! 渲染结果全静音：检查振荡器 level 是否为 0", file=sys.stderr)
        return 1
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    patch = _load(args.patch)
    instrument = PatchInstrument(patch)
    midis = [int(part) for part in str(args.midi).split(",") if part.strip()]
    pieces = [instrument.render_note(midi, args.velocity, args.duration) for midi in midis]
    length = max(piece.shape[1] for piece in pieces)
    audio = np.zeros((2, length), dtype=np.float32)
    for piece in pieces:
        audio[:, : piece.shape[1]] += piece
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_wav(target, audio, 44100, bit_depth=24)
    print(f"已写出 {target}（{length / 44100:.2f}s，峰值 "
          f"{20 * np.log10(max(float(np.max(np.abs(audio))), 1e-9)):.1f}dBFS）")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    base = load_patch(args.preset)
    data = base.to_dict()
    data["name"] = args.name or Path(args.output).stem.replace(".synth", "")
    data["label"] = args.label or f"{data['name']} 音色"
    patch = Patch.from_dict(data)
    target = patch.save(args.output)
    print(f"已写出脚本 {target}")
    print(f"  下一步：python -m musicgen synth check {target}")
    return 0


def register_synth_commands(sub) -> None:
    parser = sub.add_parser(
        "synth",
        help="合成音脚本：列内置音色、校验、试听、生成模板",
        description="合成音脚本是 JSON 描述的音色，工程里可直接当乐器用，也可由音频逆向生成。",
    )
    inner = parser.add_subparsers(dest="synth_command", required=True)

    p_list = inner.add_parser("list", help="列出内置与注册表里的合成音色")
    p_list.set_defaults(func=cmd_list)

    p_show = inner.add_parser("show", help="打印脚本规范化后的 JSON")
    p_show.add_argument("patch", help="内置名（warm_pad）或 .synth.json 路径")
    p_show.set_defaults(func=cmd_show)

    p_check = inner.add_parser("check", help="校验脚本并试渲染一个音，检查电平和静音")
    p_check.add_argument("patch")
    p_check.add_argument("--midi", type=int, default=60, help="试听音高（默认 60 = C4）")
    p_check.add_argument("--dur", dest="duration", type=float, default=1.0, help="时长秒")
    p_check.add_argument("--vel", dest="velocity", type=float, default=0.8, help="力度 0–1")
    p_check.set_defaults(func=cmd_check)

    p_render = inner.add_parser("render", help="把脚本渲染成 WAV（可给和弦）")
    p_render.add_argument("patch")
    p_render.add_argument("-o", "--output", required=True, help="输出 WAV 路径")
    p_render.add_argument("--midi", default="60", help="音高，多个用逗号分隔，如 60,64,67")
    p_render.add_argument("--dur", dest="duration", type=float, default=1.5, help="时长秒")
    p_render.add_argument("--vel", dest="velocity", type=float, default=0.8, help="力度 0–1")
    p_render.set_defaults(func=cmd_render)

    p_new = inner.add_parser("new", help="以某个内置音色为模板，生成一份可改的脚本")
    p_new.add_argument("-o", "--output", required=True, help="输出路径，如 synths/my_pad.synth.json")
    p_new.add_argument("--preset", default="warm_pad", help="模板内置音色（默认 warm_pad）")
    p_new.add_argument("--name", default="", help="脚本名（默认取文件名）")
    p_new.add_argument("--label", default="", help="中文标签")
    p_new.set_defaults(func=cmd_new)
