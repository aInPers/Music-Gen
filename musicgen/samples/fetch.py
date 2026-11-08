"""开源乐器音源获取。

设计要点：
* 只用标准库（urllib），不依赖第三方下载器；
* 每个乐器声明多个镜像 URL 模板，单个文件下载失败/超时自动换下一个镜像；
* 多线程并发下载，整体有明确的总超时预算，不会卡死；
* 下载结果缓存到磁盘，带 manifest，二次渲染不再联网。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..core.notes import midi_to_name
from . import registry as _registry

#: 单个文件每个镜像的下载超时（秒）
PER_FILE_TIMEOUT = 20.0
#: 并发下载线程数
MAX_WORKERS = 8
#: 一次 fetch 的整体时间预算（秒），超出后停止提交新任务
TOTAL_BUDGET = 300.0

_UA = "MusicGen/1.0 (+local render tool)"


def default_cache_dir() -> Path:
    """音源缓存目录：环境变量 > 项目内 samples_cache > 用户目录。"""
    env = os.environ.get("MUSICGEN_SAMPLES")
    if env:
        return Path(env).expanduser()
    project_root = Path(__file__).resolve().parent.parent.parent
    if (project_root / "musicgen").is_dir():
        return project_root / "samples_cache"
    return Path.home() / ".musicgen" / "samples"


@dataclass
class InstrumentSource:
    """一个乐器音源的描述。"""

    key: str
    title: str
    license: str
    homepage: str
    extension: str
    url_templates: list[str]
    midi_low: int = 21
    midi_high: int = 108
    note_range_label: str = ""
    extra: dict = field(default_factory=dict)

    def url_for(self, note_name: str, mirror_index: int) -> str:
        return self.url_templates[mirror_index].format(
            name=note_name, ext=self.extension
        )

    @property
    def mirror_count(self) -> int:
        return len(self.url_templates)

    def midi_range(self) -> Iterable[int]:
        return range(self.midi_low, self.midi_high + 1)


def _fluid_mirrors(gm_name: str) -> list[str]:
    """兼容旧调用：镜像模板现在由 :mod:`registry` 统一生成。"""
    return _registry.fluid_mirrors(gm_name)


_FLUID_LICENSE = _registry.FLUID_LICENSE
_FLUID_HOME = _registry.FLUID_HOME

#: 以下四张表由 :func:`reload_registry` 从磁盘注册表**原地填充**。
#: 其它模块直接引用这些对象，所以注册表变更时必须原地更新（不能整个替换），
#: 否则已经 import 的模块会一直拿着旧字典。
SOURCES: dict[str, InstrumentSource] = {}
BUILTIN: dict[str, str] = {}
LABELS: dict[str, str] = {}
ALIASES: dict[str, str] = {}
GAINS: dict[str, float] = {}


def reload_registry(entries: list[dict] | None = None) -> list[dict]:
    """从注册表 JSON 重建四张表（原地更新，保持对象引用不变）。

    返回本次使用的条目列表，方便调用方显示或校验。文件损坏时
    ``registry.load()`` 会回退内置默认，界面不至于打不开。
    """
    data = _registry.load() if entries is None else entries
    sources: dict[str, InstrumentSource] = {}
    builtins: dict[str, str] = {}
    labels: dict[str, str] = {}
    aliases: dict[str, str] = {}
    gains: dict[str, float] = {}
    for entry in data:
        key = entry["key"]
        labels[key] = entry.get("label") or key
        aliases[key] = key
        aliases[labels[key]] = key
        for alias in entry.get("aliases") or []:
            aliases[alias] = key
            aliases[alias.lower()] = key
        if entry.get("kind") == "builtin":
            builtins[key] = entry.get("description") or "内置乐器"
            continue
        urls = list(entry.get("urls") or _registry.fluid_mirrors(entry.get("gm", "")))
        sources[key] = InstrumentSource(
            key=key,
            title=entry.get("title") or f"自定义音源 — {key}",
            license=entry.get("license") or _FLUID_LICENSE,
            homepage=entry.get("homepage") or _FLUID_HOME,
            extension=entry.get("extension") or "mp3",
            url_templates=urls,
            midi_low=int(entry.get("midi_low", 21)),
            midi_high=int(entry.get("midi_high", 108)),
            note_range_label=entry.get("range_label") or "",
        )
        gains[key] = float(entry.get("gain") or 0) or 10.0
    SOURCES.clear(), SOURCES.update(sources)
    BUILTIN.clear(), BUILTIN.update(builtins)
    LABELS.clear(), LABELS.update(labels)
    ALIASES.clear(), ALIASES.update(aliases)
    GAINS.clear(), GAINS.update(gains)
    return data


def instrument_gain(key: str) -> float | None:
    """注册表登记的采样电平补偿；没有登记时由 sampler 用内置默认值。"""
    return GAINS.get(resolve(key))


def instrument_keys() -> list[str]:
    """界面下拉框里的全部乐器（按注册表顺序）。"""
    return list(LABELS)


reload_registry()


def resolve(name: str | None) -> str:
    """把工程里的乐器名规整成注册键（``大提琴`` → ``cello``）。"""
    key = (name or "piano").strip()
    lowered = key.lower()
    return ALIASES.get(key, ALIASES.get(lowered, lowered))


def label(instrument: str) -> str:
    """中文名（没有登记时退回注册键）。"""
    key = resolve(instrument)
    return LABELS.get(key, key)


def display(instrument: str) -> str:
    """下拉框/日志里的写法：``小提琴 (violin)``。"""
    key = resolve(instrument)
    return f"{LABELS[key]} ({key})" if key in LABELS else key


class FetchError(RuntimeError):
    """音源下载失败。"""


@dataclass
class FetchReport:
    instrument: str
    cache_dir: Path
    downloaded: list[str]
    skipped: list[str]
    failed: dict[str, str]
    elapsed: float

    @property
    def total(self) -> int:
        return len(self.downloaded) + len(self.skipped) + len(self.failed)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        lines = [
            f"乐器: {display(self.instrument)}",
            f"缓存目录: {self.cache_dir}",
            f"总计 {self.total} 个采样 | 新下载 {len(self.downloaded)} | "
            f"已存在 {len(self.skipped)} | 失败 {len(self.failed)} | "
            f"耗时 {self.elapsed:.1f}s",
        ]
        if self.failed:
            lines.append("失败的音符: " + ", ".join(sorted(self.failed)))
        return "\n".join(lines)


def cache_dir_for(instrument: str, cache_root: Path | None = None) -> Path:
    root = Path(cache_root) if cache_root else default_cache_dir()
    return root / instrument


def sample_path(instrument: str, note_name: str, cache_root: Path | None = None) -> Path:
    key = resolve(instrument)
    source = SOURCES.get(key)
    ext = source.extension if source else "wav"
    return cache_dir_for(key, cache_root) / f"{note_name}.{ext}"


def _looks_like_audio(payload: bytes) -> bool:
    """拒绝 404 页面 / 空文件 / HTML 错误页。"""
    if len(payload) < 512:
        return False
    head = payload[:3]
    if head == b"ID3":  # MP3 with ID3 tag
        return True
    if payload[0] == 0xFF and (payload[1] & 0xE0) == 0xE0:  # MP3 frame sync
        return True
    if payload[:4] == b"RIFF":  # WAV
        return True
    if payload[:4] == b"OggS":  # OGG
        return True
    return False


def _download_one(
    source: InstrumentSource,
    note_name: str,
    dest: Path,
    deadline: float,
) -> str:
    """按镜像顺序尝试下载单个采样，返回成功使用的 URL。"""
    errors: list[str] = []
    for idx in range(source.mirror_count):
        if time.monotonic() > deadline:
            errors.append("超出总时间预算")
            break
        url = source.url_for(note_name, idx)
        remaining = max(3.0, min(PER_FILE_TIMEOUT, deadline - time.monotonic()))
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                payload = response.read()
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            errors.append(f"镜像{idx + 1}: {type(exc).__name__}")
            continue
        if not _looks_like_audio(payload):
            errors.append(f"镜像{idx + 1}: 返回内容不是音频")
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(dest)
        return url
    raise FetchError("; ".join(errors) or "所有镜像均失败")


def fetch_instrument(
    instrument: str = "piano",
    cache_root: Path | None = None,
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> FetchReport:
    """下载（或复用）某个乐器的全套采样。"""
    instrument = resolve(instrument)
    source = SOURCES.get(instrument)
    if source is None:
        raise FetchError(
            f"未知乐器 '{instrument}'，可选: "
            + "、".join(f"{LABELS.get(k, k)}({k})" for k in SOURCES)
        )

    target_dir = cache_dir_for(instrument, cache_root)
    target_dir.mkdir(parents=True, exist_ok=True)

    notes = [midi_to_name(m) for m in source.midi_range()]
    todo: list[tuple[str, Path]] = []
    skipped: list[str] = []
    for note in notes:
        path = target_dir / f"{note}.{source.extension}"
        if path.exists() and path.stat().st_size > 512 and not force:
            skipped.append(note)
        else:
            todo.append((note, path))

    started = time.monotonic()
    deadline = started + TOTAL_BUDGET
    downloaded: list[str] = []
    failed: dict[str, str] = {}

    if todo:
        total_todo = len(todo)
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_download_one, source, note, path, deadline): note
                for note, path in todo
            }
            for future in as_completed(futures):
                note = futures[future]
                done += 1
                try:
                    future.result()
                    downloaded.append(note)
                except Exception as exc:  # noqa: BLE001 - 汇总报告给用户
                    failed[note] = str(exc)
                if progress:
                    progress(done, total_todo, note)

    elapsed = time.monotonic() - started
    manifest = target_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "instrument": instrument,
                "title": source.title,
                "license": source.license,
                "homepage": source.homepage,
                "mirrors": source.url_templates,
                "note_range": [source.midi_low, source.midi_high],
                "files": sorted(p.name for p in target_dir.glob(f"*.{source.extension}")),
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = FetchReport(
        instrument=instrument,
        cache_dir=target_dir,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        elapsed=elapsed,
    )
    if len(failed) == len(notes):
        raise FetchError("全部采样下载失败，请检查网络或代理设置\n" + report.summary())
    return report


def available_notes(instrument: str, cache_root: Path | None = None) -> list[int]:
    """已缓存采样的 MIDI 音高列表（升序）。"""
    from ..core.notes import parse_pitch

    instrument = resolve(instrument)
    source = SOURCES.get(instrument)
    ext = source.extension if source else "wav"
    folder = cache_dir_for(instrument, cache_root)
    if not folder.is_dir():
        return []
    result = []
    for path in folder.glob(f"*.{ext}"):
        try:
            result.append(parse_pitch(path.stem))
        except Exception:  # noqa: BLE001 - 忽略非法文件名
            continue
    return sorted(result)


def instrument_status(instrument: str, cache_root: Path | None = None) -> tuple[int, int]:
    """返回（已缓存数量, 应有数量）。"""
    instrument = resolve(instrument)
    source = SOURCES.get(instrument)
    expected = len(list(source.midi_range())) if source else 0
    return len(available_notes(instrument, cache_root)), expected
