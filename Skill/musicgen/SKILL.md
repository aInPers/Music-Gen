---
name: musicgen
description: 用 MusicGen 命令行把音乐需求做成可交付的 WAV——写 *.musicproj 工程与音轨 XML、设计和声与配器、渲染并验收。当用户要求写背景音乐、配乐、编曲、BGM、纯音乐小样，或提到 musicgen、musicproj、乐器轨、钢琴卷帘时使用。
---

# MusicGen 音乐设计与输出

## 用途

把「写一段 30 秒治愈系钢琴曲」落成两样东西：一个可复现的工程目录 + 一个 WAV。
底层是仓库自带的 `python -m musicgen`，不需要任何外部音乐库或在线服务。

**能力边界**：13 件内置音色（钢琴 / 小提琴 / 大提琴 / 小号 / 竖笛 / 吉他 / 贝斯 / 人声 / 合成鼓组 /
合成垫 / 合成主音 / 合成贝斯 / 合成拨弦）+ 任意 `.synth.json` 自定义合成音色、11 个效果、
44.1kHz 立体声 WAV。内置 `vocal` 音色只有元音吟唱（没有歌词）；音频分析里人声是原唱分轨原样保留。
没有 MIDI 导出、速度渐变、延音踏板（导入有 `import-midi`）。

## 开工前检查

```bash
cd /f/Music-Gen            # cmd 里写 cd /d F:\Music-Gen
python -m musicgen samples list
```

- 八件采样都显示「就绪」才有真实音色；缺了先 `python -m musicgen samples fetch`（联网约 10MB）。
  合成音色与鼓组无需下载，永远就绪。
- 工程写 `songs/<slug>/`，成品写 `output/<slug>.wav`；**不要改 `examples/`**，那是回归样例。
- 目录名与文件名用英文或拼音 slug，中文只放 `name` 属性里。

## 五步工作流

### 1. 先写设计单

动手前把五项定死，缺省就用括号里的默认值。最忌一边写音符一边想结构。

| 项 | 默认 | 说明 |
| --- | --- | --- |
| 用途与时长 | 30 秒 | 短视频 BGM 15–30s；播客片头 10s；展示片 60–90s |
| 情绪 | 温暖 | 温暖 / 忧郁 / 明亮 / 紧张 / 史诗；映射表见 `references/编曲设计.md` §2 |
| 编制 | 钢琴 + 大提琴 | 2–5 轨；轨数越多越难混，独奏钢琴也很好听 |
| 调性与速度 | C 大调 96BPM | 按情绪表选，升降号别超过三个 |
| 曲式 | 4 前奏 + 8 A + 4 尾 | 用小节数写，换算公式 `小节 = 秒 × BPM ÷ 240`（4/4） |

### 2. 建工程目录

多轨一次成型，直接写 XML（推荐）：

```
songs/<slug>/<slug>.musicproj
songs/<slug>/tracks/melody.track.xml
songs/<slug>/tracks/chords.track.xml
songs/<slug>/tracks/cello.track.xml
```

需要脚手架时用 `python -m musicgen init songs/<slug> --bpm 96 --template empty`，再把空轨改写。
字段、时值语法、可直接复制的模板见 `references/工程格式.md`。

### 3. 写音轨

- **一轨一个角色**：旋律 / 和声 / 低音 / 节奏。不要把所有东西塞进一轨。
- **每个 `<note>` 都写 `time`**。省略 `time` 表示「接上一个音结尾」，多声部时必然错位。
- **一次写完整段**。不要用 `note add` 逐音堆几十次——慢、容易错位；`note add/set/del` 只用于事后微调。
- **力度分层**：伴奏 0.30–0.45、中声部 0.50–0.65、旋律 0.70–0.85；每拍第一个音再 +0.05–0.1。
- **和弦写成一行**：`pitch="C4,E4,G4"`，不要拆成三个音（省三分之二的行数，也不会漏拍）。
- **鼓轨写鼓件名**：`pitch="kick,hat"`、`pitch="snare"`，不用记 GM 编号。

### 4. 校验

```bash
python -m musicgen validate songs/<slug>/<slug>.musicproj
```

退出码 0 才继续。注意：`validate` **查不出音域问题**——那只在渲染时以 `!` 警告出现。

### 5. 渲染与验收

```bash
python -m musicgen render songs/<slug>/<slug>.musicproj -o output/<slug>.wav
```

输出里盯三行：

```
  时长 0:21.50 | 峰值 -1.0dBFS | RMS -19.8dBFS | 5.4MB | 位深 24
  音符事件 61 个 | 耗时 1.4s (15.8× 实时)
    · Melody [piano] 音符 21 峰值 0.550
```

