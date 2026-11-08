"""核心逻辑单元测试：音名、解析、渲染、WAV 写出。

运行：``py -3 -m unittest discover -s tests -v``
"""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen.core import project as P  # noqa: E402
from musicgen.core.notes import (  # noqa: E402
    PitchError,
    midi_to_freq,
    midi_to_name,
    parse_pitch,
    parse_pitches,
)
from musicgen.core.render import render_project  # noqa: E402
from musicgen.core.sampler import (  # noqa: E402
    INSTRUMENT_GAIN,
    SampleInstrument,
)
from musicgen.samples import fetch  # noqa: E402
from musicgen.core.wavio import write_wav  # noqa: E402

DEMO = ROOT / "examples" / "demo" / "demo.musicproj"
ENSEMBLE = ROOT / "examples" / "ensemble" / "ensemble.musicproj"
BAND = ROOT / "examples" / "band" / "band.musicproj"
MOONLIGHT = ROOT / "examples" / "moonlight" / "moonlight.musicproj"


class NoteTests(unittest.TestCase):
    def test_parse_basic(self) -> None:
        self.assertEqual(parse_pitch("C4"), 60)
        self.assertEqual(parse_pitch("A4"), 69)
        self.assertEqual(parse_pitch("C-1"), 0)
        self.assertEqual(parse_pitch("C8"), 108)

    def test_parse_accidentals(self) -> None:
        self.assertEqual(parse_pitch("Db4"), 61)
        self.assertEqual(parse_pitch("F#3"), 54)
        self.assertEqual(parse_pitch("bB4"), 70)   # 变音记号前置的写法
        self.assertEqual(parse_pitch("\u266fF4"), 66)  # ♯F4

    def test_parse_numeric(self) -> None:
        self.assertEqual(parse_pitch("60"), 60)
        with self.assertRaises(PitchError):
            parse_pitch("128")

    def test_bad_input(self) -> None:
        for bad in ("", "H4", "C", "Cx4", "C4.5"):
            with self.assertRaises(PitchError):
                parse_pitch(bad)

    def test_chord_list(self) -> None:
        self.assertEqual(parse_pitches("C4,E4,G4"), [60, 64, 67])
        self.assertEqual(parse_pitches("C4 E4"), [60, 64])

    def test_roundtrip(self) -> None:
        for midi in (0, 21, 60, 108, 127):
            self.assertEqual(parse_pitch(midi_to_name(midi)), midi)

    def test_frequency(self) -> None:
        self.assertAlmostEqual(midi_to_freq(69), 440.0)
        self.assertAlmostEqual(midi_to_freq(60), 261.6256, places=3)


class BeatParsingTests(unittest.TestCase):
    def test_fraction_and_decimal(self) -> None:
        self.assertAlmostEqual(P._parse_beats("1/4"), 0.25)
        self.assertAlmostEqual(P._parse_beats("1/2"), 0.5)
        self.assertAlmostEqual(P._parse_beats("0.75"), 0.75)
        self.assertAlmostEqual(P._parse_beats("3"), 3.0)

    def test_times_notation(self) -> None:
        self.assertAlmostEqual(P._parse_beats("2*4"), 8.0)
        self.assertAlmostEqual(P._parse_beats("4*1/4"), 1.0)

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            P._parse_beats("1/0")
        with self.assertRaises(ValueError):
            P._parse_beats("-1")
        with self.assertRaises(ValueError):
            P._parse_beats("abc")

    def test_gain_and_velocity(self) -> None:
        self.assertAlmostEqual(P._parse_gain_db("-3dB"), -3.0)
        self.assertAlmostEqual(P._parse_gain_db("+2.5db"), 2.5)
        self.assertAlmostEqual(P._parse_gain_db("0"), 0.0)
        self.assertAlmostEqual(P._parse_velocity("0.5"), 0.5)
        self.assertAlmostEqual(P._parse_velocity("100"), 100 / 127)


