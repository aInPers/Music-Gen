"""按「音符事件」播放原始片段：一个音一个采样，变速比恒为 1。

为什么不用「每种音高一个采样 + 变速」：那套要求检测出的音高是绝对正确的。
实测合成层里十个采样有八个音高标错（差 12–36 个半音），只要名字错，
整条轨就整体移调——听起来就是「音高错了」，而且错得整齐。

改成每个音播它自己的原始片段：音高、音色、起音、咬字全都是原样，
不依赖任何音高判断。改成别的音高时才按相对音高变速（``shift`` 参数），
所以「不必知道绝对音高」——只要知道自己相对基准差几个半音就够了。

体积靠去重控制：同一个音高、而且频谱几乎一样的多个事件，共用一个采样。
去重判断保守（只在非常像的时候才合并），合错了最多是多占空间，不会变调。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .sampler import _load_audio


class EventSampleBank:
    """``<工程>/samples/*.wav`` 里的逐事件采样。"""

    def __init__(self, root: Path | str | None, sample_rate: int = 44100) -> None:
        self.root = Path(root) if root else None
        self.sample_rate = sample_rate
        self._cache: dict[str, np.ndarray | None] = {}
        self.missing = 0

    # ---- 查询 ---------------------------------------------------------
    def has(self, name: str | None) -> bool:
        if not name or self.root is None:
            return False
        return (self.root / f"{name}.wav").is_file()

    def _get(self, name: str) -> np.ndarray | None:
        if name in self._cache:
            return self._cache[name]
        path = self.root / f"{name}.wav" if self.root else None
        if path is None or not path.is_file():
            self._cache[name] = None
            return None
        try:
            audio, rate = _load_audio(path)
        except Exception:  # noqa: BLE001 - 单个采样坏了不该中断渲染
            self._cache[name] = None
            return None
        if rate != self.sample_rate:
            from .sampler import _resample

            audio = _resample(audio, rate / self.sample_rate)
        self._cache[name] = audio
        return audio

    # ---- 渲染 ---------------------------------------------------------
    def render(self, name: str, duration: float, shift: int = 0) -> np.ndarray | None:
        """播放一个事件采样。

        ``shift`` 只在这个音被改成别的音高时才非零（相对音高），
        正常播放恒为 0——原样输出，音高绝对正确。
        """
        audio = self._get(name)
        if audio is None:
            self.missing += 1
            return None

        if shift:
            from .sampler import _bend_play

            audio = _bend_play(audio, shift, [(0.0, 0.0), (1.0, 0.0)])

        total = audio.shape[1]
        hold = max(1, int(round(duration * self.sample_rate)))
        # 精确回放：片段长度与音符时值一致，说明这个音没被改过时值。
        # 原样输出（不截断、不淡入淡出）；sqrt(2) 预补偿渲染端 pan=0 的
        # 等功率声像（两声道各 0.707），立体声素材不该因此轻 3dB。
        if abs(hold - total) <= max(1, int(0.002 * self.sample_rate)):
            return audio * math.sqrt(2.0)
        fade = min(int(0.02 * self.sample_rate), max(1, total // 4))
        if hold >= total:
            seg = audio.copy()
        else:
            end = min(total, hold + fade)
            seg = audio[:, :end].copy()
        if fade > 1 and seg.shape[1] > fade:
            ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32) ** 1.4
            seg[:, -fade:] *= ramp
        attack = min(int(0.002 * self.sample_rate), seg.shape[1] // 4)
        if attack > 1:
            seg[:, :attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
        return seg
