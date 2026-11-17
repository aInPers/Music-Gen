"""混音级效果器：滤波 / 调制 / 磁带 / 限制 / 门限 / 混响。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen.core import effects as fx  # noqa: E402

SR = 44100


def tone(seconds: float = 1.0, freq: float = 220.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float32) / SR
    wave = amp * np.sin(2.0 * np.pi * freq * t)
    return np.vstack([wave, wave]).astype(np.float32)


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0


NEW_EFFECTS = ("filter", "tremolo", "autopan", "flanger", "phaser",
               "tape", "limiter", "gate", "reverb")


class NewEffectBasics(unittest.TestCase):
    def test_all_registered(self) -> None:
        for key in NEW_EFFECTS:
            self.assertIn(key, fx.EFFECT_REGISTRY)
            self.assertTrue(fx.EFFECT_REGISTRY[key].LABEL)

    def test_output_shape_and_finiteness(self) -> None:
        audio = tone()
        for key in NEW_EFFECTS:
            with self.subTest(effect=key):
                out = fx.create_effect(key).process(audio, SR)
                self.assertEqual(out.shape, audio.shape)
                self.assertEqual(out.dtype, np.float32)
                self.assertTrue(np.isfinite(out).all())

    def test_deterministic(self) -> None:
        audio = tone(0.4)
        for key in NEW_EFFECTS:
            with self.subTest(effect=key):
                first = fx.create_effect(key).process(audio, SR)
                second = fx.create_effect(key).process(audio, SR)
                np.testing.assert_array_equal(first, second)

    def test_empty_input_is_safe(self) -> None:
        empty = np.zeros((2, 0), dtype=np.float32)
        for key in NEW_EFFECTS:
            with self.subTest(effect=key):
                out = fx.create_effect(key).process(empty, SR)
                self.assertEqual(out.shape, (2, 0))


class NewEffectBehaviour(unittest.TestCase):
    def test_tremolo_modulates_level(self) -> None:
        audio = tone()
        out = fx.create_effect("tremolo", {"depth": 1.0, "rate": 4.0}).process(audio, SR)
        self.assertLess(rms(out), rms(audio) * 0.85)
        # 调制应该是周期性的：首尾不应完全一致
        self.assertFalse(np.allclose(out[0, :100], out[0, 1000:1100]))

    def test_autopan_moves_energy_between_channels(self) -> None:
        audio = tone(1.0)
        out = fx.create_effect("autopan", {"depth": 1.0, "rate": 1.0}).process(audio, SR)
        early = np.abs(out[:, : int(0.2 * SR)]).mean(axis=1)
        late = np.abs(out[:, int(0.7 * SR) : int(0.9 * SR)]).mean(axis=1)
        # 左右能量此消彼长
        self.assertGreater(abs(late[0] - early[0]) + abs(late[1] - early[1]), 0.05)

    def test_filter_attenuates_high_frequencies(self) -> None:
        high = tone(0.5, freq=8000.0)
        out = fx.create_effect("filter", {"mode": "lowpass", "freq": 500.0}).process(high, SR)
        self.assertLess(rms(out), rms(high) * 0.5)

    def test_highpass_attenuates_low_frequencies(self) -> None:
        low = tone(0.5, freq=80.0)
        out = fx.create_effect("filter", {"mode": "highpass", "freq": 2000.0}).process(low, SR)
        self.assertLess(rms(out), rms(low) * 0.5)

    def test_limiter_respects_ceiling(self) -> None:
        loud = tone(0.5, amp=2.5)
        out = fx.create_effect("limiter", {"ceiling": -3.0}).process(loud, SR)
        ceiling = 10 ** (-3.0 / 20.0)
        self.assertLessEqual(float(np.max(np.abs(out))), ceiling + 1e-4)

    def test_gate_closes_quiet_tail(self) -> None:
        audio = tone(1.0, amp=0.002)  # 约 -54dB，低于默认 -42dB 阈值
        out = fx.create_effect("gate", {"threshold": -40.0}).process(audio, SR)
        self.assertLess(rms(out), rms(audio) * 0.4)

    def test_reverb_adds_tail(self) -> None:
        audio = tone(0.25)
        padded = np.zeros((2, SR), dtype=np.float32)
        padded[:, : audio.shape[1]] = audio
        out = fx.create_effect("reverb", {"mix": 0.8, "decay": 2.5}).process(padded, SR)
        tail = out[:, int(0.6 * SR): int(0.9 * SR)]
        self.assertGreater(rms(tail), 1e-4)

    def test_flanger_mix_zero_returns_dry(self) -> None:
        audio = tone(0.3)
        out = fx.create_effect("flanger", {"mix": 0.0}).process(audio, SR)
        np.testing.assert_allclose(out, audio, atol=1e-6)

    def test_tape_keeps_level_close(self) -> None:
        audio = tone(0.5)
        out = fx.create_effect("tape", {"drive": 0.4, "wow": 0.0, "flutter": 0.0}).process(audio, SR)
        ratio = rms(out) / rms(audio)
        self.assertGreater(ratio, 0.5)
        self.assertLess(ratio, 1.5)


if __name__ == "__main__":
    unittest.main()