class ProjectTests(unittest.TestCase):
    def test_load_demo(self) -> None:
        project = P.load_project(DEMO)
        self.assertEqual(len(project.tracks), 4)
        self.assertEqual(project.bpm, 84)
        self.assertEqual(project.sample_rate, 44100)
        names = [t.name for t in project.tracks]
        self.assertEqual(names, ["Chords", "Ostinato", "Bass", "Melody"])
        self.assertTrue(project.tracks[1].loop)
        self.assertAlmostEqual(project.tracks[1].loop_len, 4.0)
        self.assertEqual(project.warnings, [])
        self.assertGreater(project.compute_length_seconds(), 20)

    def test_load_ensemble(self) -> None:
        project = P.load_project(ENSEMBLE)
        self.assertEqual(len(project.tracks), 4)
        self.assertEqual([t.instrument for t in project.tracks],
                         ["violin", "cello", "trumpet", "piano"])
        for track in project.tracks:
            source = fetch.SOURCES[track.instrument]
            for note in track.notes:
                for pitch in note.pitches:
                    self.assertGreaterEqual(pitch, source.midi_low)
                    self.assertLessEqual(pitch, source.midi_high)

    def test_auto_length_includes_tail(self) -> None:
        project = P.load_project(DEMO)
        tail = project.tail_seconds
        project.tail_seconds = 0.0
        base = project.compute_length_seconds()
        project.tail_seconds = tail
        self.assertAlmostEqual(project.compute_length_seconds() - base, tail, places=4)

    def test_loop_period_defaults_to_whole_bar(self) -> None:
        track = P.Track(name="t", loop=True)
        track.notes.append(P.Note(0.0, 5.0, [60]))
        self.assertEqual(track.period_beats(4), 8.0)  # 5 拍向上取整到 2 小节
        track.loop_len = 3.0
        self.assertEqual(track.period_beats(4), 3.0)

    def test_reject_missing_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.musicproj"
            path.write_text('<musicproj><track file="nope.track.xml"/></musicproj>',
                            encoding="utf-8")
            with self.assertRaises(P.ProjectError) as ctx:
                P.load_project(path)
            self.assertIn("音轨文件不存在", str(ctx.exception))

    def test_reject_bad_pitch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "t.track.xml").write_text(
                '<track name="t"><note time="0" dur="1" pitch="H9"/></track>', encoding="utf-8"
            )
            proj = tmp_path / "x.musicproj"
            proj.write_text('<musicproj bpm="120"><track file="t.track.xml"/></musicproj>',
                            encoding="utf-8")
            with self.assertRaises(P.ProjectError):
                P.load_project(proj)

    def test_warning_for_note_outside_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "t.track.xml").write_text(
                '<track name="t" loop="true" loopLen="2">'
                '<note time="0" dur="1" pitch="C4"/>'
                '<note time="3" dur="1" pitch="D4"/>'
                "</track>",
                encoding="utf-8",
            )
            proj = tmp_path / "x.musicproj"
            proj.write_text('<musicproj bpm="120"><track file="t.track.xml"/></musicproj>',
                            encoding="utf-8")
            loaded = P.load_project(proj)
            self.assertTrue(any("循环周期" in str(w) for w in loaded.warnings))

    def test_save_track_keeps_triplet_precision(self) -> None:
        """三连音 1/3 拍回写后不能被截成 0.333333（会累积节拍漂移）。"""
        import xml.etree.ElementTree as ET

        track = P.Track(name="triplet", instrument="piano")
        track.notes.append(P.Note(1 / 3, 1 / 3, [60], 0.7))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.track.xml"
            P.save_track(track, path)
            note = ET.fromstring(path.read_text(encoding="utf-8")).find("note")
            self.assertIsNotNone(note)
            self.assertAlmostEqual(float(note.get("time")), 1 / 3, places=10)
            self.assertAlmostEqual(float(note.get("dur")), 1 / 3, places=10)
            self.assertNotEqual(note.get("time"), "0.333333")

    def test_save_track_roundtrip(self) -> None:
        track = P.Track(name="往返", instrument="piano", gain_db=-2.5, pan=0.25,
                        reverb=0.3, loop=True, loop_len=4.0)
        track.notes.append(P.Note(0.0, 1.0, [60, 64, 67], 0.8))
        track.notes.append(P.Note(1.5, 0.5, [72], 0.5))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rt.track.xml"
            P.save_track(track, path)
            text = path.read_text(encoding="utf-8")
            self.assertIn('pitch="C4,E4,G4"', text)  # 用音名写出，便于手改
            (Path(tmp) / "p.musicproj").write_text(
                '<musicproj bpm="120"><track file="rt.track.xml"/></musicproj>', encoding="utf-8"
            )
            loaded = P.load_project(Path(tmp) / "p.musicproj")
            again = loaded.tracks[0]
            self.assertEqual(again.name, "往返")
            self.assertAlmostEqual(again.gain_db, -2.5)
            self.assertEqual(len(again.notes), 2)
            self.assertEqual(again.notes[0].pitches, [60, 64, 67])
            self.assertAlmostEqual(again.notes[0].velocity, 0.8)


