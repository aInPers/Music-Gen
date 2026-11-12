"""``musicgen analyze``：从音频重建工程。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import AnalysisError, AnalysisOptions, analyze, describe_result
from .analysis.separate import DEFAULT_MODEL, MODEL_STEMS
from .progress import Progress


def cmd_analyze(args: argparse.Namespace) -> int:
    source = Path(args.audio)
    if not source.is_file():
        print(f"音频文件不存在：{source}", file=sys.stderr)
        return 1
    output = Path(args.output) if args.output else Path.cwd() / f"{source.stem}-musicproj"

    options = AnalysisOptions(
        source=source,
        output=output,
        name=args.name,
        bpm=args.bpm,
        quantize=args.quantize,
        min_note_ms=max(20, int(args.min_note)),
        model=args.model,
        device=args.device,
        keep_stems=not args.no_stems or args.audio_tracks,
        reuse_stems=not args.no_reuse,
        audio_tracks=args.audio_tracks,
        exact_replay=not args.no_exact_replay,
        force=args.force,
        cache_root=args.cache_dir,
        vocal_instrument=args.vocal_instrument,
        max_polyphony=max(1, min(8, int(args.max_polyphony))),
        layers=not args.no_layers,
        max_layers=max(1, min(4, int(args.max_layers))),
        write_brief=not args.no_brief,
        auto_eq=not args.no_auto_eq,
        master_comp=not args.no_master_comp,
        sample_instruments=not args.no_sampled,
        decisions=Path(args.decisions).expanduser() if args.decisions else None,
    )

    print(f"分析 {source.name} → {output}")
    progress = Progress(enabled=not args.quiet)
    try:
        result = analyze(options, progress=progress)
    except AnalysisError as exc:
        progress.done()
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        progress.done()
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        progress.done()
        raise
    finally:
        progress.done()

    print(describe_result(result))
    print(f"\n下一步：python -m musicgen render \"{result.project_path}\" -o \"{output / (source.stem + '.wav')}\"")
    return 0


def register_analyze_command(subparsers) -> None:
    parser = subparsers.add_parser(
        "analyze",
        help="分析音频并重建 musicproj 工程",
        description=(
            "用 Demucs 把人声与各件乐器分开，再用 FFT 做转谱与音色识别，"
            "最后写出可直接渲染的 *.musicproj 工程。人声不转谱，整条分轨作为一条音频轨。"
        ),
    )
    parser.add_argument("audio", help="输入音频（wav/mp3/flac/ogg/aiff）")
    parser.add_argument("-o", "--output", default=None,
                        help="输出工程目录（默认 ./<音频名>-musicproj）")
    parser.add_argument("--name", default=None, help="工程名（默认用音频文件名）")
    parser.add_argument("--bpm", type=float, default=None,
                        help="手动指定速度；不给则自动检测")
    parser.add_argument("--quantize", default="1/16",
                        choices=("1/4", "1/8", "1/16", "1/32", "off"),
                        help="音符量化网格（默认 1/16，off 表示不量化）；"
                             "精确回放（默认）不量化，该参数只在 --no-exact-replay 下生效")
    parser.add_argument("--min-note", type=float, default=65,
                        help="最短音符长度，毫秒（默认 65）")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=tuple(MODEL_STEMS),
                        help=f"Demucs 模型（默认 {DEFAULT_MODEL}，6 轨含钢琴/吉他）")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"),
                        help="推理设备（默认 auto）")
    parser.add_argument(
        "--audio-tracks", action="store_true",
        help="只分离、不转谱：工程里直接引用分轨 WAV（保留原始音色，最快最保真）",
    )
    parser.add_argument(
        "--no-exact-replay", action="store_true",
        help="关掉精确回放：回到「量化到网格 + 逐事件采样 + 自动增益/EQ/母带压缩」的旧行为",
    )
    parser.add_argument("--no-stems", action="store_true", help="不保存分轨 WAV")
    parser.add_argument("--no-reuse", action="store_true", help="忽略分轨缓存，重新分离")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的工程")
    parser.add_argument("--vocal-instrument", default="vocal",
                        help="已废弃：人声固定为音频轨，此参数保留兼容旧脚本，不再生效")
    parser.add_argument("--max-polyphony", type=int, default=6,
                        help="每轨最多同时检测几个音（默认 6）")
    parser.add_argument("--no-layers", action="store_true",
                        help="不按音区拆分「其他」分轨（默认会拆成低/中/高音区）")
    parser.add_argument("--max-layers", type=int, default=3,
                        help="每条分轨最多拆几层（默认 3）")
    parser.add_argument("--no-brief", action="store_true",
                        help="不写 reverse_brief.json（默认写出，供大模型判断）")
    parser.add_argument("--no-auto-eq", action="store_true",
                        help="不自动补 EQ（默认按「渲染 vs 原分轨」的频谱差补 3dB 以上的差）")
    parser.add_argument("--no-sampled", action="store_true",
                        help="不用原曲采样当音色（默认从分轨切采样，音色即原曲）")
    parser.add_argument("--no-master-comp", action="store_true",
                        help="不在母带补压缩（默认按原曲峰值因数压掉转谱多出来的瞬态）")
    parser.add_argument("--decisions", default=None,
                        help="应用决策文件（大模型/人工给出的音色、效果、删轨结论）")
    parser.add_argument("--quiet", action="store_true", help="不显示进度条")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="音源缓存目录（音色模板用，默认与渲染相同）")
    parser.set_defaults(func=cmd_analyze)
