"""MIDI 导出：格式、通道、音色、循环展开与弯音。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import mido

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen.core.project import load_project  # noqa: E402
from musicgen.export_midi import DRUM_CHANNEL, TICKS_PER_BEAT, export_midi, export_midi_split  # noqa: E402

PROJECT = """<?xml version="1.0" encoding="UTF-8"?>
<musicproj name="MidiOut" bpm="120" sampleRate="44100" beatsPerBar="4"
           length="4s" normalize="true">
  <track file="tracks/kick.track.xml"/>
  <track file="tracks/bass.track.xml"/>
  <track file="tracks/lead.track.xml"/>
</musicproj>
"""

KICK = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Kick" instrument="drums" gain="-3dB">
  <note time="0" dur="0.25" pitch="C2" vel="0.9"/>
  <note time="1" dur="0.25" pitch="D2" vel="0.8"/>
</track>
"""

BASS = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Bass" instrument="cello" loop="true" loopLen="4" loopCount="2">
  <note time="0" dur="2" pitch="C2" vel="0.7"/>
</track>
"""

LEAD = """<?xml version="1.0" encoding="UTF-8"?>
<track name="Lead" instrument="piano">
  <note time="0" dur="2" pitch="C4,E4" vel="0.6" bend="0:0;0.5:-0.5;1:0"/>
</track>
"""


def build(tmp: str) -> Path:
    root = Path(tmp)
    (root / "tracks").mkdir(parents=True, exist_ok=True)
    project = root / "MidiOut.musicproj"
    project.write_text(PROJECT, encoding="utf-8")
    for name, text in (("kick", KICK), ("bass", BASS), ("lead", LEAD)):
        (root / "tracks" / f"{name}.track.xml").write_text(text, encoding="utf-8")
    return project


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project_path = build(self._tmp.name)
        self.project = load_project(self.project_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load(self, result) -> mido.MidiFile:
        return mido.MidiFile(str(result.path))

    def test_file_structure(self) -> None:
        result = export_midi(self.project, self.root / "out.mid")
        midi = self._load(result)
        self.assertEqual(midi.type, 1)
        self.assertEqual(midi.ticks_per_beat, TICKS_PER_BEAT)
        self.assertEqual(len(midi.tracks), len(self.project.tracks) + 1)
        self.assertEqual(result.tracks, 3)

    def test_conductor_has_tempo_and_meter(self) -> None:
        result = export_midi(self.project, self.root / "out.mid")
        conductor = self._load(result).tracks[0]
        kinds = {msg.type for msg in conductor}
        self.assertIn("set_tempo", kinds)
        self.assertIn("time_signature", kinds)
        tempo = next(msg.tempo for msg in conductor if msg.type == "set_tempo")
        self.assertAlmostEqual(mido.tempo2bpm(tempo), 120.0, places=1)

    def test_drum_channel_and_programs(self) -> None:
        result = export_midi(self.project, self.root / "out.mid")
        midi = self._load(result)
        programs: dict[str, int] = {}
        channels: dict[str, int] = {}
        for track in midi.tracks[1:]:
            name = track.name
            channels[name] = next(
                msg.channel for msg in track if msg.type == "note_on"
            )
            programs[name] = next(
                (msg.program for msg in track if msg.type == "program_change"), -1
            )
        self.assertEqual(channels["Kick"], DRUM_CHANNEL)
        self.assertNotEqual(channels["Bass"], DRUM_CHANNEL)
        self.assertNotEqual(channels["Lead"], DRUM_CHANNEL)
        self.assertEqual(programs["Bass"], 42)   # GM Cello
        self.assertEqual(programs["Lead"], 0)    # GM Acoustic Grand Piano

    def test_channels_never_use_nine(self) -> None:
        result = export_midi(self.project, self.root / "out.mid")
        midi = self._load(result)
        used = {
            msg.channel
            for track in midi.tracks[1:]
            for msg in track
            if msg.type == "note_on" and track.name != "Kick"
        }
        self.assertNotIn(DRUM_CHANNEL, used)

    def test_loop_expansion(self) -> None:
        result = export_midi(self.project, self.root / "out.mid")
        midi = self._load(result)
        bass = next(track for track in midi.tracks if track.name == "Bass")
        self.assertEqual(sum(1 for msg in bass if msg.type == "note_on"), 2)
        # 第二次重复应落在第 4 拍
        ticks = []
        cursor = 0
        for msg in bass:
            cursor += msg.time
            if msg.type == "note_on":
                ticks.append(cursor)
        self.assertIn(4 * TICKS_PER_BEAT, ticks)

    def test_no_loop_keeps_single_pass(self) -> None:
        result = export_midi(self.project, self.root / "out.mid", expand_loop=False)
        midi = self._load(result)
        bass = next(track for track in midi.tracks if track.name == "Bass")
        self.assertEqual(sum(1 for msg in bass if msg.type == "note_on"), 1)

    def test_chord_and_bend(self) -> None:
        result = export_midi(self.project, self.root / "out.mid")
        midi = self._load(result)
        lead = next(track for track in midi.tracks if track.name == "Lead")
        pitches = [msg.note for msg in lead if msg.type == "note_on"]
        self.assertEqual(sorted(pitches), [60, 64])
        wheel = [msg.pitch for msg in lead if msg.type == "pitchwheel"]
        self.assertLess(min(wheel), -1000)  # 0.5 处 -0.5 半音，按 ±2 半音换算
        self.assertEqual(wheel[-1], 0)  # 音符结束回中

    def test_only_selection(self) -> None:
        result = export_midi(self.project, self.root / "out.mid", only=[2])
        midi = self._load(result)
        self.assertEqual(len(midi.tracks), 2)
        self.assertEqual(midi.tracks[1].name, "Lead")

    def test_split_files(self) -> None:
        results = export_midi_split(self.project, self.root / "midi")
        self.assertEqual(len(results), 3)
        for item in results:
            self.assertTrue(item.path.is_file())
            midi = mido.MidiFile(str(item.path))
            self.assertEqual(midi.type, 0)


if __name__ == "__main__":
    unittest.main()
