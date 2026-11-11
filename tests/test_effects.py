"""音效：注册表、七个预设效果、工程格式、编辑与渲染集成。

运行：``py -3 -m unittest discover -s tests -v``
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 注册表测试隔离：必须在 import musicgen 之前设置环境变量
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _registry_env  # noqa: F401
except ImportError:  # pragma: no cover - 某些运行方式下 tests/ 不在 sys.path
    pass

from musicgen import cli  # noqa: E402
from musicgen.core import effects as fx  # noqa: E402
from musicgen.core import project as P  # noqa: E402
from musicgen.core import trackedit as te  # noqa: E402
from musicgen.core.render import render_project  # noqa: E402

SR = 44100
DEMO = ROOT / "examples" / "demo"


def _peak(audio: np.ndarray) -> float:
    return float(np.max(np.abs(audio))) if audio.size else 0.0


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0


# ---------------------------------------------------------------------------
# 注册表与参数
# ---------------------------------------------------------------------------


class RegistryTests(unittest.TestCase):
    def test_builtin_types_in_order(self) -> None:
        self.assertEqual(
            fx.effect_keys(),
            ["stereo", "echo", "fade", "electro", "8bit", "4bit", "2bit"],
        )

    def test_resolve_key_chinese_and_alias(self) -> None:
        for key, label in (("stereo", "立体"), ("echo", "回声"),
                           ("fade", "淡入淡出"), ("electro", "电音"),
                           ("8bit", "8bit"), ("4bit", "4bit"), ("2bit", "2bit")):
            self.assertEqual(fx.resolve_effect(label), key)
            self.assertEqual(fx.resolve_effect(key.upper()), key)
        self.assertEqual(fx.resolve_effect("delay"), "echo")
        self.assertEqual(fx.resolve_effect("淡入"), "fade")
        self.assertEqual(fx.resolve_effect("8-bit"), "8bit")
        self.assertEqual(fx.resolve_effect("8位"), "8bit")
        self.assertEqual(fx.resolve_effect("2-BIT"), "2bit")
        self.assertIsNone(fx.resolve_effect("compressor"))

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(fx.EffectError):
            fx.create_effect("compressor")

    def test_defaults_and_override(self) -> None:
        echo = fx.create_effect("echo")
        self.assertEqual(echo["time"], 320)
        self.assertAlmostEqual(echo["feedback"], 0.36)
        self.assertTrue(echo["pingpong"])

        custom = fx.create_effect("echo", {"time": 500})
        self.assertEqual(custom["time"], 500)
        self.assertAlmostEqual(custom["feedback"], 0.36)  # 其余仍是默认值

    def test_param_parsing(self) -> None:
        echo = fx.effect_class("echo")
        self.assertEqual(echo.param("time").coerce("450ms"), (450, False))
        self.assertEqual(echo.param("time").coerce(120.4), (120, False))
        self.assertEqual(echo.param("feedback").coerce(1.5), (0.9, True))
        self.assertEqual(echo.param("pingpong").coerce("off"), (False, False))
        self.assertEqual(echo.param("pingpong").coerce("是"), (True, False))
        with self.assertRaises(fx.EffectError):
            echo.param("time").coerce("慢慢来")
        with self.assertRaises(fx.EffectError):
            echo.param("tone").coerce("loud")

    def test_normalize_strict_and_lenient(self) -> None:
        with self.assertRaises(fx.EffectError):
            fx.normalize_effect_params("echo", {"nope": 1}, strict=True)
        with self.assertRaises(fx.EffectError):
            fx.normalize_effect_params("echo", {"mix": 2.0}, strict=True)
        values, problems = fx.normalize_effect_params("echo", {"nope": 1, "mix": 2.0})
        self.assertEqual(values["mix"], 1.0)
        self.assertEqual(len(problems), 2)

    def test_spec_normalizes_type_and_attrs(self) -> None:
        spec = fx.EffectSpec("回声", {"mix": 0.3, "time": 450})
        self.assertEqual(spec.type, "echo")
        attrs = spec.to_attrs()
        self.assertEqual(list(attrs), ["type", "time", "mix"])  # 按参数表顺序
        self.assertEqual(attrs["time"], "450")
        self.assertIn("time=450ms", spec.summary())


# ---------------------------------------------------------------------------
# 七个效果的 DSP 行为
# ---------------------------------------------------------------------------


class StereoEffectTests(unittest.TestCase):
    def test_mono_is_untouched(self) -> None:
        mono = np.full((2, 2000), 0.5, dtype=np.float32)
        out = fx.create_effect("stereo", {"width": 1.8}).process(mono, SR)
        self.assertTrue(np.allclose(out, mono, atol=1e-6))

    def test_side_signal_is_widened(self) -> None:
        side = np.vstack([np.full(2000, 0.5, np.float32), np.full(2000, -0.5, np.float32)])
        out = fx.create_effect("stereo", {"width": 1.6}).process(side, SR)
        self.assertGreater(abs(float(out[0, 0])), 0.5)
        self.assertAlmostEqual(float(out[0, 0]), -float(out[1, 0]), places=5)

    def test_width_zero_collapses_to_mono(self) -> None:
        sig = np.vstack([np.full(500, 0.3, np.float32), np.full(500, -0.1, np.float32)])
        out = fx.create_effect("stereo", {"width": 0.0}).process(sig, SR)
        self.assertTrue(np.allclose(out[0], out[1], atol=1e-6))
        self.assertAlmostEqual(float(out[0, 0]), 0.1, places=5)

    def test_mix_zero_keeps_dry(self) -> None:
        sig = np.vstack([np.full(500, 0.3, np.float32), np.full(500, -0.1, np.float32)])
        out = fx.create_effect("stereo", {"width": 2.0, "mix": 0.0}).process(sig, SR)
        self.assertTrue(np.array_equal(out, sig))


class EchoEffectTests(unittest.TestCase):
    def _impulse(self, frames: int = SR) -> np.ndarray:
        audio = np.zeros((2, frames), dtype=np.float32)
        audio[:, 0] = 0.5
        return audio

    def test_echo_lands_on_delay_time(self) -> None:
        out = fx.create_effect(
            "echo", {"time": 100, "feedback": 0.0, "tone": 1.0, "mix": 1.0}
        ).process(self._impulse(), SR)
        delay = int(round(100 * SR / 1000))
        self.assertAlmostEqual(float(out[0, delay]), 0.5, places=3)
        self.assertLess(_peak(out[:, 1:delay - 10]), 0.01)

    def test_feedback_repeats_decay(self) -> None:
        out = fx.create_effect(
            "echo", {"time": 50, "feedback": 0.5, "tone": 1.0, "mix": 1.0}
        ).process(self._impulse(), SR)
        delay = int(round(50 * SR / 1000))
        self.assertAlmostEqual(float(out[0, delay]), 0.5, places=3)
        self.assertAlmostEqual(float(out[0, 2 * delay]), 0.25, places=3)
        self.assertAlmostEqual(float(out[0, 3 * delay]), 0.125, places=3)

    def test_mix_zero_keeps_dry(self) -> None:
        audio = self._impulse(5000)
        out = fx.create_effect("echo", {"mix": 0.0}).process(audio, SR)
        self.assertTrue(np.array_equal(out, audio))

    def test_delay_longer_than_audio_is_noop(self) -> None:
        audio = self._impulse(1000)  # 约 23ms
        out = fx.create_effect("echo", {"time": 2000, "mix": 1.0}).process(audio, SR)
        self.assertTrue(np.array_equal(out, audio))


class FadeEffectTests(unittest.TestCase):
    def test_linear_envelope(self) -> None:
        audio = np.full((2, SR), 0.5, dtype=np.float32)
        out = fx.create_effect(
            "fade", {"in": 0.1, "out": 0.2, "curve": "linear"}
        ).process(audio, SR)
        fade_in = int(0.1 * SR)
        fade_out = int(0.2 * SR)
        self.assertLess(abs(float(out[0, 0])), 1e-4)
        self.assertAlmostEqual(float(out[0, fade_in - 1]), 0.5, places=3)
        self.assertAlmostEqual(float(out[0, SR - fade_out]), 0.5, places=2)
        self.assertLess(abs(float(out[0, -1])), 1e-4)

    def test_expo_starts_quieter_than_linear(self) -> None:
        audio = np.full((2, SR), 0.5, dtype=np.float32)
        at = int(0.1 * SR)

        def level(curve: str) -> float:
            out = fx.create_effect("fade", {"in": 0.5, "out": 0.0, "curve": curve}).process(
                audio, SR
            )
            return float(out[0, at])

        self.assertLess(level("expo"), level("linear"))

    def test_silent_audio_stays_silent(self) -> None:
        audio = np.zeros((2, 1000), dtype=np.float32)
        out = fx.create_effect("fade").process(audio, SR)
        self.assertTrue(np.array_equal(out, audio))


class ElectroEffectTests(unittest.TestCase):
    def test_bit_depth_quantizes(self) -> None:
        audio = np.full((2, 1000), 0.1234, dtype=np.float32)
        out = fx.create_effect(
            "electro", {"bits": 4, "drive": 0.0, "depth": 0.0, "mix": 1.0}
        ).process(audio, SR)
        self.assertAlmostEqual(float(out[0, 0]), 0.125, places=5)

    def test_mix_zero_keeps_dry(self) -> None:
        audio = np.full((2, 500), 0.2, dtype=np.float32)
        out = fx.create_effect("electro", {"mix": 0.0}).process(audio, SR)
        self.assertTrue(np.array_equal(out, audio))

    def test_ring_mod_changes_waveform_and_stays_bounded(self) -> None:
        audio = np.full((2, 2000), 0.5, dtype=np.float32)
        out = fx.create_effect(
            "electro", {"rate": 30, "depth": 0.9, "bits": 16, "drive": 0.0, "mix": 0.85}
        ).process(audio, SR)
        self.assertFalse(np.array_equal(out, audio))
        self.assertLessEqual(_peak(out), 1.0)
        # 左右有相位差，不再是完全相同的两个声道
        self.assertGreater(float(np.max(np.abs(out[0] - out[1]))), 1e-3)


class BitcrushEffectTests(unittest.TestCase):
    """8bit / 4bit / 2bit：位深越低电平越少，降采样把波形变成阶梯。"""

    def _ramp(self) -> np.ndarray:
        return np.vstack([np.linspace(-0.99, 0.99, 4000, dtype=np.float32)] * 2)

    def _clean(self, key: str, **params) -> np.ndarray:
        """关掉降采样 / 平滑 / 前级，只看量化本身。"""
        base = {"rate": SR, "tone": 1.0, "drive": 0.0, "mix": 1.0}
        base.update(params)
        return fx.create_effect(key, base).process(self._ramp(), SR)

    def test_quantization_levels(self) -> None:
        # 步长分别是 1/128、1/8、1/2
        self.assertEqual(len(np.unique(self._clean("8bit")[0])), 257)
        self.assertEqual(len(np.unique(self._clean("4bit")[0])), 17)
        self.assertEqual(len(np.unique(self._clean("2bit")[0])), 5)

    def test_level_is_preserved(self) -> None:
        """按素材自身峰值量化再还原，插入效果不该改变轨道电平。"""
        for key in ("8bit", "4bit", "2bit"):
            out = fx.create_effect(key, {"drive": 0.0}).process(self._ramp() * 0.1, SR)
            self.assertAlmostEqual(_peak(out), 0.099, places=3, msg=key)

    def test_lower_bits_have_fewer_levels(self) -> None:
        counts = [len(np.unique(self._clean(k)[0])) for k in ("2bit", "4bit", "8bit")]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[-1])

    def test_decimation_holds_each_sample(self) -> None:
        # 44100 / 11025 = 4，每 4 个采样应完全相同
        out = self._clean("8bit", rate=11025)[0]
        block = out.reshape(-1, 4)
        self.assertTrue(np.all(block == block[:, :1]))

    def test_no_decimation_at_full_rate(self) -> None:
        out = self._clean("8bit", rate=SR)[0]
        block = out.reshape(-1, 4)
        self.assertFalse(np.all(block == block[:, :1]))

    def test_mix_zero_keeps_dry(self) -> None:
        dry = np.random.RandomState(0).randn(2, 500).astype(np.float32) * 0.3
        for key in ("8bit", "4bit", "2bit"):
            out = fx.create_effect(key, {"mix": 0.0}).process(dry, SR)
            self.assertTrue(np.array_equal(out, dry), key)

    def test_peak_stays_in_range(self) -> None:
        loud = np.full((2, 2000), 0.99, dtype=np.float32)
        for key in ("8bit", "4bit", "2bit"):
            self.assertLessEqual(_peak(fx.create_effect(key).process(loud, SR)), 1.0)

    def test_quantization_error_grows_as_bits_drop(self) -> None:
        """位深越低，量化误差越大。"""
        src = self._ramp()
        errors = [float(np.mean(np.abs(self._clean(k) - src)))
                  for k in ("8bit", "4bit", "2bit")]
        self.assertLess(errors[0], errors[1])
        self.assertLess(errors[1], errors[2])

    def test_default_sample_rates_step_down(self) -> None:
        rates = [fx.create_effect(k)["rate"] for k in ("8bit", "4bit", "2bit")]
        self.assertEqual(rates, [11025, 8000, 4000])

    def test_deterministic(self) -> None:
        for key in ("8bit", "4bit", "2bit"):
            first = fx.create_effect(key, {"rate": 8000}).process(self._ramp(), SR)
            second = fx.create_effect(key, {"rate": 8000}).process(self._ramp(), SR)
            self.assertTrue(np.array_equal(first, second), key)


# ---------------------------------------------------------------------------
# 工程格式
# ---------------------------------------------------------------------------


class ProjectEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mgfx_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, track_xml: str, project_xml: str | None = None) -> Path:
        (self.tmp / "fx.track.xml").write_text(track_xml, encoding="utf-8")
        (self.tmp / "fx.musicproj").write_text(
            project_xml
            or '<musicproj name="P" bpm="120" length="2"><track file="fx.track.xml"/></musicproj>',
            encoding="utf-8",
        )
        return self.tmp / "fx.musicproj"

    def test_parse_order_and_params(self) -> None:
        path = self._write(
            """<?xml version="1.0" encoding="UTF-8"?>