class RenderTests(unittest.TestCase):
    def _project(self, bpm: float = 120.0, notes=None, length: str = "2") -> P.Project:
        project = P.Project(name="t", path=Path("t.musicproj"), bpm=bpm,
                            sample_rate=44100, length_spec=length, tail_seconds=0.0)
        track = P.Track(name="t", instrument="piano", path=Path("t.xml"))
        for time, dur, pitches, vel in (notes or [(0.0, 1.0, [60], 0.8)]):
            track.notes.append(P.Note(time, dur, pitches, vel))
        project.tracks.append(track)
        return project

    def test_pitch_is_correct(self) -> None:
        result = render_project(self._project())
        self.assertEqual(result.audio.shape[0], 2)
        mono = result.audio.mean(axis=0)
        start = int(0.05 * 44100)
        window = mono[start : start + 16384] * np.hanning(16384)
        spectrum = np.abs(np.fft.rfft(window))
        freqs = np.fft.rfftfreq(16384, 1 / 44100)
        peak = freqs[int(np.argmax(spectrum))]
        self.assertAlmostEqual(peak, 261.63, delta=4.0)

    def test_length_and_normalize(self) -> None:
        project = self._project(length="2")  # 2 拍 @120BPM = 1 秒
        result = render_project(project)
        self.assertAlmostEqual(result.duration, 1.0, places=3)
        self.assertLessEqual(result.peak, 0.892)
        self.assertGreater(result.peak, 0.5)

    def test_silent_track_stays_silent(self) -> None:
        project = self._project()
        project.tracks[0].mute = True
        result = render_project(project)
        self.assertEqual(float(np.max(np.abs(result.audio))), 0.0)

    def test_loop_expands_notes(self) -> None:
        project = self._project(length="8")
        track = project.tracks[0]
        track.loop = True
        track.loop_len = 2.0
        result = render_project(project)
        self.assertEqual(result.notes_rendered, 4)  # 8 拍 / 2 拍循环 = 4 次

    def test_unknown_instrument_falls_back(self) -> None:
        project = self._project()
        project.tracks[0].instrument = "banjo"
        result = render_project(project)
        self.assertGreater(result.peak, 0.0)
        self.assertTrue(any("合成器" in w for w in result.warnings))

    def test_note_starts_on_beat(self) -> None:
        project = self._project(bpm=60.0, length="6",
                                notes=[(0.0, 0.5, [60], 0.9), (4.0, 0.5, [72], 0.9)])
        result = render_project(project, normalize=False)
        mono = np.abs(result.audio.mean(axis=0))
        loud = np.where(mono > mono.max() * 0.05)[0]
        segments = np.split(loud, np.where(np.diff(loud) > 2000)[0] + 1)
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0][0] / 44100, 0.0, delta=0.02)
        self.assertAlmostEqual(segments[1][0] / 44100, 4.0, delta=0.02)

    def test_chord_is_louder_than_single_note(self) -> None:
        # 关掉归一化，否则两者峰值被拉到同一水平，比较失去意义
        single = render_project(self._project(notes=[(0.0, 1.0, [60], 0.8)]), normalize=False)
        chord = render_project(self._project(notes=[(0.0, 1.0, [60, 64, 67], 0.8)]), normalize=False)
        self.assertGreater(chord.rms, single.rms)


