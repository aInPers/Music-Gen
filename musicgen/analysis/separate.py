"""Demucs 分离封装。

只调用 ``demucs.api.Separator.separate_tensor``：音频由本项目自己用
``soundfile`` 解码后再喂张量，因此**不需要 ffmpeg/ffprobe**（Demucs 默认的
``separate_audio_file`` 走 ffmpeg，Windows 上很容易缺）。

模型权重从 HuggingFace 下载，国内网络可先设置镜像：

    set HF_ENDPOINT=https://hf-mirror.com
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import Audio

DEFAULT_MODEL = "htdemucs_6s"

#: 可用模型 -> 会产出哪些分轨
MODEL_STEMS: dict[str, tuple[str, ...]] = {
    "htdemucs_6s": ("drums", "bass", "other", "vocals", "piano", "guitar"),
    "htdemucs": ("drums", "bass", "other", "vocals"),
    "htdemucs_ft": ("drums", "bass", "other", "vocals"),
}

#: 分轨中文名（报告与音轨名用）
STEM_LABELS = {
    "vocals": "人声",
    "drums": "鼓组",
    "bass": "贝斯",
    "piano": "钢琴",
    "guitar": "吉他",
    "other": "其他",
    "mix": "混音",
}

ProgressFn = Callable[[float, str], None]


class SeparationError(RuntimeError):
    """Demucs 不可用 / 模型下载失败 / 分离过程出错。"""


@dataclass
class StemSet:
    stems: dict[str, np.ndarray]  # 名称 -> (2, N) float32
    sample_rate: int
    model: str

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.stems)

    def duration(self) -> float:
        if not self.stems:
            return 0.0
        first = next(iter(self.stems.values()))
        return first.shape[1] / float(self.sample_rate)


def availability() -> tuple[bool, str]:
    """检查 torch / demucs 是否装好。"""
    try:
        import torch  # noqa: F401
        from demucs.api import Separator  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 缺依赖或版本不兼容都算不可用
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def default_device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def stem_names(model: str) -> tuple[str, ...]:
    return MODEL_STEMS.get(model, MODEL_STEMS[DEFAULT_MODEL])


def separate(
    audio: Audio,
    model: str = DEFAULT_MODEL,
    device: str = "auto",
    progress: ProgressFn | None = None,
    span: tuple[float, float] = (0.04, 0.55),
) -> StemSet:
    """把整段音频分离成若干分轨。"""
    ok, reason = availability()
    if not ok:
        raise SeparationError(
            "没有可用的 Demucs（torch / demucs 未安装或版本不兼容）。\n"
            "  安装：py -3 -m pip install -r requirements.txt\n"
            f"  底层错误：{reason}"
        )

    import torch
    from demucs.api import Separator

    if model not in MODEL_STEMS:
        raise SeparationError(
            f"未知分离模型 {model!r}，可用：{'、'.join(MODEL_STEMS)}"
        )
    device = default_device() if device in ("auto", "", None) else str(device)
    if device.startswith("cuda"):
        try:
            if not torch.cuda.is_available():
                device = "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"

    low, high = span
    last = [low]

    def report(fraction: float, message: str) -> None:
        if not progress:
            return
        value = low + (high - low) * max(0.0, min(1.0, fraction))
        # Demucs 的分段回调会从 0 重新开始，进度只能单调往前走
        last[0] = max(last[0], value)
        progress(last[0], message)

    length = int(audio.samples.shape[1])

    def on_progress(payload: dict) -> None:
        if not progress:
            return
        total = max(1, int(payload.get("audio_length") or length))
        offset = int(payload.get("segment_offset") or 0)
        report(min(0.98, offset / total), "正在分离人声与各件乐器…")

    report(0.0, f"加载分离模型 {model}（{device}，首次运行需下载权重）…")
    try:
        separator = Separator(model=model, device=device, shifts=1, overlap=0.25,
                              split=True, progress=False, callback=on_progress)
    except Exception as exc:  # noqa: BLE001 - 多半是权重下载失败
        raise SeparationError(
            "分离模型加载失败（首次运行需要下载权重，约 300MB）。\n"
            "  国内网络可先设置镜像后重试：set HF_ENDPOINT=https://hf-mirror.com\n"
            f"  底层错误：{type(exc).__name__}: {exc}"
        ) from exc

    report(0.02, "正在分离人声与各件乐器…")

    tensor = torch.from_numpy(np.ascontiguousarray(audio.samples, dtype=np.float32))
    try:
        _origin, separated = separator.separate_tensor(tensor, sr=int(audio.sample_rate))
    except Exception as exc:  # noqa: BLE001
        raise SeparationError(f"分离过程失败：{type(exc).__name__}: {exc}") from exc

    stems: dict[str, np.ndarray] = {}
    for name, data in separated.items():
        array = data.detach().cpu().numpy().astype(np.float32, copy=False)
        if array.ndim == 1:
            array = np.vstack([array, array])
        elif array.shape[0] == 1:
            array = np.vstack([array, array])
        stems[name] = np.ascontiguousarray(array[:, :length])

    # 模型可能只给 4 轨；按模型声明补齐顺序，且过滤空轨
    ordered = {name: stems[name] for name in stem_names(model) if name in stems}
    for name, array in stems.items():
        ordered.setdefault(name, array)

    report(1.0, "分离完成")
    return StemSet(stems=ordered, sample_rate=int(separator.samplerate), model=model)


def cache_stems(stem_set: StemSet, directory: Path) -> list[Path]:
    """把分轨写成 WAV（24bit），返回写出的文件。"""
    from .audio import write_wav

    directory = Path(directory)
    written: list[Path] = []
    for name, samples in stem_set.stems.items():
        written.append(write_wav(directory / f"{name}.wav", samples, stem_set.sample_rate))
    return written
