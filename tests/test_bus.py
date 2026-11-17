"""编组总线与发送：解析、路由、电平与编辑命令。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen.core import trackedit as te  # noqa: E402
from musicgen.core.project import Send, load_project  # noqa: E402
from musicgen.core.render import render_project  # noqa: E402

PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<musicproj name="BusTest" bpm="120" sampleRate="44100" beatsPerBar="4"
           length="3s" normalize="false">
  <bus name="Drums" gain="-2dB">
    <effect type="compressor" threshold="-20" ratio="4"/>
  </bus>
  <bus name="Music" gain="-6dB"/>
  <bus name="Reverb" gain="0dB">
    <effect type="reverb" decay="2.5" mix="1"/>
  </bus>
  <track file="tracks/kick.track.xml"/>
  <track file="tracks/pad.track.xml"/>
</musicproj>
"""

KICK = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Kick" instrument="drums" bus="Drums" gain="-3dB">
  <note time="0" dur="0.25" pitch="C2" vel="0.9"/>
  <note time="1" dur="0.25" pitch="C2" vel="0.8"/>
</track>
"""

PAD = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Pad" instrument="drums" bus="Music" gain="-4dB">
  <send bus="Reverb" amount="0.5"/>
  <note time="0" dur="0.5" pitch="C4" vel="0.7"/>
</track>
"""