<track name="Fx" instrument="piano">
  <effect type="echo" time="450" mix="0.35"/>
  <effect type="淡入淡出" in="0.5" curve="linear"/>
  <note time="0" dur="1" pitch="C4"/>
</track>"""
        )
        loaded = P.load_project(path)
        track = loaded.tracks[0]
        self.assertEqual([spec.type for spec in track.effects], ["echo", "fade"])
        self.assertEqual(track.effects[0].params["time"], 450)
        self.assertEqual(track.effects[1].params["curve"], "linear")
        self.assertEqual(len(track.notes), 1)
        self.assertEqual(loaded.warnings, [])

    def test_unknown_type_and_param_warn(self) -> None:
        path = self._write(
            """<track name="Fx" instrument="piano">
  <effect type="echo" wrong="1"/>
  <effect type="compressor"/>
  <effect type="立体" width="9"/>
  <note time="0" dur="1" pitch="C4"/>
</track>"""
        )
        loaded = P.load_project(path)
        messages = [str(w) for w in loaded.warnings]
        self.assertTrue(any("没有参数 wrong" in m for m in messages))
        self.assertTrue(any("未知音效" in m for m in messages))
        self.assertTrue(any("超出" in m for m in messages))
        self.assertEqual([spec.type for spec in loaded.tracks[0].effects], ["echo", "stereo"])
        self.assertEqual(loaded.tracks[0].effects[1].params["width"], 2.0)

    def test_save_roundtrip_keeps_order_and_explicit_params(self) -> None:
        path = self._write(
            """<track name="Fx" instrument="piano">
  <effect type="echo" time="450" mix="0.35"/>
  <effect type="electro" bits="6"/>
  <note time="0" dur="1" pitch="C4"/>