- 所有 `!` 开头的行都要处理，最常见是音高超出采样音域（会静默换成合成器，音色断层）。
- 任一轨 `峰值 0` = 空轨或全静音。
- 验收标准与听感处方见 `references/混音与验收.md`。

## 命令速查

```bash
# 环境
cd /f/Music-Gen            # cmd 里写 cd /d F:\Music-Gen
python -m musicgen -V
python -m musicgen samples list
python -m musicgen samples fetch --instrument cello,guitar

# 建工程
python -m musicgen init songs/MySong --bpm 96 --template empty

# 校验与概要（不渲染、不联网）
python -m musicgen validate songs/MySong/MySong.musicproj
python -m musicgen info songs/MySong/MySong.musicproj

# 渲染：默认 24bit / 44100Hz / 立体声 / 归一化到 -1dBFS
python -m musicgen render songs/MySong/MySong.musicproj -o output/MySong.wav
python -m musicgen render songs/MySong/MySong.musicproj -o output/ --bits 16 --quiet

# 轨道与音符微调（写入时保留 XML 注释与排版）
python -m musicgen track list songs/MySong/MySong.musicproj
python -m musicgen track edit songs/MySong/MySong.musicproj --track 1 --gain -6dB --reverb 0.3
python -m musicgen track add  songs/MySong/MySong.musicproj --name Guitar --instrument guitar --gain -6dB
python -m musicgen note add songs/MySong/MySong.musicproj --track 1 --time 8 --dur 1/2 --pitch E5 --vel 0.7
python -m musicgen note set songs/MySong/MySong.musicproj --track 1 --index 3 --pitch G5
python -m musicgen note del songs/MySong/MySong.musicproj --track 1 --index 5

# 从音频还原（可选，先读 `references/音频还原.md`）
python -m musicgen analyze input/song.mp3 -o songs/Reverse --bpm 96               # 默认：可改谱 + 保真
python -m musicgen analyze input/song.mp3 -o songs/Reverse-Audio --audio-tracks   # 纯音频轨，完全不可编辑
# 默认「精确回放」不做自动增益/EQ/母带压缩，渲染≈原曲；要旧行为加 --no-exact-replay

# 逆向精修（先读 `references/逆向判断.md`）：读简报 → 写决策 → 回灌
python -m musicgen analyze input/song.mp3 -o songs/Reverse --no-layers --no-brief  # 只出工程
python -m musicgen analyze input/song.mp3 -o songs/Reverse --decisions songs/Reverse/decisions.json

# 合成音脚本（代替缺失音色；也能挂在音轨上）
python -m musicgen synth list
python -m musicgen synth check warm_pad --midi 60 --dur 0.8
python -m musicgen synth render analog_lead -o output/lead.wav --midi 60,64,67
python -m musicgen synth new -o songs/MySong/synths/pad.synth.json --preset warm_pad --label 暖垫
python -m musicgen synth show songs/MySong/synths/pad.synth.json

# 效果链（可选；顺序即信号流，每轨 2–3 个足够）
python -m musicgen effect types
python -m musicgen effect add songs/MySong/MySong.musicproj --track 1 --type echo -p time=375 -p mix=0.22
python -m musicgen effect add songs/MySong/MySong.musicproj --track 1 --type 立体 -p width=1.7
python -m musicgen effect set songs/MySong/MySong.musicproj --track 1 --index 1 -p feedback=0.45
python -m musicgen effect list songs/MySong/MySong.musicproj --track 1
python -m musicgen effect remove songs/MySong/MySong.musicproj --track 1 --index 2

# 音效脚本（.fx.json）：存 / 校验 / 复用
python -m musicgen effect save songs/MySong/MySong.musicproj --track 1 -o fx/bus.fx.json
python -m musicgen effect check fx/bus.fx.json
python -m musicgen effect apply songs/MySong/MySong.musicproj --track 2 --script fx/bus.fx.json
```

效果链：`stereo` 立体 / `echo` 回声 / `fade` 淡入淡出 / `electro` 电音 / `8bit` `4bit` `2bit` 位深压缩 /
`eq` 均衡（`low` `mid` `high` `mid_freq`）/ `drive` 失真（`amount` `tone` `mix`）/
`compressor` 压缩（`threshold` `ratio` `release` `makeup`）/ `chorus` 合唱（`rate` `depth` `mix`），参数只写要改的。
逆向精修补频谱用 `eq`，补谐波密度用 `drive`，压动态用 `compressor`，加宽用 `chorus`。
`fade` 放在链尾；回声尾巴受 `length` 限制，需要余韵就加 `tail`；`electro` 只给 1–2 条轨，别全铺。
`8bit` / `4bit` / `2bit` 位深固定，调 `rate`（越低越破）与 `mix`（留一点干声更耐听），插入后响度不变。

