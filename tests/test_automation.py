"""自动化曲线：贝塞尔求值、包络、解析与写回。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen.core.automation import (  # noqa: E402
    Curve,
    CurvePoint,
    apply_gain_curve,
    apply_pan_curve,
    curve_envelope,
    parse_points,
    parse_handle,
)


class CurveEvalTests(unittest.TestCase):
    def test_endpoints_and_clamp(self) -> None:
        curve = Curve("gain", parse_points("0:0; 8:-6; 16:0"))
        self.assertAlmostEqual(curve.value_at(-4), 0.0)
        self.assertAlmostEqual(curve.value_at(0), 0.0)
        self.assertAlmostEqual(curve.value_at(16), 0.0)
        self.assertAlmostEqual(curve.value_at(100), 0.0)

    def test_midpoint_of_symmetric_curve(self) -> None:
        curve = Curve("gain", parse_points("0:0; 8:-6; 16:0"))
        # 对称曲线的最低点应在 8 拍处，且正好是 -6
        self.assertAlmostEqual(curve.value_at(8), -6.0, places=3)
        # 4 拍处应低于 0 且高于 -6
        mid = curve.value_at(4)
        self.assertTrue(-6.0 < mid < 0.0)

    def test_linear_mode_is_straight(self) -> None:
        curve = Curve("gain", parse_points("0:0; 8:-8"), mode="linear")
        self.assertAlmostEqual(curve.value_at(4), -4.0, places=6)
        self.assertAlmostEqual(curve.value_at(2), -2.0, places=6)

    def test_step_mode_holds(self) -> None:
        curve = Curve("gain", parse_points("0:0; 8:-8; 16:0"), mode="step")
        self.assertAlmostEqual(curve.value_at(7.9), 0.0)
        self.assertAlmostEqual(curve.value_at(8), -8.0)
        self.assertAlmostEqual(curve.value_at(15), -8.0)

    def test_zero_tension_equals_linear(self) -> None:
        curve = Curve("gain", parse_points("0:0; 4:-6; 8:0"), tension=0.0)
        self.assertAlmostEqual(curve.value_at(2), -3.0, places=6)

    def test_bezier_differs_from_linear(self) -> None:
        """贝塞尔曲线不走直线，但关键点必须精确落位。"""
        bezier = Curve("gain", parse_points("0:0; 4:-6; 8:0"))
        linear = Curve("gain", parse_points("0:0; 4:-6; 8:0"), mode="linear")
        self.assertNotAlmostEqual(bezier.value_at(2), linear.value_at(2), places=2)
        self.assertAlmostEqual(bezier.value_at(4), -6.0, places=3)
        self.assertAlmostEqual(bezier.value_at(0), 0.0, places=3)
        # 过冲不应超过控制点的幅度太多
        self.assertLess(abs(bezier.value_at(3)), 8.0)

    def test_explicit_handles(self) -> None:
        pts = [
            CurvePoint(0, 0, out_dx=0.5, out_dy=3.0),
            CurvePoint(4, 0, in_dx=-0.5, in_dy=3.0),
        ]
        curve = Curve("gain", pts)
        # 两个控制点都把曲线往上拉，中点应高于直线的 0
        self.assertGreater(curve.value_at(2), 0.3)

    def test_sample_matches_value_at(self) -> None:
        curve = Curve("gain", parse_points("0:0; 3:-6; 7:2; 12:0"))
        beats = np.array([0.0, 0.7, 2.9, 3.0, 5.5, 7.0, 11.9, 12.0])
        sampled = curve.sample(beats)
        for beat, got in zip(beats, sampled):
            self.assertAlmostEqual(float(got), curve.value_at(float(beat)), places=4)

    def test_single_point_curve_is_flat(self) -> None:
        curve = Curve("gain", [CurvePoint(4, -3)])
        self.assertTrue(curve.is_empty())
        self.assertAlmostEqual(curve.value_at(0), -3.0)
        self.assertAlmostEqual(curve.value_at(99), -3.0)

    def test_points_are_sorted(self) -> None:
        curve = Curve("gain", parse_points("8:-6; 0:0"))
        self.assertEqual([p.beat for p in curve.points], [0.0, 8.0])


class ParseTests(unittest.TestCase):
    def test_parse_variants(self) -> None:
        self.assertEqual(len(parse_points("0:0; 8:-6")), 2)
        self.assertEqual(len(parse_points("0:0, 8:-6, 16:0")), 3)
        self.assertEqual(len(parse_points("0@0;4@2")), 2)

    def test_bad_point_raises(self) -> None:
        with self.assertRaises(Exception):
            parse_points("0=0;4=2")

    def test_handle(self) -> None:
        self.assertEqual(parse_handle("2,-1.5"), (2.0, -1.5))
        self.assertEqual(parse_handle(None), (None, None))


class EnvelopeTests(unittest.TestCase):
    def test_envelope_length_and_shape(self) -> None:
        curve = Curve("gain", parse_points("0:0; 4:6; 8:0"))
        env = curve_envelope(curve, 44100 * 4, 44100, bpm=120)
        self.assertEqual(env.shape[0], 44100 * 4)
        # 120BPM 下 8 拍 = 4 秒，2 秒处是 4 拍，应为峰值 6dB
        peak_index = int(2.0 * 44100)
        self.assertAlmostEqual(float(env[peak_index]), 6.0, delta=0.2)
        self.assertAlmostEqual(float(env[0]), 0.0, delta=0.05)

    def test_gain_curve_scales_audio(self) -> None:
        buffer = np.ones((2, 44100 * 4), dtype=np.float32)
        curve = Curve("gain", parse_points("0:0; 4:-6; 8:0"))
        out = apply_gain_curve(buffer, curve, 44100, bpm=120)
        self.assertAlmostEqual(float(out[0, 0]), 1.0, delta=0.02)
        # 2 秒 = 4 拍 = -6dB ≈ 0.5
        self.assertAlmostEqual(float(out[0, 2 * 44100]), 0.5, delta=0.03)

    def test_pan_curve_moves_side(self) -> None:
        buffer = np.ones((2, 44100 * 4), dtype=np.float32)
        curve = Curve("pan", parse_points("0:-1; 8:1"))
        out = apply_pan_curve(buffer, curve, 44100, bpm=120)
        # 开头 pan=-1 全左，结尾 pan=+1 全右
        self.assertGreater(float(out[0, 0]), float(out[1, 0]))
        self.assertGreater(float(out[1, -1]), float(out[0, -1]))


if __name__ == "__main__":
    unittest.main()
