"""轨道组、自动化曲线、部分音效与周期重复：解析、渲染与编辑。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen.core import trackedit as te  # noqa: E402
from musicgen.core.project import load_project  # noqa: E402
from musicgen.core.render import render_project  # noqa: E402

PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<musicproj name="GroupTest" bpm="120" sampleRate="44100" beatsPerBar="4"
           length="8s" normalize="false">
  <group name="Drums" gain="-2dB">
    <effect type="compressor" threshold="-20" ratio="4"/>
  </group>
  <group name="Keys" gain="0dB">
    <automation target="gain" points="0:0; 2:-6; 4:0"/>
  </group>
  <track file="tracks/kick.track.xml"/>
  <track file="tracks/pad.track.xml"/>
</musicproj>
"""

KICK = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Kick" instrument="drums" group="Drums" gain="-3dB">
  <note time="0" dur="0.25" pitch="C2" vel="0.9"/>
  <note time="1.5" dur="0.25" pitch="C2" vel="0.8"/>
</track>
"""

PAD = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Pad" instrument="synth_pad" group="Keys" gain="-6dB">
  <automation target="pan" points="0:-0.9; 2:0.9"/>
  <effect type="filter" mode="lowpass" freq="2000"/>
  <effect type="echo" time="250" mix="0.5" start="2" end="4"/>
  <note time="0" dur="4" pitch="C4" vel="0.7"/>
</track>
"""


def build(tmp: str, kick: str = KICK, pad: str = PAD) -> Path:
    root = Path(tmp)
    (root / "tracks").mkdir(parents=True, exist_ok=True)
    project = root / "GroupTest.musicproj"
    project.write_text(PROJECT, encoding="utf-8")
    (root / "tracks" / "kick.track.xml").write_text(kick, encoding="utf-8")
    (root / "tracks" / "pad.track.xml").write_text(pad, encoding="utf-8")
    return project


class ParseTests(unittest.TestCase):
    def test_group_and_curves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp))
        self.assertEqual([g.name for g in project.groups], ["Drums", "Keys"])
        kick = project.tracks[0]
        self.assertEqual(kick.group, "Drums")
        pad = project.tracks[1]
        self.assertEqual(pad.group, "Keys")
        self.assertEqual([c.target for c in pad.automation], ["pan"])
        self.assertAlmostEqual(pad.automation[0].points[0].value, -0.9)
        self.assertEqual(len(project.groups[1].automation), 1)

    def test_effect_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp))
        echo = project.tracks[1].effects[1]
        self.assertEqual(echo.type, "echo")
        self.assertAlmostEqual(echo.start, 2.0)
        self.assertAlmostEqual(echo.end, 4.0)
        self.assertTrue(echo.ranged)
        self.assertFalse(project.tracks[1].effects[0].ranged)

    def test_unknown_group_warns(self) -> None:
        bad = KICK.replace('group="Drums"', 'group="Nope"')
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp, kick=bad))
        self.assertTrue(any("Nope" in str(w) for w in project.warnings))


