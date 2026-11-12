"""内置合成鼓组。

FluidR3 的体积是「GM 旋律音色」，没有单独的打击乐音色包；架子鼓又不像钢琴那样
一个键一个采样就够用（真鼓还要力度层、房间声），所以这里改成**程序化合成**：
每个 GM 打击乐音高合成一件鼓/镲，力度控制音量与亮度，完全不依赖下载。

写谱时可以直接用鼓件名字当音高：``<note time="0" dur="0.25" pitch="kick,snare"/>``，
名字与 GM 编号的对照见 :data:`musicgen.core.notes.PERCUSSION_ALIASES`。
"""

from __future__ import annotations

import math

import numpy as np

#: 每件鼓/镲的目标峰值（力度 0.8 时），混音时靠它维持平衡
PAD_LEVEL: dict[str, float] = {
    "kick": 0.62,
    "snare": 0.50,
    "rim": 0.34,
    "clap": 0.42,
    "hat": 0.55,
    "openhat": 0.58,
    "pedalhat": 0.28,
    "crash": 0.62,
    "ride": 0.46,
    "bell": 0.40,
    "tom": 0.46,
    "tamb": 0.22,
    "cowbell": 0.26,
    "shaker": 0.16,
}

#: 各件鼓的声像（−1 左 … +1 右），模拟鼓手坐姿下的摆位
PAD_PAN: dict[str, float] = {
    "kick": 0.0,
    "snare": 0.05,
    "rim": 0.08,
    "clap": 0.10,
    "hat": 0.30,
    "openhat": 0.30,
    "pedalhat": 0.28,
    "crash": -0.34,
    "ride": 0.36,
    "bell": 0.34,
    "tom": -0.05,
    "tamb": 0.22,
    "cowbell": -0.18,
    "shaker": 0.26,
}

#: 鼓组整体输出电平（相对采样乐器校准，见 core/sampler.py 的 INSTRUMENT_GAIN）
DRUM_GAIN = 0.9

#: GM 打击乐音高 → 鼓件。未列出的音高按军鼓处理。
PAD_OF_MIDI: dict[int, str] = {
    35: "kick", 36: "kick",
    37: "rim",
    38: "snare", 40: "snare",
    39: "clap",
    41: "tom", 43: "tom", 45: "tom", 47: "tom", 48: "tom", 50: "tom",
    42: "hat", 44: "pedalhat",
    46: "openhat",
    49: "crash", 52: "crash", 55: "crash", 57: "crash",
    51: "ride", 59: "ride",
    53: "bell",
    54: "tamb",
    56: "cowbell",
    70: "shaker", 82: "shaker",
}

#: 鼓件中文名（钢琴卷帘、日志里显示）
PAD_LABELS: dict[str, str] = {
    "kick": "底鼓",
    "snare": "军鼓",
    "rim": "边击",
    "clap": "拍手",
    "hat": "闭镲",
    "openhat": "开镲",
    "pedalhat": "踏镲",
    "crash": "吊镲",
    "ride": "叮叮镲",
    "bell": "镲帽",
    "tom": "嗵鼓",
    "tamb": "铃鼓",
    "cowbell": "牛铃",
    "shaker": "沙锤",
}

_METAL = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)


# ---------------------------------------------------------------------------
# 合成工具
# ---------------------------------------------------------------------------


def _time(n: int, sr: int) -> np.ndarray:
    return np.arange(n, dtype=np.float32) / np.float32(sr)


def _noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n).astype(np.float32)


def _shape(sig: np.ndarray, sr: int, low: float | None = None, high: float | None = None) -> np.ndarray:
    """零相位带通（在频域上给低切/高切加一阶软斜坡），用来给噪声塑形。"""
    n = sig.size
    if n < 32:
        return sig
    spec = np.fft.rfft(sig)
    freq = np.fft.rfftfreq(n, 1.0 / sr)
    freq[0] = 1.0
    gain = np.ones_like(freq)
    if low:
        gain *= 1.0 / (1.0 + (low / freq) ** 2)
    if high:
        gain *= 1.0 / (1.0 + (freq / high) ** 2)
    return np.fft.irfft(spec * gain, n).astype(np.float32)


def _metal(t: np.ndarray) -> np.ndarray:
    """金属声：几个不成谐波关系的方波叠起来，接近镲片的非谐泛音。"""
    out = np.zeros_like(t)
    for freq in _METAL:
        out += np.sign(np.sin(2 * np.pi * freq * t))
    return (out / len(_METAL)).astype(np.float32)


