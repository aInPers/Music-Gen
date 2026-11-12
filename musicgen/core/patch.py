"""合成音脚本（patch）：用一份 JSON 描述一件合成音色。

工程里的乐器既可以是采样音源，也可以是一段合成音脚本。脚本是**纯数据**，
不含可执行代码，便于大模型生成、人工审阅与版本管理：

.. code-block:: json

    {
      "name": "warm_pad",
      "label": "暖垫",
      "gain": 0.22,
      "oscillators": [
        {"wave": "saw", "detune": -7, "level": 0.5},
        {"wave": "saw", "detune": 7, "level": 0.5, "pan": 0.3}
      ],
      "sub": {"wave": "sine", "octave": -1, "level": 0.2},
      "noise": {"level": 0.01, "decay": 0.5},
      "amp": {"attack": 0.45, "decay": 1.2, "sustain": 0.75, "release": 1.4},
      "filter": {"cutoff": 1600, "resonance": 1.1, "env": 900, "env_decay": 0.8},
      "vibrato": {"rate": 4.6, "depth": 6, "delay": 0.25},
      "fm": {"ratio": 2.0, "index": 0.4, "decay": 0.6}
    }

设计约束：

* **确定性**：噪声用固定种子的伪随机数，同一脚本 + 同一音符渲染结果完全一致；
* **带限**：锯齿/方波用谐波叠加合成，谐波数按 Nyquist 截断，不产生混叠；
* **可缺省**：除 ``oscillators`` 外全部字段都有默认值，最短的脚本可以只有振荡器；
* **中文别名**：波形可以写 ``锯齿`` / ``方波`` / ``三角`` / ``正弦`` / ``噪声``。
"""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

EPS = 1e-9

#: 允许的波形：``别名 -> 规范名``
WAVES = {
    "sine": "sine", "sin": "sine", "正弦": "sine", "正弦波": "sine",
    "saw": "saw", "sawtooth": "saw", "锯齿": "saw", "锯齿波": "saw",
    "square": "square", "sqr": "square", "方波": "square",
    "triangle": "triangle", "tri": "triangle", "三角": "triangle", "三角波": "triangle",
    "noise": "noise", "噪声": "noise", "白噪": "noise",
}

#: 每种波形的谐波权重：``(次数, 幅度, 相位偏移)``
_WAVE_SPECS: dict[str, tuple[float, float]] = {
    # (幅度随谐波次数的幂次, 是否只取奇次谐波)
    "saw": (1.0, 0.0),
    "square": (1.0, 1.0),
    "triangle": (2.0, 1.0),
}


class PatchError(ValueError):
    """脚本不合法（字段类型错、波形未知、数值越界）。"""