class RenderTests(unittest.TestCase):
    def test_group_changes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with_group = render_project(load_project(build(tmp)))
        no_group = f"{PROJECT}".replace('group="Drums"', "").replace('group="Keys"', "")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tracks").mkdir(parents=True, exist_ok=True)
            (root / "GroupTest.musicproj").write_text(no_group, encoding="utf-8")
            (root / "tracks" / "kick.track.xml").write_text(
                KICK.replace('group="Drums"', ""), encoding="utf-8")
            (root / "tracks" / "pad.track.xml").write_text(
                PAD.replace('group="Keys"', ""), encoding="utf-8")
            without = render_project(load_project(root / "GroupTest.musicproj"))
        self.assertGreater(float(np.abs(with_group.audio).max()), 0.0)
        # 组推子 -2dB + 压缩，整体必然与不带组时不同
        self.assertFalse(np.allclose(with_group.audio, without.audio, atol=1e-4))

    def test_group_gain_curve_changes_output(self) -> None:
        """组上的 gain 曲线必须真的改变输出（曲线低谷处更轻）。"""
        curve = '<automation target="gain" points="0:0; 4:-18; 8:0"/>'
        with_curve = PROJECT.replace(
            '<automation target="gain" points="0:0; 2:-6; 4:0"/>', curve)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tracks").mkdir(parents=True, exist_ok=True)
            (root / "GroupTest.musicproj").write_text(with_curve, encoding="utf-8")
            (root / "tracks" / "kick.track.xml").write_text(KICK, encoding="utf-8")
            (root / "tracks" / "pad.track.xml").write_text(PAD, encoding="utf-8")
            ducked = render_project(load_project(root / "GroupTest.musicproj"), only=[1])
        with tempfile.TemporaryDirectory() as tmp:
            flat = render_project(load_project(build(tmp)), only=[1])
        sr = ducked.sample_rate
        a = float(np.abs(ducked.audio[:, int(1.9 * sr): int(2.0 * sr)]).max())
        b = float(np.abs(flat.audio[:, int(1.9 * sr): int(2.0 * sr)]).max())
        self.assertGreater(b, 0.0)
        self.assertLess(a, b * 0.5)

    def test_effect_range_keeps_dry_outside(self) -> None:
        """区间外必须是原样干声，区间内才混湿声。"""
        from musicgen.core.render import _blend_range

        dry = np.ones((2, 44100 * 2), dtype=np.float32)
        wet = np.zeros((2, 44100 * 2), dtype=np.float32)
        # 120BPM 下 2 拍 = 1 秒
        mixed = _blend_range(dry, wet, 2.0, 3.0, bpm=120, sr=44100)  # 1.0–1.5 秒
        self.assertAlmostEqual(float(mixed[0, 0]), 1.0, places=3)
        self.assertAlmostEqual(float(mixed[0, int(0.9 * 44100)]), 1.0, places=3)
        self.assertAlmostEqual(float(mixed[0, int(1.2 * 44100)]), 0.0, places=3)
        self.assertAlmostEqual(float(mixed[0, -1]), 1.0, places=3)
        # 边界是渐变的，不是硬切
        edge = float(mixed[0, int(1.005 * 44100)])
        self.assertGreater(edge, 0.0)
        self.assertLess(edge, 1.0)

    def test_ranged_effect_render_differs(self) -> None:
        ranged = PAD
        full = PAD.replace(' start="2" end="4"', "")
        with tempfile.TemporaryDirectory() as tmp:
            a = render_project(load_project(build(tmp, pad=ranged)), only=[1])
        with tempfile.TemporaryDirectory() as tmp:
            b = render_project(load_project(build(tmp, pad=full)), only=[1])
        self.assertFalse(np.allclose(a.audio, b.audio, atol=1e-5))

    def test_pan_curve_moves_to_right(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp))
            result = render_project(project, only=[1])
        sr = result.sample_rate
        head = result.audio[:, : int(0.2 * sr)]
        tail = result.audio[:, int(1.7 * sr): int(1.9 * sr)]
        self.assertGreater(float(np.abs(head[0]).max()), float(np.abs(head[1]).max()))
        self.assertGreater(float(np.abs(tail[1]).max()), float(np.abs(tail[0]).max()))


