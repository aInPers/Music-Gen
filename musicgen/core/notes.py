"""音名 <-> MIDI 音高 <-> 频率 的转换工具。

约定：C4 = MIDI 60 = 中央 C（科学音高记法 / Scientific Pitch Notation）。
"""

from __future__ import annotations

NATURAL_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# 用降号拼写，与 FluidR3 / MIDI.js 音源文件命名保持一致（A0, Bb0, B0, C1, Db1...）
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

MIDI_MIN = 0
MIDI_MAX = 127


class PitchError(ValueError):
    """音名无法解析。"""


def parse_pitch(text: str) -> int:
    """把音名解析成 MIDI 音高。

    支持 ``C4`` / ``Db4`` / ``F#3`` / ``C-1`` / ``bB4``（老式写法）/ 纯数字 ``60``。
    """
    raw = str(text).strip()
    if not raw:
        raise PitchError("空音名")
    if raw.lstrip("+-").isdigit():
        value = int(raw)
        if not MIDI_MIN <= value <= MIDI_MAX:
            raise PitchError(f"MIDI 音高超出范围 0-127: {value}")
        return value

    text = raw.replace("\u266f", "#").replace("\u266d", "b")
    # 兼容 bB4 / #F4 这种把变音记号写在字母前面的写法
    if text[0] in "b#":
        prefix = text[0]
        rest = text[1:]
        if rest and rest[0].isalpha():
            text = rest[0] + prefix + rest[1:]

    letter = text[0].upper()
    if letter not in NATURAL_SEMITONES:
        raise PitchError(f"无法识别的音名: {raw}")

    pos = 1
    accidental = 0
    while pos < len(text) and text[pos] in "#b":
        accidental += 1 if text[pos] == "#" else -1
        pos += 1

    octave_text = text[pos:]
    try:
        octave = int(octave_text)
    except ValueError:
        raise PitchError(f"无法识别的音名: {raw}") from None

    midi = (octave + 1) * 12 + NATURAL_SEMITONES[letter] + accidental
    if not MIDI_MIN <= midi <= MIDI_MAX:
        raise PitchError(f"音高超出 MIDI 范围 0-127: {raw}")
    return midi


def parse_pitches(text: str) -> list[int]:
    """解析音高列表，支持 ``C4,E4,G4`` / ``C4 E4 G4``，也接受单个音名。"""
    parts = [p for p in str(text).replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    if not parts:
        raise PitchError("音高列表为空")
    return [parse_pitch(p) for p in parts]


def midi_to_name(midi: int, flats: bool = True) -> str:
    """MIDI 音高 -> 音名（默认用降号拼写，匹配音源文件命名）。"""
    midi = int(midi)
    names = FLAT_NAMES if flats else SHARP_NAMES
    return f"{names[midi % 12]}{midi // 12 - 1}"


def midi_to_freq(midi: float) -> float:
    """MIDI 音高 -> 频率（A4 = 440Hz）。"""
    return 440.0 * (2.0 ** ((float(midi) - 69.0) / 12.0))


def semitone_ratio(semitones: float) -> float:
    """半音数 -> 采样变速倍率。"""
    return 2.0 ** (float(semitones) / 12.0)
