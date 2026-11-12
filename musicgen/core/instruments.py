"""乐器注册表：把音轨里的 ``instrument="piano"`` 变成可渲染的对象。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .drums import DrumInstrument, pad_label
from .notes import midi_to_name
from .patch import PatchError, PatchInstrument, load_patch
from .sampler import SampleInstrument, SynthInstrument
from ..samples import fetch


class InstrumentRegistry:
    """按需创建并缓存乐器实例。"""

    def __init__(
        self,
        cache_root: Path | None = None,
        sample_rate: int = 44100,
        warn: Callable[[str], None] | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.cache_root = cache_root
        self.sample_rate = sample_rate
        self._warn = warn or (lambda msg: None)
        self.base_dir = base_dir
        self._cache: dict[str, SampleInstrument | SynthInstrument | PatchInstrument] = {}
        self._warned: set[str] = set()

    @property
    def available(self) -> list[str]:
        return fetch.instrument_keys()

    def synth(self) -> SynthInstrument:
        """兜底合成器（不产生警告），用于采样缺失时顶替。"""
        if "__synth__" not in self._cache:
            self._cache["__synth__"] = SynthInstrument("synth", sample_rate=self.sample_rate)
        return self._cache["__synth__"]

    def _synth(self, key: str, ref: str) -> PatchInstrument:
        cache_key = f"@synth:{key}:{ref}"
        if cache_key not in self._cache:
            try:
                patch = load_patch(ref, base_dir=self.base_dir)
            except PatchError as exc:
                self._warn(f"合成音脚本 {ref!r} 载入失败，改用兜底合成器：{exc}")
                return self.synth()
            self._cache[cache_key] = PatchInstrument(
                patch, name=key, sample_rate=self.sample_rate
            )
        return self._cache[cache_key]  # type: ignore[return-value]

    def patch_instrument(self, ref: Path | str, name: str = "") -> PatchInstrument:
        """载入一个脚本文件（项目内 ``<track patch="…">`` 用），失败时退回兜底合成器。"""
        key = f"@file:{Path(ref)}"
        if key not in self._cache:
            try:
                patch = load_patch(ref, base_dir=self.base_dir)
            except PatchError as exc:
                self._warn(f"合成音脚本 {ref} 载入失败，改用兜底合成器：{exc}")
                return self.synth()
            self._cache[key] = PatchInstrument(
                patch, name=name or patch.name, sample_rate=self.sample_rate
            )
        return self._cache[key]  # type: ignore[return-value]

    def get_for_track(self, track) -> object:
        """按音轨取乐器：有 ``patch_path`` 时用脚本，否则走注册表。"""
        patch_path = getattr(track, "patch_path", None)
        if patch_path is not None:
            return self.patch_instrument(patch_path, getattr(track, "instrument", ""))
        return self.get(track.instrument)

    def get(self, name: str):
        key = fetch.resolve(name)
        if key in self._cache:
            return self._cache[key]

        if key in fetch.SYNTHS:
            return self._synth(key, fetch.patch_spec(key))

        if key in fetch.BUILTIN:
            instrument = DrumInstrument(key, sample_rate=self.sample_rate)
            self._cache[key] = instrument
            return instrument

        instrument: SampleInstrument | SynthInstrument
        # 只要有采样就用采样：不限于内置乐器。从原曲分轨切出来的「原曲采样」
        # 也走这条路（键名以 src_ 开头，不在 SOURCES 里），音色才对得上。
        if fetch.available_notes(key, self.cache_root):
            instrument = SampleInstrument(
                key,
                cache_root=self.cache_root,
                sample_rate=self.sample_rate,
                warn=self._warn,
                raw=key not in fetch.SOURCES,
            )
        else:
            instrument = SynthInstrument(key, sample_rate=self.sample_rate)
            if key not in self._warned:
                if key in fetch.SOURCES:
                    self._warn(
                        f"{fetch.label(key)}的采样尚未下载，本次使用内置合成器。"
                        f"运行 `python -m musicgen samples fetch --instrument {key}` "
                        f"或在界面点「音源」后可获得真实音色"
                    )
                else:
                    self._warn(
                        f"未注册的乐器 '{key}'，使用内置合成器。已支持: "
                        + "、".join(f"{fetch.label(k)}({k})" for k in self.available)
                    )
                self._warned.add(key)
        self._cache[key] = instrument
        return instrument


def note_names(instrument: str, pitches) -> str:
    """音符在界面上的写法：鼓轨显示「底鼓/军鼓…」，其余乐器显示音名。"""
    if fetch.resolve(instrument) in fetch.BUILTIN:
        return ",".join(pad_label(p) for p in pitches)
    return ",".join(midi_to_name(p) for p in pitches)
