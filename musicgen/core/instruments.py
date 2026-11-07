"""乐器注册表：把音轨里的 ``instrument="piano"`` 变成可渲染的对象。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .sampler import SampleInstrument, SynthInstrument
from ..samples import fetch


class InstrumentRegistry:
    """按需创建并缓存乐器实例。"""

    def __init__(
        self,
        cache_root: Path | None = None,
        sample_rate: int = 44100,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.cache_root = cache_root
        self.sample_rate = sample_rate
        self._warn = warn or (lambda msg: None)
        self._cache: dict[str, SampleInstrument | SynthInstrument] = {}
        self._warned: set[str] = set()

    @property
    def available(self) -> list[str]:
        return list(fetch.SOURCES)

    def synth(self) -> SynthInstrument:
        """兜底合成器（不产生警告），用于采样缺失时顶替。"""
        if "__synth__" not in self._cache:
            self._cache["__synth__"] = SynthInstrument("synth", sample_rate=self.sample_rate)
        return self._cache["__synth__"]

    def get(self, name: str):
        key = fetch.resolve(name)
        if key in self._cache:
            return self._cache[key]

        instrument: SampleInstrument | SynthInstrument
        if key in fetch.SOURCES and fetch.available_notes(key, self.cache_root):
            instrument = SampleInstrument(
                key,
                cache_root=self.cache_root,
                sample_rate=self.sample_rate,
                warn=self._warn,
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
