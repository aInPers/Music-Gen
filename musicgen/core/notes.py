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

#: 架子鼓的写法：鼓件名 → GM 打击乐音高。
#: 让鼓谱可以直接写 ``pitch="kick,snare"``，不用记 GM 编号（36=底鼓、38=军鼓…）；
#: 写 ``pitch="C2"`` 这种音名同样有效，两者等价。
PERCUSSION_ALIASES: dict[str, int] = {
    "kick": 36, "bd": 36, "bassdrum": 36, "底鼓": 36, "大鼓": 36, "脚鼓": 36,
    "kick2": 35, "bd2": 35,
    "snare": 38, "sd": 38, "军鼓": 38, "小鼓": 38,
    "snare2": 40,
    "rim": 37, "sidestick": 37, "rimshot": 37, "边击": 37, "鼓边": 37,
    "clap": 39, "handclap": 39, "拍手": 39,
    "hat": 42, "hihat": 42, "hh": 42, "closedhat": 42, "闭镲": 42, "踩镲": 42,
    "pedalhat": 44, "pedal": 44, "踏镲": 44,
    "openhat": 46, "oh": 46, "开镲": 46,
    "crash": 49, "吊镲": 49, "强音镲": 49,
    "crash2": 57,
    "splash": 55, "水镲": 55,
    "china": 52, "中国镲": 52,
    "ride": 51, "叮叮镲": 51,
    "ride2": 59,
    "bell": 53, "ridebell": 53, "镲帽": 53,
    "tom1": 50, "hightom": 50, "高音嗵鼓": 50,
    "tom2": 48, "midtom": 48, "中音嗵鼓": 48,
    "tom3": 47, "中低嗵鼓": 47,
    "tom4": 45, "lowtom": 45, "低音嗵鼓": 45,
    "floor": 41, "floortom": 41, "落地嗵鼓": 41,
    "tamb": 54, "tambourine": 54, "铃鼓": 54,
    "cowbell": 56, "牛铃": 56,
    "shaker": 82, "沙锤": 82,
}


class PitchError(ValueError):
    """音名无法解析。"""


def parse_pitch(text: str) -> int:
    """把音名解析成 MIDI 音高。

    支持 ``C4`` / ``Db4`` / ``F#3`` / ``C-1`` / ``bB4``（老式写法）/ 纯数字 ``60``，
    以及架子鼓的鼓件名 ``kick`` / ``snare`` / ``hihat``（等价于对应的 GM 打击乐编号）。
    """
    raw = str(text).strip()
    if not raw:
        raise PitchError("空音名")
    if raw.lstrip("+-").isdigit():
        value = int(raw)
        if not MIDI_MIN <= value <= MIDI_MAX:
            raise PitchError(f"MIDI 音高超出范围 0-127: {value}")
        return value

    drum = PERCUSSION_ALIASES.get(raw.lower())
    if drum is not None:
        return drum

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
