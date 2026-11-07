"""生成《月光》示例工程（贝多芬 Op.27 No.2 第一乐章，1 分钟改编）。

60BPM · 4/4 · 15 小节 = 60 拍 = 60 秒。三连音用 1/3 拍精确分数，避免浮点累积。

改和弦、旋律或力度后直接运行本脚本即可重新生成 moonlight.musicproj 与
tracks/piano.track.xml（226 个音符手写不现实），然后：
    python -m musicgen render examples/moonlight/moonlight.musicproj -o output/moonlight.wav
"""
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent
(ROOT / "tracks").mkdir(parents=True, exist_ok=True)


def fmt(value: F) -> str:
    value = F(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


# 每小节：(和弦名, 低音八度, 三连音分解的三个音, 三连音力度, 低音力度)
CHORDS = [
    ("C#m",    "C#2,C#3", ("G#3", "C#4", "E4"),  0.34, 0.44),
    ("C#m",    "G#2,G#3", ("G#3", "C#4", "E4"),  0.35, 0.42),
    ("A",      "A1,A2",   ("E3",  "A3",  "C#4"), 0.36, 0.46),
    ("G#7",    "G#1,G#2", ("D#3", "G#3", "B3"),  0.37, 0.48),
    ("C#m",    "C#2,C#3", ("G#3", "C#4", "E4"),  0.39, 0.50),
    ("F#m",    "F#1,F#2", ("C#3", "F#3", "A3"),  0.40, 0.50),
    ("G#7",    "G#1,G#2", ("D#3", "G#3", "B3"),  0.42, 0.52),
    ("C#m",    "C#2,C#3", ("G#3", "C#4", "E4"),  0.41, 0.50),
    ("E",      "E2,E3",   ("B3",  "E4",  "G#4"), 0.44, 0.54),
    ("A",      "A1,A2",   ("E3",  "A3",  "C#4"), 0.43, 0.52),
    ("F#m",    "F#1,F#2", ("C#3", "F#3", "A3"),  0.43, 0.52),
    ("G#7",    "G#1,G#2", ("D#3", "G#3", "B3"),  0.41, 0.50),
    ("C#m",    "C#2,C#3", ("G#3", "C#4", "E4"),  0.39, 0.48),
    ("F#m",    "F#1,F#2", ("C#3", "F#3", "A3"),  0.34, 0.44),
    ("C#m",    "C#2,C#3", ("G#3", "C#4", "E4"),  0.28, 0.38),
]

# 旋律：小节号 -> [(相对该小节的起始拍, 时值, 音名, 力度)]
# 动机特征：同音反复 + 级进/半音上行，第 9–11 小节推向高潮，最后两小节渐弱收束
MELODY = {
    3:  [(0, 1, "G#4", .44), (1, F(1, 2), "G#4", .42),
         (F(3, 2), F(1, 2), "A#4", .45), (2, 2, "B4", .48)],
    4:  [(0, 1, "C#5", .50), (1, 1, "B4", .48), (2, 2, "G#4", .46)],
    5:  [(0, 1, "C#5", .58), (1, F(1, 2), "C#5", .56),
         (F(3, 2), F(1, 2), "D#5", .60), (2, 1, "E5", .64), (3, 1, "D#5", .60)],
    6:  [(0, 2, "C#5", .62), (2, 1, "B4", .58), (3, 1, "G#4", .56)],
    7:  [(0, 1, "G#4", .60), (1, 1, "A#4", .64), (2, 1, "B4", .68), (3, 1, "C#5", .70)],
    8:  [(0, 2, "E5", .72), (2, 1, "D#5", .68), (3, 1, "C#5", .64)],
    9:  [(0, 1, "E5", .74), (1, 1, "F#5", .76), (2, 2, "G#5", .80)],
    10: [(0, 1, "G#5", .78), (1, 1, "F#5", .76), (2, 2, "E5", .72)],
    11: [(0, 1, "D#5", .70), (1, 1, "E5", .72), (2, 2, "C#5", .68)],
    12: [(0, 2, "B4", .64), (2, 1, "C#5", .66), (3, 1, "B4", .62)],
    13: [(0, 1, "A#4", .60), (1, 1, "B4", .60), (2, 2, "G#4", .58)],
    14: [(0, 2, "E5", .56), (2, 2, "C#5", .52)],
    15: [(0, 2, "C#5", .44)],
}

lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    "<!-- 贝多芬《月光奏鸣曲》Op.27 No.2 第一乐章 · 1 分钟改编（钢琴独奏）",
    "     升 c 小调 · Adagio sostenuto · 60BPM · 4/4 · 15 小节 = 60 秒。",
    "",
    "     声部：① 低音八度（每小节第 1 拍）② 每拍三个音的分解和弦三连音（1/3 拍）",
    "            ③ 第 3 小节起进入的主题旋律。三个声部都写在同一条钢琴轨里。",
    "     没有延音踏板：三连音 dur=3/4 拍（略长于 1/3 拍的间隔）与低音长时值互相重叠，",
    "     模拟 una corda 踏板的持续和声，同时保留每拍一组的三连音起音；混响 0.35 给一点空间感。",
    "",
    "     和声：C#m C#m | A G#7 | C#m F#m | G#7 C#m | E A | F#m G#7 | C#m F#m | C#m",
    "     力度：pianissimo 起，第 9–11 小节推到高潮，最后两小节渐弱回 C#m 终止。 -->",
    '<track name="Piano" instrument="piano" gain="0dB" pan="0" reverb="0.35">',
    "",
]