def build(tmp: str) -> Path:
    root = Path(tmp)
    (root / "tracks").mkdir(parents=True, exist_ok=True)
    project = root / "BusTest.musicproj"
    project.write_text(PROJECT, encoding="utf-8")
    (root / "tracks" / "kick.track.xml").write_text(KICK, encoding="utf-8")
    (root / "tracks" / "pad.track.xml").write_text(PAD, encoding="utf-8")
    return project


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = build(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_buses_parsed_in_order(self) -> None:
        project = load_project(self.project_path)
        self.assertEqual([b.name for b in project.buses], ["Drums", "Music", "Reverb"])
        self.assertEqual(project.find_bus("drums").gain_db, -2.0)
        self.assertEqual(len(project.find_bus("Drums").effects), 1)
        self.assertEqual(project.find_bus("Reverb").effects[0].type, "reverb")

    def test_track_routing_and_sends(self) -> None:
        project = load_project(self.project_path)
        kick, pad = project.tracks
        self.assertEqual(kick.bus, "Drums")
        self.assertEqual(pad.bus, "Music")
        self.assertEqual([(s.bus, s.amount) for s in pad.sends], [("Reverb", 0.5)])
        self.assertEqual(kick.sends, [])

    def test_missing_bus_warns(self) -> None:
        text = PROJECT.replace('bus="Music"', 'bus="Nope"').replace(
            '<send bus="Reverb" amount="0.5"/>', '<send bus="Ghost" amount="0.5"/>'
        )
        self.project_path.write_text(text, encoding="utf-8")
        # 音轨文件里还有 bus="Music"，所以只清 pad 的归属
        pad_path = Path(self._tmp.name) / "tracks" / "pad.track.xml"
        pad_path.write_text(PAD.replace('bus="Music"', 'bus="Nope"').replace(
            'bus="Reverb"', 'bus="Ghost"'), encoding="utf-8")
        project = load_project(self.project_path)
        messages = [str(w) for w in project.warnings]
        self.assertTrue(any("Nope" in m for m in messages))
        self.assertTrue(any("Ghost" in m for m in messages))


class RenderRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = build(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _render(self, project=None):
        return render_project(project or load_project(self.project_path), normalize=False)

    def test_bus_stats_present(self) -> None:
        result = self._render()
        kinds = {stat.name: stat.kind for stat in result.track_stats}
        self.assertEqual(kinds["Drums"], "bus")
        self.assertEqual(kinds["Reverb"], "bus")
        self.assertEqual(kinds["Kick"], "notes")

    def test_bus_gain_applied(self) -> None:
        project = load_project(self.project_path)
        project.find_bus("Drums").gain_db = 0.0
        louder = self._render(project)
        project2 = load_project(self.project_path)
        quieter = self._render(project2)
        dr_loud = next(s for s in louder.track_stats if s.name == "Drums").peak
        dr_quiet = next(s for s in quieter.track_stats if s.name == "Drums").peak
        self.assertGreater(dr_loud, dr_quiet)

    def test_bus_effect_changes_signal(self) -> None:
        project = load_project(self.project_path)
        without = self._render(project)
        project2 = load_project(self.project_path)
        project2.find_bus("Drums").effects.clear()
        clean = self._render(project2)
        a = next(s for s in without.track_stats if s.name == "Drums").rms
        b = next(s for s in clean.track_stats if s.name == "Drums").rms
        self.assertNotAlmostEqual(a, b, places=4)

    def test_send_feeds_return_bus(self) -> None:
        project = load_project(self.project_path)
        project.buses = [b for b in project.buses if b.name == "Reverb"]
        for track in project.tracks:
            track.bus = None
        project.tracks[1].sends = [Send("Reverb", 0.8)]
        result = self._render(project)
        reverb = next(s for s in result.track_stats if s.name == "Reverb")
        self.assertGreater(reverb.peak, 0.0)

        # 撤掉发送后，返回总线应当没有输入
        project.tracks[1].sends = []
        result = self._render(project)
        reverb = next(s for s in result.track_stats if s.name == "Reverb")
        self.assertEqual(reverb.peak, 0.0)

    def test_muted_bus_keeps_signal_out_of_master(self) -> None:
        project = load_project(self.project_path)
        project.find_bus("Drums").mute = True
        muted = self._render(project)
        project2 = load_project(self.project_path)
        normal = self._render(project2)
        self.assertLess(muted.rms, normal.rms)
        self.assertTrue(any("已静音" in w for w in muted.warnings))

    def test_track_without_bus_goes_to_master(self) -> None:
        project = load_project(self.project_path)
        project.tracks[0].bus = None  # 这条轨不归属任何总线
        for bus in project.buses:
            bus.mute = True
        result = self._render(project)
        self.assertGreater(result.peak, 0.0)  # 直连母带的轨道不受总线静音影响


class EditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = build(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_set_remove(self) -> None:
        te.add_bus(self.project_path, "FX", gain_db=-3)
        project = load_project(self.project_path)
        self.assertIsNotNone(project.find_bus("FX"))
        self.assertEqual(project.find_bus("FX").gain_db, -3.0)

        te.set_bus(self.project_path, "FX", gain_db=-6, mute=True)
        project = load_project(self.project_path)
        self.assertEqual(project.find_bus("FX").gain_db, -6.0)
        self.assertTrue(project.find_bus("FX").mute)

        te.remove_bus(self.project_path, "FX")
        self.assertIsNone(load_project(self.project_path).find_bus("FX"))

    def test_rename_updates_references(self) -> None:
        te.set_bus(self.project_path, "Music", rename="Band")
        project = load_project(self.project_path)
        self.assertIsNone(project.find_bus("Music"))
        self.assertEqual(project.tracks[1].bus, "Band")

    def test_remove_in_use_requires_force(self) -> None:
        with self.assertRaises(te.TrackEditError):
            te.remove_bus(self.project_path, "Drums")
        cleared = te.remove_bus(self.project_path, "Drums", force=True)
        self.assertIn("Kick", cleared)
        project = load_project(self.project_path)
        self.assertIsNone(project.tracks[0].bus)

    def test_track_edit_sets_bus(self) -> None:
        te.edit_track(self.project_path, 1, bus="Music")
        self.assertEqual(load_project(self.project_path).tracks[0].bus, "Music")
        te.edit_track(self.project_path, 1, bus=None)
        self.assertIsNone(load_project(self.project_path).tracks[0].bus)

    def test_send_round_trip(self) -> None:
        te.set_send(self.project_path, "Kick", "Reverb", 0.35)
        kick = load_project(self.project_path).tracks[0]
        self.assertEqual([(s.bus, s.amount) for s in kick.sends], [("Reverb", 0.35)])
        te.set_send(self.project_path, "Kick", "Reverb", 0.6)
        kick = load_project(self.project_path).tracks[0]
        self.assertEqual([(s.bus, s.amount) for s in kick.sends], [("Reverb", 0.6)])
        te.remove_send(self.project_path, "Kick", "Reverb")
        self.assertEqual(load_project(self.project_path).tracks[0].sends, [])

    def test_bus_effect_edit(self) -> None:
        spec = te.add_bus_effect(self.project_path, "Music", "limiter",
                                 {"ceiling": -2.0})
        self.assertEqual(spec.type, "limiter")
        bus = load_project(self.project_path).find_bus("Music")
        self.assertEqual(len(bus.effects), 1)
        te.set_bus_effect(self.project_path, "Music", 1, ceiling=-4.0)
        bus = load_project(self.project_path).find_bus("Music")
        self.assertAlmostEqual(float(bus.effects[0].params["ceiling"]), -4.0)
        te.remove_bus_effect(self.project_path, "Music", 1)
        self.assertEqual(load_project(self.project_path).find_bus("Music").effects, [])


if __name__ == "__main__":
    unittest.main()
