# MusicGen 命令行（CLI）手册

> 与当前版本同步维护。`python -m musicgen --version` 可确认版本。
> 工程格式、渲染管线与扩展方法见 [项目文档](项目文档.md)；本文只讲命令怎么用、参数什么含义、出错怎么办。

**目录**：[调用方式](#1-三种调用方式) · [命令总览](#2-命令总览) · [退出码与环境变量](#3-退出码与环境变量) ·
[render](#4-render--渲染成-wav) · [validate](#5-validate--只校验) · [info](#6-info--工程概要) ·
[init](#7-init--生成新工程) · [samples fetch](#8-samples-fetch--下载音源) · [samples list](#9-samples-list--查看音源状态) ·
[gui](#10-gui--图形界面) · [track / note](#11-track--note--轨道与音符编辑) · [instrument](#12-instrument--乐器注册表) ·
[effect](#13-effect--音轨效果链) · [analyze](#14-analyze--从音频重建工程) · [synth](#15-synth--合成音脚本) ·
[工作流](#16-常见工作流) · [报错对照](#17-提示信息对照表) ·
[CLI 与 GUI](#18-与-gui-的能力对照) · [速查卡](#19-速查卡)

---

## 1. 三种调用方式

```bat
python -m musicgen <命令> [参数]      :: 通用写法
py -3 -m musicgen <命令> [参数]       :: 指定用 Python 3.14（GUI 必须用这个）
MusicGen-CLI.bat <命令> [参数]        :: 仓库根目录的包装脚本
```

| 方式 | 说明 |
| --- | --- |
| `python -m musicgen` | 需要当前解释器装了 `numpy` + `soundfile`。渲染、校验、下载只用这两个依赖。 |
| `py -3 -m musicgen` | 本机默认的 `python`（3.13）没有编译 `tkinter`，**开界面必须用 `py -3`**；渲染也用同一个解释器更省心。 |
| `MusicGen-CLI.bat` | 会自动先试 `py -3`，失败再退回 `python`；并且 `cd` 到仓库根目录，所以**相对路径按仓库根目录解析**，返回码原样透传。 |

> `MusicGen.bat` 不是命令行包装：它用 `pythonw` 起界面并立刻返回（无控制台窗口），
> 参数直接传给 `gui`，所以 `MusicGen.bat examples\demo\demo.musicproj` 等于「开界面并打开这个工程」。
> 要拿渲染结果，用前三行里的任意一种。

---

## 2. 命令总览

| 命令 | 作用 | 是否需要音源 |
| --- | --- | --- |
| [`render`](#4-render--渲染成-wav) | 把工程渲染成 WAV | 缓存齐全时不需要联网，缺失则用合成器兜底 |
| [`validate`](#5-validate--只校验) | 只解析校验，列出全部错误与警告 | 不需要 |
| [`info`](#6-info--工程概要) | 打印工程概要（速度、时长、每轨参数） | 不需要 |
| [`init`](#7-init--生成新工程) | 按模板生成一个新工程 | 不需要 |
| [`samples fetch`](#8-samples-fetch--下载音源) | 下载乐器采样（多镜像并发） | 联网 |
| [`samples list`](#9-samples-list--查看音源状态) | 查看缓存里各乐器齐不齐 | 不需要 |
| [`gui`](#10-gui--图形界面) | 启动桌面界面 | 打开工程时按需 |
| [`track`](#11-track--note--轨道与音符编辑) | 新增 / 编辑 / 删除 / 列出音轨 | 不需要 |
| [`note`](#11-track--note--轨道与音符编辑) | 增删改查音轨里的音符 | 不需要 |
| [`instrument`](#12-instrument--乐器注册表) | 增删查改用户乐器注册表 | 不需要 |
| [`effect`](#13-effect--音轨效果链) | 查看音效类型、增删改音轨效果链、音效脚本 | 不需要 |
| [`transcribe`](#20-transcribe-import-midi-bundle--转谱与自带音源) | 音频 → MIDI（MuScriptor），可顺手重建工程 | 联网（首次下权重） |
| [`import-midi`](#20-transcribe-import-midi-bundle--转谱与自带音源) | MIDI → 工程，默认自带采样 | 需要本地已有采样 |
| [`bundle`](#20-transcribe-import-midi-bundle--转谱与自带音源) | 把采样打包进工程，脱离全局缓存 | 需要本地已有采样 |
| [`analyze`](#14-analyze--从音频重建工程) | 分析成品音频并重建 musicproj 工程 | 需要 torch + demucs，首次联网下载权重 |
| [`synth`](#15-synth--合成音脚本) | 合成音脚本：列出、打印、自检、试渲染、生成 | 不需要 |

全局选项：

| 选项 | 说明 |
| --- | --- |
| `-h`, `--help` | 显示帮助；子命令也一样：`python -m musicgen render -h` |
| `-V`, `--version` | 打印 `MusicGen <版本号>` |

---

## 3. 退出码与环境变量

### 3.1 退出码

| 码 | 含义 | 典型场景 |
| --- | --- | --- |
| `0` | 成功（`validate` 允许带警告） | — |
| `1` | 参数或环境问题 | 乐器名不认识、下载失败、`init` 目标已存在/参数越界、
`track`/`note` 参数错误或找不到轨道、GUI 起不来、`analyze` 缺依赖或音频读不出来 |
| `2` | 工程文件本身有错 | `render` / `validate` / `info` / `track list` / `note list` 解析失败 |
| `3` | 渲染阶段失败 | 采样文件读不出来等 I/O 问题 |
| `130` | 被 `Ctrl+C` 中断 | 长渲染中途取消 |

`render` 遇到未预料的异常时，会先打印一行 `渲染失败：<异常类型>: <说明>`，再把原始 traceback 抛出来（方便报 bug），此时退出码由 Python 决定（通常为 `1`）。

### 3.2 环境变量

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `MUSICGEN_SAMPLES` | 音源缓存根目录 | 仓库下 `samples_cache/`；非仓库布局时用 `~/.musicgen/samples` |
| `MUSICGEN_UI` | 界面布局文件路径（只有 GUI 用） | `~/.musicgen/ui.json` |
| `MUSICGEN_INSTRUMENTS` | 乐器注册表 JSON 路径（CLI 与 GUI 共用） | `~/.musicgen/instruments.json` |
| `HF_ENDPOINT` | Demucs 权重下载源（HuggingFace 镜像） | 官方站；国内可设 `https://hf-mirror.com` |

优先级：命令行 `--cache-dir` > `MUSICGEN_SAMPLES` > 默认目录。

```bat
set MUSICGEN_SAMPLES=D:\audio\musicgen-samples
python -m musicgen samples list
```

---

## 4. `render` — 渲染成 WAV

```
python -m musicgen render <工程> [-o 输出] [--bits 16|24|32] [--float32]
                          [--no-normalize] [--no-dither] [--quiet] [--cache-dir 目录]
                          [--tracks 1,3,5|Piano,Guitar]
```

### 4.1 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `project` | 必填 | `*.musicproj` 路径，相对当前目录 |
| `-o`, `--output` | `<工程名>.wav`，**与工程同目录** | 输出路径。给的是目录（或以 `/`、`\` 结尾）则自动补成 `<目录>/<工程名>.wav`；父目录不存在会自动创建 |
| `--bits` | `24` | 整数位深，可选 `16` / `24` / `32` |
| `--float32` | 关 | 写 32bit 浮点 WAV。**优先级高于 `--bits`**，且浮点不抖动 |
| `--no-normalize` | 关 | 强制关闭峰值归一化（工程里写了 `normalize="true"` 也不生效） |
| `--no-dither` | 关 | 关闭 TPDF 抖动（只影响整数位深） |
| `--quiet` | 关 | 不显示进度条；摘要与统计照常打印 |
| `--tracks` | 全部 | 只渲染这些轨：1 起的序号或轨名，逗号分隔。找不到时报错并列出现有轨 |
| `--cache-dir` | 见 §3.2 | 音源缓存目录 |

### 4.2 输出往哪走

**stdout**：工程摘要、完成信息、每轨统计 → 可以直接重定向成报告。
**stderr**：工程警告、渲染警告（前缀 `!`）、进度条、所有错误 → 出错时不会污染 stdout。

```bat
rem 只看结果，进度和警告另存
python -m musicgen render examples\demo\demo.musicproj -o out.wav > render.log 2> render.err
```

进度条：终端里单行刷新 `[██████░░░░] 22.5% 混响：Chords`；重定向到文件时退化为每 25% 一行。
音轨阶段占 0–90%，母带 92%，收尾 100%。`--quiet` 只是关掉这个进度，不影响任何结果。

### 4.3 完整输出范例

```bat
> python -m musicgen render examples\demo\demo.musicproj -o out\demo.wav

渲染 Nocturne in A minor → out\demo.wav
  4 条音轨 | 84BPM | 44100Hz | 时长 0:24.79
完成：out\demo.wav
  时长 0:24.79 | 峰值 -1.0dBFS | RMS -19.8dBFS | 6.3MB | 位深 24
  音符事件 126 个 | 耗时 2.2s (11.2× 实时)
    · Chords [piano] 音符 16 峰值 0.631
    · Ostinato [piano] 音符 70 峰值 0.254
    · Bass [piano] 音符 16 峰值 0.290
    · Melody [piano] 音符 24 峰值 0.615
```

各项含义：

| 行 | 说明 |
| --- | --- |
| `时长 0:24.79` | 含 `tail` 余韵的实际文件长度 |
| `峰值 -1.0dBFS` | 归一化后的峰值；目标是 −1dBFS |
| `RMS -19.8dBFS` | 整体响度参考，纯钢琴独奏通常在 −20dBFS 上下 |
| `音符事件 126 个` | **已展开循环**后实际触发的音符数（`Ostinato` 只有 8 个 `<note>`，循环后变 70 个） |
| `11.2× 实时` | 渲染速度，示例工程约 10–12× |
| `峰值 0.631` | 单轨在总线上的贡献峰值（0–1 线性） |

### 4.4 位深怎么选

| 输出 | 命令 | 24.79s 体积 | 用途 |
| --- | --- | --- | --- |
| 16bit | `--bits 16` | 4.2MB | 分享、普通播放器、视频配乐 |
| 24bit | 默认 | 6.3MB | 归档、后期处理 |
| 32bit int | `--bits 32` | 约 8.4MB | 极少用，整数位深超过 24bit 没有实际收益 |
| 32bit float | `--float32` | 约 8.4MB | 送进 DAW 再混音，避免二次量化 |

体积公式：`采样率 × 2 声道 × 位深 ÷ 8`（字节/秒）。44.1kHz/24bit ≈ 264KB/s。

### 4.5 归一化与削波

* 默认跟随工程里的 `normalize`（默认 `true`）：渲染完把峰值拉到 −1dBFS，多轨混音不会爆。
* `--no-normalize` 保留 `masterGain` 后的原始电平。若峰值仍超过 0dBFS，会**自动硬削波**并给出警告——这是保护，不是可选项：

  ```
    ! 峰值 +2.3dBFS 已超出 0dB，已做削波保护
  ```
* 想留动态又不想削波，改工程里的 `masterGain`（例如 `-3dB`），别关归一化。

### 4.6 音源缺失会怎样

不会失败。未注册乐器、未下载采样、单个采样损坏都会退回内置合成器，并在 stderr 警告：

```
  ! 未注册的乐器 'kazoo'，使用内置合成器。已支持: 钢琴(piano)、大提琴(cello)、小提琴(violin)、小号(trumpet)
  ! 大提琴的采样尚未下载，本次使用内置合成器。运行 `python -m musicgen samples fetch --instrument cello` …
  ! 乐器 'cello' 的采样音域为 C2–C6，B1 超出范围，该音改用内置合成器
```

第二种是整件乐器都还没下采样（`samples list` 会显示「未下载」）；第三种是音高跑出了乐器的采样音域
（大提琴 C2–C6、小提琴 G3–C7、小号 E3–C6、钢琴 A0–C8），改音高或整体 `transpose` 即可。
想要真实音色，先跑 `samples fetch`。

---

## 5. `validate` — 只校验

```
python -m musicgen validate <工程>
```

只解析、不渲染、不碰音源，所以**可以放心放进 CI**。遇到错误不会中断，一次把全部问题列完。

| 情况 | 输出 | 退出码 |
| --- | --- | --- |
| 完全正确 | `校验通过：demo.musicproj` / `没有警告` | `0` |
| 只有警告 | `校验通过：…` + 警告条数与明细 | `0` |
| 有错误 | `校验失败（N 个问题）：` + 明细 | `2` |

行格式：`级别 | 位置: 说明`。级别是 `错误` / `警告`，位置可能是工程文件、音轨文件名，或 `<音轨> 第 N 个 <note>`。

```bat
> python -m musicgen validate bad.musicproj

校验失败（5 个问题）：
  错误 | bad.musicproj: bpm 超出合理范围 5-600: 0
  警告 | lead.track.xml: bpm 无效: bpm 必须大于 0
  错误 | lead.track.xml 第 1 个 <note>: dur 必须大于 0
  警告 | tracks/lead.track.xml: gain 无效: 增益 不是有效数字: 'loud'
  错误 | bad.musicproj: 音轨文件不存在: tracks/missing.track.xml
```

---

## 6. `info` — 工程概要

```
python -m musicgen info <工程>
```

不渲染、不下载，纯读文件，适合写脚本时快速确认「这个工程到底几拍、几轨、什么乐器」。

```bat
> python -m musicgen info examples\demo\demo.musicproj

工程: Nocturne in A minor
文件: F:\Music-Gen\examples\demo\demo.musicproj
BPM: 84 | 采样率: 44100 | 拍号: 4/4 | 时长: 24.79s | 母带: -1dB | 归一化: 开
音轨: 4
  1. Chords [piano] 音符 16 | 84BPM | -4dB | pan -0.08 | 混响 0.28
  2. Ostinato [piano] 音符 8 | 84BPM | -4dB | pan -0.28 | 混响 0.18 | 循环 4拍×无限
  3. Bass [piano] 音符 16 | 84BPM | -5dB | pan -0.05 | 混响 0.10
  4. Melody [piano] 音符 24 | 84BPM | -3dB | pan +0.16 | 混响 0.32
```

* `音符 8` 是文件里写的原始音符数（未展开循环），和 `render` 输出的「音符事件」不是一回事。
* `母带: -1dB` 就是工程里的 `masterGain`；归一化目标固定为 −1dBFS，不随它变。`归一化: 开/关` 对应 `normalize`。
* 时长与 `render` 报的一致（含 `tail`）。
* 工程有错时退出码 `2`，不会打印半截信息。

---

## 7. `init` — 生成新工程

```
python -m musicgen init <目录> [--name 名字] [--force]
                    [--bpm 100] [--sample-rate 44100]
                    [--beats-per-bar 2|3|4|6] [--template demo|empty]
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `path` | 必填 | 工程目录，**不存在会自动创建**（含 `tracks/`） |
| `--name` | 目录名 | 工程**显示名**（写进 `name` 属性），不改文件名 |
| `--force` | 关 | 目标 `<目录>/<目录名>.musicproj` 已存在时覆盖 |
| `--bpm` | `100` | 速度，范围 5–600 |
| `--sample-rate` | `44100` | 可选 22050 / 32000 / 44100 / 48000 / 88200 / 96000 |
| `--beats-per-bar` | `4` | 每小节拍数，可选 2 / 3 / 4 / 6 |
| `--template` | `demo` | `demo`＝两轨示例（旋律 + 循环音型）；`empty`＝一条空音轨，注释里写了音符语法 |

文件名取自**目录名**：`init MySong` 得到 `MySong/MySong.musicproj`。想改名就改目录名，或用 `--name` 只改显示名。

```bat
> python -m musicgen init songs\MySong --bpm 96 --template empty

已创建工程：songs\MySong\MySong.musicproj
下一步：python -m musicgen render songs\MySong\MySong.musicproj
```

生成结构：

```
demo 模板                        empty 模板
MySong/                          MySong/
├─ MySong.musicproj              ├─ MySong.musicproj
└─ tracks/                       └─ tracks/
   ├─ lead.track.xml                └─ track1.track.xml
   └─ pad.track.xml
```

失败情形：目标已存在 → `目标已存在：…（加 --force 可覆盖）`，退出码 `1`；`--bpm 9999` → `参数无效：速度超出范围 5-600：9999.0`，退出码 `1`。

---

## 8. `samples fetch` — 下载音源

```
python -m musicgen samples fetch [--instrument 名字]... [--all] [--force] [--cache-dir 目录]
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--instrument` | 不给就下载全部 | 乐器名，**可重复**也可**逗号分隔**；认注册键也认中文名/别名 |
| `--all` | 关 | 显式下载全部已注册乐器（等价于不给 `--instrument`） |
| `--force` | 关 | 忽略已有文件重新下载 |
| `--cache-dir` | 见 §3.2 | 缓存根目录，实际写到 `<缓存>/<乐器>/` |

可用的名字：

| 注册键 | 中文名 | 其他别名 |
| --- | --- | --- |
| `piano` | 钢琴 | `grand piano` |
| `violin` | 小提琴 | `strings` |
| `cello` | 大提琴 | `violoncello` |
| `trumpet` | 小号 | — |
| `recorder` | 竖笛 | 牧童笛 / 木笛 |
| `guitar` | 吉他 | 民谣吉他 / 木吉他 / `acoustic` |
| `drums` | 架子鼓 | 鼓 / 鼓组 / 打击乐 / `drum` / `kit`（**内置鼓组，无需下载**） |

```bat
python -m musicgen samples fetch                        :: 八件采样全下（约 10MB）
python -m musicgen samples fetch --instrument cello      :: 只下大提琴
python -m musicgen samples fetch --instrument 竖笛,吉他   :: 中文名、逗号分隔
python -m musicgen samples fetch --instrument piano --instrument cello --force :: 重下
python -m musicgen samples fetch --instrument drums      :: 内置鼓组，只会回一句「不需要下载」
```

下载行为：

* 8 线程并发，每个文件**依次尝试 4 个镜像**（jsDelivr → fastly → gcore → raw.githubusercontent），单镜像 20s 超时后换下一个；
* 整体 300s 预算，超时后不再提交新任务；
* 已存在且大于 512 字节的文件直接跳过，所以重复执行几乎不花钱（缓存命中时 `耗时 0.0s`）；
* 每个乐器下载完会在目录里写 `manifest.json`（来源、许可、文件清单、时间）。

输出范例（缓存已满）：

```bat
> python -m musicgen samples fetch --instrument 大提琴,violin

下载 大提琴 (cello) — FluidR3 GM — Cello
  许可：FluidR3 GM SoundFont（MIT / 自由使用，源自 Frank Wen）
  音域：C2–C6（大提琴常用音域） 来源：https://github.com/gleitz/midi-js-soundfonts
乐器: 大提琴 (cello)
缓存目录: F:\Music-Gen\samples_cache\cello
总计 49 个采样 | 新下载 0 | 已存在 49 | 失败 0 | 耗时 0.0s
下载 小提琴 (violin) — FluidR3 GM — Violin
  ...
总计 42 个采样 | 新下载 0 | 已存在 42 | 失败 0 | 耗时 0.0s
```

出错处理：

* 乐器名不认识 → stderr `未知乐器：kazoo（可选：钢琴(piano)、大提琴(cello)、小提琴(violin)、小号(trumpet)）`，退出码 `1`，其他乐器继续下；
* 部分采样失败 → 报告里列出 `失败的音符: …`，退出码 `1`，**已下载的仍然可用**（缺的那几个音渲染时会走合成器），重跑一次通常就补齐了。

---

## 9. `samples list` — 查看音源状态

```
python -m musicgen samples list [--cache-dir 目录]
```

```bat
> python -m musicgen samples list

缓存目录：F:\Music-Gen\samples_cache
  钢琴    piano    FluidR3 GM — Acoustic Grand Piano
           采样 88/88 [就绪] · 音域 A0–C8（88 键全覆盖）
           FluidR3 GM SoundFont（MIT / 自由使用，源自 Frank Wen）
           https://github.com/gleitz/midi-js-soundfonts
  大提琴   cello    FluidR3 GM — Cello
           采样 49/49 [就绪] · 音域 C2–C6（大提琴常用音域）
           ...
  吉他    guitar   FluidR3 GM — Acoustic Guitar (steel)
           采样 49/49 [就绪] · 音域 E2–E6（民谣吉他常用音域）
           ...
  架子鼓  drums    内置合成鼓组（GM 打击乐映射）
```

状态含义：`就绪`（齐全）、`部分`（下过但缺文件，再跑一次 `fetch` 会补）、`未下载`。

---

## 10. `gui` — 图形界面

```
python -m musicgen gui [工程]
```

| 参数 | 说明 |
| --- | --- |
| `project` | 可选，启动时直接打开这个工程；省略则停在空工作区，用 `Ctrl+O` 打开或 `Ctrl+N` 新建 |

* 前台阻塞，直到关闭窗口，退出码即窗口的返回码。
* 解释器没有 `tkinter` 时会提示换解释器并返回 `1`：

  ```
  无法启动图形界面（缺少 tkinter？）：No module named '_tkinter'
  提示：用带 tkinter 的解释器运行，例如 `py -3 -m musicgen gui`
  ```

界面操作、快捷键、停靠布局见[项目文档 §11](项目文档.md#11-gui-使用)。

---

## 11. `track` / `note` — 轨道与音符编辑

不用打开界面也能改工程：**新增轨道、改轨道参数、删轨道、增删改音符**。
所有写操作都**原地修改 XML 并保留注释与排版**（不是重新生成整个文件），
命令行与界面的「新建轨道」共用同一套 `core/trackedit.py`。

### 11.1 命令一览

| 命令 | 作用 |
| --- | --- |
| `track list <proj>` | 列出音轨：序号、名称、乐器、音符数、音量/声像/混响、文件路径 |
| `track add <proj> --name X` | 新增一条轨道，写 `tracks/*.track.xml` 并往工程里追加 `<track>` 引用 |
| `track edit <proj> --track SEL ...` | 改名称 / 乐器 / 音量 / 声像 / 混响 / 循环 / 移调 … |
| `track remove <proj> --track SEL` | 移除工程里的引用（默认**保留**音轨文件） |
| `note list <proj> --track SEL` | 列出该轨所有音符（1-based 序号） |
| `note add <proj> --track SEL --time T --dur D --pitch P` | 追加一个音符 |
| `note set <proj> --track SEL --index N ...` | 改第 N 个音符的起点/时值/音高/力度 |
| `note del <proj> --track SEL --index N` | 删第 N 个音符 |

### 11.2 用 `--track` 定位音轨

依次尝试：**1-based 序号**（`1`、`2`）→ **名称精确匹配**（`Melody`，忽略大小写）
→ **文件名**（`bass.track.xml` 或 `bass`）→ **名称/文件名部分匹配**（`stin`）。
匹配到多条会报错并列出候选，请改用序号或完整名称。找不到时错误里会附上现有音轨清单。

### 11.3 `track add`

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--name` | **必填** | 音轨名；也决定默认文件名 |
| `--instrument` | `piano` | 注册键或中文名（含 `drums`）；未注册的名字会警告，渲染时回退合成器 |
| `--gain` | `0dB` | `-4dB` / `+2` / `0` 都行，负值时见 §11.7 |
| `--pan` / `--reverb` | `0` / `0.2` | −1…+1 / 0…1 |
| `--bpm` | 跟随工程 | 逐轨速度覆盖 |
| `--loop` / `--loop-len` | 关 | 设为循环轨并指定周期（拍） |
| `--file` | `tracks/<名称>.track.xml` | 指定文件相对路径（必须在工程目录内） |
| `--force` | 关 | 目标文件已存在时覆盖；不给则自动加 `_2`、`_3` 避让 |
| `--note SPEC` | 无 | 初始音符，可重复；格式 `"起点 时值 音高 力度"`，和弦写在音高里 |

```bat
python -m musicgen track add songs\Song\Song.musicproj --name Guitar --instrument 吉他 --gain -4dB
python -m musicgen track add songs\Song\Song.musicproj --name Drums --instrument drums ^
  --note "0 1 kick,hat 0.8" --note "1 1 snare 0.75"
```

新建的空轨道自带一段语法提示注释；乐器是鼓组时音符会写成 `底鼓,闭镲` 而不是 `C2,Gb2`。
创建完输出新轨道的序号与文件路径，接着 `note add` 或直接编辑 XML 都可以。

### 11.4 `track edit`

只写你给的参数，没给的一律不动。`--loop` / `--mute` / `--solo` 用
`--loop` 与 `--no-loop` 这一对开关；`--loop-len none` 删除该属性；
`--transpose 0` / `--loop-count 0` 同样会删掉对应属性（回到默认）。

| 选项 | 说明 |
| --- | --- |
| `--name` / `--instrument` / `--bpm` | 改名 / 换乐器 / 逐轨速度 |
| `--gain` / `--pan` / `--reverb` | 混音参数 |
| `--loop` / `--no-loop` | 开关循环 |
| `--loop-len` / `--loop-count` | 循环周期（拍）/ 次数（0 = 无限） |
| `--mute` / `--no-mute` / `--solo` / `--no-solo` | 静音 / 独奏 |
| `--transpose` | 整体移调（半音，−48…+48） |

```bat
python -m musicgen track edit songs\Song\Song.musicproj --track 1 --gain -6dB --no-loop
python -m musicgen track edit songs\Song\Song.musicproj --track Melody --instrument violin --transpose 12
```

输出会逐行列出 `字段  旧值 → 新值`。改完会**清掉工程文件里针对这条轨的参数覆盖**
（`gain`/`pan`/`mute`/`solo`/`loop`/`name`），否则下次载入会被旧的工程层值盖回去。
不给任何修改参数会报错退出 1。

### 11.5 `track remove`

```bat
python -m musicgen track remove songs\Song\Song.musicproj --track 3
python -m musicgen track remove songs\Song\Song.musicproj --track 3 --delete-file
```

默认只移除工程里的引用、保留轨道文件（安全，可随时加回来）；要连文件一起删加 `--delete-file`。
工程只剩一条轨道时拒绝删除并退出 1。

### 11.6 `note add` / `set` / `del` / `list`

| 命令 | 主要选项 | 说明 |
| --- | --- | --- |
| `note list` | `--track` | 列音符，序号从 1 开始 |
| `note add` | `--time` `--dur` `--pitch`（必填）、`--vel`（默认 0.8） | 时值支持 `1/3`、`1/4`；`pitch` 逗号分隔即和弦，鼓轨可写 `kick` / `snare` |
| `note set` | `--index`（必填）+ 要改的字段 | 只改给的字段 |
| `note del` | `--index`（必填） | 返回被删音符的属性 |

```bat
python -m musicgen note list songs\Song\Song.musicproj --track Guitar
python -m musicgen note add songs\Song\Song.musicproj --track Guitar --time 2 --dur 1/2 --pitch F#4 --vel 0.5
python -m musicgen note set songs\Song\Song.musicproj --track Guitar --index 1 --pitch C5 --vel 0.9
python -m musicgen note del songs\Song\Song.musicproj --track Guitar --index 2
```

音符增删改都只动对应的 `<note>` 节点，音轨里的小节注释与排版会原样保留。

### 11.7 注意事项

* **负数值**：`--gain -4dB`、`--pan -0.2`、`--transpose -12` 直接写即可；
  CLI 会自动把这类参数合并成 `--gain=-4dB`，不必自己加等号。
* **退出码**：`0` 成功，`1` 参数/定位/写文件错误（错误写到 stderr，前缀「操作失败：」），
  `2` 工程文件本身有错；`argparse` 缺必填参数是 `2`。
* 编辑后建议跑一次 `validate` 再 `render`；`track` / `note` 命令本身不联网、不碰音源缓存。
* 界面里的「新建轨道…」（`Ctrl+Shift+T`）用的是同一个 `add_track()`，
  两边建出来的工程完全一致；界面里改轨道参数仍走右侧「属性」面板。

---

## 12. `instrument` — 管理乐器注册表

乐器清单**不再写死在代码里**：默认 9 件（8 件 FluidR3 GM 采样 + 内置鼓组）作为
初始内容写进 JSON 注册表，之后可以增删任意条目，命令行与界面读写的是同一个文件。

### 12.1 文件位置

默认 `~/.musicgen/instruments.json`，用 `MUSICGEN_INSTRUMENTS` 覆盖（见 §3.2）。
首次运行自动创建；文件损坏时回退内置默认且**不覆盖原文件**，用 `instrument reset` 可重写。
`instrument path` 打印当前路径。

### 12.2 命令一览

| 命令 | 作用 |
| --- | --- |
| `instrument list` | 列出所有乐器：键 / 名称 / 类型 / 音域 / 采样状态 / 增益 / 别名 |
| `instrument path` | 打印注册表文件路径 |
| `instrument add <key> --name 中文名 --gm 包名` | 注册一件采样乐器 |
| `instrument remove <key>` | 删除条目（**不删采样缓存**） |
| `instrument reset` | 恢复内置默认 9 件（自定义条目会丢失） |

### 12.3 添加乐器

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--name` | 必填 | 中文名，会自动成为别名 |
| `--gm` | — | FluidR3 GM 音色包名，如 `flute`；自动生成 4 个下载镜像 |
| `--url` | — | 自定义地址，可重复/逗号分隔，需含 `{name}` 与 `{ext}`；与 `--gm` 二选一 |
| `--alias` | — | 额外别名，可重复/逗号分隔 |
| `--low` / `--high` | 21 / 108 | MIDI 音域（0–127） |
| `--gain` | 10.0 | 采样电平补偿倍数，声音偏小就调大 |
| `--range-label` / `--title` | 空 | 列表里显示的说明文字 |
| `--force` | 关 | 覆盖同名注册键 |

```bat
python -m musicgen instrument add flute --name 长笛 --gm flute --low 60 --high 96 --alias 长笛声部 --gain 8.5
python -m musicgen samples fetch --instrument flute
python -m musicgen track add songs\Song\Song.musicproj --name Flute --instrument 长笛声部
```

注册后立刻生效：界面属性面板的乐器下拉框会刷新，工程里可以直接写中文名或别名。
采样还没下载时渲染会回退内置合成器（与其它乐器一致）。

### 12.4 删除与恢复

```bat
python -m musicgen instrument remove flute
python -m musicgen instrument reset
```

删除只改注册表：已经下载的采样缓存保留，工程里继续写这个名字会走合成器兜底。
内置乐器（含 `drums`）也能删，`reset` 可找回；删除内置鼓组后鼓谱不再按鼓件合成。

### 12.5 注意事项

* 注册键只允许小写字母 / 数字 / 下划线 / 连字符；音域必须在 0–127 且低音 ≤ 高音。
* 出错统一输出「注册表错误：…」到 stderr，退出码 `1`；参数缺失由 argparse 返回 `2`。
* 界面入口：编辑 → 乐器注册表… / 工具栏齿轮按钮 / `Ctrl+Shift+G`，改动实时刷新下拉框。
* 自定义乐器的增益写在注册表里；内置默认 8 件采样仍保留代码中的校准值作为兜底。

---

## 13. `effect` — 音轨效果链

效果写在音轨文件的 `<effect>` 里，按出现顺序串联，处理位置在**声像之后、汇入总线之前**。
DSP 细节与工程格式见[项目文档 §6.4](项目文档.md#64-效果链-effect)。

### 13.1 命令一览

| 命令 | 作用 |
| --- | --- |
| `effect types` | 列出已注册的 11 个音效，以及每个参数的范围与默认值 |
| `effect list <proj> [--track X]` | 查看效果链；不给 `--track` 就列出全部音轨 |
| `effect add <proj> --track X --type T [-p 键=值 …]` | 给音轨追加一个音效 |
| `effect set <proj> --track X --index N -p 键=值 …` | 改第 N 个音效（1-based）；值写 `none` 表示删掉该参数 |
| `effect remove <proj> --track X --index N` | 删除第 N 个音效 |
| `effect save <proj> --track X -o F` | 把该轨效果链存成 `.fx.json` 脚本 |
| `effect check F` | 校验脚本（不写工程） |
| `effect apply <proj> --track X --script F [--append]` | 应用脚本：默认整链替换，`--append` 追加 |

`--track` 与 `track` / `note` 命令一致：1-based 序号、名称、文件名都能定位，支持部分匹配。

### 13.2 十一个预设效果

| 类型 | 中文 | 参数（括号里是默认值） |
| --- | --- | --- |
| `stereo` | 立体 | `width` 0–2（1.6，M/S 加宽）· `haas` 0–30ms（0，右耳微延迟）· `mix` 0–1（1） |
| `echo` | 回声 | `time` 20–2000ms（320）· `feedback` 0–0.9（0.36）· `tone` 0–1（0.5）· `pingpong` 开/关（开）· `mix` 0–1（0.3） |
| `fade` | 淡入淡出 | `in` 0–10s（0.8）· `out` 0–10s（1.5）· `curve` linear/expo（expo） |
| `electro` | 电音 | `rate` 0.5–200Hz（30）· `depth` 0–1（0.8）· `drive` 0–1（0.35）· `bits` 3–16（8）· `mix` 0–1（0.85） |
| `8bit` | 8bit | `rate` 500–44100Hz（11025）· `tone` 0–1（0.75）· `drive` 0–1（0）· `mix` 0–1（1） |
| `4bit` | 4bit | `rate` 500–44100Hz（8000）· `tone` 0–1（0.6）· `drive` 0–1（0）· `mix` 0–1（1） |
| `2bit` | 2bit | `rate` 500–44100Hz（4000）· `tone` 0–1（0.45）· `drive` 0–1（0）· `mix` 0–1（1） |
| `eq` | 均衡 | `low` ±24dB（0）· `mid` ±24dB（0）· `high` ±24dB（0）· `mid_freq` 200–6000Hz（900） |
| `drive` | 失真 | `amount` 0–1（0.35）· `tone` 0–1（0.55）· `output` ±24dB（0）· `mix` 0–1（1） |
| `compressor` | 压缩 | `threshold` −48–0dB（−18）· `ratio` 1–20（3）· `release` 20–1000ms（180）· `makeup` ±18dB（0） |
| `chorus` | 合唱 | `rate` 0.05–6Hz（0.6）· `depth` 0.5–20ms（6）· `mix` 0–1（0.35）· `voices` 1–3（2） |

* **只写要改的参数**，其余用注册表默认值，所以 XML 里可以只留 `<effect type="echo" mix="0.25"/>`。
* 单位可带可不带：`-p time=450` 与 `-p time=450ms` 等价；数值 `450`、`0.25` 都行。
* 布尔写 `true/false`、`on/off`、`是/否`；枚举写 `linear` / `expo`。
* 中文类型名也认：`--type 回声`、`--type 立体`、`--type 电音`。
* `8bit` / `4bit` / `2bit` 是降采样 + 位深压缩：`bits` 分别固定为 8 / 4 / 2 位（256 / 16 / 4 级电平），`rate` 越低越破、`tone` 越小越闷；三者都按素材自身峰值量化并还原电平，插入后轨道响度基本不变。

### 13.3 例子

```bat
:: 先看有哪些参数
python -m musicgen effect types

:: 给吉他加 375ms 回声
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type echo -p time=375 -p feedback=0.3 -p mix=0.22

:: 再加加宽与淡出（顺序即处理顺序）
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type 立体 -p width=1.7
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type fade -p in=1.2 -p out=2

:: 调参 / 删参数 / 删效果
python -m musicgen effect set songs\Song\Song.musicproj --track Guitar --index 1 -p feedback=0.45
python -m musicgen effect set songs\Song\Song.musicproj --track Guitar --index 1 -p time=none
python -m musicgen effect remove songs\Song\Song.musicproj --track Guitar --index 2

:: 查看结果并渲染
python -m musicgen effect list songs\Song\Song.musicproj --track Guitar
python -m musicgen render songs\Song\Song.musicproj -o out\song.wav
```

### 13.4 音效脚本 `.fx.json`

```bat
:: 存：把当前效果链导出
python -m musicgen effect save songs\Song\Song.musicproj --track Guitar -o fx\guitar.fx.json

:: 校验：不写工程
python -m musicgen effect check fx\guitar.fx.json

:: 复用：整链替换（默认）或追加
python -m musicgen effect apply songs\Song\Song.musicproj --track Guitar --script fx\guitar.fx.json
python -m musicgen effect apply songs\Song\Song.musicproj --track Guitar --script fx\guitar.fx.json --append
```

脚本格式：

```json
{
  "version": 1,
  "name": "guitar-bus",
  "description": "失真节奏吉他的常用链路",
  "effects": [
    {"type": "eq", "low": -3, "high": 2},
    {"type": "drive", "amount": 0.25},
    {"type": "compressor", "threshold": -20, "ratio": 3}
  ]
}
```

* `check` 只解析不落盘；`apply` 前会先完整校验，脚本有问题直接退出码 `1`，不会写出半成品工程。
* 默认**整链替换**，`--append` 追加到现有链尾；总数上限 12 个。
* `type` 可写注册键或中文名；参数只写要改的，越界值夹到合法范围。
* 脚本只含效果链，不含轨道 `gain` / `pan` / `reverb`。

### 13.5 注意事项

* **顺序有意义**：`回声 → 立体` 与 `立体 → 回声` 听感不同，`effect list` 按 XML 里的顺序显示。
* **未知类型 / 未知参数不中断渲染**：`validate` 只给警告，该条效果或该参数被跳过；`effect add/set` 是严格模式，直接报错（退出码 `1`）。
* **越界值**：手写 XML 时夹到范围内并给警告；用 `effect add/set` 时直接拒绝。
* **回声尾巴受工程长度限制**：超出 `length` 的部分会被截掉；需要更长余韵就加 `tail="3s"` 或加大 `length`。
* **淡入淡出按「实际发声区间」算**：2 秒的轨在 60 秒工程里也只淡它自己的头尾，不会淡一整分钟。

---

## 14. `analyze` — 从音频重建工程

把成品音频还原成可编辑工程，是 `render` 的逆向：
**Demucs 分离 → FFT 转谱 → 音色识别 → 写 musicproj**。

```bat
python -m musicgen analyze song.mp3 -o songs\Song
python -m musicgen analyze song.mp3 -o songs\Song --bpm 96 --quantize 1/16 --force
```

### 14.1 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `audio` | 必填 | 输入音频：wav / mp3 / flac / ogg / aiff |
| `-o`, `--output` | `./<音频名>-musicproj` | 输出工程目录 |
| `--name` | 音频文件名 | 工程名（写进 `<musicproj name="...">`） |
| `--bpm` | 自动检测 | 手动指定速度；检测置信度低时建议指定 |
| `--quantize` | `1/16` | 量化网格：`1/4` / `1/8` / `1/16` / `1/32` / `off`；**精确回放（默认）不量化**，只在 `--no-exact-replay` 下生效 |
| `--min-note` | `65` | 最短音符（毫秒），更短的碎片会被丢掉 |
| `--model` | `htdemucs_6s` | 分离模型：6 轨（推荐）/ `htdemucs`（快）/ `htdemucs_ft`（精细） |
| `--device` | `auto` | `auto` / `cpu` / `cuda` |
| `--audio-tracks` | 关 | 只分离不转谱：工程里写 `<audio>` 音频轨，保留原始音色 |
| `--no-exact-replay` | 关 | 关掉默认的精确回放：回到「量化到 1/16 网格 + 逐事件采样 + 自动增益/EQ/母带压缩」的旧行为 |
| `--no-stems` | 关 | 不保存 `stems/*.wav`；但 `stems/vocals.wav` 是工程引用的人声音频轨，仍会写出（`--audio-tracks` 则强制保存全部分轨） |
| `--no-reuse` | 关 | 忽略分轨缓存，强制重新分离 |
| `--force` | 关 | 覆盖已存在的工程 |
| `--vocal-instrument` | `vocal` | **已废弃**：人声固定为音频轨，参数保留兼容旧脚本，不再生效 |
| `--max-polyphony` | `6` | 每轨最多同时检测几个音 |
| `--no-layers` | 关 | 不把「其他」分轨按音区拆层 |
| `--max-layers` | `3` | 每条分轨最多拆几层 |
| `--no-brief` | 关 | 不写 `reverse_brief.json`（默认写出，供大模型判断） |
| `--no-auto-eq` | 关 | 不按「渲染 vs 原分轨」的频谱差自动补 EQ |
| `--no-sampled` | 关 | 不用原曲采样当音色（默认从分轨切采样，音色即原曲） |
| `--no-master-comp` | 关 | 不在母带补压缩（默认按原曲峰值因数压掉转谱多出来的瞬态） |
| `--decisions` | 无 | 应用决策文件（换音色/脚本/效果/删轨/段落标签） |
| `--quiet` | 关 | 不显示进度条 |
| `--cache-dir` | 注册表默认 | 音源缓存目录（构建音色模板用） |

> **人声是音频轨**：默认转谱模式下只有人声这一条不拆音高，工程里写
> `<audio file="../stems/vocals.wav"/>`，原样重放 Demucs 分出来的人声；
> 其余分轨照常转成可编辑音符。想让所有分轨都变成音频轨，用 `--audio-tracks`。

### 14.2 输出

```
songs/Song/
├─ Song.musicproj              工程（bpm / length / 音轨引用）
├─ tracks/01-vocal.track.xml   人声音频轨（<audio>，没有 <note>）
├─ tracks/02-bass.track.xml    其余分轨：可编辑音符轨
├─ ...
├─ stems/vocals.wav            人声音频轨引用的分轨（24bit，必定写出）
├─ stems/stems.json            分轨缓存元数据
├─ instruments/src_*/          原曲采样（每条轨一组，按音高建模）
├─ samples/<分轨>_bNNNNN.wav   精确回放的立体声时间轴块（默认）
├─ beds/<分轨>.wav             补全轨：原分轨挖掉块覆盖区间，块+bed = 原分轨
├─ samples/tNN_eNNNNN.wav      旧模式（--no-exact-replay）的逐事件采样
├─ reverse_brief.json          逆向简报（--no-brief 可关）
├─ synths/*.synth.json         决策生成的合成音脚本（仅 --decisions 用到时）
└─ analysis.json               速度/调性/每轨参数/置信度/段落/分层/警告
```

分轨与乐器的对应：

| 分轨 | 工程乐器 | 说明 |
| --- | --- | --- |
| `vocals` | 音频轨 | 原样引用 `stems/vocals.wav`，不转谱、不换音色、没有音符 |
| `drums` | `drums` | 内置鼓组，写 `kick/snare/hat/...` |
| `bass` | `bass` | GM Electric Bass (finger) |
| `piano` | `piano` | 6 轨模型才有 |
| `guitar` | `guitar` | 6 轨模型才有 |
| `other` | 识别结果 | 小提琴 / 大提琴 / 小号 / 竖笛 / 钢琴 / 吉他 |

### 14.3 完整输出范例

```text
    1.0%  读取音频…
   56.0%  分析速度与调性…
   83.0%  构建音色模板库…
  100.0%  完成
分析 daybreak-20s.wav → songs\Reverse
重建工程：songs\Reverse\Reverse.musicproj
  4 条音轨 | 246 个音符 | 100.1BPM | C major | 时长 20.0s | 耗时 32.3s
  · 鼓组     drums    音符 60    -2.7dB  pan +0.03  置信 0.83
  · 小提琴    violin   音符 45    -12.7dB  pan +0.07  置信 0.50
  · 吉他     guitar   音符 63    -16.1dB  pan +0.16  置信 0.56
  · 钢琴     piano    78    -18.1dB  pan -0.00  置信 0.47
  ! 没有从人声分轨里检测到旋律，人声轨已跳过

下一步：python -m musicgen render "songs\Reverse\Reverse.musicproj" -o "songs\Reverse\daybreak-20s.wav"
```

> 上面是 `output/` 里《晨光》前 20 秒的实测输出（真值 100BPM / C 大调，鼓件 9 底鼓 +
> 9 军鼓 + 35 踩镲）。**该样例采自 v1.27 之前**：当前版本不会再做「人声转谱」，
> 那条 `! 没有从人声分轨里检测到旋律` 警告已不存在，输出里会多一条
> `· 人声 [音频] vocals … 文件 vocals.wav`。音符数与音轨数随素材变化。

### 14.4 音频轨模式（保真优先）

```bat
python -m musicgen analyze song.mp3 -o songs\Song-Audio --audio-tracks
```

不转谱，直接把 6 条分轨写成音频轨：

```text
分析 song.mp3 → songs\Song-Audio
重建工程：songs\Song-Audio\Song-Audio.musicproj
  6 条音轨 | 6 条音频轨（原样混音） | 129.1BPM | C major | 时长 186.2s | 耗时 22.7s
  · 人声     [音频] vocals  pan +0.00  文件 vocals.wav
  · 其他     [音频] other   pan +0.00  文件 other.wav
  · 吉他     [音频] guitar  pan +0.00  文件 guitar.wav
  · 贝斯     [音频] bass    pan +0.00  文件 bass.wav
  · 钢琴     [音频] piano   pan +0.00  文件 piano.wav
  · 鼓组     [音频] drums   pan +0.00  文件 drums.wav
```

- 增益/声像全为 0，渲染 = 分轨求和再归一化；实测与原曲相关系数 0.997、SNR 21.8dB
- 比转谱模式快一个数量级（复用分轨时 3 分钟歌约 20 秒）
- 音符不可编辑；要改谱就用不带 `--audio-tracks` 的默认模式，或两种工程并存
- 分轨 WAV 必须保留；即使加 `--no-stems` 也会强制写出
- 默认模式本来就是混合的：人声一条音频轨 + 其余分轨的音符轨，
  所以 `stems/vocals.wav` 在默认模式下也不能删

### 14.5 注意事项

* **首次运行下载权重**：约 300MB，之后走本地缓存。国内网络先
  `set HF_ENDPOINT=https://hf-mirror.com`。
* **不需要 ffmpeg**：音频由 soundfile 解码后直接喂给 Demucs 的 `separate_tensor`。
* **分轨缓存**：`stems/` 里的 WAV 与 `stems.json` 会在源文件大小/mtime、模型都没变时复用；
  改参数重跑很快，要重分离加 `--no-reuse`。
* **精度**：干净、稀疏的编曲还原度高；密集和弦、强混响、失真吉他、同时敲响的鼓件会失准。
  这是转谱而不是「解混音」，重建后建议在界面里删改。
* **人声**：不转谱，整条 Demucs 人声分轨作为一条 `<audio>` 音频轨原样重放；
  气声、转音、歌词都在，但这一轨没有音符可改，`stems/vocals.wav` 不能删。
* **速度**：自动检测可能八度错，置信度低于 0.25 会警告，此时用 `--bpm` 指定。
* **默认精确回放已解决「切得不准/不完整 + 音效发闷」**：按原始起音切立体声块，
  块外由 `beds/` 补全轨原样填回，二者相加 = 原分轨；不量化、不二次截断、不淡入淡出，
  也不做自动增益/EQ/母带压缩。工程目录下 `beds/` 是工程的一部分，不能删。
* **旧模式（--no-exact-replay）才有限制**：播放时间量化到网格、时值被裁剪、单声道、
  只覆盖检测到的音符；v1.28 起每条轨的采样文件带 `tNN_` 前缀，不再互相覆盖。
  想要完全不转谱、音符也不可编辑的纯保真版本，用 `--audio-tracks`。

---

### 14.6 鼓件拆轨、可编辑性、分层与段落

默认行为：

- **鼓件拆轨**：鼓分轨按鼓件家族拆成「鼓-底鼓 / 鼓-军鼓 / 鼓-踩镲 / 鼓-嗵鼓 / 鼓-镲片」，
  每件一条轨，增益以底鼓为基准、其余只允许 +6/−9dB 浮动（内置鼓组的镲片比真鼓轻 30dB，
  硬匹配会把整首顶成刺耳）。
- **原曲采样**：从每条分轨里切出「干净的单音」写成采样（`<工程>/instruments/src_<分轨>_<序号>/`），
  相邻采样不超过 3 个半音（采样器的变速上限是 7），渲染时按音高变速重放。
  音色因此直接来自原曲，工程自带采样、可脱离内置音色库渲染；`--no-sampled` 关闭。
- **人声弯音**：人声分轨保留逐帧音高的偏离，写成「音符 + 弯音曲线」，渲染时做时变重采样，
  滑音/颤音不再被压成台阶。
- **可编辑性整理**：同轨音符不重叠（时值收到下一个起音）、碎片轨（<24 音）并回主轨、
  音色判不出时兜底合成音。
- **分层**：`other` 分轨按音高间隙拆成 2–3 条音轨（「其他 低音区 / 中音区 / 高音区」），
  每层独立识别音色；`--no-layers` 关闭。
- **段落**：谱新颖度切段，写进 `analysis.json` 的 `regions[]`（`rms_db` / `centroid_hz` /
  `onsets_per_second` / 各分轨电平），标签留空。
- **简报**：`reverse_brief.json` 汇总段落、每轨可解释音色指标、最近邻候选概率、效果估计
  （`crest_db` / `rt60_s` / `width` / `echo_ms` / `suggested[]`）、可用乐器与 7 个合成音预设，
  并附决策文件 schema。`--no-brief` 关闭。

### 14.7 大模型决策回灌

```bat
:: ① 先跑一次，产出简报
python -m musicgen analyze song.mp3 -o songs\Reverse --force

:: ② 读 songs\Reverse\reverse_brief.json，按判据写 decisions.json

:: ③ 回灌：复用分轨，只重跑分层/转谱/重建（20–40 秒）
python -m musicgen analyze song.mp3 -o songs\Reverse --decisions songs\Reverse\decisions.json
```

```json
{
  "version": 1,
  "regions": [{"index": 2, "label": "chorus"}],
  "synth_patches": {"air_pad": {"name": "air_pad", "gain": 0.18,
                                "oscillators": [{"wave": "saw", "detune": -9},
                                                {"wave": "saw", "detune": 9}]}},
  "tracks": [
    {"track": "其他 高音区", "action": "retimbre", "instrument": "synth",
     "patch": "air_pad", "name": "Pad", "gain_db": -7, "pan": 0.15,
     "effects": [{"type": "eq", "low": -3, "high": 2}], "reason": "持续音层"}
  ]
}
```

* `track` 写轨名、1-based `id`，或用 `stem` + `layer`（1-based）；匹配不到**静默跳过**。
* `action`：`keep` / `retimbre` / `delete`。
* 写 `patch` 后音轨强制 `instrument="synth"`，脚本落盘到 `synths/`。
* 给了 `gain_db` 就不再被自动响度校准覆盖。
* 决策有错（未知效果、版本不符、脚本载入失败）直接退出码 `1`，不写半成品。
* 判据与完整字段见 `Skill/musicgen/references/逆向判断.md`。

---

## 15. `synth` — 合成音脚本

合成音色用**纯 JSON 脚本**描述（`.synth.json`），不含可执行代码。可以注册成乐器，
也可以直接挂在音轨上。字段全集与设计说明见
[项目文档 §8.5](项目文档.md#85-合成音脚本-synthjson) 与 `Skill/musicgen/references/合成音脚本.md`。

```bat
python -m musicgen synth list
python -m musicgen synth show warm_pad
python -m musicgen synth check warm_pad --midi 60 --dur 0.8
python -m musicgen synth render analog_lead -o out\lead.wav --midi 60,64,67
python -m musicgen synth new -o songs\Song\synths\pad.synth.json --preset warm_pad --label 暖垫
```

### 15.1 子命令

| 命令 | 作用 |
| --- | --- |
| `synth list` | 列出内置 7 个预设 + 注册表里的合成音色 |
| `synth show <名\|路径>` | 打印规范化后的 JSON（可直接改成新脚本） |
| `synth check <名\|路径> [--midi 60] [--dur 1.0] [--vel 0.8]` | 校验并试渲染一个音，报告峰值/RMS/是否静音 |
| `synth render <名\|路径> -o out.wav [--midi 60,64,67] [--dur 1.5] [--vel 0.8]` | 渲染成 WAV（多个音高会和弦叠放） |
| `synth new -o F [--preset warm_pad] [--name N] [--label 标签]` | 以预设为模板生成可改脚本 |

### 15.2 脚本字段

```json
{
  "name": "air_pad",
  "label": "空气垫",
  "gain": 0.18,
  "oscillators": [
    {"wave": "saw", "detune": -9, "level": 0.5, "pan": -0.35},
    {"wave": "saw", "detune": 9,  "level": 0.5, "pan": 0.35}
  ],
  "sub":      {"wave": "sine", "octave": -1, "level": 0.25},
  "noise":    {"level": 0.02, "decay": 0.6},
  "amp":      {"attack": 0.65, "decay": 1.4, "sustain": 0.8, "release": 1.6},
  "filter":   {"cutoff": 1400, "resonance": 1.0, "env": 700, "env_decay": 0.9, "keytrack": 0.4},
  "fm":       {"ratio": 2.0, "index": 0.3, "decay": 0.5},
  "vibrato":  {"rate": 4.6, "depth": 6, "delay": 0.25},
  "max_harmonics": 32
}
```

| 组 | 字段 | 范围 | 说明 |
| --- | --- | --- | --- |
| 全局 | `name` / `label` / `description` | — | 注释与命名 |
| | `gain` | 0–1.5（0.22） | 每音符绝对增益，健康峰值 0.1–0.5 |
| 振荡器 | `oscillators[]` | 1–8 个 | `wave`（`sine/saw/square/triangle/noise`，可中文）· `octave` ±3 · `detune` ±100 音分 · `level` 0–4 · `pan` ±1 · `phase` ±1 |
| | `sub` | — | 低八度补声，`octave` 省略时自动 −1 |
| | `noise.level` / `decay` | 0–0.6 / 0.01–6s | 起音噪声 |
| 包络 | `amp.attack/decay/sustain/release` | 0–8 / 0–12 / 0–1.5 / 0–12 | ADSR（秒） |
| 滤波 | `filter.cutoff` | 40–20000Hz | 基础截止 |
| | `filter.resonance` | 0.3–8 | 共振 |
| | `filter.env` / `env_decay` | 0–16000Hz / 0.01–8s | 包络推高截止的量与回落 |
| | `filter.keytrack` | 0–1 | 截止跟随基频 |
| 调制 | `fm.ratio` / `index` / `decay` | 0.25–16 / 0–8 / 0.02–8s | 频率调制 |
| | `vibrato.rate` / `depth` / `delay` | 0.1–12Hz / 0–100 音分 / 0–3s | 颤音 |
| | `max_harmonics` | 1–128（32） | 带限合成的最高谐波 |

* 只写要改的字段；除 `oscillators` 外全部可省，最短脚本是 `{"oscillators": ["saw"]}`。
* 不认识的字段**直接报错**（防止拼错键以为生效了）；数值越界夹到范围。
* 锯齿/方波用谐波叠加合成，按 Nyquist 截断，不混叠；噪声固定种子，渲染完全确定。

### 15.3 挂到工程上

```bat
:: ① 注册成乐器（全局可用）
python -m musicgen instrument add airpad --name 空气垫 --synth air_pad --low 48 --high 96
python -m musicgen samples list
```

```xml
<!-- ② 或挂在单条音轨上，工程自包含 -->
<track name="Pad" instrument="synth" patch="../synths/air_pad.synth.json" gain="-7dB" pan="0.15">
  <note time="0" dur="4" pitch="C4,E4,G4"/>
</track>
```

* `instrument="synth"` 必须带 `patch`，否则退回兜底合成器（只警告，音色不对）。
* `patch` 相对**音轨文件**目录；脚本坏了在打开工程时报错（退出码 2）。
* 优先级：音轨 `patch` > 注册表里的合成音色 > 采样音源 > 兜底合成器。

### 15.4 内置预设

| 预设 | 适合 | 特征 |
| --- | --- | --- |
| `warm_pad` | 铺底、弦垫 | 双锯齿微失谐、慢起音、低通 |
| `glass_bell` | 高音点缀、钟琴 | FM 为主、快衰减 |
| `analog_lead` | 主旋律 | 锯齿 + 滤波包络 + 轻颤音 |
| `deep_bass` | sub / 808 | 正弦 + 低八度 sub |
| `pluck_string` | 拨弦、琶音 | 三角 + 短衰减 + 高共振 |
| `soft_keys` | 电钢、键盘层 | 正弦叠 FM、中速衰减 |
| `air_texture` | 气声、氛围 | 高比例噪声 + 窄滤波 |

### 15.5 注意事项

* **`gain` 不要写 1.0**：单音就接近满刻度，多轨必削波；0.15–0.3 是常用区间。
* `synth check` 的峰值 <0.01 说明基本没声音（多半 `gain` 太小或包络 `sustain` 为 0）。
* 慢起音 pad 配 0.1s 的音符会「还没起来就结束」，起音应小于音符时值的三分之一。
* 脚本只描述音色；`gain` / `pan` / `reverb` / 效果链仍写在音轨上。

---

## 16. 常见工作流

**第一次跑通**

```bat
py -3 -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
py -3 -m musicgen samples fetch
py -3 -m musicgen render examples\demo\demo.musicproj -o demo.wav
```

**先校验再渲染（改完 XML 的习惯动作）**

```bat
python -m musicgen validate songs\MySong\MySong.musicproj && python -m musicgen render songs\MySong\MySong.musicproj
```

**批量渲染整个目录**

```bat
for /r songs %f in (*.musicproj) do python -m musicgen render "%f" -o "out\%~nf.wav" --quiet
```

> 上面是**命令行里直接敲**的写法（`%f`）；写进 `.bat` 文件要改成 `%%f`。

```bash
# Git Bash / Linux
for p in songs/*/*.musicproj; do
  python -m musicgen render "$p" -o "out/$(basename "${p%.musicproj}").wav" --quiet || echo "失败: $p"
done
```

**离线渲染**：`render` 全程不联网——缓存里有采样就用，没有就用合成器。所以先在一台有网的机器跑 `samples fetch`，把整个 `samples_cache/` 拷过去（或用 `--cache-dir` 指过去）即可。

**交付不同格式**

```bat
python -m musicgen render song.musicproj -o dist\song-16.wav --bits 16   :: 分享
python -m musicgen render song.musicproj -o dist\song-24.wav            :: 归档
python -m musicgen render song.musicproj -o dist\song-f32.wav --float32 :: 后续混音
```

**CI 里守住工程不会坏**

```yaml
# GitHub Actions 片段
- name: 校验全部工程
  run: |
    for p in $(git ls-files '*.musicproj'); do
      python -m musicgen validate "$p" || exit 1
    done
```

---

## 17. 提示信息对照表

### 17.1 工程解析（退出码 2）

| 提示 | 原因 | 处理 |
| --- | --- | --- |
| `文件不存在` | 路径写错 / 相对路径基准不对（`.bat` 以仓库根目录为基准） | 用绝对路径，或先 `cd` 到工程目录 |
| `XML 解析失败: not well-formed …` | XML 语法错（未闭合标签、属性引号、`&` 没转义） | 按报的行列号修 |
| `音轨文件不存在: tracks/x.track.xml` | `file` 属性的相对路径错了 | 路径是**相对工程目录**，不是当前目录 |
| `bpm 超出合理范围 5-600` | 速度填错 | 改 5–600 |
| `dur 必须大于 0` | 音符时值为 0 / 负数 | 至少 `1/16` |
| `无法识别的音名: H9` | 音名拼错 | 用 `C4` / `C#4` / `Db4` / MIDI 数字 `60` |
| `缺少 pitch 属性` | `<note>` 没写 `pitch` | 补上音名 |
| `音高超出 0-127` | 移调后越界 | 降 `transpose` |
| `根节点必须是 <musicproj>` | 文件不是工程文件 | 检查打开的是不是 `*.musicproj` |
| `工程里没有任何 <track>` | 一条音轨都没引用 | 加 `<track file="…"/>` |
| `缺少 file 属性` | `<track>` 没写 `file` | 补上音轨文件路径 |

### 17.2 忽略即警告（退出码 0，但值没生效）

| 提示 | 原因 | 处理 |
| --- | --- | --- |
| `gain 无效: 增益 不是有效数字: 'loud'` | `gain` 得是 `-3dB`、`-3` 或 `-3db` | 改成带单位或纯数字 |
| `pan 无效: …` | `pan` 不是数字 | −1…+1 |
| `属性 reverb 无效，已用默认值: …` | 数值属性写成了非法值 | 改数字（`reverb` 0–1） |
| `忽略未知节点 <x>` | 音轨文件里出现了 `<note>` 以外的标签 | 删掉，或改成 XML 注释 |
| `bpm 无效: bpm 必须大于 0` | 音轨级 `bpm` ≤ 0 | 删掉该属性即跟随工程 |
| `非常用采样率 22050，仍会使用` | `sampleRate` 不在推荐表里 | 一般 44100 或 48000 |
| `N 个音符起点超出循环周期 X 拍，不会被播放` | `loopLen` 比内容短 | 加大 `loopLen` 或把音符挪回周期内 |
| `未知音效 'x'，已忽略（可用：…）` | `<effect type="…">` 写错类型 | 用 `effect types` 里的注册键或中文名 |
| `音效「回声」没有参数 wrong，已忽略` | 参数名拼错 / 不属于该效果 | 照 `effect types` 的参数表写 |
| `参数 width 超出 0–2 ×，已按 2× 处理` | 数值越界 | 手写 XML 会被夹到范围内；要精确控制改用 `effect set` |

### 17.3 渲染警告（stderr，前缀 `!`）

| 提示 | 含义 | 处理 |
| --- | --- | --- |
| `未注册的乐器 'x'，使用内置合成器。已支持: …` | 乐器名不在注册表 | 改成已支持的一件（含 `drums`），或按[项目文档 §8.2](项目文档.md#82-新增一个乐器)扩展 |
| `大提琴的采样尚未下载，本次使用内置合成器。运行 …` | 这件乐器一次都没下过 | `python -m musicgen samples fetch --instrument cello` |
| `乐器 'x' 的采样音域为 …，B1 超出范围，该音改用内置合成器` | 音高超出采样范围 | 移调 / 换音区 |
| `没有可渲染的音轨（全部静音或为空）` | 全静音、全空轨，或独奏轨为空 | 检查 `mute` / `solo` |
| `峰值 +2.3dBFS 已超出 0dB，已做削波保护` | 关了归一化又太响 | 降 `masterGain`，或去掉 `--no-normalize` |
| `音效 回声（echo）在音轨「Melody」上处理失败，该效果已跳过：…` | 效果内部异常（参数异常或注册表被改动） | 修参数或删掉该效果；其余音轨与效果照常渲染 |

---

## 18. 与 GUI 的能力对照

| 能力 | CLI | GUI |
| --- | --- | --- |
| 渲染 WAV | `render` | 「渲染」按钮 / `Ctrl+R` |
| 试听 | 导出后用播放器打开 | 传输条直接播（`空格`） |
| 新建工程 | `init` | `Ctrl+N` 向导（含浏览目录） |
| 新增轨道 | `track add` | 文件 → 新建轨道… / `Ctrl+Shift+T` 对话框 |
| 管理乐器注册表 | `instrument list/add/remove/reset` | 编辑 → 乐器注册表… / `Ctrl+Shift+G` |
| 编辑轨道 / 音符 | `track edit`、`note add/set/del` | 属性面板 / 音符面板，应用即写回 |
| 编辑效果链 | `effect add/set/remove`、`effect list` | 属性面板「效果」分组：添加… / 编辑… / 删除 |
| 改工程参数 | 编辑 XML | 属性面板 / 音符面板，应用即写回 |
| 校验 | `validate` | 载入时自动校验，问题进输出窗口 |
| 下载音源 | `samples fetch` | 「渲染 → 下载音源」按工程用到的乐器批量下 |
| 从音频重建工程 | `analyze` | 文件 → 从音频重建工程… / `Ctrl+Shift+A`（含「音频轨」选项） |
| 音效脚本 `.fx.json` | `effect save/check/apply` | 暂不支持（可用属性面板逐条加） |
| 合成音脚本 `.synth.json` | `synth list/show/check/render/new` | 暂不支持（工程里能打开、能渲染） |
| 分层 / 段落 / 决策回灌 | `analyze --decisions` 等 | 暂不支持 |
| 批量 / 自动化 | 适合 | 不适合 |

一句话：**批量、CI、无桌面环境用 CLI；调音、试听、逐个音符微调用 GUI**。两者读写同一套 XML，可以混着用。

---

## 19. 速查卡

```bat
:: 看版本 / 帮助
python -m musicgen -V
python -m musicgen render -h

:: 建工程
python -m musicgen init songs\Song --bpm 92 --template demo

:: 校验 / 看概要
python -m musicgen validate songs\Song\Song.musicproj
python -m musicgen info songs\Song\Song.musicproj

:: 渲（默认 24bit，输出到目录时自动用工程名）
python -m musicgen render songs\Song\Song.musicproj -o out\song.wav
python -m musicgen render songs\Song\Song.musicproj -o out\ --bits 16 --quiet

:: 音源
python -m musicgen samples list
python -m musicgen samples fetch --instrument 小提琴,cello

:: 轨道编辑
python -m musicgen track list songs\Song\Song.musicproj
python -m musicgen track add songs\Song\Song.musicproj --name Guitar --instrument guitar
python -m musicgen note add songs\Song\Song.musicproj --track Guitar --time 0 --dur 1 --pitch C4
python -m musicgen track edit songs\Song\Song.musicproj --track 1 --gain -6dB

:: 从音频重建工程
python -m musicgen analyze song.mp3 -o songs\Song
python -m musicgen analyze song.mp3 -o songs\Song --bpm 96 --quantize off --force
python -m musicgen analyze song.mp3 -o songs\Song-Audio --audio-tracks   :: 保真
python -m musicgen analyze song.mp3 -o songs\Song --decisions songs\Song\decisions.json  :: 回灌决策

:: 合成音脚本
python -m musicgen synth list
python -m musicgen synth check warm_pad --midi 60 --dur 0.8
python -m musicgen synth new -o songs\Song\synths\pad.synth.json --preset warm_pad
python -m musicgen instrument add airpad --name 空气垫 --synth warm_pad --low 48 --high 96

:: 乐器注册表
python -m musicgen instrument list
python -m musicgen instrument add flute --name 长笛 --gm flute --low 60 --high 96
python -m musicgen instrument remove flute
python -m musicgen instrument reset

:: 效果链
python -m musicgen effect types
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type echo -p time=375 -p mix=0.22
python -m musicgen effect add songs\Song\Song.musicproj --track Guitar --type 立体 -p width=1.7
python -m musicgen effect set songs\Song\Song.musicproj --track Guitar --index 1 -p feedback=0.45
python -m musicgen effect list songs\Song\Song.musicproj --track Guitar
python -m musicgen effect remove songs\Song\Song.musicproj --track Guitar --index 2
python -m musicgen effect save songs\Song\Song.musicproj --track Guitar -o fx\guitar.fx.json
python -m musicgen effect apply songs\Song\Song.musicproj --track Guitar --script fx\guitar.fx.json

:: 界面
py -3 -m musicgen gui songs\Song\Song.musicproj
```

---

## 20. `transcribe` / `import-midi` / `bundle` — 转谱与自带音源

```bat
:: 音频 → MIDI → 工程（MuScriptor；权重需先在 HuggingFace 接受许可）
python -m musicgen transcribe song.mp3 -o songs/Song/song.mid --model small --import-to songs/Song

:: 已有 MIDI：直接导入
python -m musicgen import-midi song.mid -o songs/Song [--bpm 128] [--no-bundle]

:: 给已有工程补打包（分析出来的工程也适用）
python -m musicgen bundle songs/Song
```

### 20.1 参数

| 命令 | 参数 | 默认 | 说明 |
| --- | --- | --- | --- |
| `transcribe` | `audio` | 必填 | wav / mp3 / flac / ogg / m4a |
| | `-o, --output` | `<音频名>.mid` | 输出 MIDI |
| | `--model` | `small` | `small`（CPU 可跑）/ `medium` / `large` |
| | `--instruments` | 不限 | 限定乐器组，见 `muscriptor list-instruments` |
| | `--endpoint` | `https://hf-mirror.com` | HuggingFace 镜像；官方站填 `https://huggingface.co` |
| | `--import-to` | 无 | 转完直接导入成工程 |
| | `--no-bundle` | 关 | 导入时不拷采样 |
| `import-midi` | `midi` | 必填 | `*.mid` / `*.midi` |
| | `-o, --output` | 必填 | 工程目录 |
| | `--name` | 目录名 | 工程名 |
| | `--bpm` | MIDI 内速度 | 覆盖速度 |
| | `--no-bundle` | 关 | 不拷采样，仍依赖全局缓存 |
| `bundle` | `project` | 必填 | 工程文件路径 |

### 20.2 MIDI 怎么分轨

按「MIDI 轨 × 通道 × 音色号」拆：同一轨里换了音色会拆成两条音轨。乐器按 GM 家族映射：

| GM 音色号 | 本项目乐器 |
| --- | --- |
| 0–7 | 钢琴（中位音高 < 40 走到大提琴） |
| 24–31 | 吉他 |
| 32–39 | 贝斯 |
| 40–47 | 弦乐（中位音高 < 52 大提琴，否则小提琴） |
| 52–55 | 人声 |
| 56–63 | 小号 |
| 72–79 | 竖笛 |
| 80–87 / 88–95 | 合成主音 / 合成垫 |
| 通道 10（0 基 9） | 鼓组（音高按 GM 打击乐原样保留） |

速度取第一个 `set_tempo`，拍号取第一个 `time_signature`；MIDI 内部若有速度变化，
工程里统一按首段速度展开（当前不支持 tempo map）。

### 20.3 自带音源

`import-midi` / `transcribe --import-to` 默认把用到的采样拷到 `<工程>/instruments/<乐器>/`；
渲染时如果工程目录下有 `instruments/`，`InstrumentRegistry` 用它当采样根，不再读全局
`samples_cache`。所以整个工程目录可以拷到别的机器直接 `render`。

合成音色是代码生成的，不需要采样；鼓组是内置合成鼓，也不需要外部文件。
