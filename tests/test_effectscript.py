"""音效脚本（.fx.json）与新增音效（均衡/失真/压缩/合唱）测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _registry_env  # noqa: F401
except ImportError:  # pragma: no cover
    pass

from musicgen.core import effects as fx  # noqa: E402
from musicgen.core.effectscript import EffectScript, EffectScriptError  # noqa: E402
from musicgen.core.effects import EffectSpec  # noqa: E402
from musicgen.core.project import load_project  # noqa: E402

SR = 22050


def _tone(freq: float = 220.0, seconds: float = 1.0, noise: float = 0.0) -> np.ndarray:
    t = np.arange(int(seconds * SR), dtype=np.float32) / SR
    signal = 0.3 * np.sin(2 * np.pi * freq * t) + 0.12 * np.sin(2 * np.pi * 3000 * t)
    if noise:
        rng = np.random.default_rng(7)
        signal = signal + noise * rng.standard_normal(t.size).astype(np.float32)
    return np.vstack([signal, signal * 0.9]).astype(np.float32)


class ScriptParseTests(unittest.TestCase):
    def test_load_with_alias_and_defaults(self) -> None:
        script = EffectScript.from_dict({
            "name": "gtr",
            "effects": [{"type": "均衡", "low": -3}, {"type": "echo", "time": 240}],
        })
        self.assertEqual([spec.type for spec in script.specs], ["eq", "echo"])
        self.assertEqual(script.specs[0].params, {"low": -3.0})
        self.assertIn("均衡", script.summary())

    def test_rejects_unknown_type_and_param(self) -> None:
        with self.assertRaises(EffectScriptError):
            EffectScript.from_dict({"effects": [{"type": "tremolo"}]})
        with self.assertRaises(EffectScriptError):
            EffectScript.from_dict({"effects": [{"type": "echo", "nope": 1}]})
        with self.assertRaises(EffectScriptError):
            EffectScript.from_dict({"effects": []})
        with self.assertRaises(EffectScriptError):
            EffectScript.from_dict({"effects": [{"type": "echo"}], "extra": 1})

    def test_save_and_reload(self) -> None:
        script = EffectScript.from_specs(
            [EffectSpec("eq", {"low": 2.5}), EffectSpec("compressor", {"ratio": 4})],
            name="bus",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = script.save(Path(temp) / "bus.fx.json")
            again = EffectScript.load(path)
        self.assertEqual(again.name, "bus")
        self.assertEqual([spec.type for spec in again.specs], ["eq", "compressor"])
        self.assertEqual(again.specs[0].params["low"], 2.5)

    def test_broken_json_reports_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.fx.json"
            path.write_text('{"effects": [}', encoding="utf-8")
            with self.assertRaises(EffectScriptError) as ctx:
                EffectScript.load(path)
            self.assertIn("JSON", str(ctx.exception))


class NewEffectTests(unittest.TestCase):
    def test_eq_boosts_low_and_cuts_high(self) -> None:
        dry = _tone()
        wet = fx.create_effect("eq", {"low": 9, "high": -9}).process(dry, SR)
        self.assertEqual(wet.shape, dry.shape)
        self.assertTrue(np.isfinite(wet).all())

        def band(signal: np.ndarray, low: float, high: float) -> float:
            freqs = np.fft.rfftfreq(signal.size, 1 / SR)
            spec = np.abs(np.fft.rfft(signal))
            mask = (freqs >= low) & (freqs <= high)
            return float(spec[mask].sum())

        self.assertGreater(band(wet[0], 180, 260), band(dry[0], 180, 260))
        self.assertLess(band(wet[0], 2800, 3200), band(dry[0], 2800, 3200))

    def test_drive_adds_harmonics(self) -> None:
        dry = _tone(220.0)
        wet = fx.create_effect("drive", {"amount": 0.8, "mix": 1.0}).process(dry, SR)
        freqs = np.fft.rfftfreq(wet[0].size, 1 / SR)
        spec = np.abs(np.fft.rfft(wet[0]))
        mask = (freqs > 400) & (freqs < 1200)
        dry_spec = np.abs(np.fft.rfft(dry[0]))
        self.assertGreater(float(spec[mask].sum()), float(dry_spec[mask].sum()) * 2)

    def test_compressor_reduces_peak(self) -> None:
        dry = _tone()
        wet = fx.create_effect(
            "compressor", {"threshold": -28, "ratio": 8}
        ).process(dry, SR)
        self.assertLess(float(np.max(np.abs(wet))), float(np.max(np.abs(dry))) * 0.8)

    def test_chorus_is_stable_and_decorrelates(self) -> None:
        dry = _tone(440.0, seconds=1.0, noise=0.01)
        wet = fx.create_effect("chorus", {"mix": 0.6}).process(dry, SR)
        self.assertTrue(np.isfinite(wet).all())
        correlation = float(np.corrcoef(wet[0], wet[1])[0, 1])
        self.assertLess(correlation, 0.999)

    def test_effects_are_deterministic(self) -> None:
        dry = _tone()
        for key in ("eq", "drive", "compressor", "chorus"):
            effect = fx.create_effect(key)
            first = effect.process(dry, SR)
            second = fx.create_effect(key).process(dry, SR)
            self.assertTrue(np.array_equal(first, second), key)


class EffectScriptCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        (root / "tracks").mkdir()
        self.project = root / "Song.musicproj"
        self.project.write_text(
            '<musicproj name="Song" bpm="120" sampleRate="44100" length="2s">'
            '<track file="tracks/gtr.track.xml"/></musicproj>', encoding="utf-8")
        (root / "tracks" / "gtr.track.xml").write_text(
            '<track name="Gtr" instrument="guitar">'
            '<note time="0" dur="1" pitch="60"/></track>', encoding="utf-8")
        self.script = root / "gtr.fx.json"
        self.script.write_text(json.dumps({
            "name": "gtr-chain",
            "effects": [
                {"type": "eq", "low": -2},
                {"type": "drive", "amount": 0.2},
                {"type": "compressor", "threshold": -20},
            ],
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "musicgen", *args],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def test_check_apply_and_save_roundtrip(self) -> None:
        result = self._cli("effect", "check", str(self.script))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gtr-chain", result.stdout)

        result = self._cli("effect", "apply", str(self.project), "--track", "Gtr",
                           "--script", str(self.script))
        self.assertEqual(result.returncode, 0, result.stderr)
        track = load_project(self.project).tracks[0]
        self.assertEqual([spec.type for spec in track.effects], ["eq", "drive", "compressor"])

        exported = Path(self._temp.name) / "back.fx.json"
        result = self._cli("effect", "save", str(self.project), "--track", "Gtr",
                           "-o", str(exported))
        self.assertEqual(result.returncode, 0, result.stderr)
        again = EffectScript.load(exported)
        self.assertEqual([spec.type for spec in again.specs], ["eq", "drive", "compressor"])

    def test_apply_append_keeps_existing(self) -> None:
        self._cli("effect", "apply", str(self.project), "--track", "Gtr",
                  "--script", str(self.script))
        result = self._cli("effect", "apply", str(self.project), "--track", "Gtr",
                           "--script", str(self.script), "--append")
        self.assertEqual(result.returncode, 0, result.stderr)
        types = [spec.type for spec in load_project(self.project).tracks[0].effects]
        self.assertEqual(types, ["eq", "drive", "compressor"] * 2)

    def test_cli_rejects_broken_script(self) -> None:
        broken = Path(self._temp.name) / "broken.fx.json"
        broken.write_text('{"effects": [{"type": "tremolo"}]}', encoding="utf-8")
        result = self._cli("effect", "apply", str(self.project), "--track", "Gtr",
                           "--script", str(broken))
        self.assertEqual(result.returncode, 1)
        self.assertIn("未知音效", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
