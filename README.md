# MusicGen

用 `*.musicproj`（XML）工程文件渲染出乐器纯音乐 WAV。带命令行与桌面图形界面。

![界面](docs/screenshot.png)

## 特性

- **工程即 XML**：一个 `*.musicproj` + 每条音轨一个 `*.track.xml`，可读、可手改、好做版本管理
- **按音轨控制**：乐器、速度（可逐轨覆盖）、音量、声像、混响、是否循环、循环长度/次数、移调
- **真实钢琴音源**：FluidR3 GM 88 键逐键采样（开源），多镜像并发下载、本地缓存；缺失时自动回退内置合成器
- **渲染质量**：力度→音量+亮度、松键释放、合成 IR 混响、等功率声像、峰值归一化、16/24/32bit 与 32bit float 输出
- **两套入口**：`python -m musicgen render` 与 Tkinter 桌面界面（卷帘预览、参数编辑回写、波形图）

## 快速开始

```bat
py -3 -m pip install -r requirements.txt
py -3 -m musicgen samples fetch                     # 下载钢琴音源（约 2.6MB）
py -3 -m musicgen render examples\demo\demo.musicproj -o demo.wav
py -3 -m musicgen gui examples\demo\demo.musicproj  # 或者双击 MusicGen.bat
```

当前机器上默认的 `python` 没有 tkinter，界面请用 `py -3`（3.14）。

## 工程文件示例

```xml
<!-- demo.musicproj -->
<musicproj name="Nocturne in A minor" bpm="84" sampleRate="44100" length="auto">
  <track file="tracks/chords.track.xml"/>
  <track file="tracks/ostinato.track.xml" gain="-4dB"/>
</musicproj>
```

```xml
<!-- tracks/ostinato.track.xml：4 拍一循环，平铺到整首 -->
<track name="Ostinato" instrument="piano" loop="true" loopLen="4"
       gain="-4dB" pan="-0.28" reverb="0.18">
  <note time="0"   dur="0.46" pitch="A4" vel="0.47"/>
  <note time="0.5" dur="0.46" pitch="C5" vel="0.42"/>
  <note time="1"   dur="0.46" pitch="E5" vel="0.42"/>
</track>
```

时值统一用「拍」，支持 `1/4` 与 `0.5` 两种写法；`pitch="C4,E4,G4"` 直接写和弦。

## 命令

| 命令 | 作用 |
| --- | --- |
| `render <proj> [-o out.wav]` | 渲染 WAV（`--bits 16/24/32`、`--float32`、`--no-normalize`） |
| `validate <proj>` | 校验工程，列出全部错误与警告 |
| `info <proj>` | 工程概要 |
| `init <目录>` | 生成示例工程 |
| `samples fetch/list` | 下载/查看音源 |
| `gui [proj]` | 启动界面 |

## 文档与测试

- 格式规范、渲染管线细节、扩展新乐器的方法：[docs/项目文档.md](docs/项目文档.md)
- 测试：`py -3 -m unittest discover -s tests -v`