</track>"""
        )
        track = P.load_project(path).tracks[0]
        P.save_track(track)
        text = (self.tmp / "fx.track.xml").read_text(encoding="utf-8")
        self.assertLess(text.index('<effect type="echo"'), text.index('<effect type="electro"'))
        self.assertLess(text.index("<effect"), text.index("<note"))
        self.assertIn('time="450"', text)

        again = P.load_project(path).tracks[0]
        self.assertEqual([spec.type for spec in again.effects], ["echo", "electro"])
        self.assertEqual(again.effects[0].params["time"], 450)
        self.assertEqual(again.effects[1].params["bits"], 6)
        self.assertEqual(len(again.notes), 1)

    def test_describe_lists_chain(self) -> None:
        path = self._write(
            """<track name="Fx" instrument="piano">
  <effect type="echo"/>
  <effect type="stereo"/>
  <note time="0" dur="1" pitch="C4"/>
</track>"""
        )
        text = P.describe(P.load_project(path))
        self.assertIn("音效 回声 → 立体", text)


# ---------------------------------------------------------------------------
# 渲染集成
# ---------------------------------------------------------------------------


class RenderEffectTests(unittest.TestCase):
    def _project(self, effects: list[fx.EffectSpec], length: str = "8") -> P.Project:
        project = P.Project(
            name="fx", path=Path("fx.musicproj"), bpm=120, sample_rate=SR,
            length_spec=length, tail_seconds=0.0, normalize=False,
        )
        track = P.Track(name="t", instrument="piano", path=Path("t.track.xml"),
                        effects=list(effects))
        track.notes.append(P.Note(0.0, 1.0, [60], 0.8))
        project.tracks.append(track)
        return project

    def test_fade_softens_attack(self) -> None:
        base = render_project(self._project([]))
        faded = render_project(self._project([fx.EffectSpec("fade", {"in": 0.5, "out": 0.5})]))
        window = int(0.05 * SR)
        self.assertLess(_peak(faded.audio[:, :window]), _peak(base.audio[:, :window]) * 0.5)
        self.assertLess(_rms(faded.audio), _rms(base.audio))
        self.assertEqual(faded.track_stats[0].effects, "淡入淡出")

    def test_echo_adds_tail_energy(self) -> None:
        base = render_project(self._project([]))
        echoed = render_project(
            self._project([fx.EffectSpec("echo", {"time": 300, "mix": 0.7, "feedback": 0.5})])
        )
        tail = slice(int(1.2 * SR), int(2.0 * SR))
        self.assertGreater(_rms(echoed.audio[:, tail]), _rms(base.audio[:, tail]))
        self.assertEqual(echoed.track_stats[0].effects, "回声")

    def test_unknown_effect_warns_instead_of_failing(self) -> None:
        result = render_project(self._project([fx.EffectSpec("compressor")]))
        self.assertEqual(result.notes_rendered, 1)
        self.assertTrue(any("已跳过" in w for w in result.warnings))
        self.assertEqual(result.track_stats[0].effects, "compressor")


# ---------------------------------------------------------------------------
# 编辑（保留注释）
# ---------------------------------------------------------------------------


class TrackEditEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mgfxe_"))
        self.project = self.tmp / "demo" / "demo.musicproj"
        shutil.copytree(DEMO, self.project.parent)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _track_file(self, name: str) -> Path:
        return self.project.parent / "tracks" / f"{name}.track.xml"

    def _inject_comment(self, path: Path, marker: str = "保留我") -> None:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.lstrip().startswith("<track "):
                lines.insert(index + 1, f"  <!-- {marker} -->\n")
                break
        path.write_text("".join(lines), encoding="utf-8")

    def test_add_set_remove_keeps_comment_and_order(self) -> None:
        track_path = self._track_file("melody")
        self._inject_comment(track_path)

        ref, spec = te.add_effect(self.project, "Melody", "回声", {"time": 260, "mix": 0.3})
        self.assertEqual(ref.name, "Melody")
        self.assertEqual(spec.type, "echo")
        te.add_effect(self.project, "Melody", "stereo", {"width": 1.7})
        self.assertIn("保留我", track_path.read_text(encoding="utf-8"))

        chain = P.load_project(self.project).tracks[3].effects
        self.assertEqual([s.type for s in chain], ["echo", "stereo"])

        applied = te.set_effect(self.project, "Melody", 1, feedback=0.5)
        self.assertEqual(applied[0][0], "feedback")
        self.assertEqual(
            [s.params.get("feedback") for s in P.load_project(self.project).tracks[3].effects],
            [0.5, None],
        )

        removed = te.remove_effect(self.project, "Melody", 1)
        self.assertEqual(removed["type"], "echo")
        remaining = P.load_project(self.project).tracks[3].effects
        self.assertEqual([s.type for s in remaining], ["stereo"])
        self.assertIn("保留我", track_path.read_text(encoding="utf-8"))  # 注释仍在
        self.assertIn("<note", track_path.read_text(encoding="utf-8"))   # 音符没被动

    def test_effect_is_inserted_before_notes(self) -> None:
        te.add_effect(self.project, "Melody", "fade", {"in": 0.2})
        text = self._track_file("melody").read_text(encoding="utf-8")
        self.assertLess(text.index("<effect"), text.index("<note"))

    def test_rejects_unknown_type_and_bad_value(self) -> None:
        with self.assertRaises(te.TrackEditError):
            te.add_effect(self.project, "Melody", "compressor")
        with self.assertRaises(te.TrackEditError):
            te.add_effect(self.project, "Melody", "echo", {"mix": 2.0})
        with self.assertRaises(te.TrackEditError):
            te.add_effect(self.project, "Melody", "echo", {"nope": 1})

    def test_set_removes_param_with_none(self) -> None:
        te.add_effect(self.project, "Melody", "echo", {"time": 300})
        te.set_effect(self.project, "Melody", 1, time=None)
        spec = P.load_project(self.project).tracks[3].effects[0]
        self.assertNotIn("time", spec.params)


class CliEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mgfxc_"))
        self.project = self.tmp / "demo" / "demo.musicproj"
        shutil.copytree(DEMO, self.project.parent)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_effect_flow(self) -> None:
        code, out, _ = self.run_cli("effect", "types")
        self.assertEqual(code, 0)
        for label in ("立体", "回声", "淡入淡出", "电音"):
            self.assertIn(label, out)

        code, out, _ = self.run_cli(
            "effect", "add", str(self.project), "--track", "Melody",
            "--type", "回声", "-p", "time=260", "-p", "mix=0.3",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("回声", out)

        code, out, _ = self.run_cli("effect", "list", str(self.project), "--track", "Melody")
        self.assertEqual(code, 0)
        self.assertIn("time=260ms", out)

        code, out, _ = self.run_cli(
            "effect", "set", str(self.project), "--track", "Melody",
            "--index", "1", "-p", "feedback=0.5",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("feedback", out)
        self.assertEqual(
            P.load_project(self.project).tracks[3].effects[0].params["feedback"], 0.5
        )

        code, out, _ = self.run_cli(
            "effect", "remove", str(self.project), "--track", "Melody", "--index", "1"
        )
        self.assertEqual(code, 0)
        self.assertIn("已删除", out)
        self.assertEqual(P.load_project(self.project).tracks[3].effects, [])

    def test_cli_rejects_unknown_type(self) -> None:
        code, _, err = self.run_cli(
            "effect", "add", str(self.project), "--track", "Melody", "--type", "compressor"
        )
        self.assertEqual(code, 1)
        self.assertIn("未知音效", err)


if __name__ == "__main__":
    unittest.main()