def _norm(sig: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(sig))) if sig.size else 0.0
    if peak > 1e-6:
        sig = sig / peak
    return sig.astype(np.float32)


# ---------------------------------------------------------------------------
# 各鼓件
# ---------------------------------------------------------------------------


def _kick(sr: int) -> np.ndarray:
    n = int(0.50 * sr)
    t = _time(n, sr)
    pitch = 47.0 + 96.0 * np.exp(-t / 0.032)
    phase = 2 * np.pi * np.cumsum(pitch, dtype=np.float64) / sr
    body = np.sin(phase) * np.exp(-t / 0.125)
    beater = _shape(_noise(n, 7), sr, 1200, 6000) * np.exp(-t / 0.004) * 0.8
    # 轻微软削波，给纯正弦加上二次/三次谐波：小音箱上也听得见鼓点
    return _norm(np.tanh((body + beater) * 1.9) * 0.72)


def _snare(sr: int) -> np.ndarray:
    n = int(0.34 * sr)
    t = _time(n, sr)
    rattle = _shape(_noise(n, 11), sr, 1100, 8500) * np.exp(-t / 0.100)
    crack = _shape(_noise(n, 12), sr, 2800, 9000) * np.exp(-t / 0.020) * 0.55
    body = (0.60 * np.sin(2 * np.pi * 186 * t) + 0.45 * np.sin(2 * np.pi * 331 * t)) * np.exp(-t / 0.050)
    return _norm(rattle + crack + body * 0.85)


def _rim(sr: int) -> np.ndarray:
    n = int(0.14 * sr)
    t = _time(n, sr)
    click = _shape(_noise(n, 13), sr, 900, 9000) * np.exp(-t / 0.006)
    tone = 0.6 * np.sin(2 * np.pi * 1720 * t) * np.exp(-t / 0.012)
    return _norm(click + tone)


def _clap(sr: int) -> np.ndarray:
    n = int(0.38 * sr)
    t = _time(n, sr)
    env = np.zeros(n, dtype=np.float32)
    for delay in (0.0, 0.010, 0.021):
        start = int(delay * sr)
        env[start:] += np.exp(-_time(n - start, sr) / 0.0075)
    tail = 0.45 * np.exp(-t / 0.11)
    body = _shape(_noise(n, 17), sr, 850, 4600) * (env + tail)
    return _norm(body)


def _hat(sr: int, decay: float, seed: int, low: float = 6800.0) -> np.ndarray:
    n = int(max(0.12, decay * 6.5) * sr)
    t = _time(n, sr)
    source = 0.7 * _metal(t) + 0.6 * _noise(n, seed)
    return _norm(_shape(source, sr, low, 16000) * np.exp(-t / decay))


def _crash(sr: int, decay: float = 0.80, low: float = 3100.0, seed: int = 23) -> np.ndarray:
    n = int(max(0.6, decay * 3.2) * sr)
    t = _time(n, sr)
    source = _metal(t) + 0.85 * _noise(n, seed)
    env = 0.62 * np.exp(-t / decay) + 0.38 * np.exp(-t / (decay * 0.3))
    return _norm(_shape(source, sr, low, 15500) * env)


def _ride(sr: int) -> np.ndarray:
    n = int(1.45 * sr)
    t = _time(n, sr)
    body = _shape(_metal(t) * 0.85 + 0.45 * _noise(n, 29), sr, 3600, 12000) * np.exp(-t / 0.55)
    bell = (np.sin(2 * np.pi * 1760 * t) + 0.6 * np.sin(2 * np.pi * 2630 * t)) * np.exp(-t / 0.32) * 0.22
    return _norm(body + bell)


def _bell(sr: int) -> np.ndarray:
    n = int(0.95 * sr)
    t = _time(n, sr)
    tone = (np.sin(2 * np.pi * 1180 * t) + 0.7 * np.sin(2 * np.pi * 1790 * t)
            + 0.4 * np.sin(2 * np.pi * 2480 * t))
    return _norm(tone * np.exp(-t / 0.42))


def _tom(sr: int, midi: int) -> np.ndarray:
    base = 110.0 * 2.0 ** ((midi - 45) / 12.0)
    n = int(0.48 * sr)
    t = _time(n, sr)
    pitch = base * (1.0 + 0.55 * np.exp(-t / 0.030))
    phase = 2 * np.pi * np.cumsum(pitch, dtype=np.float64) / sr
    body = np.sin(phase) * np.exp(-t / 0.165)
    skin = _shape(_noise(n, 31), sr, 700, 4200) * np.exp(-t / 0.007) * 0.30
    return _norm(np.tanh((body + skin) * 1.4) * 0.8)


