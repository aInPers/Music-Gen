"""WAV 写出。

标准库 ``wave`` 不支持 24bit / 浮点，所以这里手写 RIFF 头，支持 16 / 24 / 32bit
整数与 32bit 浮点，并对低比特位加 TPDF 抖动。
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

SUPPORTED_BITS = (16, 24, 32)
FLOAT_FORMAT = "float32"


def _dither(samples: np.ndarray, bits: int, rng: np.random.Generator) -> np.ndarray:
    """TPDF 抖动：量化前加入 1 LSB 峰峰值的三角分布噪声。"""
    if bits >= 32:
        return samples
    lsb = 1.0 / (2 ** (bits - 1))
    noise = (rng.random(samples.shape, dtype=np.float32) - rng.random(samples.shape, dtype=np.float32))
    return samples + noise * lsb * 0.5


def encode_pcm(audio: np.ndarray, bit_depth: int = 24, dither: bool = True) -> tuple[bytes, int]:
    """把 (通道, 样本) 的 float32 数组编码成交错小端字节流。

    返回 (数据字节, wavFormatTag)。
    """
    if audio.ndim != 2:
        raise ValueError("audio 必须是 (通道, 样本) 的二维数组")
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    flat = interleaved.reshape(-1)
    flat = np.clip(flat, -1.0, 1.0)

    rng = np.random.default_rng(19970317)
    if bit_depth == FLOAT_FORMAT or bit_depth == "f32":
        return flat.astype("<f4").tobytes(), 3
    if bit_depth not in SUPPORTED_BITS:
        raise ValueError(f"不支持的位深: {bit_depth}")

    if dither:
        flat = _dither(flat, int(bit_depth), rng)
        flat = np.clip(flat, -1.0, 1.0)

    if bit_depth == 16:
        pcm = np.round(flat * 32767.0).astype("<i2")
        return pcm.tobytes(), 1
    if bit_depth == 24:
        pcm = np.round(flat * 8388607.0).astype("<i4")
        raw = pcm.view(np.uint8).reshape(-1, 4)
        return np.ascontiguousarray(raw[:, :3]).tobytes(), 1
    pcm = np.round(flat * 2147483647.0).astype("<i4")
    return pcm.tobytes(), 1


def write_wav(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    bit_depth: int = 24,
    dither: bool = True,
) -> Path:
    """写出立体声 WAV 文件，返回最终路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    channels = int(audio.shape[0])
    payload, format_tag = encode_pcm(audio, bit_depth, dither)
    if format_tag == 3:
        bits = 32
    else:
        bits = int(bit_depth)
    block_align = channels * bits // 8

    header = bytearray()
    header += b"RIFF"
    header += struct.pack("<I", 36 + len(payload))
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<IHHIIHH", 16, format_tag, channels, sample_rate,
                          sample_rate * block_align, block_align, bits)
    header += b"data"
    header += struct.pack("<I", len(payload))

    with open(target, "wb") as handle:
        handle.write(header)
        handle.write(payload)
    return target