class PeriodTests(unittest.TestCase):
    def test_repeat_expands_events(self) -> None:
        looped = KICK.replace('<track name="Kick" instrument="drums" group="Drums" gain="-3dB">',
                              '<track name="Kick" instrument="drums" group="Drums" gain="-3dB"'
                              ' period="2" repeat="3">')
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp, kick=looped))
            self.assertTrue(project.tracks[0].loop)
            self.assertEqual(project.tracks[0].loop_count, 3)
            result = render_project(project, only=[0])
        sr = result.sample_rate
        mono = np.abs(result.audio.mean(axis=0))
        # period=2 拍 @120BPM = 1 秒，repeat=3 → 0 / 1 / 2 秒各一次，3 秒后不该再有
        for expected in (0.0, 1.0, 2.0):
            index = int(expected * sr)
            self.assertGreater(float(mono[index: index + int(0.1 * sr)].max()), 0.02,
                               f"{expected}s 处应有音符")
        self.assertLess(float(mono[int(3.0 * sr): int(3.4 * sr)].max()), 0.03)

    def test_gap_offsets_each_repeat(self) -> None:
        looped = KICK.replace(
            'group="Drums" gain="-3dB">',
            'group="Drums" gain="-3dB" period="2" repeat="3" gap="1">')
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp, kick=looped))
            result = render_project(project, only=[0])
        sr = result.sample_rate
        mono = np.abs(result.audio.mean(axis=0))
        # 120BPM：一拍 0.5 秒，周期 2 拍 + 间隔 1 拍 = 每 1.5 秒一次
        for expected in (0.0, 1.5, 3.0):
            index = int(expected * sr)
            self.assertGreater(float(mono[index: index + int(0.1 * sr)].max()), 0.02,
                               f"{expected}s 处应有音符")

    def test_start_offset_shifts(self) -> None:
        shifted = KICK.replace('group="Drums" gain="-3dB">',
                               'group="Drums" gain="-3dB" offset="2">')
        with tempfile.TemporaryDirectory() as tmp:
            project = load_project(build(tmp, kick=shifted))
            result = render_project(project, only=[0])
        sr = result.sample_rate
        mono = np.abs(result.audio.mean(axis=0))
        self.assertLess(float(mono[: int(0.9 * sr)].max()), 0.01)
        self.assertGreater(float(mono[int(1.0 * sr): int(1.1 * sr)].max()), 0.02)


class EditTests(unittest.TestCase):
    def test_add_assign_remove_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = build(tmp)
            te.add_group(project_path, "Extra", gain_db=-4.0, pan=0.2)
            project = load_project(project_path)
            self.assertIsNotNone(project.find_group("Extra"))
            te.assign_group(project_path, "Pad", "Extra")
            project = load_project(project_path)
            self.assertEqual(project.tracks[1].group, "Extra")
            with self.assertRaises(te.TrackEditError):
                te.remove_group(project_path, "Extra")
            removed = te.remove_group(project_path, "Extra", force=True)
            self.assertEqual(removed, 1)
            project = load_project(project_path)
            self.assertIsNone(project.find_group("Extra"))
            self.assertIsNone(project.tracks[1].group)

    def test_curve_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = build(tmp)
            te.set_curve(project_path, "track", "Kick", "gain", "0:0; 4:-3; 8:0")
            te.set_curve(project_path, "group", "Drums", "pan", "0:-1; 8:1", mode="linear")
            project = load_project(project_path)
            kick = project.tracks[0]
            self.assertEqual(len([c for c in kick.automation if c.target == "gain"]), 1)
            self.assertAlmostEqual(kick.automation[0].value_at(4), -3.0, places=3)
            drums = project.find_group("Drums")
            self.assertEqual(len(drums.automation), 1)
            self.assertEqual(drums.automation[0].mode, "linear")
            # 重复写同一目标应替换而不是叠加
            te.set_curve(project_path, "track", "Kick", "gain", "0:0; 8:2")
            project = load_project(project_path)
            self.assertEqual(len([c for c in project.tracks[0].automation if c.target == "gain"]), 1)
            te.remove_curve(project_path, "track", "Kick", "gain")
            project = load_project(project_path)
            self.assertFalse([c for c in project.tracks[0].automation if c.target == "gain"])

    def test_track_edit_period_and_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = build(tmp)
            te.edit_track(project_path, "Kick", loop_len=2.0, loop_count=4, gap=0.5,
                          group="Keys")
            project = load_project(project_path)
            kick = project.tracks[0]
            self.assertEqual(kick.loop_len, 2.0)
            self.assertEqual(kick.loop_count, 4)
            self.assertEqual(kick.loop_gap_beats, 0.5)
            self.assertEqual(kick.group, "Keys")

    def test_save_track_keeps_group_and_curves(self) -> None:
        from musicgen.core.project import save_track

        with tempfile.TemporaryDirectory() as tmp:
            project_path = build(tmp)
            project = load_project(project_path)
            pad = project.tracks[1]
            save_track(pad, pad.path)
            again = load_project(project_path)
        self.assertEqual(again.tracks[1].group, "Keys")
        self.assertEqual(len(again.tracks[1].automation), 1)
        self.assertTrue(again.tracks[1].effects[1].ranged)


if __name__ == "__main__":
    unittest.main()