class InstrumentTests(unittest.TestCase):
    """乐器注册表与采样电平/音域。"""

    def test_sample_instruments_registered(self) -> None:
        for key in ("piano", "cello", "violin", "trumpet", "recorder", "guitar"):
            self.assertIn(key, fetch.SOURCES)
        for key, source in fetch.SOURCES.items():
            self.assertLess(source.midi_low, source.midi_high)
            self.assertTrue(source.note_range_label, key)
            self.assertGreaterEqual(source.mirror_count, 3)

    def test_chinese_aliases(self) -> None:
        self.assertEqual(fetch.resolve("大提琴"), "cello")
        self.assertEqual(fetch.resolve("小提琴"), "violin")
        self.assertEqual(fetch.resolve("小号"), "trumpet")
        self.assertEqual(fetch.resolve("钢琴"), "piano")
        self.assertEqual(fetch.resolve("竖笛"), "recorder")
        self.assertEqual(fetch.resolve("吉他"), "guitar")
        self.assertEqual(fetch.resolve("架子鼓"), "drums")
        self.assertEqual(fetch.resolve("鼓组"), "drums")
        self.assertEqual(fetch.resolve("民谣吉他"), "guitar")
        self.assertEqual(fetch.resolve(" Violin "), "violin")
        self.assertEqual(fetch.resolve(None), "piano")
        self.assertEqual(fetch.label("cello"), "大提琴")
        self.assertEqual(fetch.label("recorder"), "竖笛")
        self.assertEqual(fetch.label("guitar"), "吉他")
        self.assertEqual(fetch.label("drums"), "架子鼓")
        self.assertEqual(fetch.display("trumpet"), "小号 (trumpet)")

    def test_drums_are_builtin_not_downloadable(self) -> None:
        self.assertIn("drums", fetch.BUILTIN)
        self.assertNotIn("drums", fetch.SOURCES)
        self.assertIn("drums", fetch.instrument_keys())
        self.assertEqual(fetch.instrument_status("drums"), (0, 0))

    def test_sample_path_uses_resolved_key(self) -> None:
        root = Path(tempfile.mkdtemp())
        path = fetch.sample_path("大提琴", "C3", root)
        self.assertEqual(path.parent.name, "cello")
        self.assertEqual(path.suffix, ".mp3")

    def test_every_source_has_gain(self) -> None:
        for key in fetch.SOURCES:
            self.assertIn(key, INSTRUMENT_GAIN)
            self.assertGreater(INSTRUMENT_GAIN[key], 0)

    def test_gain_applied_per_instrument(self) -> None:
        if not fetch.available_notes("cello"):
            self.skipTest("大提琴采样未缓存")
        cello = SampleInstrument("cello")
        self.assertAlmostEqual(cello.instrument_gain, INSTRUMENT_GAIN["cello"])
        self.assertLess(cello.instrument_gain, INSTRUMENT_GAIN["piano"])  # 大提琴采样本身更响

    def test_out_of_range_note_falls_back(self) -> None:
        if not fetch.available_notes("cello"):
            self.skipTest("大提琴采样未缓存")
        warnings: list[str] = []
        cello = SampleInstrument("cello", warn=warnings.append)
        self.assertIsNone(cello.render_note(110, 0.8, 0.4))  # 远超大提琴音域
        self.assertTrue(any("音域" in w for w in warnings))
        self.assertIsNotNone(cello.render_note(60, 0.8, 0.4))

    def test_cello_timbre_differs_from_piano(self) -> None:
        if not (fetch.available_notes("cello") and fetch.available_notes("piano")):
            self.skipTest("采样未缓存")

        def centroid(instrument: str) -> float:
            project = P.Project(name="t", path=Path("t.musicproj"), bpm=120.0,
                                sample_rate=44100, length_spec="2", tail_seconds=0.0)
            track = P.Track(name="t", instrument=instrument, path=Path("t.xml"))
            track.notes.append(P.Note(0.0, 1.5, [60], 0.8))
            project.tracks.append(track)
            mono = render_project(project, normalize=False).audio.mean(axis=0)
            window = mono[: 16384] * np.hanning(16384)
            spectrum = np.abs(np.fft.rfft(window))
            freqs = np.fft.rfftfreq(len(window), 1 / 44100)
            return float((spectrum * freqs).sum() / max(spectrum.sum(), 1e-9))

        cello_c, piano_c = centroid("cello"), centroid("piano")
        self.assertGreater(cello_c, 0.0)
        self.assertGreater(abs(cello_c - piano_c), 80.0)


