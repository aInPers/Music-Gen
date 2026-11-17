"""GM 128 音色目录：FluidR3 GM 音源包里全部可下载乐器的静态清单。

设计要点：

* **纯数据，无 IO**。目录不依赖网络，离线也能查询、搜索、给出下载建议；
  真正的下载仍由 :mod:`musicgen.samples.fetch` 负责。
* **与注册表解耦**。注册表（``instruments.json``）只收录「用户实际要用的」
  乐器，默认仍是常用的十来件；目录里的其余乐器按需 ``ensure`` 进来，
  渲染工程时若发现音轨用了目录中的乐器，可自动补齐注册表再下载。
* **音域是「建议演奏音域」**，用来决定下载哪些键位、以及渲染前做音域检查。
  它比 GM 规范的 0–127 窄，是为了避免为一个只用到中音区的长笛下满 88 键。

条目顺序即 GM program 顺序（0–127），``family`` 对应 GM 的 16 个音色族。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

#: 通用 MIDI 音色族（每族 8 个 program）
FAMILIES: tuple[str, ...] = (
    "钢琴", "色彩打击", "风琴", "吉他", "贝斯", "弦乐", "合奏", "铜管",
    "簧管", "吹管", "合成主音", "合成铺底", "合成音效", "民族乐器", "打击乐", "音效",
)

#: 各音色族的默认电平补偿（采样本身响度差异很大，这里给族级基准）
_FAMILY_GAIN: dict[str, float] = {
    "钢琴": 16.0, "色彩打击": 14.0, "风琴": 10.0, "吉他": 7.0,
    "贝斯": 10.0, "弦乐": 6.5, "合奏": 8.0, "铜管": 9.0,
    "簧管": 9.0, "吹管": 8.0, "合成主音": 8.0, "合成铺底": 8.0,
    "合成音效": 8.0, "民族乐器": 9.0, "打击乐": 12.0, "音效": 10.0,
}

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def midi_name(midi: int) -> str:
    """MIDI 编号转音名（``60`` → ``C4``），与 core.notes 的约定一致。"""
    octave = midi // 12 - 1
    return f"{_NOTE_NAMES[midi % 12]}{octave}"


@dataclass(frozen=True)
class GmProgram:
    """目录中的一件乐器。"""

    program: int
    gm: str                  # FluidR3 包名，同时是下载路径的一部分
    key: str                 # 注册键（默认与 gm 相同，少数沿用旧短名）
    en: str                  # 英文名（GM 规范写法）
    zh: str                  # 中文名
    family: str
    low: int                 # 建议最低 MIDI
    high: int                # 建议最高 MIDI
    gain: float
    aliases: tuple[str, ...] = field(default=())

    @property
    def range_label(self) -> str:
        return f"{midi_name(self.low)}–{midi_name(self.high)}（{self.zh}常用音域）"

    @property
    def title(self) -> str:
        return f"FluidR3 GM — {self.en}"

    def registry_entry(self) -> dict:
        """转成注册表条目（``instruments.json`` 里的形态）。"""
        aliases = [self.en.lower(), self.zh, *self.aliases]
        seen: list[str] = []
        for alias in aliases:
            alias = alias.strip()
            if alias and alias not in seen:
                seen.append(alias)
        return {
            "key": self.key,
            "label": self.zh,
            "kind": "sample",
            "gm": self.gm,
            "title": self.title,
            "midi_low": self.low,
            "midi_high": self.high,
            "range_label": self.range_label,
            "gain": self.gain,
            "aliases": seen,
        }


# (program, gm 包名, 英文名, 中文名, 音色族, 最低 MIDI, 最高 MIDI, 额外别名)
_RAW: tuple[tuple[int, str, str, str, str, int, int, tuple[str, ...]], ...] = (
    # 0–7 钢琴
    (0, "acoustic_grand_piano", "Acoustic Grand Piano", "大钢琴", "钢琴", 21, 108, ("piano", "三角钢琴", "grand")),
    (1, "bright_acoustic_piano", "Bright Acoustic Piano", "明亮钢琴", "钢琴", 21, 108, ("piano bright", "亮钢琴")),
    (2, "electric_grand_piano", "Electric Grand Piano", "电钢琴", "钢琴", 28, 103, ("电声大钢琴",)),
    (3, "honkytonk_piano", "Honky-tonk Piano", "酒吧钢琴", "钢琴", 21, 108, ("酒馆钢琴", "跑调钢琴")),
    (4, "electric_piano_1", "Electric Piano 1", "电钢琴 1", "钢琴", 28, 103, ("rhodes", "电钢1")),
    (5, "electric_piano_2", "Electric Piano 2", "电钢琴 2", "钢琴", 28, 103, ("fm piano", "电钢2")),
    (6, "harpsichord", "Harpsichord", "羽管键琴", "钢琴", 29, 89, ("大键琴", "古钢琴")),
    (7, "clavinet", "Clavinet", "击弦古钢琴", "钢琴", 36, 96, ("clavi", "clav")),
    # 8–15 色彩打击
    (8, "celesta", "Celesta", "钢片琴", "色彩打击", 60, 108, ("celeste",)),
    (9, "glockenspiel", "Glockenspiel", "钟琴", "色彩打击", 72, 108, ("铁琴", "钟片琴")),
    (10, "music_box", "Music Box", "八音盒", "色彩打击", 60, 96, ("musicbox", "音乐盒")),
    (11, "vibraphone", "Vibraphone", "颤音琴", "色彩打击", 53, 89, ("vibes", "颤音铁琴")),
    (12, "marimba", "Marimba", "马林巴", "色彩打击", 45, 96, ("木琴",)),
    (13, "xylophone", "Xylophone", "木琴", "色彩打击", 65, 108, ("xylo",)),
    (14, "tubular_bells", "Tubular Bells", "管钟", "色彩打击", 60, 84, ("chimes", "教堂钟")),
    (15, "dulcimer", "Dulcimer", "扬琴", "色彩打击", 48, 84, ("洋琴",)),
    # 16–23 风琴
    (16, "drawbar_organ", "Drawbar Organ", "拉杆风琴", "风琴", 36, 96, ("hammond", "电风琴")),
    (17, "percussive_organ", "Percussive Organ", "打击风琴", "风琴", 36, 96, ("敲击风琴",)),
    (18, "rock_organ", "Rock Organ", "摇滚风琴", "风琴", 36, 96, ("dist organ",)),
    (19, "church_organ", "Church Organ", "教堂风琴", "风琴", 36, 96, ("管风琴", "pipe organ")),
    (20, "reed_organ", "Reed Organ", "簧风琴", "风琴", 36, 96, ("pump organ", "簧片风琴")),
    (21, "accordion", "Accordion", "手风琴", "风琴", 53, 96, ("accordian",)),
    (22, "harmonica", "Harmonica", "口琴", "风琴", 60, 96, ("mouth organ", "蓝调口琴")),
    (23, "tango_accordion", "Tango Accordion", "探戈手风琴", "风琴", 53, 96, ("bandoneon",)),
    # 24–31 吉他
    (24, "acoustic_guitar_nylon", "Acoustic Guitar (nylon)", "尼龙弦吉他", "吉他", 40, 88, ("classical guitar", "古典吉他", "nylon")),
    (25, "acoustic_guitar_steel", "Acoustic Guitar (steel)", "钢弦吉他", "吉他", 40, 88, ("guitar", "民谣吉他", "木吉他", "acoustic")),
    (26, "electric_guitar_jazz", "Electric Guitar (jazz)", "爵士电吉他", "吉他", 40, 88, ("jazz guitar", "空心电吉他")),
    (27, "electric_guitar_clean", "Electric Guitar (clean)", "清音电吉他", "吉他", 40, 88, ("clean guitar", "电吉他")),
    (28, "electric_guitar_muted", "Electric Guitar (muted)", "闷音电吉他", "吉他", 40, 88, ("muted guitar", "切音吉他")),
    (29, "overdriven_guitar", "Overdriven Guitar", "过载吉他", "吉他", 40, 88, ("overdrive",)),
    (30, "distortion_guitar", "Distortion Guitar", "失真吉他", "吉他", 40, 88, ("dist guitar", "重失真")),
    (31, "guitar_harmonics", "Guitar Harmonics", "吉他泛音", "吉他", 40, 88, ("harmonics",)),
    # 32–39 贝斯
    (32, "acoustic_bass", "Acoustic Bass", "原声贝斯", "贝斯", 28, 67, ("double bass", "低音提琴", "upright bass", "jazz bass")),
    (33, "electric_bass_finger", "Electric Bass (finger)", "指弹电贝斯", "贝斯", 28, 67, ("bass", "电贝斯", "贝司", "低音吉他", "bass guitar")),
    (34, "electric_bass_pick", "Electric Bass (pick)", "拨片电贝斯", "贝斯", 28, 67, ("pick bass", "拨片贝斯")),
    (35, "fretless_bass", "Fretless Bass", "无品贝斯", "贝斯", 28, 67, ("fretless",)),
    (36, "slap_bass_1", "Slap Bass 1", "击弦贝斯 1", "贝斯", 28, 67, ("slap bass", "slap")),
    (37, "slap_bass_2", "Slap Bass 2", "击弦贝斯 2", "贝斯", 28, 67, ("slap2",)),
    (38, "synth_bass_1", "Synth Bass 1", "合成贝斯 1", "贝斯", 24, 72, ("synth bass", "synthbass")),
    (39, "synth_bass_2", "Synth Bass 2", "合成贝斯 2", "贝斯", 24, 72, ("synthbass2",)),
    # 40–47 弦乐
    (40, "violin", "Violin", "小提琴", "弦乐", 55, 96, ("fiddle 音色",)),
    (41, "viola", "Viola", "中提琴", "弦乐", 48, 88, ("中音提琴",)),
    (42, "cello", "Cello", "大提琴", "弦乐", 36, 84, ("violoncello",)),
    (43, "contrabass", "Contrabass", "低音提琴", "弦乐", 28, 72, ("double bass 弦乐", "弦乐低音")),
    (44, "tremolo_strings", "Tremolo Strings", "弦乐震音", "弦乐", 48, 96, ("tremolo", "震音弦乐")),
    (45, "pizzicato_strings", "Pizzicato Strings", "弦乐拨奏", "弦乐", 48, 96, ("pizzicato", "pizz", "拨奏")),
    (46, "orchestral_harp", "Orchestral Harp", "竖琴", "弦乐", 24, 96, ("harp", "管弦竖琴")),
    (47, "timpani", "Timpani", "定音鼓", "弦乐", 36, 60, ("kettledrum",)),
    # 48–55 合奏
    (48, "string_ensemble_1", "String Ensemble 1", "弦乐合奏 1", "合奏", 36, 96, ("弦乐群", "string ensemble", "弦乐合奏")),
    (49, "string_ensemble_2", "String Ensemble 2", "弦乐合奏 2", "合奏", 36, 96, ("slow strings", "慢弦乐")),
    (50, "synth_strings_1", "SynthStrings 1", "合成弦乐 1", "合奏", 36, 96, ("synth strings",)),
    (51, "synth_strings_2", "SynthStrings 2", "合成弦乐 2", "合奏", 36, 96, ("synth strings 2",)),
    (52, "choir_aahs", "Choir Aahs", "人声啊", "合奏", 48, 84, ("choir", "合唱", "aahs", "人声")),
    (53, "voice_oohs", "Voice Oohs", "人声哦", "合奏", 48, 84, ("vocal", "vox", "voice", "吟唱", "人声轨")),
    (54, "synth_choir", "Synth Voice", "合成人声", "合奏", 48, 84, ("synth voice", "合成合唱")),
    (55, "orchestra_hit", "Orchestra Hit", "管弦乐齐奏", "合奏", 36, 84, ("orch hit", "orchestra stab", "齐奏重音")),
    # 56–63 铜管
    (56, "trumpet", "Trumpet", "小号", "铜管", 52, 84, ("小喇叭",)),
    (57, "trombone", "Trombone", "长号", "铜管", 40, 72, ("伸缩号", "拉管")),
    (58, "tuba", "Tuba", "大号", "铜管", 28, 60, ("低音号",)),
    (59, "muted_trumpet", "Muted Trumpet", "弱音小号", "铜管", 52, 84, ("mute trumpet", "加弱音器小号")),
    (60, "french_horn", "French Horn", "圆号", "铜管", 40, 77, ("horn", "法国号")),
    (61, "brass_section", "Brass Section", "铜管合奏", "铜管", 40, 84, ("brass", "铜管群")),
    (62, "synth_brass_1", "SynthBrass 1", "合成铜管 1", "铜管", 36, 84, ("synth brass",)),
    (63, "synth_brass_2", "SynthBrass 2", "合成铜管 2", "铜管", 36, 84, ("synth brass 2",)),
    # 64–71 簧管
    (64, "soprano_sax", "Soprano Sax", "高音萨克斯", "簧管", 56, 88, ("soprano", "高萨")),
    (65, "alto_sax", "Alto Sax", "中音萨克斯", "簧管", 49, 81, ("sax", "saxophone", "萨克斯", "中萨")),
    (66, "tenor_sax", "Tenor Sax", "次中音萨克斯", "簧管", 44, 76, ("tenor", "次中萨")),
    (67, "baritone_sax", "Baritone Sax", "上低音萨克斯", "簧管", 36, 68, ("bari sax", "低萨")),
    (68, "oboe", "Oboe", "双簧管", "簧管", 58, 88, ("hautboy",)),
    (69, "english_horn", "English Horn", "英国管", "簧管", 52, 81, ("cor anglais",)),
    (70, "bassoon", "Bassoon", "巴松管", "簧管", 36, 72, ("fagotto", "大管")),
    (71, "clarinet", "Clarinet", "单簧管", "簧管", 50, 91, ("黑管",)),
    # 72–79 吹管
    (72, "piccolo", "Piccolo", "短笛", "吹管", 74, 108, ("小笛",)),
    (73, "flute", "Flute", "长笛", "吹管", 60, 96, ("横笛",)),
    (74, "recorder", "Recorder", "竖笛", "吹管", 60, 96, ("牧童笛", "木笛")),
    (75, "pan_flute", "Pan Flute", "排箫", "吹管", 60, 96, ("panpipe", "排笛")),
    (76, "blown_bottle", "Blown Bottle", "吹瓶", "吹管", 60, 96, ("bottle", "瓶口")),
    (77, "shakuhachi", "Shakuhachi", "尺八", "吹管", 60, 96, ("日本笛",)),
    (78, "whistle", "Whistle", "口哨", "吹管", 60, 96, ("tin whistle",)),
    (79, "ocarina", "Ocarina", "陶笛", "吹管", 60, 96, ("埙",)),
    # 80–87 合成主音
    (80, "lead_1_square", "Lead 1 (square)", "方波主音", "合成主音", 36, 96, ("square lead", "方波")),
    (81, "lead_2_sawtooth", "Lead 2 (sawtooth)", "锯齿波主音", "合成主音", 36, 96, ("saw lead", "锯齿波")),
    (82, "lead_3_calliope", "Lead 3 (calliope)", "汽笛风琴主音", "合成主音", 36, 96, ("calliope",)),
    (83, "lead_4_chiff", "Lead 4 (chiff)", "气声主音", "合成主音", 36, 96, ("chiff",)),
    (84, "lead_5_charang", "Lead 5 (charang)", "查朗主音", "合成主音", 36, 96, ("charang",)),
    (85, "lead_6_voice", "Lead 6 (voice)", "人声主音", "合成主音", 36, 96, ("voice lead",)),
    (86, "lead_7_fifths", "Lead 7 (fifths)", "五度主音", "合成主音", 36, 96, ("fifths",)),
    (87, "lead_8_bass__lead", "Lead 8 (bass + lead)", "贝斯主音", "合成主音", 24, 84, ("bass lead", "bass+lead")),
    # 88–95 合成铺底
    (88, "pad_1_new_age", "Pad 1 (new age)", "新时代铺底", "合成铺底", 36, 96, ("new age", "fantasia")),
    (89, "pad_2_warm", "Pad 2 (warm)", "温暖铺底", "合成铺底", 36, 96, ("warm pad", "暖垫")),
    (90, "pad_3_polysynth", "Pad 3 (polysynth)", "复音铺底", "合成铺底", 36, 96, ("polysynth",)),
    (91, "pad_4_choir", "Pad 4 (choir)", "人声铺底", "合成铺底", 36, 96, ("choir pad",)),
    (92, "pad_5_bowed", "Pad 5 (bowed)", "弓弦铺底", "合成铺底", 36, 96, ("bowed pad",)),
    (93, "pad_6_metallic", "Pad 6 (metallic)", "金属铺底", "合成铺底", 36, 96, ("metallic pad",)),
    (94, "pad_7_halo", "Pad 7 (halo)", "光环铺底", "合成铺底", 36, 96, ("halo pad",)),
    (95, "pad_8_sweep", "Pad 8 (sweep)", "扫频铺底", "合成铺底", 36, 96, ("sweep pad", "扫频")),
    # 96–103 合成音效
    (96, "fx_1_rain", "FX 1 (rain)", "雨声", "合成音效", 36, 96, ("rain", "下雨")),
    (97, "fx_2_soundtrack", "FX 2 (soundtrack)", "配乐音效", "合成音效", 36, 96, ("soundtrack",)),
    (98, "fx_3_crystal", "FX 3 (crystal)", "水晶", "合成音效", 36, 96, ("crystal", "水晶音")),
    (99, "fx_4_atmosphere", "FX 4 (atmosphere)", "氛围", "合成音效", 36, 96, ("atmosphere", "大气")),
    (100, "fx_5_brightness", "FX 5 (brightness)", "明亮音效", "合成音效", 36, 96, ("brightness",)),
    (101, "fx_6_goblins", "FX 6 (goblins)", "妖精", "合成音效", 36, 96, ("goblins",)),
    (102, "fx_7_echoes", "FX 7 (echoes)", "回声音效", "合成音效", 36, 96, ("echoes",)),
    (103, "fx_8_scifi", "FX 8 (sci-fi)", "科幻音效", "合成音效", 36, 96, ("scifi", "sci-fi", "科幻")),
    # 104–111 民族乐器
    (104, "sitar", "Sitar", "西塔琴", "民族乐器", 48, 84, ("印度琴",)),
    (105, "banjo", "Banjo", "班卓琴", "民族乐器", 48, 84, ("五弦琴",)),
    (106, "shamisen", "Shamisen", "三味线", "民族乐器", 52, 84, ("三弦", "日本三味线")),
    (107, "koto", "Koto", "古筝", "民族乐器", 48, 84, ("日本筝",)),
    (108, "kalimba", "Kalimba", "卡林巴", "民族乐器", 60, 96, ("拇指琴", "mbira")),
    (109, "bagpipe", "Bagpipe", "风笛", "民族乐器", 48, 84, ("苏格兰风笛",)),
    (110, "fiddle", "Fiddle", "民间小提琴", "民族乐器", 55, 96, ("乡村小提琴", "fiddle")),
    (111, "shanai", "Shanai", "唢呐", "民族乐器", 60, 88, ("shahnai", "锁呐")),
    # 112–119 打击乐
    (112, "tinkle_bell", "Tinkle Bell", "叮当铃", "打击乐", 72, 108, ("bell", "铃铛")),
    (113, "agogo", "Agogo", "阿哥哥铃", "打击乐", 60, 96, ("agogo bell",)),
    (114, "steel_drums", "Steel Drums", "钢鼓", "打击乐", 60, 96, ("steel drum", "钢盘")),
    (115, "woodblock", "Woodblock", "木鱼", "打击乐", 60, 84, ("木块", "响木")),
    (116, "taiko_drum", "Taiko Drum", "太鼓", "打击乐", 24, 60, ("太鼓鼓", "日本太鼓")),
    (117, "melodic_tom", "Melodic Tom", "旋律嗵鼓", "打击乐", 36, 72, ("tom", "嗵鼓")),
    (118, "synth_drum", "Synth Drum", "合成鼓", "打击乐", 36, 72, ("电子鼓",)),
    (119, "reverse_cymbal", "Reverse Cymbal", "反镲", "打击乐", 36, 72, ("倒放镲",)),
    # 120–127 音效
    (120, "guitar_fret_noise", "Guitar Fret Noise", "吉他品噪音", "音效", 40, 88, ("fret noise",)),
    (121, "breath_noise", "Breath Noise", "呼吸声", "音效", 36, 96, ("breath",)),
    (122, "seashore", "Seashore", "海浪", "音效", 21, 108, ("sea", "wave", "海边")),
    (123, "bird_tweet", "Bird Tweet", "鸟鸣", "音效", 60, 96, ("bird", "鸟叫")),
    (124, "telephone_ring", "Telephone Ring", "电话铃", "音效", 60, 96, ("phone", "电话")),
    (125, "helicopter", "Helicopter", "直升机", "音效", 21, 108, ("heli",)),
    (126, "applause", "Applause", "掌声", "音效", 21, 108, ("clap", "鼓掌")),
    (127, "gunshot", "Gunshot", "枪声", "音效", 21, 108, ("gun",)),
)

#: 少数乐器的注册键沿用旧短名，保持与已有工程/注册表兼容
_KEY_OVERRIDES: dict[str, str] = {
    "acoustic_grand_piano": "piano",
    "acoustic_guitar_steel": "guitar",
    "electric_bass_finger": "bass",
    "voice_oohs": "vocal",
}


def _build() -> tuple[GmProgram, ...]:
    programs = []
    for idx, (program, gm, en, zh, family, low, high, aliases) in enumerate(_RAW):
        assert program == idx, f"GM 目录顺序错位：{gm} 应为 {idx}"
        programs.append(
            GmProgram(
                program=program,
                gm=gm,
                key=_KEY_OVERRIDES.get(gm, gm),
                en=en,
                zh=zh,
                family=family,
                low=low,
                high=high,
                gain=_FAMILY_GAIN[family],
                aliases=aliases,
            )
        )
    assert len(programs) == 128, f"GM 目录应为 128 件，实际 {len(programs)}"
    return tuple(programs)


#: 全部 128 件，按 program 排序
PROGRAMS: tuple[GmProgram, ...] = _build()

#: 注册键 → 条目
BY_KEY: dict[str, GmProgram] = {p.key: p for p in PROGRAMS}
#: GM 包名 → 条目
BY_GM: dict[str, GmProgram] = {p.gm: p for p in PROGRAMS}


@lru_cache(maxsize=512)
def lookup(name: str) -> GmProgram | None:
    """按注册键、GM 包名、中文名或英文名查一件乐器（忽略大小写）。"""
    needle = (name or "").strip()
    if not needle:
        return None
    lowered = needle.lower()
    for table in (BY_KEY, BY_GM):
        if lowered in table:
            return table[lowered]
    for item in PROGRAMS:
        candidates = {item.key, item.gm, item.zh, item.en.lower(), *(a.lower() for a in item.aliases)}
        if lowered in candidates:
            return item
    return None


def by_family(family: str | None = None) -> list[GmProgram]:
    """按音色族筛选；``None`` 或 ``all`` 返回全部。"""
    if not family or family.lower() in ("all", "*"):
        return list(PROGRAMS)
    wanted = family.strip()
    return [p for p in PROGRAMS if p.family == wanted or wanted.lower() in p.family.lower()]


def search(keyword: str, limit: int = 20) -> list[GmProgram]:
    """模糊搜索：中文名、英文名、包名、别名、音色族都参与匹配。

    命中优先级：名称完全相等 > 名称包含 > 别名/包名包含 > 音色族包含。
    """
    needle = (keyword or "").strip().lower()
    if not needle:
        return []
    scored: list[tuple[int, GmProgram]] = []
    for item in PROGRAMS:
        names = [item.zh, item.en.lower(), item.gm, item.key, item.family.lower()]
        aliases = [a.lower() for a in item.aliases]
        score = 0
        if needle in (item.zh.lower(), item.en.lower(), item.gm, item.key):
            score = 100
        elif any(needle in n for n in names[:4]):
            score = 60
        elif any(needle in a for a in aliases):
            score = 40
        elif needle in item.family.lower():
            score = 20
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].program))
    return [item for _, item in scored[:limit]]
