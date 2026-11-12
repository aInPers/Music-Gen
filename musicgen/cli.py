"""MusicGen 命令行入口。

用法示例::

    python -m musicgen render examples/demo/demo.musicproj -o out.wav
    python -m musicgen validate examples/demo/demo.musicproj
    python -m musicgen info examples/demo/demo.musicproj
    python -m musicgen init 我的曲子
    python -m musicgen samples fetch --instrument piano
    python -m musicgen gui
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .analysis import AnalysisError
from .cli_analyze import register_analyze_command
from .cli_effects import register_effect_commands
from .cli_instruments import RegistryError, register_instrument_commands
from .synth_cli import register_synth_commands
from .cli_tracks import TrackEditError, register_track_commands
from .core.project import ProjectError, create_project, describe, load_project
from .core.render import render_project
from .core.wavio import write_wav
from .progress import Progress
from .samples import fetch


def _setup_console() -> None:
    """Windows 控制台统一用 UTF-8，避免中文报 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 老环境无 reconfigure
            pass


def _fmt_seconds(value: float) -> str:
    minutes, seconds = divmod(max(0.0, value), 60.0)
    return f"{int(minutes)}:{seconds:05.2f}"


#: 这些选项允许负值；argparse 会把 ``--gain -4dB`` 误认成新选项
_NUMERIC_OPTIONS = {
    "--gain", "--pan", "--reverb", "--bpm", "--transpose", "--loop-len",
    "--loop-count", "--time", "--dur", "--vel", "--index",
}
_NEGATIVE_VALUE = re.compile(r"^[-+]?\d", re.ASCII)


def _normalize_argv(argv: list[str]) -> list[str]:
    """把 ``--gain -4dB`` 改写成 ``--gain=-4dB``，负数参数不用被迫写等号。"""
    result: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if (token in _NUMERIC_OPTIONS and index + 1 < len(argv)
                and _NEGATIVE_VALUE.match(argv[index + 1])):
            result.append(f"{token}={argv[index + 1]}")
            index += 2
            continue
        result.append(token)
        index += 1
    return result


class _Progress(Progress):
    """兼容旧调用：渲染进度条现在与 ``analyze`` 共用 :class:`musicgen.progress.Progress`。"""


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def _parse_track_selection(project, text: str | None) -> list[int] | None:
    """``--tracks "1,3,Piano"`` → 0 起的轨道下标；没给就返回 None。"""
    if not text:
        return None
    wanted: list[int] = []
    missing: list[str] = []
    for token in text.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if token.lstrip("+").isdigit():
            index = int(token) - 1
            if 0 <= index < len(project.tracks):
                wanted.append(index)
            else:
                missing.append(token)
            continue
        matched = [index for index, track in enumerate(project.tracks)
                   if track.name == token]
        if matched:
            wanted.extend(matched)
        else:
            missing.append(token)
    if missing:
        names = "、".join(f"{i + 1}.{track.name}" for i, track in enumerate(project.tracks))
        print(f"找不到这些轨道：{'、'.join(missing)}", file=sys.stderr)
        print(f"  可选：{names}", file=sys.stderr)
        return None
    return sorted(set(wanted))