class DrumTests(unittest.TestCase):
    """内置合成鼓组：鼓件名解析、谱面往返、合成结果。"""

    def test_drum_pitch_aliases(self) -> None:
        cases = {
            "kick": 36, "底鼓": 36, "大鼓": 36, "bd": 36,
            "snare": 38, "军鼓": 38, "rim": 37, "clap": 39,
            "hat": 42, "hihat": 42, "踩镲": 42, "openhat": 46, "pedalhat": 44,
            "crash": 49, "吊镲": 49, "ride": 51, "bell": 53,
            "tom1": 50, "tom2": 48, "floor": 41, "落地嗵鼓": 41,
            "tamb": 54, "cowbell": 56, "shaker": 82,
        }
        for text, midi in cases.items():
            self.assertEqual(parse_pitch(text), midi, text)
        self.assertEqual(parse_pitches("kick,snare"), [36, 38])

    def test_pad_label_roundtrip(self) -> None:
        from musicgen.core.drums import pad_label

        for midi in (36, 37, 38, 39, 42, 44, 46, 49, 51, 53, 50, 48, 47, 45, 41, 54, 56, 82):
            self.assertEqual(parse_pitch(pad_label(midi)), midi, pad_label(midi))

    def test_drum_kit_is_deterministic_and_velocity_sensitive(self) -> None:
        from musicgen.core.drums import DrumInstrument

        kit = DrumInstrument(sample_rate=44100)
        first = kit.render_note(36, 0.8, 0.25)
        self.assertEqual(first.shape[0], 2)
        self.assertGreater(float(np.abs(first).max()), 0.05)
        self.assertTrue(np.array_equal(first, kit.render_note(36, 0.8, 0.25)))

        loud = float(np.abs(kit.render_note(38, 1.0, 0.25)).max())
        soft = float(np.abs(kit.render_note(38, 0.4, 0.25)).max())
        self.assertGreater(loud, soft * 1.8)

    def test_kick_is_low_and_snare_is_bright(self) -> None:
        from musicgen.core.drums import DrumInstrument

        kit = DrumInstrument(sample_rate=44100)

        def centroid(midi: int) -> float:
            mono = kit.render_note(midi, 0.8, 0.25).mean(axis=0)
            power = np.abs(np.fft.rfft(mono)) ** 2
            freqs = np.fft.rfftfreq(mono.size, 1 / 44100)
            return float((power * freqs).sum() / max(power.sum(), 1e-9))

        self.assertLess(centroid(36), 200.0)     # 底鼓能量集中在低频
        self.assertGreater(centroid(38), 1500.0)  # 军鼓以噪声为主

    def test_registry_returns_drum_kit(self) -> None:
        from musicgen.core.drums import DrumInstrument
        from musicgen.core.instruments import InstrumentRegistry

        registry = InstrumentRegistry()
        self.assertIsInstance(registry.get("drums"), DrumInstrument)
        self.assertIsInstance(registry.get("架子鼓"), DrumInstrument)

    def test_band_example_renders(self) -> None:
        if not BAND.exists():
            self.skipTest("示例工程不存在")
        project = P.load_project(BAND)
        self.assertEqual(len(project.tracks), 3)
        result = render_project(project, normalize=False)
        drums = [s for s in result.track_stats if s.instrument == "drums"]
        self.assertEqual(len(drums), 1)
        self.assertGreater(drums[0].notes, 50)
        self.assertGreater(drums[0].peak, 0.05)

    def test_moonlight_example_is_exactly_one_minute(self) -> None:
        """《月光》：60BPM × 15 小节 = 60.00s，三连音用 1/3 拍精确分数。"""
        if not MOONLIGHT.exists():
            self.skipTest("示例工程不存在")
        project = P.load_project(MOONLIGHT)
        self.assertEqual(project.warnings, [])
        # 只校验钢琴轨（《月光》的主体）；用户可能往示例里加自己的音轨，
        # 不断言工程总轨数，避免把正常的编辑误判成回归
        piano = next((t for t in project.tracks
                      if t.path and t.path.name == "piano.track.xml"), None)
        self.assertIsNotNone(piano, "找不到 piano.track.xml")
        notes = piano.notes
        self.assertEqual(len(notes), 226)
        self.assertAlmostEqual(project.compute_length_seconds(), 60.0, places=2)

        # 第 1 个音符是低音八度，随后是三组 1/3 拍的三连音
        for offset, expected in enumerate((0.0, 1 / 3, 2 / 3), start=1):
            self.assertAlmostEqual(notes[offset].time, expected, places=6)
        self.assertEqual(notes[1].pitches, [56])  # G#3

        result = render_project(project)
        self.assertAlmostEqual(result.duration, 60.0, delta=0.05)
        self.assertGreaterEqual(result.notes_rendered, 226)
        self.assertAlmostEqual(result.peak, 10 ** (-1 / 20), delta=0.02)