退出码：`0` 成功 · `1` 参数/环境错误 · `2` 工程文件有错 · `3` 渲染失败 · `130` 中断。
完整参数与错误对照见仓库 `docs/CLI文档.md`。

## 硬规则

| # | 规则 | 违反后的表现 | 正确做法 |
| --- | --- | --- | --- |
| 1 | 音高必须在乐器音域内 | **不报错**，只警告并换成合成器，音色断层 | 钢琴 A0–C8、小提琴 G3–C7、大提琴 C2–C6、小号 E3–C6、竖笛 C4–C7、吉他 E2–E6、贝斯 E1–G4、人声 C3–C6 |
| 2 | 单个采样约 3.13 秒 | 更长的音尾部被 0.15s 淡出，像断气 | 长音拆成 2–3 个重叠触发，或时值控制在 3 秒内 |
| 3 | 没有延音踏板、连奏、揉弦 | 钢琴长音靠采样自己衰减；弦乐每个音独立起音 | 钢琴用「时值 = 间隔 × 1.2–1.5」叠出延音；弦乐少换音、走长音 |
| 4 | 鼓的 `dur` 无效 | 以为能控长短，其实固定采样长度（开镲 1.9s、吊镲 2.5s） | 用鼓件与力度表达长短，不要调 `dur` |
| 5 | 循环轨音符起点要小于 `loopLen` | 超出周期的音不播放（`validate` 有警告） | `loopLen` ≥ 最后一个音的起点，或干脆不循环 |
| 6 | `length="auto"` 会自动加 `tail` | 成品比预期长（默认多 2 秒） | 要精确秒数就写 `length="60s"` |
| 7 | 工程层 `<track>` 不能覆盖 `reverb` | 写了不生效，还以为混响错了 | `reverb` 写进音轨文件，不要写在工程引用上 |
| 8 | 默认归一化到 −1dBFS | 多轨叠加不会削波，但整体响度由峰值决定 | 别用 RMS 跨曲比「谁更响」 |
| 9 | XML 特殊字符要转义 | `&` 未转义、注释里出现 `--` 都会解析失败（退出码 2） | 写 `&amp;`，注释里别用连续两个减号 |
| 10 | `instrument="synth"` 必须带 `patch` | 只警告并静默换成兜底合成器，音色完全不对 | 写 `patch="../synths/xxx.synth.json"`，相对音轨文件；先 `synth check` 过一遍 |
| 11 | 合成音脚本的 `oscillators` 只认 `sine/square/saw/triangle/noise` | 写错波形名载入失败，同样退回兜底音色 | 参数表见 `synth show <名>`；改完必须 `synth check` |

## 交付

一条消息里给全：

- 文件路径：`output/<slug>.wav`（24bit / 44.1kHz / 立体声）
- 时长、峰值、RMS（直接抄渲染摘要，别自己算）
- 编制与段落：`前奏 4 小节（钢琴）→ A 段 8 小节（+大提琴）→ 尾 4 小节（收束到主和弦）`
- 工程路径，方便用户改；要分享版再跑一次 `--bits 16`
- 如果是**从音频还原**：额外给 `analysis.json` 里的 bpm / 调性 / 每轨置信度 / `exact_replay`，
  并说明交付的是「保真版（默认精确回放）」还是「可改谱版（`--no-exact-replay`）」；
  精确回放下不要为了删误检去删音符——补全轨已挖空该区间，删了会变成静音
- 如果做了**逆向精修**：说明用了哪些 `decisions`（换了什么音色、删了哪轨、加了什么效果），
  并给出 `reverse_brief.json` 里支撑判断的关键数值（不是「听起来像」）

## 参考文件

| 文件 | 什么时候读 |
| --- | --- |
| `references/编曲设计.md` | 通用编曲方法（功能层、曲式、和声、配器、织体、律动、风格配方）。先按它设计，再看 §13 映射到本项目的 9 件基础乐器（+4 件合成音色） |
| `references/工程格式.md` | 写或改 `*.musicproj` / `*.track.xml` 之前 |
| `references/混音与验收.md` | 定 gain / pan / reverb、判断成品能不能交时 |
| `references/音频还原.md` | 用户给的是音频而不是需求（「把这首 mp3 还原成工程」）时；含参数、分轨映射、误检清理与验收清单 |
| `references/逆向判断.md` | 要**精修**还原结果（换音色、上合成音、加效果、拆分层、改段落标签）时；含判据、决策文件 schema 与验收方法 |

仓库 `examples/` 里还有五个可跑范例：`demo`（钢琴小品）、`ensemble`（四重奏）、`twinkle`（儿歌改编）、`band`（含鼓）、`moonlight`（三连音与长踏板模拟）。