def cmd_import_midi(args: argparse.Namespace) -> int:
    """MIDI → musicproj，默认把用到的采样一起拷进工程。"""
    from .midi_import import describe_piece, parse_midi, write_project

    source = Path(args.midi)
    if not source.is_file():
        print(f"找不到 MIDI 文件：{source}", file=sys.stderr)
        return 2
    try:
        piece = parse_midi(source)
    except Exception as exc:  # noqa: BLE001 - MIDI 解析失败给一条干净的错误
        print(f"MIDI 解析失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.bpm:
        piece.bpm = float(args.bpm)
    if not piece.parts:
        print("这个 MIDI 里没有音符", file=sys.stderr)
        return 2

    output = Path(args.output)
    project_path = write_project(piece, output, name=args.name, bundle=not args.no_bundle)
    print(f"已导入 {source.name} → {project_path}")
    print(describe_piece(piece))
    if not args.no_bundle:
        local = output / "instruments"
        count = len([p for p in local.glob("*") if p.is_dir()]) if local.is_dir() else 0
        print(f"  工程自带音源：instruments/（{count} 件乐器）"
              if count else "  工程自带音源：无（全是合成音色）")
    print(f"下一步：python -m musicgen validate \"{project_path}\" && "
          f"python -m musicgen render \"{project_path}\"")
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    """把工程用到的采样拷进工程目录，让工程可以脱离全局缓存渲染。"""
    from .midi_import import bundle_instruments

    try:
        project = load_project(args.project)
    except ProjectError as exc:
        print("工程文件有问题，无法读取：", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return 2
    copied = bundle_instruments(project, project.path.parent)
    if not copied:
        print("没有需要打包的采样音源（工程只用了合成音色/音频轨）")
        return 0
    print(f"已打包 {len(copied)} 件乐器的采样到 {project.path.parent / 'instruments'}")
    print("  " + "、".join(copied))
    print("现在把这个工程目录整个拷走也能直接渲染：不依赖全局 samples_cache。")
    return 0


MUSCRIPTOR_HINT = """MuScriptor 的模型权重是 gated 的，需要先在浏览器里接受许可：

  1. 打开 https://huggingface.co/MuScriptor/muscriptor-small（或 -medium / -large）
     登录后点 Agree，权限会立刻开通；
  2. 在 https://huggingface.co/settings/tokens 建一个 read 令牌；
  3. 设置环境变量后重跑：set HF_TOKEN=hf_xxx

国内网络可加 --endpoint https://hf-mirror.com（默认已设置）。"""


def cmd_transcribe(args: argparse.Namespace) -> int:
    """音频 → MIDI（MuScriptor），可选接着导入成工程。"""
    audio = Path(args.audio)
    if not audio.is_file():
        print(f"找不到音频文件：{audio}", file=sys.stderr)
        return 2
    output = Path(args.output) if args.output else audio.with_name(audio.name + ".mid")
    output.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", args.endpoint or "https://hf-mirror.com")
    command = [sys.executable, "-m", "muscriptor", "transcribe", str(audio),
               "-o", str(output), "--model", args.model]
    if args.instruments:
        command.extend(["--instruments", args.instruments])
    print("调用 MuScriptor：" + " ".join(command))
    try:
        code = subprocess.call(command, env=env)
    except FileNotFoundError:
        print("没装 muscriptor：pip install muscriptor", file=sys.stderr)
        return 2
    if code != 0:
        print("\nMuScriptor 没能完成转谱。", file=sys.stderr)
        print(MUSCRIPTOR_HINT, file=sys.stderr)
        return code
    if not output.is_file():
        print(f"MuScriptor 报告成功，但没有写出 {output}", file=sys.stderr)
        return 3
    print(f"MIDI 已写出：{output}")

    if args.import_to:
        from .midi_import import describe_piece, parse_midi, write_project

        piece = parse_midi(output)
        project_path = write_project(piece, Path(args.import_to), bundle=not args.no_bundle)
        print(f"工程已重建：{project_path}")
        print(describe_piece(piece))
        print(f"下一步：python -m musicgen render \"{project_path}\"")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
    except ProjectError as exc:
        print("工程文件有问题，无法渲染：", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return 2

    output = Path(args.output)
    if output.is_dir() or args.output.endswith(("/", "\\")):
        output = output / f"{project.path.stem}.wav"

    for warning in project.warnings:
        print(f"  {warning}", file=sys.stderr)

    only = _parse_track_selection(project, args.tracks)
    if only is None and args.tracks:
        return 2

    print(f"渲染 {project.name} → {output}")
    picked = f"（指定 {len(only)}/{len(project.tracks)} 条）" if only is not None else ""
    print(
        f"  {len(project.tracks)} 条音轨{picked} | {project.bpm:g}BPM | "
        f"{project.sample_rate}Hz | 时长 {_fmt_seconds(project.compute_length_seconds())}"
    )

    progress = _Progress(enabled=not args.quiet)
    started = time.time()
    try:
        result = render_project(
            project,
            cache_root=args.cache_dir,
            progress=progress,
            normalize=False if args.no_normalize else None,
            only=only,
        )
    except FileNotFoundError as exc:
        progress.done()
        print(f"渲染失败：{exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - 给用户一条干净的错误
        progress.done()
        print(f"渲染失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    progress.done()

    bit_depth = args.float32 and "float32" or args.bits
    write_wav(output, result.audio, result.sample_rate, bit_depth=bit_depth, dither=not args.no_dither)
    elapsed = time.time() - started

    print(f"完成：{output}")
    size_mb = output.stat().st_size / (1024 * 1024)
    print(
        f"  时长 {_fmt_seconds(result.duration)} | 峰值 {result.peak_db:+.1f}dBFS | "
        f"RMS {result.rms_db:+.1f}dBFS | {size_mb:.1f}MB | 位深 "
        f"{'32f' if args.float32 else args.bits}"
    )
    print(f"  音符事件 {result.notes_rendered} 个 | 耗时 {elapsed:.1f}s "
          f"({result.duration / max(elapsed, 1e-6):.1f}× 实时)")

    for stat in result.track_stats:
        effects = f" · 音效 {stat.effects}" if stat.effects else ""
        rms = f" RMS {stat.rms:.3f}" if stat.rms else ""
        if stat.kind == "audio":
            print(f"    · {stat.name} [音频] 峰值 {stat.peak:.3f}{rms}{effects}")
        else:
            print(f"    · {stat.name} [{stat.instrument}] 音符 {stat.notes} "
                  f"峰值 {stat.peak:.3f}{rms}{effects}")
    for warning in result.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
    except ProjectError as exc:
        print(f"校验失败（{len(exc.issues)} 个问题）：", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return 2
    print(f"校验通过：{project.path.name}")
    if project.warnings:
        print(f"  {len(project.warnings)} 条警告：")
        for warning in project.warnings:
            print(f"  {warning}")
    else:
        print("  没有警告")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
    except ProjectError as exc:
        print("工程文件有问题：", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return 2
    print(describe(project))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    try:
        project_file = create_project(
            Path(args.path), args.name, force=args.force, bpm=args.bpm,
            sample_rate=args.sample_rate, beats_per_bar=args.beats_per_bar,
            template=args.template,
        )
    except FileExistsError as exc:
        print(f"{exc}（加 --force 可覆盖）", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"参数无效：{exc}", file=sys.stderr)
        return 1
    print(f"已创建工程：{project_file}")
    print("下一步：python -m musicgen render " + str(project_file))
    return 0


def cmd_samples_fetch(args: argparse.Namespace) -> int:
    instruments: list[str] = []
    if args.all or not args.instrument:
        instruments = list(fetch.SOURCES)
    else:
        for raw in args.instrument:
            for part in raw.split(","):
                key = fetch.resolve(part)
                if part.strip() and key not in instruments:
                    instruments.append(key)
    exit_code = 0
    for instrument in instruments:
        if instrument in fetch.BUILTIN:
            print(f"{fetch.label(instrument)}（{instrument}）：{fetch.BUILTIN[instrument]}，不需要下载。")
            continue
        source = fetch.SOURCES.get(instrument)
        if source is None:
            print(
                "未知乐器："
                + instrument
                + "（可选："
                + "、".join(f"{fetch.label(k)}({k})" for k in fetch.instrument_keys())
                + "）",
                file=sys.stderr,
            )
            exit_code = 1
            continue
        print(f"下载 {fetch.display(instrument)} — {source.title}")
        print(f"  许可：{source.license}")
        print(f"  音域：{source.note_range_label or ''} 来源：{source.homepage}")
        done = {"n": 0}

        def progress(count: int, total: int, note: str) -> None:
            done["n"] = count
            if count % 8 == 0 or count == total:
                print(f"  {count}/{total} … {note}", flush=True)

        try:
            report = fetch.fetch_instrument(
                instrument, cache_root=args.cache_dir, force=args.force, progress=progress
            )
        except fetch.FetchError as exc:
            print(f"下载失败：{exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(report.summary())
        if not report.ok:
            exit_code = 1
    return exit_code


def cmd_samples_list(args: argparse.Namespace) -> int:
    if not fetch.SOURCES:
        print("（没有可用的音源）")
        return 0
    print(f"缓存目录：{args.cache_dir or fetch.default_cache_dir()}")
    for key, source in fetch.SOURCES.items():
        have, want = fetch.instrument_status(key, args.cache_dir)
        state = "就绪" if have >= want else ("部分" if have else "未下载")
        print(f"  {fetch.label(key):<5} {key:<8} {source.title}")
        print(f"           采样 {have}/{want} [{state}] · 音域 {source.note_range_label}")
        print(f"           {source.license}")
        print(f"           {source.homepage}")
    for key, desc in fetch.BUILTIN.items():
        print(f"  {fetch.label(key):<5} {key:<8} {desc}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui.app import run_gui
    except ImportError as exc:
        print(f"无法启动图形界面（缺少 tkinter？）：{exc}", file=sys.stderr)
        print("提示：用带 tkinter 的解释器运行，例如 `py -3 -m musicgen gui`", file=sys.stderr)
        return 1
    return run_gui(args.project)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musicgen",
        description="MusicGen —— 读 *.musicproj 工程文件，渲染出乐器纯音乐 WAV。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python -m musicgen gui
  python -m musicgen render examples/demo/demo.musicproj -o out.wav
  python -m musicgen samples fetch                    # 下载全部乐器音源
  python -m musicgen samples fetch --instrument cello # 只下大提琴
  python -m musicgen track add demo.musicproj --name Guitar --instrument guitar
  python -m musicgen note add demo.musicproj --track Guitar --time 0 --dur 1 --pitch C4
  python -m musicgen track edit demo.musicproj --track 1 --gain -4dB --no-loop
  python -m musicgen track list demo.musicproj
  python -m musicgen effect types
  python -m musicgen effect add demo.musicproj --track 1 --type echo -p time=450 -p mix=0.3
""",
    )
    parser.add_argument("-V", "--version", action="version", version=f"MusicGen {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_cache(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cache-dir", type=Path, default=None,
                       help=f"音源缓存目录（默认 {fetch.default_cache_dir()}）")

    p_render = sub.add_parser("render", help="把工程渲染成 WAV")
    p_render.add_argument("project", help="*.musicproj 文件路径")
    p_render.add_argument("-o", "--output", default=None, help="输出 wav 路径（默认同名 .wav）")
    p_render.add_argument("--bits", type=int, default=24, choices=(16, 24, 32), help="位深（默认 24）")
    p_render.add_argument("--float32", action="store_true", help="输出 32bit 浮点 WAV")
    p_render.add_argument("--no-normalize", action="store_true", help="不做峰值归一化")
    p_render.add_argument("--no-dither", action="store_true", help="不做 TPDF 抖动")
    p_render.add_argument("--tracks", default=None,
                          help="只渲染这些轨：1 起的序号或轨名，逗号分隔（如 1,3,Piano）")
    p_render.add_argument("--quiet", action="store_true", help="不显示进度条")
    add_cache(p_render)
    p_render.set_defaults(func=cmd_render)

    p_validate = sub.add_parser("validate", help="校验工程文件")
    p_validate.add_argument("project")
    p_validate.set_defaults(func=cmd_validate)

    p_trans = sub.add_parser("transcribe", help="音频转 MIDI（MuScriptor），可顺手重建工程")
    p_trans.add_argument("audio", help="输入音频（wav/mp3/flac/…）")
    p_trans.add_argument("-o", "--output", default=None, help="输出 MIDI 路径")
    p_trans.add_argument("--model", default="small", choices=("small", "medium", "large"),
                         help="模型规格（默认 small，CPU 可跑）")
    p_trans.add_argument("--instruments", default=None,
                         help="限定乐器组（逗号分隔，见 muscriptor list-instruments）")
    p_trans.add_argument("--endpoint", default=None, help="HuggingFace 镜像地址")
    p_trans.add_argument("--import-to", default=None, help="转完后直接导入成工程的目录")
    p_trans.add_argument("--no-bundle", action="store_true",
                         help="导入时不把采样拷进工程")
    p_trans.set_defaults(func=cmd_transcribe)

    p_midi = sub.add_parser("import-midi", help="把 MIDI 转成工程（可自带音源）")
    p_midi.add_argument("midi", help="*.mid / *.midi 文件")
    p_midi.add_argument("-o", "--output", required=True, help="工程输出目录")
    p_midi.add_argument("--name", default=None, help="工程名（默认用目录名）")
    p_midi.add_argument("--bpm", type=float, default=None, help="覆盖 MIDI 里的速度")
    p_midi.add_argument("--no-bundle", action="store_true",
                        help="不把采样拷进工程（仍依赖全局 samples_cache）")
    p_midi.set_defaults(func=cmd_import_midi)

    p_bundle = sub.add_parser("bundle", help="把工程用到的采样打包进工程目录")
    p_bundle.add_argument("project")
    p_bundle.set_defaults(func=cmd_bundle)

    p_info = sub.add_parser("info", help="查看工程概要")
    p_info.add_argument("project")
    p_info.set_defaults(func=cmd_info)

    p_init = sub.add_parser("init", help="生成一个示例工程")
    p_init.add_argument("path", help="工程目录")
    p_init.add_argument("--name", default=None, help="工程名（默认用目录名）")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的同名工程")
    p_init.add_argument("--bpm", type=float, default=100.0, help="速度（默认 100）")
    p_init.add_argument("--sample-rate", type=int, default=44100, help="采样率（默认 44100）")
    p_init.add_argument("--beats-per-bar", type=int, default=4, choices=(2, 3, 4, 6),
                        help="每小节拍数（默认 4）")
    p_init.add_argument("--template", default="demo", choices=("demo", "empty"),
                        help="模板：demo=两轨示例，empty=单条空音轨")
    p_init.set_defaults(func=cmd_init)

    p_samples = sub.add_parser("samples", help="音源管理与下载")
    samples_sub = p_samples.add_subparsers(dest="action", required=True)
    p_fetch = samples_sub.add_parser("fetch", help="下载乐器采样")
    p_fetch.add_argument(
        "--instrument", action="append", default=None,
        help="乐器名，可重复或逗号分隔（piano/cello/violin/trumpet，也认中文名）；"
             "不给则下载全部",
    )
    p_fetch.add_argument("--all", action="store_true", help="下载全部已注册乐器")
    p_fetch.add_argument("--force", action="store_true", help="忽略缓存重新下载")
    add_cache(p_fetch)
    p_fetch.set_defaults(func=cmd_samples_fetch)
    p_list = samples_sub.add_parser("list", help="查看音源状态")
    add_cache(p_list)
    p_list.set_defaults(func=cmd_samples_list)

    p_gui = sub.add_parser("gui", help="启动图形界面")
    p_gui.add_argument("project", nargs="?", default=None, help="启动时打开的工程")
    p_gui.set_defaults(func=cmd_gui)

    register_track_commands(sub)
    register_instrument_commands(sub)
    register_effect_commands(sub)
    register_synth_commands(sub)
    register_analyze_command(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalize_argv(raw))

    if args.command == "render" and not args.output:
        args.output = str(Path(args.project).with_suffix(".wav"))

    try:
        return int(args.func(args))
    except TrackEditError as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 1
    except RegistryError as exc:
        print(f"注册表错误：{exc}", file=sys.stderr)
        return 1
    except ProjectError as exc:
        print("工程文件有问题：", file=sys.stderr)
        for issue in exc.issues[:8]:
            print(f"  {issue}", file=sys.stderr)
        return 2
    except AnalysisError as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
