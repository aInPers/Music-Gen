# MusicGen

## 1. 用途

MusicGen 是一个**用命令行驱动的多轨编曲渲染工具**：把一个 `*.musicproj`（XML）工程和它的每条音轨（`*.track.xml`）编译成乐器纯音乐 WAV。

- **工程即文本**：一个工程文件 + 每条音轨一个文件，可读、可手改、可进 Git，适合脚本化批量生成。
- **多轨混音**：每轨独立控制乐器、速度、音量、声像、混响、循环与移调，汇总成一首立体声成品。
- **音效链**：每轨可串联 11 个音效——立体、回声、淡入淡出、电音、8bit / 4bit / 2bit 位深压缩、均衡、失真、压缩、合唱；参数写在 `<effect>` 里，CLI 与界面都能调，整条链还能存成 `.fx.json` 复用。
- **三种入口**：`musicgen` 命令行（渲染、校验、建轨、改音符、调音效、管乐器）、桌面界面（钢琴卷帘、分轨卷帘、波形、电平表、试听），以及 **AI Skill**——仓库自带 `Skill/musicgen` 技能包，AI 助手说清需求就能一路做到 WAV，见 [§2.7](#27-ai-skill)。
- **音频逆向**：`musicgen analyze song.mp3 -o songs/Song` 把成品歌曲拆成人声 / 鼓 / 贝斯 / 钢琴 / 吉他 / 其他，人声整条作为音频轨原样保留，其余分轨用 FFT 转谱、按音色识别乐器；默认「精确回放」按原始起音切立体声块，块外由 `beds/` 补全轨原样填回，**默认状态与原分轨逐样本一致，改某个音符才只影响那一处**，最后写出可继续编辑、可直接渲染的 `*.musicproj`；还能按音区拆层、切分段落、输出 `reverse_brief.json` 交给大模型判断，再用 `--decisions` 回灌换音色/效果。
- **音色开源**：8 件真实采样乐器 + 内置合成鼓组 + 4 件合成音色，另有 `.synth.json` 合成音脚本可自定义；缺采样时自动回退合成器，离线也能渲染。

```bat
python -m musicgen render examples\twinkle\twinkle.musicproj -o twinkle.wav
```

---

## 2. 使用说明

### 2.1 安装

需要 Python 3.10+。渲染与界面依赖 `numpy` / `soundfile` / `sounddevice` / `scipy`；
音频分析再加 `torch` / `demucs`（体积较大，只做渲染可以不装）。

```bat
py -3 -m pip install -r requirements.txt
```

> 国内下载 `torch` 很慢时换阿里云源：
> `py -3 -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/`
> Windows 上如果默认的 `python` 不带 tkinter，命令行可用 `python`，**界面请用 `py -3`**。
> 也可以直接用仓库里的 `MusicGen-CLI.bat`（透传参数与退出码）和 `MusicGen.bat`（启动界面）。

### 2.2 首次运行

```bat
py -3 -m musicgen samples fetch                     :: 下载全部采样（8 件，约 10MB，只需一次）
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

:: 音效脚本（.fx.json）：存 / 校验 / 复用
python -m musicgen effect save songs\Song\Song.musicproj --track Guitar -o fx\guitar.fx.json
python -m musicgen effect check fx\guitar.fx.json
python -m musicgen effect apply songs\Song\Song.musicproj --track Guitar --script fx\guitar.fx.json

:: 合成音脚本（.synth.json）：试听 / 自检 / 从预设新建 / 注册成乐器
python -m musicgen synth list
python -m musicgen synth check warm_pad --midi 60 --dur 0.8
python -m musicgen synth render analog_lead -o out\lead.wav --midi 60,64,67
python -m musicgen synth new -o songs\Song\synths\pad.synth.json --preset warm_pad --label 暖垫
python -m musicgen instrument add airpad --name 空气垫 --synth warm_pad --low 48 --high 96

:: 从音频重建工程（Demucs 分离 + FFT 转谱 + 音色识别）
python -m musicgen analyze song.mp3 -o songs\Song
python -m musicgen analyze song.mp3 -o songs\Song --bpm 96 --quantize 1/16 --force
python -m musicgen analyze song.mp3 -o songs\Song --model htdemucs --no-stems
python -m musicgen analyze song.mp3 -o songs\Song-Audio --audio-tracks   :: 只分离，保留原音色

:: 逆向精修：分析出简报 → 写 decisions.json → 回灌（复用分轨，20–40 秒）
python -m musicgen analyze song.mp3 -o songs\Reverse --force
python -m musicgen analyze song.mp3 -o songs\Reverse --decisions songs\Reverse\decisions.json
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

<!-- 音频轨：直接引用分轨 WAV，渲染时原样混音（analyze --audio-tracks 生成） -->
<track name="Vocals" instrument="audio">
  <audio file="../stems/vocals.wav" gain="0dB"/>
</track>

<!-- 合成音轨：音色由脚本提供，工程自包含（instrument="synth" 必须带 patch） -->
<track name="Pad" instrument="synth" patch="../synths/pad.synth.json" gain="-7dB" pan="0.15">
  <note time="0" dur="4" pitch="C4,E4,G4"/>
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

### 2.6 音频分析

默认是**可编辑且保真**：人声原样音频轨，伴奏按原始起音切成立体声块 + 补全轨，
默认状态逐样本等于原分轨；改哪个音符才只影响那一处。

```bat
:: 默认：可编辑音符 + 精确回放（渲染 ≈ 原曲，与分轨求和相关 0.99999）
python -m musicgen analyze song.mp3 -o songs\Song

:: 只要保真、完全不要音符：6 条分轨直接进工程（最快）
python -m musicgen analyze song.mp3 -o songs\Song-Audio --audio-tracks

:: 回到旧行为：量化到网格 + 逐事件采样 + 自动增益/EQ/母带压缩
python -m musicgen analyze song.mp3 -o songs\Song-Old --no-exact-replay
```

- 默认 `htdemucs_6s` 把人声 / 鼓 / 贝斯 / 钢琴 / 吉他 / 其他分开；4 轨模型更快，
  但钢琴吉他会混在「其他」里交给音色识别
- 人声固定是一条音频轨：直接引用 `stems/vocals.wav`，不转谱、不换音色、没有音符，
  但可静音/调音量/挪声像
- 其余分轨转成音符轨；精确回放按它们的原始起音切成首尾相接的立体声块（`samples/`），
  块外由 `beds/` 补全轨原样填回，二者相加 = 原分轨
- `--audio-tracks` 只做分离、不转谱：工程里是 6 条 `<audio>` 音频轨，增益/声像全为 0，
  渲染即原曲；比转谱快得多，但完全不可编辑
- 精确回放**不做**自动增益 / 自动 EQ / 母带压缩——电平和频谱本来就是原曲的
- 默认输出 `<工程>.musicproj`、`tracks/*.track.xml`、`samples/`、`beds/`、`stems/*.wav`
  与 `analysis.json`（速度、调性、每轨置信度、`exact_replay`、警告）
- 首次运行会从 HuggingFace 下载约 300MB 权重；国内网络先
  `set HF_ENDPOINT=https://hf-mirror.com`
- `stems/*.wav` 与 `beds/*.wav` 都是工程素材：`stems/vocals.wav` 是人声轨引用的文件，
  即使 `--no-stems` 也照样写出；**`beds/` 不能删**，删了块之间的空隙与尾音就没了
  （每条分轨一份全曲 WAV，约 235MB/首）
- 音符列表是**启发式转谱**，但精确回放下**音符不准不影响听感**——声音由块与补全轨拼回；
  只有你改了某个音，那一处才偏离原音。要逐版调试旧算法就加 `--no-exact-replay`
- 鼓分轨按鼓件拆成独立音轨（底鼓 / 军鼓 / 踩镲 / 嗵鼓 / 镲片），每件一条轨、独立增益：
  原来 1659 个鼓点挤在一条轨里，卷帘里是一整块，点不中也改不了音量
- 同一条轨内的音符不重叠（时值收到下一个起音），碎片轨（<24 音）并回主轨
- 音色判不出来时默认给合成音（`synth_pad` / `synth_pluck` / `synth_lead` / `synth_bass`），
  不再落到钢琴——流行歌这一层本来就不是声学乐器
- 每轨输出**可编辑性**指标（复音、重叠率、中位时值、网格符合率）写进 `analysis.json`
- 默认会把「其他」按音区拆成 2–3 层（`--no-layers` 关），并切分段落写进 `analysis.json`
- 同时写出 `reverse_brief.json`：每轨音色指标、音色候选概率、效果估计（RT60 / 峰值因数 /
  宽度 / 回声）、可用合成音预设。**这份简报就是交给大模型判断的输入**
- 大模型（或你）把结论写成 `decisions.json`，用 `--decisions` 回灌：换乐器、换合成音脚本、
  整链换效果、删误检轨、给段落命名；分轨会复用，通常 20–40 秒
- 旧模式（`--no-exact-replay`）会按「渲染结果 vs 原分轨」的频谱差自动挂 `eq`、
  在母带补 `compressor`；**默认精确回放不做这些**——块与补全轨本来就是原曲的电平和频谱
- 算法细节与精度边界见 [项目文档 §15](docs/项目文档.md)

**人声不转谱**：整条 Demucs 人声分轨直接写成 `<audio>` 音频轨，气声、转音、真假声、
歌词都在，代价是没有音符可改。工程格式仍保留弯音曲线
（`<note ... bend="0:0;0.5:-0.8;1:0"/>`），手写工程里的弦乐、推弦可以用它写滑音与颤音。

**伴奏默认也等于原音**：按每条分轨的原始起音切成首尾相接的立体声块，块外由
`<工程>/beds/` 补全轨原样填回，二者相加逐样本等于原分轨（实测与分轨求和相关 0.99999）。
因此默认状态听起来就是原曲，只有你改了某个音符的时值/音高，那一处才偏离原音。

**音色就是原曲的音色**：分析时会从每条分轨里切出音做采样，写进 `<工程>/instruments/`，
渲染时按音高变速重放——所以不是「用钢琴模仿合成器」，而是原曲那台合成器在弹。
采样只覆盖几个关键音高（相邻不超过 3 个半音），工程体积约 8MB，且能脱离内置音色库渲染。
`--no-sampled` 可关掉，退回内置采样库 / 合成预设。

界面里对应「文件 → 从音频重建工程…」（`Ctrl+Shift+A`），分析在后台跑，完成后自动打开工程。

### 2.7 MIDI 导入与自带音源

转谱模型（MuScriptor、Basic Pitch、DAW 导出的 MIDI 都行）出的 MIDI 可以直接变成工程：

```bat
:: 一站式：音频 → MIDI → 工程（MuScriptor 需先在 HuggingFace 接受许可并设置 HF_TOKEN）
python -m musicgen transcribe song.mp3 --model small --import-to songs/Song

:: 已经有 MIDI 时
python -m musicgen import-midi song.mid -o songs/Song
```

导入时按 GM 音色号映射乐器（钢琴/吉他/贝斯/弦乐/铜管/管乐/合成音，通道 10 当鼓组），
每个「轨 × 通道 × 音色」拆成一条音轨。**默认把用到的采样拷进 `<工程>/instruments/`**，
渲染时工程自带的音源优先，所以整个工程目录拷到没有采样缓存的机器上也能直接渲染：

```bat
python -m musicgen bundle songs/Song          :: 已有工程也能补打包
```

工程体积：每件乐器约 1–2MB，三件乐器的工程通常 3–5MB。

> MuScriptor 权重是 gated 的（CC BY-NC 4.0）：先在
> <https://huggingface.co/MuScriptor/muscriptor-small> 点 Agree，再到
> <https://huggingface.co/settings/tokens> 建 read 令牌并 `set HF_TOKEN=hf_xxx`。
> 代码 MIT、权重非商用；不装它也能用 `import-midi` 导入任何 MIDI。

### 2.8 AI Skill

`Skill/musicgen/` 是给 AI 助手用的技能包：说清情绪、时长、编制，它就按
「设计单 → 写工程 → `validate` → `render` → 验收」走完整条链路，产出 `output/<曲名>.wav`。

- 入口 `Skill/musicgen/SKILL.md`：五步工作流、命令速查、11 条硬规则（音域、采样长度、循环、`tail`、合成音脚本…）
- 参考 `Skill/musicgen/references/`：`编曲设计.md`（通用编曲：功能层 / 曲式 / 和声 / 配器 / 织体 / 律动，附到 9 件基础乐器的映射）、`工程格式.md`、`混音与验收.md`、`音频还原.md`、`逆向判断.md`、`合成音脚本.md`
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
| `bass` | 贝斯 | FluidR3 GM | E1–G4 | 40 |
| `vocal` | 人声 | FluidR3 GM | C3–C6 | 37 |
| `drums` | 架子鼓 | **内置合成**，无需下载 | GM 打击乐 35–82 | — |
| `synth_pad` | 合成垫 | **内置脚本** `warm_pad` | C2–C7 | — |
| `synth_lead` | 合成主音 | **内置脚本** `analog_lead` | C3–E7 | — |
| `synth_bass` | 合成贝斯 | **内置脚本** `deep_bass` | C1–C4 | — |
| `synth_pluck` | 合成拨弦 | **内置脚本** `pluck_string` | E2–C7 | — |

工程里写 `instrument="cello"` 或 `instrument="大提琴"` 都可以；鼓件写 `kick` / `snare` / `hat` / `openhat` / `crash` / `tom1` / `floor` 等，中文（`底鼓`、`军鼓`）也认。
`bass` / `vocal` 是音频分析新增的音色，旧注册表会在首次加载时自动补齐。
`bass` 仍用于贝斯音符轨；`vocal` 现在只服务手写工程与 MIDI 导入的人声轨——
音频分析里人声是原样音频轨，不映射到它。

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
| `eq` | 均衡 | `low` ±24dB（0）、`mid` ±24dB（0）、`high` ±24dB（0）、`mid_freq` 200–6000Hz（900） |
| `drive` | 失真 | `amount` 0–1（0.35）、`tone` 0–1（0.55）、`output` ±24dB（0）、`mix` 0–1（1） |
| `compressor` | 压缩 | `threshold` −48–0dB（−18）、`ratio` 1–20（3）、`release` 20–1000ms（180）、`makeup` ±18dB（0） |
| `chorus` | 合唱 | `rate` 0.05–6Hz（0.6）、`depth` 0.5–20ms（6）、`mix` 0–1（0.35）、`voices` 1–3（2） |

- 参数只写要改的，其余用注册表默认值；`type` 可写注册键或中文名
- `8bit` / `4bit` / `2bit` 是「降采样 + 量化」的位深压缩，分别留 256 / 16 / 4 级电平；量化按素材自身峰值做，插入后轨道响度不变
- 未知类型 / 参数只警告不中断渲染；数值越界会夹到范围内
- 效果链可存成 `.fx.json` 复用：`effect save` / `check` / `apply [--append]`
- 新增效果只要在 `core/effects.py` 写一个 `@register_effect` 的 `Effect` 子类，CLI、界面、解析与回写自动接上

### 3.4 合成音脚本（已实现）

采样库里没有的音色，用**纯 JSON 脚本**描述，不含可执行代码，可手写也可由大模型生成：

```json
{
  "name": "air_pad",
  "gain": 0.2,
  "oscillators": [
    {"wave": "saw", "detune": -9, "level": 0.5, "pan": -0.35},
    {"wave": "saw", "detune": 9,  "level": 0.5, "pan": 0.35}
  ],
  "amp": {"attack": 0.65, "decay": 1.4, "sustain": 0.8, "release": 1.6},
  "filter": {"cutoff": 1400, "resonance": 1.0, "env": 700}
}
```

- 五组字段：`oscillators`（1–8 个）、`sub`/`noise`、ADSR、滤波、调制（`fm`/`vibrato`）；除振荡器外全可省
- 锯齿/方波用**带限谐波叠加**，不会混叠；噪声固定种子，渲染完全确定
- 挂载：注册成乐器（`instrument add --synth`）或音轨上写 `instrument="synth" patch="…"`
- 内置 7 个预设：`warm_pad` / `glass_bell` / `analog_lead` / `deep_bass` / `pluck_string` / `soft_keys` / `air_texture`
- `synth check` 看峰值/RMS，健康区间 0.1–0.5；`synth new --preset` 从预设改比从零写快

### 3.5 音频分析（已实现）

| 能力 | 做法 |
| --- | --- |
| 分离 | Demucs `htdemucs_6s`（默认）/ `htdemucs` / `htdemucs_ft`；走 `separate_tensor`，不需要 ffmpeg |
| 波形分解 | STFT 2048/512 + 对数域谱白化 + 谐波/打击中值分离 |
| 转谱 | 谐波求和 + 迭代消去，每轨最多 6 个同时音；音符跟踪（精确回放不量化到网格） |
| 乐器识别 | 用注册表里的采样音源渲染模板，55 维音色特征最近邻；置信度写进 `analysis.json` |
| 人声 | Demucs 人声分轨 → 整条 `<audio>` 音频轨（不拆音高，原样重放） |
| 精确回放 | 按原始起音切立体声块 + `beds/` 补全轨，块+补全=原分轨；不做自动增益/EQ/母带压缩 |
| 鼓 | 低频/高频双链起音 + 频带密度/尾音比打分，底鼓与踩镲可同时保留 |
| 音频轨 | `--audio-tracks`：分轨直接写入 `<audio>`，保留原音色，渲染相关系数 ≈0.997 |
| 报告 | `<工程目录>/analysis.json`：bpm、bpm_confidence、key、mode、每轨 gain/pan/confidence、警告 |

细节、输出结构与精度边界见 [项目文档 §15](docs/项目文档.md)。

### 3.6 待实现

按对“写一首歌”的实际阻碍排序：`P0` 是现在就会卡住的，`P2` 是锦上添花。

**编曲与编辑**

- [ ] `P0` **撤销 / 重做**：界面每次编辑直接写回 XML，改错了只能手改文件或重开工程
- [ ] `P1` **自动化曲线**：音量、声像、效果参数随时间变化（渐强渐弱、声像扫），现在只能整轨一个定值，渐变要靠 `fade` 效果或另开一条轨
- [ ] `P1` **轨道复制 / 排序**：音符可以 `Ctrl+D` 复制，整条音轨不行，也不能在列表里拖动顺序
- [ ] `P2` **速度渐变（tempo map）**：目前只有全局 BPM + 逐轨 BPM
- [ ] `P2` **延音踏板与连奏**：弦乐揉弦、铜管滑音、钢琴 una corda 踏板
- [ ] `P2` **多力度层采样**：现在力度只影响音量与亮度，真采样应分 soft / medium / hard 多层切换
- [ ] `P1` **精确回放的音高变速**：改音符音高目前片段仍原样播（要改音高得删 `sample` 走采样乐器）；
  按 `srcMidi` 记录原音高、渲染时做相对变速即可让「改音高」真正生效

**混音与音质**

- [x] `P0` **轨道电平表**：传输条有 L/R 主电平表，分轨卷帘每条轨头也有小电平条（渲染时按 50ms 包络算）
- [ ] `P1` **总线与母带效果**：现在 11 个效果都是插入效果、只能挂在音轨上；还缺 send 总线、并联压缩与总线 EQ
- [ ] `P2` **自定义音色**：导入自己的采样（WAV/AIFF），自定义键位映射、循环点、力度分层与包络，而不只是套用 GM 音色
- [ ] `P2` **更多音色**：长笛、单簧管、弦乐群、电钢琴、贝斯等
- [ ] `P2` **MP3 / FLAC 导出**：需要引入编码器依赖

**互操作与批量**

- [ ] `P1` **MIDI 导出**：把工程导回 DAW（导入已有 `import-midi`）
- [ ] `P1` **批量分轨导出**：一次导出全部音轨的 stems，而不是逐轨点

---

## 4. LICENSES

| 组件 | 许可证 |
| --- | --- |
| MusicGen 自身代码 | MIT License，见仓库根目录 [LICENSE](LICENSE) |
| 示例曲目 | 除《小星星》（民谣，公有领域）与贝多芬《月光》（公有领域）外，其余示例为原创 |
| FluidR3 GM SoundFont（全部采样音色） | MIT / 自由使用，源自 Frank Wen，经 [gleitz/midi-js-soundfonts](https://github.com/gleitz/midi-js-soundfonts) 分发 |
| numpy | BSD-3-Clause |
| scipy | BSD-3-Clause |
| soundfile（Python 绑定） | BSD-3-Clause；底层 libsndfile 为 LGPL-2.1+ |
| sounddevice | MIT；底层 PortAudio 为 MIT |
| PyTorch | BSD-3-Clause |
| Demucs（htdemucs 模型） | MIT；模型权重随包分发 |
| Tcl/Tk（界面） | BSD 风格许可 |

每个乐器的采样目录下都会写 `manifest.json`，记录该音源的来源、许可与文件清单：

```bat
python -m musicgen samples list        :: 查看状态、音域与许可
type samples_cache\piano\manifest.json
```
