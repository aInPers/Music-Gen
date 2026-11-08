"""乐器注册表：可持久化的乐器清单（CLI 与 GUI 共用）。

注册表是一个 JSON 文件（默认 ``~/.musicgen/instruments.json``，可用环境变量
``MUSICGEN_INSTRUMENTS`` 覆盖）。首次运行会自动写入内置默认项；之后用户可以
增删任意条目，``reset()`` 可恢复默认。

本模块只处理**纯数据**（dict / list），不依赖 ``fetch.InstrumentSource``，
由 :func:`musicgen.samples.fetch.reload_registry` 把条目转换成运行时对象，
避免 samples 包内部出现循环导入。
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

VERSION = 1
FLUID_LICENSE = "FluidR3 GM SoundFont（MIT / 自由使用，源自 Frank Wen）"
FLUID_HOME = "https://github.com/gleitz/midi-js-soundfonts"

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.ASCII)

_FLUID_GH = "gh/gleitz/midi-js-soundfonts@gh-pages/FluidR3_GM/{gm}-mp3/{name}.{ext}"
_FLUID_RAW = "gleitz/midi-js-soundfonts/gh-pages/FluidR3_GM/{gm}-mp3/{name}.{ext}"


class RegistryError(RuntimeError):
    """注册表条目不合法或找不到。"""


def fluid_mirrors(gm_name: str) -> list[str]:
    """FluidR3 GM 某件乐器的多镜像地址（jsDelivr → fastly → gcore → GitHub）。"""
    gh = _FLUID_GH.format(gm=gm_name, name="{name}", ext="{ext}")
    raw = _FLUID_RAW.format(gm=gm_name, name="{name}", ext="{ext}")
    return [
        f"https://cdn.jsdelivr.net/{gh}",
        f"https://fastly.jsdelivr.net/{gh}",
        f"https://gcore.jsdelivr.net/{gh}",
        f"https://raw.githubusercontent.com/{raw}",
    ]


#: 内置默认条目。sample = 采样乐器，builtin = 程序化合成（目前只有鼓组）。
DEFAULT_ENTRIES: list[dict[str, Any]] = [
    {
        "key": "piano", "label": "钢琴", "aliases": ["grand piano"],
        "kind": "sample", "gm": "acoustic_grand_piano",
        "title": "FluidR3 GM — Acoustic Grand Piano",
        "midi_low": 21, "midi_high": 108,
        "range_label": "A0–C8（88 键全覆盖）", "gain": 16.0,
    },
    {
        "key": "cello", "label": "大提琴", "aliases": ["violoncello", "大提琴"],
        "kind": "sample", "gm": "cello",
        "title": "FluidR3 GM — Cello",
        "midi_low": 36, "midi_high": 84,
        "range_label": "C2–C6（大提琴常用音域）", "gain": 7.0,
    },
    {
        "key": "violin", "label": "小提琴", "aliases": ["strings", "小提琴"],
        "kind": "sample", "gm": "violin",
        "title": "FluidR3 GM — Violin",
        "midi_low": 55, "midi_high": 96,
        "range_label": "G3–C7（小提琴常用音域）", "gain": 6.0,
    },
    {
        "key": "trumpet", "label": "小号", "aliases": ["小号"],
        "kind": "sample", "gm": "trumpet",
        "title": "FluidR3 GM — Trumpet",
        "midi_low": 52, "midi_high": 84,
        "range_label": "E3–C6（小号常用音域）", "gain": 9.0,
    },
    {
        "key": "recorder", "label": "竖笛",
        "aliases": ["牧童笛", "木笛", "flute", "竖笛"],
        "kind": "sample", "gm": "recorder",
        "title": "FluidR3 GM — Recorder",
        "midi_low": 60, "midi_high": 96,
        "range_label": "C4–C7（竖笛常用音域）", "gain": 5.0,
    },
    {
        "key": "guitar", "label": "吉他",
        "aliases": ["民谣吉他", "木吉他", "acoustic", "acoustic guitar", "吉他"],
        "kind": "sample", "gm": "acoustic_guitar_steel",
        "title": "FluidR3 GM — Acoustic Guitar (steel)",
        "midi_low": 40, "midi_high": 88,
        "range_label": "E2–E6（民谣吉他常用音域）", "gain": 7.0,
    },
    {
        "key": "drums", "label": "架子鼓",
        "aliases": ["架子鼓", "鼓组", "鼓", "打击乐", "drum", "drumkit",
                    "kit", "percussion"],
        "kind": "builtin",
        "description": "内置合成鼓组（GM 打击乐映射）",
    },
]


def path() -> Path:
    """注册表文件路径。"""
    env = os.environ.get("MUSICGEN_INSTRUMENTS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".musicgen" / "instruments.json"


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """校验并补全一个条目，返回规范化后的新 dict。"""
    if not isinstance(raw, dict):
        raise RegistryError("条目必须是对象")
    key = str(raw.get("key") or "").strip().lower()
    if not _KEY_RE.match(key):
        raise RegistryError(f"注册键不合法：{key!r}（只允许小写字母/数字/下划线/连字符）")
    label = str(raw.get("label") or "").strip() or key
    kind = str(raw.get("kind") or "sample").strip().lower()
    if kind not in ("sample", "builtin"):
        raise RegistryError(f"未知乐器类型：{kind}（可选 sample / builtin）")
    aliases = [str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()]
    entry: dict[str, Any] = {
        "key": key, "label": label, "kind": kind, "aliases": aliases,
    }
    if kind == "builtin":
        entry["description"] = str(raw.get("description") or "内置乐器")
        return entry

    gm = str(raw.get("gm") or "").strip()
    urls = [str(u).strip() for u in (raw.get("urls") or []) if str(u).strip()]
    if not gm and not urls:
        raise RegistryError("采样乐器需要 --gm（FluidR3 GM 包名）或 --urls（下载地址）")
    low = int(raw.get("midi_low", 21))
    high = int(raw.get("midi_high", 108))
    if not 0 <= low <= high <= 127:
        raise RegistryError(f"音域不合法：{low}–{high}（需在 0–127 且低音 ≤ 高音）")
    gain = float(raw.get("gain") or 0) or 10.0
    if gain <= 0:
        raise RegistryError(f"增益必须大于 0：{gain}")
    entry.update({
        "gm": gm,
        "urls": urls,
        "title": str(raw.get("title") or f"自定义音源 — {key}"),
        "license": str(raw.get("license") or FLUID_LICENSE),
        "homepage": str(raw.get("homepage") or FLUID_HOME),
        "extension": str(raw.get("extension") or "mp3"),
        "midi_low": low, "midi_high": high,
        "range_label": str(raw.get("range_label") or ""),
        "gain": gain,
    })
    return entry


def defaults() -> list[dict[str, Any]]:
    return copy.deepcopy(DEFAULT_ENTRIES)


def load() -> list[dict[str, Any]]:
    """读取注册表；文件不存在时写入内置默认，损坏时回退默认而不覆盖用户文件。"""
    file = path()
    if not file.is_file():
        entries = defaults()
        save(entries)
        return entries
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        raw = data["instruments"]
        if not isinstance(raw, list):
            raise ValueError("instruments 必须是数组")
        entries = [_normalize(item) for item in raw]
        if len({e["key"] for e in entries}) != len(entries):
            raise ValueError("注册键重复")
        return entries
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RegistryError):
        return defaults()


def save(entries: list[dict[str, Any]]) -> Path:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": VERSION, "instruments": entries}
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return file


def find(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    needle = str(key).strip().lower()
    for entry in entries:
        if entry["key"] == needle:
            return entry
    return None


def add(entries: list[dict[str, Any]], spec: dict[str, Any],
        *, force: bool = False) -> dict[str, Any]:
    """新增条目（写盘），返回规范化后的条目。"""
    entry = _normalize(spec)
    existing = find(entries, entry["key"])
    if existing is not None:
        if not force:
            raise RegistryError(f"乐器 '{entry['key']}' 已存在（加 --force 覆盖）")
        entries[entries.index(existing)] = entry
    else:
        entries.append(entry)
    save(entries)
    return entry


def remove(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """删除条目（写盘），返回被删条目；采样缓存不会被删除。"""
    entry = find(entries, key)
    if entry is None:
        raise RegistryError(f"注册表里没有乐器 '{key}'")
    entries.remove(entry)
    save(entries)
    return entry


def reset() -> list[dict[str, Any]]:
    """恢复内置默认条目（写盘）。"""
    entries = defaults()
    save(entries)
    return entries
