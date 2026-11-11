# MusicGen 命令行（CLI）手册

> 与当前版本同步维护。`python -m musicgen --version` 可确认版本。
> 工程格式、渲染管线与扩展方法见 [项目文档](项目文档.md)；本文只讲命令怎么用、参数什么含义、出错怎么办。

**目录**：[调用方式](#1-三种调用方式) · [命令总览](#2-命令总览) · [退出码与环境变量](#3-退出码与环境变量) ·
[render](#4-render--渲染成-wav) · [validate](#5-validate--只校验) · [info](#6-info--工程概要) ·
[init](#7-init--生成新工程) · [samples fetch](#8-samples-fetch--下载音源) · [samples list](#9-samples-list--查看音源状态) ·
[gui](#10-gui--图形界面) · [track / note](#11-track--note--轨道与音符编辑) · [instrument](#12-instrument--乐器注册表) ·
[effect](#13-effect--音轨效果链) · [工作流](#14-常见工作流) · [报错对照](#15-提示信息对照表) ·
[CLI 与 GUI](#16-与-gui-的能力对照) · [速查卡](#17-速查卡)

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
| [`effect`](#13-effect--音轨效果链) | 查看音效类型、增删改音轨效果链 | 不需要 |

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
`track`/`note` 参数错误或找不到轨道、GUI 起不来 |
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
python -m musicgen samples fetch                        :: 六件采样全下（约 8MB）
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

乐器清单**不再写死在代码里**：默认 7 件（6 件 FluidR3 GM 采样 + 内置鼓组）作为
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
| `instrument reset` | 恢复内置默认 7 件（自定义条目会丢失） |

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
* 自定义乐器的增益写在注册表里；内置 6 件仍保留代码中的校准值作为兜底。

---

## 13. `effect` — 音轨效果链

效果写在音轨文件的 `<effect>` 里，按出现顺序串联，处理位置在**声像之后、汇入总线之前**。
DSP 细节与工程格式见[项目文档 §6.4](项目文档.md#64-效果链-effect)。

### 13.1 命令一览

| 命令 | 作用 |
| --- | --- |
| `effect types` | 列出已注册的 4 个音效，以及每个参数的范围与默认值 |
| `effect list <proj> [--track X]` | 查看效果链；不给 `--track` 就列出全部音轨 |
| `effect add <proj> --track X --type T [-p 键=值 …]` | 给音轨追加一个音效 |
| `effect set <proj> --track X --index N -p 键=值 …` | 改第 N 个音效（1-based）；值写 `none` 表示删掉该参数 |
| `effect remove <proj> --track X --index N` | 删除第 N 个音效 |

`--track` 与 `track` / `note` 命令一致：1-based 序号、名称、文件名都能定位，支持部分匹配。

### 13.2 七个预设效果

| 类型 | 中文 | 参数（括号里是默认值） |
| --- | --- | --- |
| `stereo` | 立体 | `width` 0–2（1.6，M/S 加宽）· `haas` 0–30ms（0，右耳微延迟）· `mix` 0–1（1） |
| `echo` | 回声 | `time` 20–2000ms（320）· `feedback` 0–0.9（0.36）· `tone` 0–1（0.5）· `pingpong` 开/关（开）· `mix` 0–1（0.3） |
| `fade` | 淡入淡出 | `in` 0–10s（0.8）· `out` 0–10s（1.5）· `curve` linear/expo（expo） |
| `electro` | 电音 | `rate` 0.5–200Hz（30）· `depth` 0–1（0.8）· `drive` 0–1（0.35）· `bits` 3–16（8）· `mix` 0–1（0.85） |
| `8bit` | 8bit | `rate` 500–44100Hz（11025）· `tone` 0–1（0.75）· `drive` 0–1（0）· `mix` 0–1（1） |
| `4bit` | 4bit | `rate` 500–44100Hz（8000）· `tone` 0–1（0.6）· `drive` 0–1（0）· `mix` 0–1（1） |
| `2bit` | 2bit | `rate` 500–44100Hz（4000）· `tone` 0–1（0.45）· `drive` 0–1（0）· `mix` 0–1（1） |

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

### 13.4 注意事项

* **顺序有意义**：`回声 → 立体` 与 `立体 → 回声` 听感不同，`effect list` 按 XML 里的顺序显示。
* **未知类型 / 未知参数不中断渲染**：`validate` 只给警告，该条效果或该参数被跳过；`effect add/set` 是严格模式，直接报错（退出码 `1`）。
* **越界值**：手写 XML 时夹到范围内并给警告；用 `effect add/set` 时直接拒绝。
* **回声尾巴受工程长度限制**：超出 `length` 的部分会被截掉；需要更长余韵就加 `tail="3s"` 或加大 `length`。
* **淡入淡出按「实际发声区间」算**：2 秒的轨在 60 秒工程里也只淡它自己的头尾，不会淡一整分钟。

---

## 14. 常见工作流

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

## 15. 提示信息对照表

### 15.1 工程解析（退出码 2）

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

### 15.2 忽略即警告（退出码 0，但值没生效）

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

### 15.3 渲染警告（stderr，前缀 `!`）

| 提示 | 含义 | 处理 |
| --- | --- | --- |
| `未注册的乐器 'x'，使用内置合成器。已支持: …` | 乐器名不在注册表 | 改成已支持的一件（含 `drums`），或按[项目文档 §8.2](项目文档.md#82-新增一个乐器)扩展 |
| `大提琴的采样尚未下载，本次使用内置合成器。运行 …` | 这件乐器一次都没下过 | `python -m musicgen samples fetch --instrument cello` |
| `乐器 'x' 的采样音域为 …，B1 超出范围，该音改用内置合成器` | 音高超出采样范围 | 移调 / 换音区 |
| `没有可渲染的音轨（全部静音或为空）` | 全静音、全空轨，或独奏轨为空 | 检查 `mute` / `solo` |
| `峰值 +2.3dBFS 已超出 0dB，已做削波保护` | 关了归一化又太响 | 降 `masterGain`，或去掉 `--no-normalize` |
| `音效 回声（echo）在音轨「Melody」上处理失败，该效果已跳过：…` | 效果内部异常（参数异常或注册表被改动） | 修参数或删掉该效果；其余音轨与效果照常渲染 |

---

## 16. 与 GUI 的能力对照

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
| 批量 / 自动化 | 适合 | 不适合 |

一句话：**批量、CI、无桌面环境用 CLI；调音、试听、逐个音符微调用 GUI**。两者读写同一套 XML，可以混着用。

---

## 17. 速查卡

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

:: 界面
py -3 -m musicgen gui songs\Song\Song.musicproj
```