def _number(value: Any, what: str, default: float,
            minimum: float | None = None, maximum: float | None = None) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool):
        raise PatchError(f"{what} 需要一个数字，收到布尔值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PatchError(f"{what} 需要一个数字，收到 {value!r}") from exc
    if not math.isfinite(result):
        raise PatchError(f"{what} 不是有限数值：{value!r}")
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _wave_name(value: Any, what: str = "wave") -> str:
    text = str(value or "sine").strip().lower()
    if text not in WAVES:
        raise PatchError(f"{what} 不认识的波形 {value!r}，可用：{'、'.join(sorted(set(WAVES.values())))}")
    return WAVES[text]


@dataclass
class Oscillator:
    wave: str = "sine"
    octave: int = 0
    detune: float = 0.0        # 音分
    level: float = 1.0
    pan: float = 0.0           # -1 左 / +1 右
    phase: float = 0.0         # 起始相位（0–1 个周期）

    @classmethod
    def from_dict(cls, data: Any, index: int = 0) -> "Oscillator":
        if isinstance(data, str):  # 允许 "saw" 这样的简写
            return cls(wave=_wave_name(data))
        if not isinstance(data, dict):
            raise PatchError(f"oscillators[{index}] 必须是对象或波形名")
        return cls(
            wave=_wave_name(data.get("wave")),
            octave=int(_number(data.get("octave"), f"oscillators[{index}].octave", 0, -3, 3)),
            detune=_number(data.get("detune"), f"oscillators[{index}].detune", 0, -100, 100),
            level=_number(data.get("level"), f"oscillators[{index}].level", 1.0, 0, 4),
            pan=_number(data.get("pan"), f"oscillators[{index}].pan", 0, -1, 1),
            phase=_number(data.get("phase"), f"oscillators[{index}].phase", 0, -1, 1),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"wave": self.wave, "level": round(self.level, 3)}
        if self.octave:
            data["octave"] = self.octave
        if self.detune:
            data["detune"] = round(self.detune, 2)
        if self.pan:
            data["pan"] = round(self.pan, 3)
        if self.phase:
            data["phase"] = round(self.phase, 3)
        return data


@dataclass
class AmpEnvelope:
    attack: float = 0.01
    decay: float = 0.35
    sustain: float = 0.7
    release: float = 0.6

    @classmethod
    def from_dict(cls, data: Any) -> "AmpEnvelope":
        data = data or {}
        if not isinstance(data, dict):
            raise PatchError("amp 必须是对象")
        return cls(
            attack=_number(data.get("attack"), "amp.attack", 0.01, 0.0, 8.0),
            decay=_number(data.get("decay"), "amp.decay", 0.35, 0.0, 12.0),
            sustain=_number(data.get("sustain"), "amp.sustain", 0.7, 0.0, 1.5),
            release=_number(data.get("release"), "amp.release", 0.6, 0.0, 12.0),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "attack": round(self.attack, 4), "decay": round(self.decay, 4),
            "sustain": round(self.sustain, 4), "release": round(self.release, 4),
        }


@dataclass
class FilterSpec:
    cutoff: float = 12000.0
    resonance: float = 0.7
    env: float = 0.0           # 包络把截止频率最多推高多少 Hz
    env_decay: float = 0.4
    keytrack: float = 0.0      # 0 = 固定；1 = 跟随基频（每八度翻倍）

    @classmethod
    def from_dict(cls, data: Any) -> "FilterSpec":
        data = data or {}
        if not isinstance(data, dict):
            raise PatchError("filter 必须是对象")
        return cls(
            cutoff=_number(data.get("cutoff"), "filter.cutoff", 12000, 40, 20000),
            resonance=_number(data.get("resonance"), "filter.resonance", 0.7, 0.3, 8.0),
            env=_number(data.get("env"), "filter.env", 0.0, 0, 16000),
            env_decay=_number(data.get("env_decay"), "filter.env_decay", 0.4, 0.01, 8.0),
            keytrack=_number(data.get("keytrack"), "filter.keytrack", 0.0, 0, 1),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "cutoff": round(self.cutoff, 1), "resonance": round(self.resonance, 3),
            "env": round(self.env, 1), "env_decay": round(self.env_decay, 3),
            "keytrack": round(self.keytrack, 3),
        }


@dataclass
class FMSpec:
    ratio: float = 2.0
    index: float = 0.0
    decay: float = 0.5

    @classmethod
    def from_dict(cls, data: Any) -> "FMSpec":
        data = data or {}
        if not isinstance(data, dict):
            raise PatchError("fm 必须是对象")
        return cls(
            ratio=_number(data.get("ratio"), "fm.ratio", 2.0, 0.25, 16.0),
            index=_number(data.get("index"), "fm.index", 0.0, 0.0, 8.0),
            decay=_number(data.get("decay"), "fm.decay", 0.5, 0.02, 8.0),
        )

    def to_dict(self) -> dict[str, float]:
        return {"ratio": round(self.ratio, 3), "index": round(self.index, 3),
                "decay": round(self.decay, 3)}


@dataclass
class VibratoSpec:
    rate: float = 5.0
    depth: float = 0.0         # 音分
    delay: float = 0.2

    @classmethod
    def from_dict(cls, data: Any) -> "VibratoSpec":
        data = data or {}
        if not isinstance(data, dict):
            raise PatchError("vibrato 必须是对象")
        return cls(
            rate=_number(data.get("rate"), "vibrato.rate", 5.0, 0.1, 12.0),
            depth=_number(data.get("depth"), "vibrato.depth", 0.0, 0.0, 100.0),
            delay=_number(data.get("delay"), "vibrato.delay", 0.2, 0.0, 3.0),
        )

    def to_dict(self) -> dict[str, float]:
        return {"rate": round(self.rate, 3), "depth": round(self.depth, 2),
                "delay": round(self.delay, 3)}


@dataclass
class Patch:
    """一件合成音色的完整描述。"""

    name: str = "patch"
    label: str = ""
    gain: float = 0.22
    oscillators: list[Oscillator] = field(default_factory=lambda: [Oscillator("saw")])
    sub: Oscillator | None = None
    noise_level: float = 0.0
    noise_decay: float = 0.4
    amp: AmpEnvelope = field(default_factory=AmpEnvelope)
    filter: FilterSpec = field(default_factory=FilterSpec)
    fm: FMSpec = field(default_factory=FMSpec)
    vibrato: VibratoSpec = field(default_factory=VibratoSpec)
    #: 允许渲染的最高谐波次数（控制 CPU 与亮度）
    max_harmonics: int = 32
    source: Path | None = None

    # ---- 解析 ---------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any], source: Path | None = None) -> "Patch":
        if not isinstance(data, dict):
            raise PatchError("脚本根节点必须是对象")
        unknown = set(data) - {
            "name", "label", "gain", "oscillators", "sub", "noise", "amp", "filter",
            "fm", "vibrato", "max_harmonics", "description", "$schema",
        }
        if unknown:
            raise PatchError(f"不认识的字段：{'、'.join(sorted(unknown))}")
        raw_osc = data.get("oscillators")
        if raw_osc is None:
            raw_osc = [{"wave": "sine"}]
        if not isinstance(raw_osc, list) or not raw_osc:
            raise PatchError("oscillators 必须是非空数组")
        if len(raw_osc) > 8:
            raise PatchError("振荡器最多 8 个")
        oscillators = [Oscillator.from_dict(item, index) for index, item in enumerate(raw_osc)]
        sub_raw = data.get("sub")
        sub = None if sub_raw in (None, False) else Oscillator.from_dict(sub_raw, 0)
        if sub is not None:
            sub.octave = -1 if sub.octave == 0 else sub.octave
        noise = data.get("noise") or {}
        if noise and not isinstance(noise, dict):
            raise PatchError("noise 必须是对象")
        return cls(
            name=str(data.get("name") or (source.stem if source else "patch")),
            label=str(data.get("label") or ""),
            gain=_number(data.get("gain"), "gain", 0.22, 0.0, 1.5),
            oscillators=oscillators,
            sub=sub,
            noise_level=_number(noise.get("level"), "noise.level", 0.0, 0.0, 0.6),
            noise_decay=_number(noise.get("decay"), "noise.decay", 0.4, 0.01, 6.0),
            amp=AmpEnvelope.from_dict(data.get("amp")),
            filter=FilterSpec.from_dict(data.get("filter")),
            fm=FMSpec.from_dict(data.get("fm")),
            vibrato=VibratoSpec.from_dict(data.get("vibrato")),
            max_harmonics=int(_number(data.get("max_harmonics"), "max_harmonics", 32, 1, 128)),
            source=source,
        )

    @classmethod
    def load(cls, path: Path | str) -> "Patch":
        file = Path(path).expanduser()
        if not file.is_file():
            raise PatchError(f"合成音脚本不存在：{file}")
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PatchError(f"{file.name} 不是合法 JSON：第 {exc.lineno} 行 {exc.msg}") from exc
        except OSError as exc:
            raise PatchError(f"{file} 读取失败：{exc}") from exc
        return cls.from_dict(data, source=file)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "gain": round(self.gain, 3),
            "oscillators": [osc.to_dict() for osc in self.oscillators],
            "amp": self.amp.to_dict(),
            "filter": self.filter.to_dict(),
        }
        if self.label:
            data["label"] = self.label
        if self.sub is not None:
            data["sub"] = self.sub.to_dict()
        if self.noise_level > 0:
            data["noise"] = {"level": round(self.noise_level, 4),
                             "decay": round(self.noise_decay, 3)}
        if self.fm.index > 0:
            data["fm"] = self.fm.to_dict()
        if self.vibrato.depth > 0:
            data["vibrato"] = self.vibrato.to_dict()
        if self.max_harmonics != 32:
            data["max_harmonics"] = self.max_harmonics
        return data

    def save(self, path: Path | str) -> Path:
        file = Path(path).expanduser()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return file

    # ---- 渲染 ---------------------------------------------------------
    def render(self, midi: int, velocity: float, duration: float,
               sample_rate: int = 44100) -> np.ndarray:
        """渲染一个音符，返回 ``(2, N)`` 立体声。"""
        hold = max(1, int(round(max(duration, 0.02) * sample_rate)))
        release = max(0.02, self.amp.release)
        tail = int(min(4.0, release) * sample_rate)
        total = hold + tail
        t = np.arange(total, dtype=np.float64) / sample_rate
        base = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        if base > sample_rate * 0.45:
            base = sample_rate * 0.45

        amp = self._amplitude(t, hold, total, sample_rate)
        envelope_norm = float(np.max(amp)) or 1.0
        amp = amp / envelope_norm

        left = np.zeros(total, dtype=np.float64)
        right = np.zeros(total, dtype=np.float64)
        levels = sum(max(0.0, osc.level) for osc in self.oscillators)
        levels = levels or 1.0
        for osc in self.oscillators:
            signal = self._oscillator(osc, base, t, sample_rate)
            weight = max(0.0, osc.level) / levels
            pan = max(-1.0, min(1.0, osc.pan))
            left += signal * weight * math.sqrt(0.5 * (1.0 - pan))
            right += signal * weight * math.sqrt(0.5 * (1.0 + pan))
        if self.sub is not None:
            signal = self._oscillator(self.sub, base, t, sample_rate)
            weight = max(0.0, self.sub.level) * 0.6
            left += signal * weight
            right += signal * weight
        if self.noise_level > 0:
            noise = self._noise(midi, total, sample_rate)
            left += noise * self.noise_level
            right += noise * self.noise_level * 0.92

        gain = self.gain * max(0.0, min(1.0, velocity)) ** 1.25
        stereo = np.vstack([left, right])
        # 先把裸波形归一化到峰值 1，再按 gain 定电平：不同振荡器组合（1 个正弦
        # 与 3 个锯齿）的原始幅度差很多，不归一化的话音色一换就得重调音量。
        peak = float(np.max(np.abs(stereo)))
        if peak > EPS:
            stereo = stereo / peak
        stereo *= amp[None, :] * gain * 3.0
        return np.ascontiguousarray(stereo, dtype=np.float32)

    # ---- 内部 ---------------------------------------------------------
    def _amplitude(self, t: np.ndarray, hold: int, total: int,
                   sample_rate: int) -> np.ndarray:
        """ADSR：起音线性、衰减指数趋近 sustain、松手后指数收尾。"""
        env = self.amp
        attack = max(1e-4, float(env.attack))
        decay = max(1e-4, float(env.decay))
        release = max(0.02, float(env.release))

        amp = np.minimum(1.0, t / attack)
        after = np.maximum(0.0, t - attack)
        fall = env.sustain + (1.0 - env.sustain) * np.exp(-after / (decay / 3.0))
        amp = np.where(t < attack, amp, fall)

        if total > hold:
            tail_t = np.arange(total - hold, dtype=np.float64) / sample_rate
            # 指数收尾，最后 8ms 再乘一段线性淡出，避免尾部「咔」声
            fade = np.exp(-tail_t / (release / 3.0))
            edge = min(64, fade.size)
            if edge > 1:
                fade[-edge:] *= np.linspace(1.0, 0.0, edge)
            amp[hold:] *= fade
        return np.maximum(amp, 0.0)

    def _oscillator(self, osc: Oscillator, base: float, t: np.ndarray,
                    sample_rate: int) -> np.ndarray:
        freq = base * (2.0 ** osc.octave) * (2.0 ** (osc.detune / 1200.0))
        if osc.wave == "noise":
            return self._noise(int(round(freq)), t.size, sample_rate)
        phase = 2.0 * math.pi * freq * t
        if self.vibrato.depth > 0:
            delay = self.vibrato.delay
            mod = np.clip((t - delay) / 0.25, 0.0, 1.0)
            mod *= np.sin(2.0 * math.pi * self.vibrato.rate * t)
            # 音分 → 频率比：2^(cent/1200) ≈ 1 + cent·ln2/1200
            instantaneous = freq * (1.0 + (self.vibrato.depth / 1200.0) * 0.693 * mod)
            phase = 2.0 * math.pi * np.cumsum(instantaneous) / sample_rate
        if self.fm.index > 0:
            mod_env = np.exp(-t / max(0.02, self.fm.decay))
            phase = phase + self.fm.index * mod_env * np.sin(self.fm.ratio * phase)
        return _band_limited(phase, osc.wave, freq, sample_rate,
                             osc.phase, self.max_harmonics)

    def _noise(self, seed: int, total: int, sample_rate: int) -> np.ndarray:
        # hash() 受 PYTHONHASHSEED 影响，跨进程不稳定；用 crc32 保证同一脚本同一声部
        # 每次渲染都得到完全相同的噪声。
        token = f"{self.name}:{int(seed)}".encode("utf-8")
        rng = np.random.default_rng(zlib.crc32(token))
        noise = rng.standard_normal(total)
        decay = np.exp(-np.arange(total) / max(1.0, self.noise_decay * sample_rate / 3.0))
        return noise * decay