class WavTests(unittest.TestCase):
    def _audio(self) -> np.ndarray:
        t = np.arange(4410) / 44100.0
        tone = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
        return np.vstack([tone, tone])

    def test_bit_depths(self) -> None:
        import soundfile as sf

        for bits in (16, 24, 32):
            with tempfile.TemporaryDirectory() as tmp:
                path = write_wav(Path(tmp) / f"a{bits}.wav", self._audio(), 44100, bit_depth=bits)
                info = sf.info(str(path))
                self.assertEqual(info.samplerate, 44100)
                self.assertEqual(info.channels, 2)
                self.assertEqual(info.subtype, {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}[bits])
                data, rate = sf.read(str(path), dtype="float32", always_2d=True)
                self.assertEqual(data.shape, (4410, 2))
                self.assertAlmostEqual(float(np.abs(data).max()), 0.5, delta=0.01)

    def test_float32(self) -> None:
        import soundfile as sf

        with tempfile.TemporaryDirectory() as tmp:
            path = write_wav(Path(tmp) / "f.wav", self._audio(), 48000, bit_depth="float32")
            info = sf.info(str(path))
            self.assertEqual(info.subtype, "FLOAT")
            self.assertEqual(info.samplerate, 48000)

    def test_clipping_is_limited(self) -> None:
        import soundfile as sf

        loud = self._audio() * 4.0
        with tempfile.TemporaryDirectory() as tmp:
            path = write_wav(Path(tmp) / "c.wav", loud, 44100, bit_depth=16)
            data, _ = sf.read(str(path), dtype="float32")
            self.assertLessEqual(float(np.abs(data).max()), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
