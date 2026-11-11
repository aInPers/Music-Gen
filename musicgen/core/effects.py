"""音效（effect）注册表。

效果是**代码插件**：每个效果用 :func:`register_effect` 自注册，并声明自己的参数表
:attr:`Effect.PARAMS`（键名、中文名、默认值、范围、单位、枚举）。工程里写
``<effect type="echo" time="450" mix="0.3"/>``，渲染时按 XML 里出现的顺序串联。

设计原则：

* 参数默认值只存在于注册表，XML 里只写显式给出的参数，工程文件保持简短；
* 未知效果类型 / 未知参数 / 越界数值都给警告后跳过或夹取，渲染不会因此失败；
* DSP 全部确定性（不使用随机数），同一工程多次渲染结果完全一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class EffectError(ValueError):
    """音效类型不存在或参数不合法。"""


# ---------------------------------------------------------------------------
# 参数表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    """一个效果参数的元数据。

    ``kind``：``number`` 小数 / ``int`` 整数 / ``bool`` 开关 / ``choice`` 枚举。
    """

    key: str
    label: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    kind: str = "number"
    choices: tuple[str, ...] = ()

    @property
    def is_numeric(self) -> bool:
        return self.kind in ("number", "int")

    # ---- 解析 ---------------------------------------------------------
    def coerce(self, raw: Any) -> tuple[Any, bool]:
        """把 XML / CLI / 界面传来的值转成规范值。

        返回 ``(值, 是否被夹到范围内)``；格式无法识别时抛 :class:`EffectError`。
        """
        if self.kind == "bool":
            return self._coerce_bool(raw), False
        if self.kind == "choice":
            return self._coerce_choice(raw), False
        value = self._coerce_number(raw)
        clamped = False
        if self.minimum is not None and value < self.minimum:
            value, clamped = float(self.minimum), True
        if self.maximum is not None and value > self.maximum:
            value, clamped = float(self.maximum), True
        if self.kind == "int":
            value = int(round(value))
        return value, clamped

    def _coerce_number(self, raw: Any) -> float:
        if isinstance(raw, bool):
            raise EffectError(f"{self.label} 需要一个数字，收到布尔值")
        if isinstance(raw, (int, float)):
            return float(raw)
        text = str(raw).strip().lower()
        for suffix in ("ms", "hz", "db", "x", "s", "%"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
                break
        try:
            return float(text)
        except (TypeError, ValueError):
            raise EffectError(f"{self.label} 不是有效数字：{raw!r}") from None

    def _coerce_bool(self, raw: Any) -> bool:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on", "是", "开"):
            return True
        if text in ("0", "false", "no", "off", "否", "关", ""):
            return False
        raise EffectError(f"{self.label} 只能是开/关：{raw!r}")

    def _coerce_choice(self, raw: Any) -> str:
        text = str(raw).strip().lower()
        if text not in self.choices:
            raise EffectError(
                f"{self.label} 只能是 {'/'.join(self.choices)}：{raw!r}"
            )
        return text

    # ---- 输出 ---------------------------------------------------------
    def format(self, value: Any) -> str:
        """写进 XML 的文本。"""
        if self.kind == "bool":
            return "true" if value else "false"
        if self.kind == "int":
            return str(int(value))
        if self.kind == "choice":
            return str(value)
        return f"{float(value):g}"

    def display(self, value: Any) -> str:
        """给人看的文本（带单位）。"""
        if self.kind == "bool":
            return "开" if value else "关"
        if self.kind == "choice":
            return str(value)
        if self.kind == "int":
            return f"{int(value)}{self.unit}"
        return f"{float(value):g}{self.unit}"

    def range_text(self) -> str:
        """范围描述，如 ``0–2 ×`` / ``开/关``。"""
        if self.kind == "bool":
            return "开/关"
        if self.kind == "choice":
            return "/".join(self.choices)
        if self.minimum is None and self.maximum is None:
            return "—"
        low = "−∞" if self.minimum is None else f"{self.minimum:g}"
        high = "+∞" if self.maximum is None else f"{self.maximum:g}"
        return f"{low}–{high} {self.unit}".strip()


# ---------------------------------------------------------------------------
# 效果基类与注册表
# ---------------------------------------------------------------------------


class Effect:
    """所有效果的基类：声明元数据 + 实现 :meth:`process`。"""

    KEY: str = ""
    LABEL: str = ""
    DESCRIPTION: str = ""
    ALIASES: tuple[str, ...] = ()
    PARAMS: tuple[Param, ...] = ()

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        values = {param.key: param.default for param in self.PARAMS}
        for key, value in (params or {}).items():
            meta = self.param(key)
            if meta is None:
                raise EffectError(f"音效「{self.LABEL}」没有参数 {key}")
            values[key] = meta.coerce(value)[0]
        self.params = values

    # ---- 便捷访问 -----------------------------------------------------
    @classmethod
    def param(cls, key: str) -> Param | None:
        for param in cls.PARAMS:
            if param.key == key:
                return param
        return None

    @classmethod
    def param_keys(cls) -> tuple[str, ...]:
        return tuple(param.key for param in cls.PARAMS)

    def __getitem__(self, key: str) -> Any:
        return self.params[key]

    def values(self) -> dict[str, Any]:
        return dict(self.params)

    # ---- 处理 ---------------------------------------------------------
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """处理 ``(2, N)`` 立体声数据，返回同形状的新数组（不修改入参）。"""
        raise NotImplementedError


#: 注册键 -> 效果类（按注册顺序排列，即 ``effect types`` 的显示顺序）
EFFECT_REGISTRY: dict[str, type[Effect]] = {}
_ALIASES: dict[str, str] = {}


def register_effect(cls: type[Effect]) -> type[Effect]:
    """类装饰器：把效果登记进注册表。"""
    key = str(cls.KEY).strip().lower()
    if not key:
        raise EffectError(f"{cls.__name__} 缺少 KEY")
    EFFECT_REGISTRY[key] = cls
    for alias in (cls.LABEL, *cls.ALIASES):
        text = str(alias).strip().lower()
        if text:
            _ALIASES[text] = key
    return cls


def effect_keys() -> list[str]:
    return list(EFFECT_REGISTRY)


def resolve_effect(name: str | None) -> str | None:
    """把注册键 / 中文名 / 别名统一成注册键；不认识返回 ``None``。"""
    text = str(name or "").strip().lower()
    if not text:
        return None
    if text in EFFECT_REGISTRY:
        return text
    return _ALIASES.get(text)


def effect_types_text() -> str:
    """错误提示用：``回声(echo)、立体(stereo)…``。"""
    return "、".join(f"{cls.LABEL}({key})" for key, cls in EFFECT_REGISTRY.items())


def effect_class(name: str) -> type[Effect]:
    key = resolve_effect(name)
    if key is None:
        raise EffectError(f"未知音效「{name}」。可用：{effect_types_text()}")
    return EFFECT_REGISTRY[key]


def create_effect(name: str, params: dict[str, Any] | None = None) -> Effect:
    return effect_class(name)(params)


def normalize_effect_params(
    name: str, raw: dict[str, Any], *, strict: bool = False
) -> tuple[dict[str, Any], list[str]]:
    """校验一组参数，返回 ``(规范值, 问题列表)``。

    ``strict=True`` 时遇到未知参数或越界直接抛 :class:`EffectError`（CLI 用）；
    否则跳过未知参数、夹取越界值，并把原因收进问题列表（解析工程时用）。
    """
    cls = effect_class(name)
    values: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in raw.items():
        param = cls.param(str(key))
        if param is None:
            message = (
                f"音效「{cls.LABEL}」没有参数 {key}"
                f"（可用：{'、'.join(cls.param_keys())}）"
            )
            if strict:
                raise EffectError(message)
            problems.append(message + "，已忽略")
            continue
        try:
            parsed, clamped = param.coerce(value)
        except EffectError as exc:
            if strict:
                raise
            problems.append(str(exc) + "，已用默认值")
            continue
        if clamped:
            message = (
                f"参数 {param.key} 超出 {param.range_text()}，"
                f"已按 {param.display(parsed)} 处理"
            )
            if strict:
                raise EffectError(message)
            problems.append(message)
        values[param.key] = parsed
    return values, problems


def describe_effects() -> list[tuple[str, str, str, tuple[Param, ...]]]:
    """``[(注册键, 中文名, 说明, 参数表)]``，供 CLI / 文档使用。"""
    return [
        (key, cls.LABEL, cls.DESCRIPTION, cls.PARAMS)
        for key, cls in EFFECT_REGISTRY.items()
    ]


# ---------------------------------------------------------------------------
# 工程里的一个效果条目
# ---------------------------------------------------------------------------


@dataclass
class EffectSpec:
    """``<effect type="echo" time="450" mix="0.3"/>`` 的数据模型。

    ``params`` 只保存**显式写出**的参数，默认值由注册表补齐。
    """

    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        key = resolve_effect(self.type)
        if key is not None:
            self.type = key

    @property
    def cls(self) -> type[Effect]:
        return effect_class(self.type)

    def label(self) -> str:
        return self.cls.LABEL

    def merged(self) -> dict[str, Any]:
        """补上默认值后的完整参数。"""
        return create_effect(self.type, self.params).values()

    def summary(self) -> str:
        """``time=450ms feedback=0.35`` 形式的参数摘要。"""
        try:
            cls = self.cls
        except EffectError:  # 注册表里已经没有这个效果了，退化成原始键值
            return " ".join(f"{k}={v}" for k, v in self.params.items()) or "—"
        parts = []
        for param in cls.PARAMS:
            if param.key not in self.params:
                continue
            parts.append(f"{param.key}={param.display(self.params[param.key])}")
        return " ".join(parts) or "默认参数"

    def to_attrs(self) -> dict[str, str]:
        """写回 XML 用的属性，按参数表顺序排列。"""
        try:
            cls = self.cls
        except EffectError:
            attrs = {"type": self.type}
            attrs.update({str(k): str(v) for k, v in self.params.items()})
            return attrs
        attrs = {"type": self.type}
        for param in cls.PARAMS:
            if param.key in self.params:
                attrs[param.key] = param.format(self.params[param.key])
        for key, value in self.params.items():  # 理论上不会有未知键
            if key not in attrs:
                attrs[key] = str(value)
        return attrs


# ---------------------------------------------------------------------------
# DSP 工具
# ---------------------------------------------------------------------------


def _as_stereo(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        audio = np.vstack([audio, audio])
    return np.ascontiguousarray(audio, dtype=np.float32)


def _blend(dry: np.ndarray, wet: np.ndarray, mix: float) -> np.ndarray:
    """干湿混合；``mix <= 0`` 直接返回干声。"""
    mix = max(0.0, min(1.0, float(mix)))
    if mix <= 0.0:
        return dry.copy()
    if mix >= 1.0:
        return wet.astype(np.float32, copy=False)
    return (dry * (1.0 - mix) + wet * mix).astype(np.float32, copy=False)


def _side_scale(width: float) -> float:
    """只压侧信号：加宽时不会把单声道素材整体弄小声，也不至于让总线削波。"""
    return 1.0 / math.sqrt(width) if width > 1.0 else 1.0


# ---------------------------------------------------------------------------
# 预设效果
# ---------------------------------------------------------------------------


@register_effect
class StereoEffect(Effect):
    KEY = "stereo"
    LABEL = "立体"
    ALIASES = ("立体声", "声场", "width", "stereo-width")
    DESCRIPTION = "中侧（M/S）加宽立体声场，可选哈斯微延迟让左右更分明。"
    PARAMS = (
        Param("width", "宽度", 1.6, 0.0, 2.0, 0.05, "×"),
        Param("haas", "哈斯延迟", 0.0, 0.0, 30.0, 1.0, "ms"),
        Param("mix", "干湿", 1.0, 0.0, 1.0, 0.01),
    )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = _as_stereo(audio)
        frames = audio.shape[1]
        if frames == 0:
            return audio
        width = float(self["width"])
        haas = float(self["haas"])
        mid = (audio[0] + audio[1]) * 0.5
        side = (audio[0] - audio[1]) * 0.5 * width * _side_scale(width)
        wet = np.empty_like(audio)
        wet[0] = mid + side
        wet[1] = mid - side
        delay = int(round(haas * sample_rate / 1000.0))
        if 0 < delay < frames:
            shifted = np.zeros_like(wet)
            shifted[0] = wet[0]
            shifted[1, delay:] = wet[1, :-delay]
            wet = shifted
        return _blend(audio, wet, float(self["mix"]))


def _damp(block: np.ndarray, tone: float) -> np.ndarray:
    """反馈路径上的一阶低通：``tone`` 越小回声越闷。"""
    tone = max(0.0, min(1.0, float(tone)))
    if tone >= 0.98:
        return block
    keep = 0.15 + 0.75 * tone
    out = block.copy()
    out[:, 1:] = block[:, 1:] * keep + block[:, :-1] * (1.0 - keep)
    return out


@register_effect
class EchoEffect(Effect):
    KEY = "echo"
    LABEL = "回声"
    ALIASES = ("延迟", "回音", "delay", "echo")
    DESCRIPTION = "固定间隔的反馈延迟，可左右交替，反馈带低通衰减。"
    PARAMS = (
        Param("time", "延迟", 320, 20.0, 2000.0, 10.0, "ms", "int"),
        Param("feedback", "反馈", 0.36, 0.0, 0.9, 0.01),
        Param("tone", "亮度", 0.5, 0.0, 1.0, 0.01),
        Param("pingpong", "左右交替", True, kind="bool"),
        Param("mix", "干湿", 0.3, 0.0, 1.0, 0.01),
    )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = _as_stereo(audio)
        frames = audio.shape[1]
        if frames == 0:
            return audio
        delay = max(1, int(round(float(self["time"]) * sample_rate / 1000.0)))
        if delay >= frames:
            return audio  # 延迟比整轨还长，听不到回声
        feedback = float(self["feedback"])
        tone = float(self["tone"])
        pingpong = bool(self["pingpong"])
        echo = np.empty_like(audio)
        prev_dry = np.zeros((2, delay), dtype=np.float32)
        prev_echo = np.zeros((2, delay), dtype=np.float32)
        for start in range(0, frames, delay):
            length = min(delay, frames - start)
            block = np.zeros((2, delay), dtype=np.float32)
            block[:, :length] = audio[:, start:start + length]

            delayed_dry = _damp(prev_dry, tone)
            delayed_echo = _damp(prev_echo, tone)
            if pingpong:
                delayed_dry = delayed_dry[::-1].copy()
                delayed_echo = delayed_echo[::-1].copy()
            out = delayed_dry + delayed_echo * feedback
            echo[:, start:start + length] = out[:, :length]
            prev_dry = block
            prev_echo = out
        return _blend(audio, audio + echo, float(self["mix"]))


@register_effect
class FadeEffect(Effect):
    KEY = "fade"
    LABEL = "淡入淡出"
    ALIASES = ("淡入", "淡出", "fadein", "fadeout", "fade")
    DESCRIPTION = "在音轨实际发声区间做淡入与淡出，适合前奏渐入与收尾。"
    PARAMS = (
        Param("in", "淡入", 0.8, 0.0, 10.0, 0.05, "s"),
        Param("out", "淡出", 1.5, 0.0, 10.0, 0.05, "s"),
        Param("curve", "曲线", "expo", kind="choice", choices=("linear", "expo")),
    )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = _as_stereo(audio)
        frames = audio.shape[1]
        if frames == 0:
            return audio
        active = np.any(np.abs(audio) > 1e-7, axis=0)
        sounding = np.flatnonzero(active)
        if sounding.size == 0:
            return audio
        start = int(sounding[0])
        end = int(sounding[-1]) + 1
        span = end - start
        fade_in = min(int(round(float(self["in"]) * sample_rate)), span)
        fade_out = min(int(round(float(self["out"]) * sample_rate)), span)
        linear = str(self["curve"]) == "linear"

        dry = audio  # 入参不改
        out = dry.copy()
        if fade_in > 0:
            ramp = np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
            if not linear:
                ramp = ramp ** 2
            out[:, start:start + fade_in] *= ramp
        if fade_out > 0:
            ramp = np.linspace(0.0, 1.0, fade_out, dtype=np.float32)
            if not linear:
                ramp = (1.0 - ramp) ** 2
            else:
                ramp = 1.0 - ramp
            out[:, end - fade_out:end] *= ramp
        return out


@register_effect
class ElectroEffect(Effect):
    KEY = "electro"
    LABEL = "电音"
    ALIASES = ("电子", "机器人", "环调", "robot", "ringmod", "ring-mod")
    DESCRIPTION = "环形调制 + 过载失真 + 位深压缩，做出电子 / 机器人的音色。"
    PARAMS = (
        Param("rate", "环调频率", 30.0, 0.5, 200.0, 0.5, "Hz"),
        Param("depth", "调制深度", 0.8, 0.0, 1.0, 0.01),
        Param("drive", "失真", 0.35, 0.0, 1.0, 0.01),
        Param("bits", "位深", 8, 3.0, 16.0, 1.0, "bit", "int"),
        Param("mix", "干湿", 0.85, 0.0, 1.0, 0.01),
    )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = _as_stereo(audio)
        frames = audio.shape[1]
        if frames == 0:
            return audio
        rate = float(self["rate"])
        depth = max(0.0, min(1.0, float(self["depth"])))
        drive = float(self["drive"])
        bits = int(self["bits"])

        time = np.arange(frames, dtype=np.float32) / float(sample_rate)
        phase = 2.0 * math.pi * rate * time
        wet = audio.copy()
        # 左右相差约 52°，既有环调金属感又不至于完全单声道
        wet[0] *= (1.0 - depth) + depth * np.sin(phase)
        wet[1] *= (1.0 - depth) + depth * np.sin(phase + 0.9)

        if drive > 0.0:
            pre = 1.0 + 9.0 * drive
            wet = np.tanh(wet * pre) / math.tanh(pre)

        if bits < 16:
            levels = float(2 ** bits)
            wet = np.round(wet * levels) / levels

        return _blend(audio, wet.astype(np.float32, copy=False), float(self["mix"]))


# ---------------------------------------------------------------------------
# 位深压缩（8bit / 4bit / 2bit）
# ---------------------------------------------------------------------------


def _quantize(audio: np.ndarray, bits: int) -> np.ndarray:
    """把波形压到 ``bits`` 位：``bits`` 位对应 ``2**bits`` 个电平、步长 ``1/2**(bits-1)``。"""
    levels = float(2 ** max(1, int(bits) - 1))
    return (np.round(audio * levels) / levels).astype(np.float32, copy=False)


def _decimate(audio: np.ndarray, sample_rate: int, rate_hz: float) -> np.ndarray:
    """采样保持降采样：每 ``sample_rate / rate_hz`` 个点取一个值，再原样平铺回去。"""
    factor = max(1, int(round(sample_rate / max(1.0, float(rate_hz)))))
    if factor <= 1:
        return audio
    frames = audio.shape[1]
    held = audio[:, ::factor]
    return np.repeat(held, factor, axis=1)[:, :frames]


def _smooth(audio: np.ndarray, tone: float) -> np.ndarray:
    """重建低通：级联两级一阶低通，压掉降采样后的高频镜像，``tone`` 越小越闷。"""
    tone = max(0.0, min(1.0, float(tone)))
    if tone >= 0.98:
        return audio
    return _damp(_damp(audio, tone), tone)


def _chip_params(rate: float, tone: float, drive: float) -> tuple[Param, ...]:
    """三个位深预设共用同一组参数，只有默认值不同。"""
    return (
        Param("rate", "采样率", rate, 500.0, 44100.0, 100.0, "Hz", "int"),
        Param("tone", "亮度", tone, 0.0, 1.0, 0.01),
        Param("drive", "前级", drive, 0.0, 1.0, 0.01),
        Param("mix", "干湿", 1.0, 0.0, 1.0, 0.01),
    )


class BitcrushEffect(Effect):
    """位深压缩基类：前级过载 → 降采样 → 量化 → 重建低通。

    子类只改 :attr:`BITS` 与参数默认值，DSP 全部复用。
    """

    BITS = 8

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = _as_stereo(audio)
        if audio.shape[1] == 0:
            return audio
        # 先按素材自身峰值归一化，位深才表示「相对动态范围」而不是绝对满刻度：
        # 否则 −20dBFS 的音乐用 2bit 量化，大部分采样会被直接归零
        peak = float(np.max(np.abs(audio)))
        if peak <= 0.0:
            return audio.copy()
        wet = audio * (1.0 / peak)
        drive = float(self["drive"])
        if drive > 0.0:
            pre = 1.0 + 4.0 * drive
            wet = (np.tanh(wet * pre) / math.tanh(pre)).astype(np.float32, copy=False)
        wet = _decimate(wet, sample_rate, float(self["rate"]))
        wet = _quantize(wet, type(self).BITS)
        wet = _smooth(wet, float(self["tone"]))
        return _blend(audio, wet * peak, float(self["mix"]))


@register_effect
class Bit8Effect(BitcrushEffect):
    KEY = "8bit"
    LABEL = "8bit"
    ALIASES = ("8-bit", "8位", "红白机", "nes")
    DESCRIPTION = "8 位量化 + 11kHz 降采样，红白机式的颗粒感和高频混叠。"
    BITS = 8
    PARAMS = _chip_params(11025, 0.75, 0.0)


@register_effect
class Bit4Effect(BitcrushEffect):
    KEY = "4bit"
    LABEL = "4bit"
    ALIASES = ("4-bit", "4位")
    DESCRIPTION = "4 位量化（16 级电平）+ 8kHz 降采样，早期采样器的粗糙质感。"
    BITS = 4
    PARAMS = _chip_params(8000, 0.6, 0.0)


@register_effect
class Bit2Effect(BitcrushEffect):
    KEY = "2bit"
    LABEL = "2bit"
    ALIASES = ("2-bit", "2位")
    DESCRIPTION = "2 位量化（4 级电平）+ 4kHz 降采样，几乎只剩方波的极端破坏。"
    BITS = 2
    PARAMS = _chip_params(4000, 0.45, 0.0)
