"""合成音脚本（patch）与音轨级脚本音色测试。"""

from __future__ import annotations

import json
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

from musicgen.core import project as P  # noqa: E402
from musicgen.core.instruments import InstrumentRegistry  # noqa: E402
from musicgen.core.patch import (  # noqa: E402
    Patch,
    PatchError,
    PatchInstrument,
    builtin_patches,
    load_patch,
)
from musicgen.core.render import render_project  # noqa: E402
from musicgen.samples import fetch  # noqa: E402


class PatchParseTests(unittest.TestCase):
    def test_builtin_patches_render_audible_notes(self) -> None:
        patches = builtin_patches()
        self.assertGreaterEqual(len(patches), 6)
        for name, patch in patches.items():
            audio = patch.render(60, 0.8, 0.6)
            self.assertEqual(audio.shape[0], 2, name)
            self.assertGreater(audio.shape[1], 0, name)
            self.assertTrue(np.isfinite(audio).all(), name)
            self.assertGreater(float(np.max(np.abs(audio))), 0.01, name)

    def test_render_is_deterministic(self) -> None:
        patch = load_patch("air_texture")  # 带噪声，最容易不确定
        first = patch.render(57, 0.7, 0.5)
        second = patch.render(57, 0.7, 0.5)
        self.assertTrue(np.array_equal(first, second))

    def test_roundtrip_save_load(self) -> None:
        patch = load_patch("warm_pad")
        with tempfile.TemporaryDirectory() as temp:
            target = patch.save(Path(temp) / "x.synth.json")
            again = Patch.load(target)
        self.assertEqual(again.name, patch.name)
        self.assertEqual(len(again.oscillators), len(patch.oscillators))
        self.assertAlmostEqual(again.amp.attack, patch.amp.attack, places=4)
        self.assertAlmostEqual(again.filter.cutoff, patch.filter.cutoff, places=1)

    def test_unknown_field_and_wave_raise(self) -> None:
        with self.assertRaises(PatchError):
            Patch.from_dict({"oscillators": ["saw"], "reverb": 1})
        with self.assertRaises(PatchError):
            Patch.from_dict({"oscillators": [{"wave": "sawtooth2"}]})
        with self.assertRaises(PatchError):
            Patch.from_dict({"oscillators": []})
        with self.assertRaises(PatchError):
            Patch.from_dict({"oscillators": [{"wave": "saw", "level": "big"}]})

    def test_missing_file_reports_available_builtins(self) -> None:
        with self.assertRaises(PatchError) as ctx:
            load_patch("no_such_patch")
        self.assertIn("warm_pad", str(ctx.exception))

    def test_chinese_wave_alias(self) -> None:
        patch = Patch.from_dict({"oscillators": ["锯齿"]})
        self.assertEqual(patch.oscillators[0].wave, "saw")

    def test_velocity_and_pitch_scale_output(self) -> None:
        patch = load_patch("soft_keys")
        quiet = patch.render(60, 0.2, 0.5)
        loud = patch.render(60, 1.0, 0.5)
        self.assertGreater(float(np.max(np.abs(loud))), float(np.max(np.abs(quiet))) * 2)
        low = patch.render(48, 0.8, 0.5)
        high = patch.render(72, 0.8, 0.5)
        # 高八度的音符不应该和低八度完全一样
        length = min(low.shape[1], high.shape[1])
        self.assertFalse(np.allclose(low[:, :length], high[:, :length]))


class RegistrySynthTests(unittest.TestCase):
    def test_synth_entries_are_registered(self) -> None:
        keys = fetch.instrument_keys()
        self.assertIn("synth_pad", keys)
        self.assertTrue(fetch.is_synth("synth_pad"))
        self.assertFalse(fetch.is_synth("piano"))
        self.assertEqual(fetch.patch_spec("synth_bass"), "deep_bass")
        self.assertGreater(len(fetch.available_notes("synth_pad", None)), 12)

    def test_registry_renders_synth_instrument(self) -> None:
        registry = InstrumentRegistry(sample_rate=22050)
        player = registry.get("synth_lead")
        audio = player.render_note(64, 0.8, 0.4)
        self.assertEqual(audio.shape[0], 2)
        self.assertIn("合成音脚本", player.describe())

    def test_user_synth_entry_via_registry_dict(self) -> None:
        entries = [
            {"key": "my_pad", "label": "我的垫", "kind": "synth",
             "patch": "warm_pad", "midi_low": 40, "midi_high": 90, "gain": 8},
        ]
        fetch.reload_registry(entries)
        try:
            self.assertTrue(fetch.is_synth("my_pad"))
            self.assertEqual(fetch.label("my_pad"), "我的垫")
            audio = InstrumentRegistry(sample_rate=22050).get("my_pad").render_note(60, 0.8, 0.3)
            self.assertGreater(float(np.max(np.abs(audio))), 0.01)
        finally:
            fetch.reload_registry()


class ProjectPatchTests(unittest.TestCase):
    def _project(self, root: Path, patch_ref: str) -> Path:
        (root / "tracks").mkdir(parents=True)
        (root / "synths").mkdir()
        load_patch("pluck_string").save(root / "synths" / "lead.synth.json")
        (root / "P.musicproj").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<musicproj name="P" bpm="100" sampleRate="44100" length="1s" '
            'masterGain="0dB" normalize="false">\n'
            '  <track file="tracks/a.track.xml"/>\n</musicproj>\n',
            encoding="utf-8",
        )
        (root / "tracks" / "a.track.xml").write_text(
            f'<track name="Lead" instrument="synth" patch="{patch_ref}">\n'
            '  <note time="0" dur="1" pitch="60" vel="0.7"/>\n'
            '</track>',
            encoding="utf-8",
        )
        return root / "P.musicproj"

    def test_project_track_uses_patch_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_path = self._project(root, "../synths/lead.synth.json")
            project = P.load_project(project_path)
            track = project.tracks[0]
            self.assertIsNotNone(track.patch_path)
            self.assertEqual(track.patch_path.name, "lead.synth.json")
            result = render_project(project)
            self.assertGreater(result.peak, 0.05)
            self.assertEqual(result.track_stats[0].instrument, "synth")

            P.save_track(track, track.path)
            reloaded = P.load_project(project_path)
            self.assertIsNotNone(reloaded.tracks[0].patch_path)

    def test_broken_patch_is_reported_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_path = self._project(root, "../synths/lead.synth.json")
            (root / "synths" / "lead.synth.json").write_text(
                json.dumps({"oscillators": [{"wave": "nope"}]}), encoding="utf-8"
            )
            with self.assertRaises(P.ProjectError):
                P.load_project(project_path)

    def test_instrument_without_patch_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tracks").mkdir(parents=True)
            (root / "P.musicproj").write_text(
                '<musicproj name="P" bpm="100" sampleRate="44100" length="0.5s">'
                '<track file="tracks/a.track.xml"/></musicproj>', encoding="utf-8")
            (root / "tracks" / "a.track.xml").write_text(
                '<track name="A" instrument="synth"><note time="0" dur="1" pitch="60"/></track>',
                encoding="utf-8")
            project = P.load_project(root / "P.musicproj")
            result = render_project(project)
            # instrument="synth" 没有脚本时走兜底合成器，并给出警告
            self.assertGreater(result.peak, 0.01)
            self.assertTrue(any("合成器" in w or "未注册" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