def _tamb(sr: int) -> np.ndarray:
    n = int(0.45 * sr)
    t = _time(n, sr)
    source = _noise(n, 37)
    env = np.zeros(n, dtype=np.float32)
    for delay in (0.0, 0.014, 0.029):
        start = int(delay * sr)
        env[start:] += np.exp(-_time(n - start, sr) / 0.055)
    return _norm(_shape(source, sr, 5200, 14000) * (env * 0.8 + 0.3 * np.exp(-t / 0.2)))


def _cowbell(sr: int) -> np.ndarray:
    n = int(0.40 * sr)
    t = _time(n, sr)
    tone = np.sign(np.sin(2 * np.pi * 543 * t)) + 0.8 * np.sign(np.sin(2 * np.pi * 812 * t))
    env = np.minimum(1.0, _time(n, sr) / 0.004) * np.exp(-t / 0.26)
    return _norm(_shape(tone, sr, 420, 4200) * env)


def _shaker(sr: int) -> np.ndarray:
    n = int(0.20 * sr)
    t = _time(n, sr)
    env = np.minimum(1.0, t / 0.012) * np.exp(-t / 0.055)
    return _norm(_shape(_noise(n, 41), sr, 4200, 13500) * env)


def _render_pad(pad: str, midi: int, sr: int) -> np.ndarray:
    if pad == "kick":
        return _kick(sr)
    if pad == "snare":
        return _snare(sr)
    if pad == "rim":
        return _rim(sr)
    if pad == "clap":
        return _clap(sr)
    if pad == "hat":
        return _hat(sr, 0.036, 19)
    if pad == "openhat":
        return _hat(sr, 0.30, 43, 6200.0)
    if pad == "pedalhat":
        return _hat(sr, 0.055, 47)
    if pad == "crash":
        return _crash(sr) if midi != 55 else _crash(sr, decay=0.40, low=4200, seed=53)
    if pad == "ride":
        return _ride(sr)
    if pad == "bell":
        return _bell(sr)
    if pad == "tom":
        return _tom(sr, midi)
    if pad == "tamb":
        return _tamb(sr)
    if pad == "cowbell":
        return _cowbell(sr)
    if pad == "shaker":
        return _shaker(sr)
    return _snare(sr)


class DrumInstrument:
    """架子鼓：按 GM 打击乐音高合成，无需采样文件。"""

    name = "drums"

    def __init__(self, name: str = "drums", sample_rate: int = 44100, **_kwargs) -> None:
        self.name = name
        self.sample_rate = sample_rate
        self._cache: dict[tuple[str, int, int], np.ndarray] = {}

    @property
    def ready(self) -> bool:
        return True

    def describe(self) -> str:
        return "内置合成鼓组（GM 打击乐映射）"

    def render_note(self, midi: int, velocity: float, duration: float,
                    bend: list[tuple[float, float]] | None = None) -> np.ndarray:
        velocity = max(0.0, min(1.0, float(velocity)))
        midi = int(midi)
        pad = PAD_OF_MIDI.get(midi, "snare")
        key = (pad, midi if pad == "tom" else 0, int(round(velocity / 0.05)))
        audio = self._cache.get(key)
        if audio is None:
            mono = _render_pad(pad, midi, self.sample_rate)
            # 轻打更闷：截止频率随力度从 ~11kHz 抬到 ~18kHz（鼓刷轻击 vs 全力敲）
            mono = _shape(mono, self.sample_rate, None, min(20000.0, 9000.0 * (2.0 ** velocity)))
            mono = mono * (PAD_LEVEL[pad] * DRUM_GAIN * velocity ** 1.25)
            theta = (PAD_PAN[pad] + 1.0) * math.pi / 4.0
            audio = np.vstack([mono * math.cos(theta), mono * math.sin(theta)]).astype(np.float32)
            self._cache[key] = audio
        return audio


def pad_label(midi: int) -> str:
    """鼓件中文名（给界面显示用）。嗵鼓按音高区分，保证显示的名字能再解析回同一个音。"""
    midi = int(midi)
    tom = {
        50: "高音嗵鼓", 48: "中音嗵鼓", 47: "中低嗵鼓",
        45: "低音嗵鼓", 43: "落地嗵鼓", 41: "落地嗵鼓",
    }.get(midi)
    if tom:
        return tom
    return PAD_LABELS.get(PAD_OF_MIDI.get(midi, "snare"), "军鼓")


def is_drum_midi(midi: int) -> bool:
    return int(midi) in PAD_OF_MIDI
