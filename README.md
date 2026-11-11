# MusicGen

## 1. 用途

MusicGen 是一个**用命令行驱动的多轨编曲渲染工具**：把一个 `*.musicproj`（XML）工程和它的每条音轨（`*.track.xml`）编译成乐器纯音乐 WAV。

- **工程即文本**：一个工程文件 + 每条音轨一个文件，可读、可手改、可进 Git，适合脚本化批量生成。
- **多轨混音**：每轨独立控制乐器、速度、音量、声像、混响、循环与移调，汇总成一首立体声成品。
- **音效链**：每轨可串联 7 个预设音效——立体、回声、淡入淡出、电音，以及 8bit / 4bit / 2bit 三种位深压缩，参数写在 `<effect>` 里，CLI 与界面都能调。
- **三种入口**：`musicgen` 命令行（渲染、校验、建轨、改音符、调音效、管乐器）、桌面界面（钢琴卷帘、波形、试听），以及 **AI Skill**——仓库自带 `Skill/musicgen` 技能包，AI 助手说清需求就能一路做到 WAV，见 [§2.6](#26-ai-skill)。
- **音色开源**：6 件真实采样乐器 + 1 套内置合成鼓组，缺采样时自动回退合成器，离线也能渲染。

```bat
python -m musicgen render examples\twinkle\twinkle.musicproj -o twinkle.wav
```

---

## 2. 使用说明

### 2.1 安装

需要 Python 3.10+，依赖只有三个（`numpy` / `soundfile` / `sounddevice`）：

```bat
py -3 -m pip install -r requirements.txt
```

> Windows 上如果默认的 `python` 不带 tkinter，命令行可用 `python`，**界面请用 `py -3`**。
> 也可以直接用仓库里的 `MusicGen-CLI.bat`（透传参数与退出码）和 `MusicGen.bat`（启动界面）。

### 2.2 首次运行

```bat
py -3 -m musicgen samples fetch                     :: 下载全部音源（6 件乐器，约 8MB，只需一次）
py -3 -m musicgen validate examples\demo\demo.musicproj
py -3 -m musicgen render examples\demo\demo.musicproj -o demo.wav
py -3 -m musicgen gui examples\demo\demo.musicproj   :: 打开界面
```

仓库自带 6 个示例工程：

| 示例 | 内容 |
| --- | --- |
| `examples/demo` | 8 小节 a 小调钢琴小品（最小可用工程） |
| `examples/ensemble` | 小提琴 + 大提琴 + 小号 + 钢琴，D 小调 |
| `examples/twinkle` | 《小星星》完整编曲（四件乐器，42 秒） |
| `examples/band` | 竖笛 + 吉他 + 架子鼓，8 小节 |
| `examples/moonlight` | 贝多芬《月光》第一乐章 1 分钟钢琴独奏改编（附编曲脚本） |
| `examples/daybreak` | 原创《晨光》16 小节，钢琴 + 吉他 + 小提琴 + 贝斯 + 鼓，带效果链 |

### 2.3 命令行

```bat
:: 渲染：默认 24bit / 44100Hz / 立体声 / 归一化到 −1dBFS
python -m musicgen render songs\Song\Song.musicproj -o out\song.wav
python -m musicgen render songs\Song\Song.musicproj -o out\ --bits 16 --no-normalize

:: 校验与概况（不渲染、不联网，可放进 CI）
python -m musicgen validate songs\Song\Song.musicproj
python -m musicgen info songs\Song\Song.musicproj

:: 新建工程（demo / empty 模板）
python -m musicgen init songs\NewSong --bpm 96 --template empty

:: 改轨道：新增 / 编辑 / 删除 / 列出
python -m musicgen track list songs\Song\Song.musicproj
python -m musicgen track add songs\Song\Song.musicproj --name Guitar --instrument guitar --gain -4dB
python -m musicgen track edit songs\Song\Song.musicproj --track 1 --gain -6dB --no-loop
python -m musicgen track remove songs\Song\Song.musicproj --track 3

:: 改音符：增 / 改 / 删 / 列（--track 支持序号、名称、文件名）
python -m musicgen note add songs\Song\Song.musicproj --track Guitar --time 2 --dur 1/2 --pitch F#4 --vel 0.5
python -m musicgen note set songs\Song\Song.musicproj --track Guitar --index 1 --pitch C5
python -m musicgen note del songs\Song\Song.musicproj --track Guitar --index 2

:: 音源与乐器注册表
python -m musicgen samples list
python -m musicgen samples fetch --instrument cello,violin
python -m musicgen instrument list
python -m musicgen instrument add flute --name 长笛 --gm flute --low 60 --high 96 --gain 8.5
python -m musicgen instrument remove flute
python -m musicgen instrument reset

:: 音效：类型清单与效果链
python -m musicgen effect types
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type echo -p time=375 -p mix=0.22
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type 立体 -p width=1.7
python -m musicgen effect set songs\Song\Song.musicproj --track Guitar --index 1 -p feedback=0.45
python -m musicgen effect list songs\Song\Song.musicproj --track Guitar
python -m musicgen effect remove songs\Song\Song.musicproj --track Guitar --index 2
```

退出码：`0` 成功 · `1` 参数/环境错误 · `2` 工程文件有错 · `3` 渲染失败 · `130` 中断。
完整参数表、真实输出、报错对照与批量脚本：[docs/CLI文档.md](docs/CLI文档.md)。

### 2.4 工程文件

```xml
<!-- demo.musicproj：一个工程 + 每轨一个文件 -->
<musicproj name="Nocturne in A minor" bpm="84" sampleRate="44100" length="auto">
  <track file="tracks/violin.track.xml"/>
  <track file="tracks/drums.track.xml" gain="-2dB"/>
</musicproj>

<!-- tracks/violin.track.xml：乐器可写键名或中文名 -->
<track name="Violin" instrument="violin" gain="-3dB" pan="0.22" reverb="0.35">
  <effect type="stereo" width="1.5"/>                 <!-- 音效按顺序串联 -->
  <note time="0" dur="1" pitch="A4" vel="0.72"/>
  <note time="2" dur="2" pitch="C4,E4,G4" vel="0.6"/>   <!-- 逗号即和弦 -->
</track>

<!-- 鼓件名直接当音高写，不用记 GM 编号 -->
<track name="Drums" instrument="drums">
  <note time="0" dur="0.25" pitch="kick,hat"/>
  <note time="1" dur="0.25" pitch="snare"/>
</track>
```

时值单位是「拍」，支持 `1/4` 与 `0.5` 两种写法，也支持 `1/3` 三连音；循环轨用 `loop="true" loopLen="4"` 平铺整首。
音效写在音符前面、按出现顺序串联：`<effect type="echo" time="375" feedback="0.3" mix="0.22"/>`，
类型与参数用 `python -m musicgen effect types` 查看。
完整字段规范见 [项目文档](docs/项目文档.md)。

### 2.5 界面

`MusicGen.bat` 或 `py -3 -m musicgen gui`。Visual Studio 2022 深色风格，四个工具窗口（资源管理器 / 属性 / 音符 / 输出）按住标题栏可拖到左、右、底部停靠，布局自动记忆。

- 卷帘：`滚轮` 上下 · `Alt+滚轮` 左右 · `Ctrl+滚轮` 缩放 · 中键拖动平移
- 常用键：`Ctrl+N` 新建工程 · `Ctrl+Shift+T` 新建轨道 · `Ctrl+Shift+Delete` 删除音轨 · `Ctrl+Shift+V` 单独查看音轨 · `Ctrl+Shift+G` 乐器注册表 · `Ctrl+R` 渲染 · `Ctrl+S` 导出
- 渲染完可直接试听，波形页有播放头；单个音轨可单独试听并导出分轨 WAV
- 属性面板「效果」分组可添加 / 编辑 / 删除音效，滑杆调参后随「应用并保存」写入音轨 XML

### 2.6 AI Skill

`Skill/musicgen/` 是给 AI 助手用的技能包：说清情绪、时长、编制，它就按
「设计单 → 写工程 → `validate` → `render` → 验收」走完整条链路，产出 `output/<曲名>.wav`。

- 入口 `Skill/musicgen/SKILL.md`：五步工作流、命令速查、9 条硬规则（音域、采样长度、循环、`tail` …）
- 参考 `Skill/musicgen/references/`：`编曲设计.md`（通用编曲：功能层 / 曲式 / 和声 / 配器 / 织体 / 律动，附到 7 件乐器的映射）、`工程格式.md`、`混音与验收.md`
- 让 WorkBuddy 加载（它只扫描 `.workbuddy/skills/`，用目录联接把技能挂进去，`Skill/` 仍是唯一源）：

```bat
mklink /J .workbuddy\skills\musicgen Skill\musicgen
```

---

## 3. TODOLIST

### 3.1 已支持的乐器

| 键名 | 名称 | 来源 | 音域 | 采样数 |
| --- | --- | --- | --- | --- |
| `piano` | 钢琴 | FluidR3 GM | A0–C8 | 88 |
| `violin` | 小提琴 | FluidR3 GM | G3–C7 | 42 |
| `cello` | 大提琴 | FluidR3 GM | C2–C6 | 49 |
| `trumpet` | 小号 | FluidR3 GM | E3–C6 | 33 |
| `recorder` | 竖笛 | FluidR3 GM | C4–C7 | 37 |
| `guitar` | 吉他（钢弦） | FluidR3 GM | E2–E6 | 49 |
| `drums` | 架子鼓 | **内置合成**，无需下载 | GM 打击乐 35–82 | — |

工程里写 `instrument="cello"` 或 `instrument="大提琴"` 都可以；鼓件写 `kick` / `snare` / `hat` / `openhat` / `crash` / `tom1` / `floor` 等，中文（`底鼓`、`军鼓`）也认。

### 3.2 自行添加乐器（已实现）

乐器清单是**可编辑的 JSON 注册表**（默认 `~/.musicgen/instruments.json`，可用 `MUSICGEN_INSTRUMENTS` 覆盖）：

```bat
python -m musicgen instrument add flute --name 长笛 --gm flute --low 60 --high 96 --gain 8.5
python -m musicgen samples fetch --instrument flute
```

- `--gm` 给 FluidR3 GM 音色包名，自动生成 4 个下载镜像；也可用 `--url` 指自定义地址
- 支持别名、音域、采样增益、许可与主页信息
- 界面「编辑 → 乐器注册表…」（`Ctrl+Shift+G`）可添加、删除、恢复默认，改完下拉框立即刷新
- 删除条目不会删已下载的采样缓存

### 3.3 音效效果链（已实现）

每轨可写多条 `<effect>`，按顺序串联，处理位置在声像之后、汇入母带之前：

| 类型 | 中文 | 参数（默认值） |
| --- | --- | --- |
| `stereo` | 立体 | `width` 0–2（1.6）、`haas` 0–30ms（0）、`mix` 0–1（1） |
| `echo` | 回声 | `time` 20–2000ms（320）、`feedback` 0–0.9（0.36）、`tone` 0–1（0.5）、`pingpong`（开）、`mix` 0–1（0.3） |
| `fade` | 淡入淡出 | `in` 0–10s（0.8）、`out` 0–10s（1.5）、`curve` linear/expo（expo） |
| `electro` | 电音 | `rate` 0.5–200Hz（30）、`depth` 0–1（0.8）、`drive` 0–1（0.35）、`bits` 3–16（8）、`mix` 0–1（0.85） |
| `8bit` | 8bit | `rate` 500–44100Hz（11025）、`tone` 0–1（0.75）、`drive` 0–1（0）、`mix` 0–1（1） |
| `4bit` | 4bit | `rate` 500–44100Hz（8000）、`tone` 0–1（0.6）、`drive` 0–1（0）、`mix` 0–1（1） |
| `2bit` | 2bit | `rate` 500–44100Hz（4000）、`tone` 0–1（0.45）、`drive` 0–1（0）、`mix` 0–1（1） |

- 参数只写要改的，其余用注册表默认值；`type` 可写注册键或中文名
- `8bit` / `4bit` / `2bit` 是「降采样 + 量化」的位深压缩，分别留 256 / 16 / 4 级电平；量化按素材自身峰值做，插入后轨道响度不变
- 未知类型 / 参数只警告不中断渲染；数值越界会夹到范围内
- 新增效果只要在 `core/effects.py` 写一个 `@register_effect` 的 `Effect` 子类，CLI、界面、解析与回写自动接上

### 3.4 待实现

按对“写一首歌”的实际阻碍排序：`P0` 是现在就会卡住的，`P2` 是锦上添花。

**编曲与编辑**

- [ ] `P0` **撤销 / 重做**：界面每次编辑直接写回 XML，改错了只能手改文件或重开工程
- [ ] `P1` **自动化曲线**：音量、声像、效果参数随时间变化（渐强渐弱、声像扫），现在只能整轨一个定值，渐变要靠 `fade` 效果或另开一条轨
- [ ] `P1` **轨道复制 / 排序**：音符可以 `Ctrl+D` 复制，整条音轨不行，也不能在列表里拖动顺序
- [ ] `P2` **速度渐变（tempo map）**：目前只有全局 BPM + 逐轨 BPM
- [ ] `P2` **延音踏板与连奏**：弦乐揉弦、铜管滑音、钢琴 una corda 踏板
- [ ] `P2` **多力度层采样**：现在力度只影响音量与亮度，真采样应分 soft / medium / hard 多层切换

**混音与音质**

- [ ] `P0` **轨道电平表**：混音时看不到每轨峰值 / RMS，只能渲染完看总电平。实测同一份工程里钢琴和吉他能差 13dB，得写脚本逐轨渲染才发现
- [ ] `P1` **更多效果**：EQ、压缩、失真、chorus；以及独立 send 总线与母带效果（当前 4 个都是插入效果，且只能挂在音轨上）
- [ ] `P2` **自定义音色**：导入自己的采样（WAV/AIFF），自定义键位映射、循环点、力度分层与包络，而不只是套用 GM 音色
- [ ] `P2` **更多音色**：长笛、单簧管、弦乐群、电钢琴、贝斯等
- [ ] `P2` **MP3 / FLAC 导出**：需要引入编码器依赖

**互操作与批量**

- [ ] `P1` **MIDI 导入 / 导出**：把现有工程与 DAW 打通
- [ ] `P1` **批量分轨导出**：一次导出全部音轨的 stems，而不是逐轨点

---

## 4. LICENSES

| 组件 | 许可证 |
| --- | --- |
| MusicGen 自身代码 | MIT License，见仓库根目录 [LICENSE](LICENSE) |
| 示例曲目 | 除《小星星》（民谣，公有领域）与贝多芬《月光》（公有领域）外，其余示例为原创 |
| FluidR3 GM SoundFont（全部采样音色） | MIT / 自由使用，源自 Frank Wen，经 [gleitz/midi-js-soundfonts](https://github.com/gleitz/midi-js-soundfonts) 分发 |
| numpy | BSD-3-Clause |
| soundfile（Python 绑定） | BSD-3-Clause；底层 libsndfile 为 LGPL-2.1+ |
| sounddevice | MIT；底层 PortAudio 为 MIT |
| Tcl/Tk（界面） | BSD 风格许可 |

每个乐器的采样目录下都会写 `manifest.json`，记录该音源的来源、许可与文件清单：

```bat
python -m musicgen samples list        :: 查看状态、音域与许可
type samples_cache\piano\manifest.json
```
