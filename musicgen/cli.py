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
import re
import sys
import time
from pathlib import Path

from . import __version__
from .cli_effects import register_effect_commands
from .cli_instruments import RegistryError, register_instrument_commands
from .cli_tracks import TrackEditError, register_track_commands
from .core.project import ProjectError, create_project, describe, load_project
from .core.render import render_project
from .core.wavio import write_wav
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


class _Progress:
    """终端进度条，非 TTY 环境退化为按行输出。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.is_tty = sys.stderr.isatty()
        self._last = -1.0
        self._start = time.time()

    def __call__(self, fraction: float, message: str) -> None:
        if not self.enabled:
            return
        if self.is_tty:
            if fraction - self._last < 0.02 and fraction < 1.0:
                return
            self._last = fraction
            width = 28
            filled = int(width * fraction)
            bar = "█" * filled + "░" * (width - filled)
            sys.stderr.write(f"\r  [{bar}] {fraction * 100:5.1f}%  {message[:28]:<28}")
            sys.stderr.flush()
        elif fraction >= 1.0 or fraction - self._last >= 0.25:
            self._last = fraction
            sys.stderr.write(f"  {fraction * 100:5.1f}%  {message}\n")
            sys.stderr.flush()

    def done(self) -> None:
        if self.enabled and self.is_tty:
            sys.stderr.write("\n")
            sys.stderr.flush()


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


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

    print(f"渲染 {project.name} → {output}")
    print(
        f"  {len(project.tracks)} 条音轨 | {project.bpm:g}BPM | "
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
        print(f"    · {stat.name} [{stat.instrument}] 音符 {stat.notes} 峰值 {stat.peak:.3f}{effects}")
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
    p_render.add_argument("--quiet", action="store_true", help="不显示进度条")
    add_cache(p_render)
    p_render.set_defaults(func=cmd_render)

    p_validate = sub.add_parser("validate", help="校验工程文件")
    p_validate.add_argument("project")
    p_validate.set_defaults(func=cmd_validate)

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
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