def _band_limited(phase: np.ndarray, wave: str, freq: float, sample_rate: int,
                  start_phase: float, max_harmonics: int) -> np.ndarray:
    """按谐波叠加生成带限波形（不做混叠）。"""
    limit = int(min(max_harmonics, max(1, (sample_rate * 0.45) // max(freq, 1.0))))
    if wave == "sine" or limit < 2:
        return np.sin(phase + 2.0 * math.pi * start_phase)
    power, odd_only = _WAVE_SPECS.get(wave, (1.0, 0.0))
    total = np.zeros_like(phase)
    normalizer = 0.0
    for harmonic in range(1, limit + 1):
        if odd_only and harmonic % 2 == 0:
            continue
        weight = 1.0 / (harmonic ** power)
        if wave == "triangle" and harmonic % 4 == 3:
            weight = -weight
        total += weight * np.sin(harmonic * (phase + 2.0 * math.pi * start_phase))
        normalizer += abs(weight)
    return total / (normalizer or 1.0)


class PatchInstrument:
    """把 :class:`Patch` 包装成和采样乐器一致的可渲染对象。"""

    instrument_gain = 0.25

    def __init__(self, patch: Patch, name: str = "", sample_rate: int = 44100) -> None:
        self.patch = patch
        self.name = name or patch.name
        self.sample_rate = sample_rate

    @property
    def ready(self) -> bool:
        return True

    def describe(self) -> str:
        waves = "、".join(sorted({osc.wave for osc in self.patch.oscillators}))
        return f"合成音脚本「{self.patch.label or self.patch.name}」（{waves}，增益 {self.patch.gain:g}）"

    def render_note(self, midi: int, velocity: float, duration: float,
                    bend: list[tuple[float, float]] | None = None) -> np.ndarray:
        return self.patch.render(midi, velocity, duration, self.sample_rate)


# ---------------------------------------------------------------------------
# 内置音色
# ---------------------------------------------------------------------------

_BUILTIN_DATA: dict[str, dict[str, Any]] = {
    "warm_pad": {
        "label": "暖垫", "gain": 0.20,
        "oscillators": [
            {"wave": "saw", "detune": -8, "level": 0.5, "pan": -0.35},
            {"wave": "saw", "detune": 8, "level": 0.5, "pan": 0.35},
            {"wave": "triangle", "octave": 1, "level": 0.18},
        ],
        "amp": {"attack": 0.55, "decay": 1.4, "sustain": 0.82, "release": 1.6},
        "filter": {"cutoff": 1500, "resonance": 1.0, "env": 900, "env_decay": 1.2,
                   "keytrack": 0.35},
        "vibrato": {"rate": 4.2, "depth": 5, "delay": 0.5},
    },
    "glass_bell": {
        "label": "玻璃铃", "gain": 0.24,
        "oscillators": [{"wave": "sine", "level": 0.7},
                        {"wave": "sine", "octave": 1, "detune": 4, "level": 0.25}],
        "amp": {"attack": 0.004, "decay": 0.9, "sustain": 0.12, "release": 1.2},
        "fm": {"ratio": 3.51, "index": 1.6, "decay": 0.35},
        "filter": {"cutoff": 9000, "resonance": 0.7},
    },
    "analog_lead": {
        "label": "模拟主音", "gain": 0.19,
        "oscillators": [{"wave": "square", "detune": -4, "level": 0.45},
                        {"wave": "saw", "detune": 6, "level": 0.55}],
        "amp": {"attack": 0.02, "decay": 0.25, "sustain": 0.72, "release": 0.35},
        "filter": {"cutoff": 2400, "resonance": 2.2, "env": 3200, "env_decay": 0.35,
                   "keytrack": 0.5},
        "vibrato": {"rate": 5.4, "depth": 14, "delay": 0.22},
    },
    "deep_bass": {
        "label": "深贝斯", "gain": 0.26,
        "oscillators": [{"wave": "saw", "level": 0.6}],
        "sub": {"wave": "sine", "level": 0.9},
        "amp": {"attack": 0.006, "decay": 0.28, "sustain": 0.7, "release": 0.18},
        "filter": {"cutoff": 420, "resonance": 1.3, "env": 700, "env_decay": 0.22,
                   "keytrack": 0.6},
    },
    "pluck_string": {
        "label": "拨弦", "gain": 0.22,
        "oscillators": [{"wave": "triangle", "level": 0.55},
                        {"wave": "saw", "detune": 5, "level": 0.45}],
        "noise": {"level": 0.03, "decay": 0.06},
        "amp": {"attack": 0.003, "decay": 0.55, "sustain": 0.08, "release": 0.4},
        "filter": {"cutoff": 2200, "resonance": 1.6, "env": 4200, "env_decay": 0.2,
                   "keytrack": 0.55},
    },
    "soft_keys": {
        "label": "柔键盘", "gain": 0.21,
        "oscillators": [{"wave": "sine", "level": 0.7},
                        {"wave": "triangle", "octave": 1, "level": 0.22}],
        "amp": {"attack": 0.02, "decay": 1.1, "sustain": 0.35, "release": 0.9},
        "filter": {"cutoff": 2600, "resonance": 0.8, "env": 1200, "env_decay": 0.7},
    },
    "air_texture": {
        "label": "空气质感", "gain": 0.10,
        "oscillators": [{"wave": "noise", "level": 0.5},
                        {"wave": "sine", "octave": 1, "level": 0.12}],
        "noise": {"level": 0.22, "decay": 1.8},
        "amp": {"attack": 1.2, "decay": 2.0, "sustain": 0.6, "release": 2.4},
        "filter": {"cutoff": 5200, "resonance": 0.6, "env": 2600, "env_decay": 2.0},
    },
}


def builtin_patches() -> dict[str, Patch]:
    """内置音色（每次返回新对象，调用方可随意修改）。"""
    result = {}
    for key, data in _BUILTIN_DATA.items():
        payload = dict(data)
        payload["name"] = key
        result[key] = Patch.from_dict(payload)
    return result


def load_patch(ref: str | Path, base_dir: Path | None = None) -> Patch:
    """按「内置名 / 文件路径」加载音色脚本。"""
    text = str(ref or "").strip()
    if not text:
        raise PatchError("空的合成音脚本引用")
    builtin = builtin_patches()
    if text in builtin:
        return builtin[text]
    path = Path(text).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = (Path(base_dir) / path)
    if path.is_file():
        return Patch.load(path)
    raise PatchError(
        f"找不到合成音脚本 {text!r}；内置音色：{'、'.join(sorted(builtin))}"
    )
