"""内置预览播放器：把渲染结果直接送到声卡，支持播放/暂停/停止/定位。

用 ``sounddevice``（PortAudio）做输出；回调里只读写数组与计数，不碰任何界面对象，
界面通过定时轮询 :attr:`AudioPlayer.position` 来画播放头。
没有声卡/驱动时抛 :class:`PlaybackUnavailable`，由界面退回系统播放器。
"""

from __future__ import annotations

import threading

import numpy as np

try:  # sounddevice 是可选依赖，缺失时只影响内置预览
    import sounddevice as sd

    HAS_OUTPUT = True
    IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001
    sd = None  # type: ignore[assignment]
    HAS_OUTPUT = False
    IMPORT_ERROR = str(exc)


class PlaybackUnavailable(RuntimeError):
    """没有可用的音频输出设备。"""


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    channels, frames = audio.shape
    out_frames = max(1, int(round(frames * dst_rate / src_rate)))
    positions = np.linspace(0, frames - 1, out_frames)
    index = np.floor(positions).astype(np.int64)
    frac = (positions - index).astype(np.float32)
    nxt = np.minimum(index + 1, frames - 1)
    out = np.empty((channels, out_frames), dtype=np.float32)
    for ch in range(channels):
        a, b = audio[ch, index], audio[ch, nxt]
        out[ch] = a + (b - a) * frac
    return out


class AudioPlayer:
    """单实例播放器；线程安全（界面线程与音频回调线程共用）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._audio: np.ndarray | None = None   # (2, N) float32，已按设备采样率重采样
        self._source_rate = 44100
        self._device_rate = 44100
        self._pos = 0                            # 帧
        self._stream = None
        self._playing = False
        self.volume = 0.9

    # ---- 状态 ---------------------------------------------------------
    @property
    def available(self) -> bool:
        return HAS_OUTPUT

    @property
    def has_audio(self) -> bool:
        return self._audio is not None and self._audio.size > 0

    @property
    def duration(self) -> float:
        if not self.has_audio:
            return 0.0
        return self._audio.shape[1] / self._device_rate

    @property
    def position(self) -> float:
        with self._lock:
            return min(self._pos / self._device_rate, self.duration)

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._playing

    # ---- 装载 ---------------------------------------------------------
    def load(self, audio: np.ndarray, sample_rate: int) -> None:
        """载入一段立体声 float32 音频（会按输出设备采样率重采样）。"""
        self.pause()
        if HAS_OUTPUT:
            try:
                device_rate = int(sd.query_devices(kind="output")["default_samplerate"])
            except Exception:  # noqa: BLE001 - 查询失败就按源采样率
                device_rate = sample_rate
        else:
            device_rate = sample_rate
        data = np.ascontiguousarray(audio, dtype=np.float32)
        if data.ndim == 1:
            data = np.vstack([data, data])
        with self._lock:
            self._device_rate = device_rate or sample_rate
            self._source_rate = sample_rate
            self._audio = _resample(data, sample_rate, self._device_rate)
            self._pos = 0

    def unload(self) -> None:
        self.pause()
        with self._lock:
            self._audio = None
            self._pos = 0

    # ---- 传输控制 -----------------------------------------------------
    def play(self) -> None:
        with self._lock:
            if not self.has_audio:
                return
            if self._pos >= self._audio.shape[1] - 1:
                self._pos = 0
            if self._playing and self._stream is not None:
                return
            if not HAS_OUTPUT:
                raise PlaybackUnavailable(f"没有可用的音频输出（{IMPORT_ERROR or 'sounddevice 未安装'}）")
            try:
                self._stream = sd.OutputStream(
                    samplerate=self._device_rate,
                    channels=2,
                    dtype="float32",
                    blocksize=1024,
                    callback=self._callback,
                    finished_callback=None,
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001 - 设备被占用/无驱动
                self._stream = None
                self._playing = False
                raise PlaybackUnavailable(f"无法打开音频设备：{exc}") from exc
            self._playing = True

    def pause(self) -> None:
        stream = self._stream
        with self._lock:
            self._stream = None
            self._playing = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - 关闭失败无所谓
                pass

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self.pause()
        with self._lock:
            self._pos = 0

    def seek(self, seconds: float) -> None:
        with self._lock:
            if not self.has_audio:
                return
            frames = int(max(0.0, min(seconds, self.duration)) * self._device_rate)
            self._pos = min(frames, self._audio.shape[1] - 1)
            was_playing = self._playing
        if was_playing:
            self.pause()
            self.play()

    # ---- 音频回调（不要在这里碰 Tk） -----------------------------------
    def _callback(self, outdata, frames, _time_info, status) -> None:  # noqa: ANN001
        with self._lock:
            if self._audio is None:
                outdata[:] = 0.0
                raise sd.CallbackStop  # type: ignore[union-attr]
            chunk = self._audio[:, self._pos : self._pos + frames]
            count = chunk.shape[1]
            outdata[:count, :] = (chunk * self.volume).T
            self._pos += count
            if count < frames:
                outdata[count:, :] = 0.0
                self._pos = self._audio.shape[1]
                self._playing = False
                raise sd.CallbackStop  # type: ignore[union-attr]