for bar, (name, bass, arp, arp_vel, bass_vel) in enumerate(CHORDS, start=1):
    start = (bar - 1) * 4
    last = bar == len(CHORDS)
    lines.append(f"  <!-- 第 {bar:2d} 小节 · {name:4s} "
                 + ("（终止，只写第 1 拍，留余韵到 60s）" if last else "") + " -->")

    bass_dur = 2 if last else F(19, 5)          # 3.8 拍
    lines.append(f'  <!-- 低音八度 -->')
    lines.append(f'  <note time="{start}" dur="{fmt(bass_dur)}" pitch="{bass}" vel="{bass_vel:.2f}"/>')

    lines.append(f'  <!-- 分解和弦三连音（每拍 3 个 1/3 拍；dur=3/4 保留一点踏板延音，',)
    lines.append(f'      每拍第 1 个音略强，做出三连音的分组脉冲） -->')
    beats = (1,) if last else range(4)
    for beat in beats:
        for index, pitch in enumerate(arp):
            t = F(start + beat) + F(index, 3)
            weight = (1.12, 0.94, 0.94)[index]
            lines.append(f'  <note time="{fmt(t)}" dur="3/4" pitch="{pitch}" '
                         f'vel="{arp_vel * weight:.2f}"/>')

    if bar in MELODY:
        lines.append(f'  <!-- 主题旋律 -->')
        for offset, dur, pitch, vel in MELODY[bar]:
            t = F(start) + F(offset)
            # 旋律略长于谱面，做连奏；力度高于伴奏，让主题浮在分解和弦之上
            note_dur = F(dur) + F(1, 4) if dur >= 1 else F(dur)
            vel = min(0.95, vel * 1.15)
            lines.append(f'  <note time="{fmt(t)}" dur="{fmt(note_dur)}" '
                         f'pitch="{pitch}" vel="{vel:.2f}"/>')
    lines.append("")

lines.append("</track>")
(ROOT / "tracks" / "piano.track.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")

project = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- 贝多芬《月光奏鸣曲》Op.27 No.2 第一乐章 · 1 分钟改编
     60BPM × 4/4 × 15 小节 = 60 秒；内容在 58.7 秒收束，固定 length 让母带末尾淡出收尾。
     这是钢琴独奏编制，改编自呈示部开头，不是完整乐章（原曲约 6 分钟）。 -->
<musicproj name="月光 · Moonlight" bpm="60" sampleRate="44100" beatsPerBar="4"
           length="60s" tail="2s" masterGain="-1dB" normalize="true">
  <track file="tracks/piano.track.xml"/>
</musicproj>
'''
(ROOT / "moonlight.musicproj").write_text(project, encoding="utf-8")

notes = sum(1 for line in lines if line.strip().startswith("<note"))
print(f"生成完成：{len(CHORDS)} 小节，{notes} 个音符")
print(ROOT / "moonlight.musicproj")
print(ROOT / "tracks" / "piano.track.xml")
