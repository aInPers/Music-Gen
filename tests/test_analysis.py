"""音频分析单元测试：STFT、节拍、转谱、鼓件分类、音色模板与工程重建。

运行：``py -3 -m unittest tests.test_analysis -v``
（Demucs 分离需要模型权重，不在单元测试里跑；端到端验收见项目文档。）
"""

from __future__ import annotations

import json
import os
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

from musicgen.analysis import audio as audio_io  # noqa: E402
from musicgen.analysis import percussion, pitch, rhythm, spectrum, timbre  # noqa: E402
from musicgen.analysis.rebuild import TrackDraft, write_project  # noqa: E402
from musicgen.core.project import load_project  # noqa: E402
from musicgen.samples import fetch, registry  # noqa: E402

SR = 44100
RNG = np.random.default_rng(20261111)


def _tone(midi: int, duration: float, harmonics: int = 4, vibrato: float = 0.0,
          velocity: float = 0.5) -> np.ndarray:
    t = np.arange(int(duration * SR), dtype=np.float32) / SR
    base = 440.0 * 2 ** ((midi - 69) / 12.0)
    if vibrato:
        instantaneous = base * (1.0 + vibrato * np.sin(2 * np.pi * 5.5 * t))
        phase = 2 * np.pi * np.cumsum(instantaneous.astype(np.float64)) / SR
    else:
        phase = 2 * np.pi * base * t
    signal = np.zeros_like(t)
    for h in range(1, harmonics + 1):
        if base * h > SR * 0.45:
            break
        signal += (0.8 ** (h - 1)) * np.sin(phase * h)
    envelope = np.minimum(1.0, t / 0.01) * np.exp(-t * 1.4)
    return (velocity * envelope * signal).astype(np.float32)


def _kick(duration: float = 0.35) -> np.ndarray:
    t = np.arange(int(duration * SR), dtype=np.float32) / SR
    freq = 55.0 + 90.0 * np.exp(-t * 35.0)
    return (0.9 * np.sin(2 * np.pi * freq * t) * np.exp(-t * 18.0)).astype(np.float32)


def _snare(duration: float = 0.3) -> np.ndarray:
    t = np.arange(int(duration * SR), dtype=np.float32) / SR
    noise = RNG.standard_normal(t.size).astype(np.float32)
    body = np.sin(2 * np.pi * 190.0 * t).astype(np.float32)
    return (0.55 * noise + 0.35 * body) * np.exp(-t * 22.0).astype(np.float32)


def _hat(duration: float = 0.12) -> np.ndarray:
    t = np.arange(int(duration * SR), dtype=np.float32) / SR
    noise = RNG.standard_normal(t.size).astype(np.float32)
    bright = np.diff(noise, prepend=0.0)  # 简易高通，把能量抬到高频
    return (0.4 * bright * np.exp(-t * 55.0)).astype(np.float32)


class AudioResampleTests(unittest.TestCase):
    """解码与重采样：长音频的长度差 1 个样本不能抛广播错误。"""

    def test_long_resample_does_not_raise(self):
        """48k → 44.1k：resample_poly 的实际长度比 round 多 1，不能直接赋值。"""
        from musicgen.analysis.audio import resample

        rng = np.random.default_rng(7)
        for frames in (1245400, 960000):
            samples = rng.standard_normal((2, frames)).astype(np.float32)
            out = resample(samples, 48000, 44100)
            self.assertEqual(out.shape[0], 2)
            self.assertLessEqual(abs(out.shape[1] - frames * 44100 / 48000), 2)

    def test_same_rate_is_passthrough(self):
        from musicgen.analysis.audio import resample

        samples = np.zeros((2, 1000), dtype=np.float32)
        self.assertEqual(resample(samples, 44100, 44100).shape, (2, 1000))


class SpectrumTests(unittest.TestCase):
    def test_stft_shape_and_centroid(self):
        tone = _tone(69, 1.0)
        mag = spectrum.magnitude(spectrum.stft(tone))
        self.assertEqual(mag.shape[0], spectrum.DEFAULT_N_FFT // 2 + 1)
        freqs = spectrum.frequencies()
        peak = freqs[int(np.argmax(mag.mean(axis=1)))]
        self.assertAlmostEqual(float(peak), 440.0, delta=22.0)
        feats = spectrum.spectral_features(mag, freqs)
        self.assertGreater(float(np.median(feats.centroid)), 440.0)
        self.assertGreater(float(np.median(feats.flatness)), 0.0)

    def test_hpss_splits_tone_and_click(self):
        t = np.arange(int(1.0 * SR), dtype=np.float32) / SR
        tone = 0.4 * np.sin(2 * np.pi * 220.0 * t)
        click = np.zeros_like(t)
        click[1000:1200] = 0.9
        mag = spectrum.magnitude(spectrum.stft(tone + click))
        harmonic, percussive = spectrum.harmonic_percussive(mag)
        self.assertGreater(float(harmonic.sum()), 0.0)
        self.assertGreater(float(percussive.sum()), 0.0)

    def test_key_detection_on_c_major_chord(self):
        chord = sum(_tone(m, 1.0, harmonics=3) for m in (60, 64, 67))
        mag = spectrum.magnitude(spectrum.stft(chord))
        # 用整段平均谱当「色度」输入，C 大调应排在前两名
        chroma = spectrum.chroma(mag, spectrum.frequencies())
        key, score = spectrum.estimate_key(chroma)
        self.assertIn("major", key)
        self.assertGreater(score, 0.2)


class RhythmTests(unittest.TestCase):
    def setUp(self):
        self.signal = np.zeros(int(8.0 * SR), dtype=np.float32)
        for index in range(16):
            start = int(index * 0.5 * SR)  # 120BPM，每拍一下
            click = _kick(0.12)
            end = min(self.signal.size, start + click.size)
            self.signal[start:end] += click[: end - start]

    def test_tempo_estimate(self):
        mag = spectrum.magnitude(spectrum.stft(self.signal, n_fft=1024, hop=256))
        env = spectrum.onset_envelope(mag, SR / 256.0)
        bpm, confidence = rhythm.estimate_tempo(env, SR / 256.0)
        self.assertAlmostEqual(bpm, 120.0, delta=8.0)
        self.assertGreater(confidence, 0.1)

    def test_phase_and_quantize(self):
        self.assertEqual(rhythm.parse_grid("1/16"), 0.25)
        self.assertEqual(rhythm.parse_grid("off"), 0.0)
        self.assertAlmostEqual(rhythm.quantize_time(1.12, 0.125, 0.0), 1.125, places=6)
        self.assertAlmostEqual(rhythm.quantize_time(1.12, 0.125, 0.06), 1.06, places=6)
        self.assertAlmostEqual(rhythm.quantize_duration(0.03, 0.125, 0.06), 0.125, places=6)


class PitchTests(unittest.TestCase):
    def test_single_note(self):
        signal = _tone(69, 0.8, harmonics=5)
        notes = pitch.detect_notes(signal, SR, fmin=80, fmax=1200, max_polyphony=1)
        self.assertTrue(notes, "没有检测到音符")
        self.assertEqual(notes[0].midi, 69)
        self.assertGreater(notes[0].duration, 0.2)

    def test_two_note_sequence(self):
        signal = np.concatenate([_tone(60, 0.5, 4), _tone(67, 0.5, 4)])
        notes = pitch.detect_notes(signal, SR, fmin=80, fmax=1200, max_polyphony=1)
        self.assertEqual([n.midi for n in notes], [60, 67])

    def test_chord(self):
        signal = sum(_tone(m, 1.0, harmonics=4) for m in (60, 64, 67)).astype(np.float32)
        notes = pitch.detect_notes(signal, SR, fmin=80, fmax=1200, max_polyphony=3)
        found = {n.midi for n in notes}
        self.assertGreaterEqual(len(found & {60, 64, 67}), 2)

    def test_silence_gives_nothing(self):
        self.assertEqual(pitch.detect_notes(np.zeros(SR, dtype=np.float32), SR), [])


class DrumTests(unittest.TestCase):
    def test_classify_kick_snare_hat(self):
        signal = np.zeros(int(2.4 * SR), dtype=np.float32)
        events = [(0.0, _kick()), (0.6, _snare()), (1.2, _hat()),
                  (1.8, _kick())]
        for offset, sound in events:
            start = int(offset * SR)
            signal[start:start + sound.size] += sound
        hits = percussion.detect_hits(signal, SR)
        labels = [hit.detail.get("drum") for hit in hits]
        self.assertGreaterEqual(len(hits), 3)
        self.assertIn("kick", labels)
        self.assertIn("snare", labels)
        self.assertIn("hat", labels)


    def test_dense_sixteenth_hats_stay_hats(self):
        """129BPM 的 16 分踩镲 + 四拍底鼓：不能把连续踩镲判成吊镲。"""
        bpm = 129.0
        step = 60.0 / bpm / 4.0
        duration = 6.0
        signal = np.zeros(int(duration * SR), dtype=np.float32)
        t = 0.0
        index = 0
        while t < duration - 0.2:
            sound = _hat(0.10)
            start = int(t * SR)
            signal[start:start + sound.size] += 0.5 * sound
            if index % 4 == 0:
                kick = _kick(0.3)
                signal[start:start + kick.size] += kick
            t += step
            index += 1
        hits = percussion.detect_hits(signal, SR)
        labels = [hit.detail.get("drum") for hit in hits]
        self.assertGreaterEqual(labels.count("kick"), 10)
        self.assertGreaterEqual(labels.count("hat") + labels.count("openhat"), 15)
        self.assertLessEqual(labels.count("crash"), 2)


class TimbreTests(unittest.TestCase):
    def test_feature_shape(self):
        vector = timbre.segment_features(_tone(60, 0.5), SR)
        self.assertEqual(vector.shape[0], 55)
        self.assertTrue(np.all(np.isfinite(vector)))

    def test_template_bank_identifies_itself(self):
        available = [key for key in fetch.SOURCES if fetch.available_notes(key)]
        if len(available) < 2:
            self.skipTest("本机没有下载乐器采样")
        bank = timbre.TimbreBank(instruments=available[:3])
        self.assertGreater(bank.build(), 0)
        from musicgen.core.instruments import InstrumentRegistry

        registry_ = InstrumentRegistry(sample_rate=SR)
        key = available[0]
        piece = registry_.get(key).render_note(60, 0.8, 0.9)
        mono = np.mean(piece, axis=0).astype(np.float32)
        result = bank.classify(timbre.segment_features(mono, SR), 60)
        self.assertEqual(result.instrument, key)
        self.assertGreater(result.confidence, 0.3)


class RebuildTests(unittest.TestCase):
    def test_write_and_load_project(self):
        notes = [
            pitch.NoteEvent(start=0.0, duration=0.5, midi=60, velocity=0.8),
            pitch.NoteEvent(start=0.5, duration=0.5, midi=67, velocity=0.6),
        ]
        drums = [pitch.NoteEvent(start=0.0, duration=0.2, midi=36, velocity=0.9, instrument="drums")]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Song"
            written = write_project(
                target, "Song", bpm=120, sample_rate=SR, duration=2.0,
                tracks=[
                    TrackDraft("Lead", "piano", notes, gain_db=-2.0, pan=0.1),
                    TrackDraft("Drums", "drums", drums),
                ],
                report={"bpm": 120},
            )
            self.assertTrue(written.project_path.is_file())
            project = load_project(written.project_path)
            self.assertEqual(project.bpm, 120)
            self.assertEqual(len(project.tracks), 2)
            self.assertEqual(project.tracks[0].notes[0].pitches, [60])
            self.assertAlmostEqual(project.tracks[0].notes[0].time, 0.0)
            self.assertAlmostEqual(project.tracks[0].notes[1].time, 1.0)  # 0.5s @120BPM = 1 拍
            self.assertEqual(json.loads((target / "analysis.json").read_text("utf-8"))["bpm"], 120)

            with self.assertRaises(FileExistsError):
                write_project(
                    target, "Song", bpm=120, sample_rate=SR, duration=2.0,
                    tracks=[TrackDraft("Lead", "piano", notes)],
                )


class RegistryMigrationTests(unittest.TestCase):
    def test_old_registry_gains_new_instruments(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "instruments.json"
            file.write_text(json.dumps({
                "version": 1,
                "instruments": [{
                    "key": "piano", "label": "钢琴", "kind": "sample",
                    "gm": "acoustic_grand_piano", "midi_low": 21, "midi_high": 108,
                    "gain": 16.0,
                }],
            }), encoding="utf-8")
            old = os.environ.get("MUSICGEN_INSTRUMENTS")
            os.environ["MUSICGEN_INSTRUMENTS"] = str(file)
            try:
                entries = registry.load()
                keys = {entry["key"] for entry in entries}
                self.assertIn("piano", keys)
                self.assertIn("bass", keys)
                self.assertIn("vocal", keys)
                self.assertEqual(json.loads(file.read_text("utf-8"))["version"], registry.VERSION)
            finally:
                if old is None:
                    os.environ.pop("MUSICGEN_INSTRUMENTS", None)
                else:
                    os.environ["MUSICGEN_INSTRUMENTS"] = old


class PipelineTests(unittest.TestCase):
    """用打桩的分轨跑完整管线，不依赖 Demucs 与模型权重。"""

    def _stems(self, duration: float = 4.0):
        from musicgen.analysis.separate import StemSet

        size = int(duration * SR)
        vocals = np.zeros(size, dtype=np.float32)
        for index, midi in enumerate((60, 64)):
            tone = _tone(midi, 1.8, harmonics=6, vibrato=0.004)
            start = int(index * 2.0 * SR)
            vocals[start:start + tone.size] += tone
        bass = _tone(36, 3.5, harmonics=3)
        other = _tone(72, 3.5, harmonics=5)
        drums = np.zeros(size, dtype=np.float32)
        for beat in range(8):
            hit = _kick(0.25)
            start = int(beat * 0.5 * SR)
            drums[start:start + hit.size] += hit
        stems = {
            "drums": np.vstack([drums, drums]),
            "bass": np.vstack([bass, bass]),
            "other": np.vstack([other, other]),
            "vocals": np.vstack([vocals, vocals]),
            "piano": np.zeros((2, size), dtype=np.float32),
            "guitar": np.zeros((2, size), dtype=np.float32),
        }
        for key in stems:
            stems[key] = np.pad(stems[key], ((0, 0), (0, max(0, size - stems[key].shape[1]))))[:, :size]
        return StemSet(stems=stems, sample_rate=SR, model="htdemucs_6s")

    def test_pipeline_end_to_end(self):
        from musicgen.analysis import pipeline
        from unittest.mock import patch

        stem_set = self._stems()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Reverse"
            source = Path(temp) / "source.wav"
            mix = sum(stem_set.stems.values()) / len(stem_set.stems)
            audio_io.write_wav(source, mix, SR, subtype="FLOAT")
            options = pipeline.AnalysisOptions(
                source=source,
                output=target,
                bpm=120.0,
                quantize="1/16",
                model="htdemucs_6s",
                keep_stems=False,
                reuse_stems=False,
                # 这个用例验的是转谱与分轨归属；原曲采样另有专门用例
                sample_instruments=False,
            )
            # source 只用于存在性检查与时序，打桩后不会真读音频
            with patch.object(pipeline, "availability", return_value=(True, "")), \
                 patch.object(pipeline, "separate", return_value=stem_set):
                result = pipeline.analyze(options)

            self.assertTrue(result.project_path.is_file())
            project = load_project(result.project_path)
            self.assertEqual(project.bpm, 120.0)
            self.assertGreaterEqual(len(project.tracks), 3)
            self.assertGreater(result.note_count, 5)
            instruments = {track.instrument for track in project.tracks}
            self.assertIn("bass", instruments)
            self.assertIn("drums", instruments)

            # 人声不拆音高：整条分轨就是一条 <audio> 音频轨
            vocal = next(track for track in project.tracks if track.name == "人声")
            self.assertIsNotNone(vocal.audio_path)
            self.assertTrue(vocal.audio_path.is_file())
            self.assertEqual(len(vocal.notes), 0)
            # keep_stems=False 时其余分轨不落盘，但人声 WAV 必须留下
            self.assertTrue((target / "stems" / "vocals.wav").is_file())
            self.assertFalse((target / "stems" / "drums.wav").is_file())
            from musicgen.core.render import render_project

            rendered = render_project(project)
            self.assertTrue(any(stat.kind == "audio" for stat in rendered.track_stats))

            report = json.loads((target / "analysis.json").read_text("utf-8"))
            self.assertEqual(report["bpm"], 120.0)
            self.assertGreater(len(report["tracks"]), 2)
            self.assertEqual(report["stems"]["vocals"]["audio"], "stems/vocals.wav")
            self.assertEqual(report["stems"]["vocals"]["notes"], 0)
            self.assertEqual(report["mode"], "notes")

    def test_event_samples_no_cross_track_collision(self):
        """端到端：不同音轨的事件采样不能互相覆盖，文件必须都在。"""
        from musicgen.analysis import pipeline
        from unittest.mock import patch

        stem_set = self._stems()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Samples"
            source = Path(temp) / "source.wav"
            mix = sum(stem_set.stems.values()) / len(stem_set.stems)
            audio_io.write_wav(source, mix, SR, subtype="FLOAT")
            options = pipeline.AnalysisOptions(
                source=source, output=target, bpm=120.0,
                model="htdemucs_6s", keep_stems=True, reuse_stems=False,
                exact_replay=False,   # 旧路径：每条轨独立前缀，防 v1.28 覆盖 bug 回归
            )
            with patch.object(pipeline, "availability", return_value=(True, "")), \
                 patch.object(pipeline, "separate", return_value=stem_set):
                result = pipeline.analyze(options)

            per_track = []
            for draft in result.tracks:
                names = {note.detail["sample"] for note in draft.notes
                         if note.detail.get("sample")}
                if names:
                    per_track.append((draft.name, names))
            self.assertGreaterEqual(len(per_track), 2)
            for index, (name_a, names_a) in enumerate(per_track):
                for name_b, names_b in per_track[index + 1:]:
                    self.assertTrue(names_a.isdisjoint(names_b),
                                    f"{name_a} 与 {name_b} 的采样名冲突")
            all_names = set().union(*(names for _, names in per_track))
            missing = [name for name in all_names
                       if not (target / "samples" / f"{name}.wav").is_file()]
            self.assertFalse(missing, f"采样文件缺失或被覆盖：{missing[:5]}")

    def test_exact_replay_matches_stems(self):
        """精确回放：立体声块 + 补全轨 + 人声音频轨相加应等于原分轨求和。"""
        from musicgen.analysis import pipeline
        from musicgen.core.render import render_project
        from unittest.mock import patch

        stem_set = self._stems()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Exact"
            source = Path(temp) / "source.wav"
            mix = sum(stem_set.stems.values()) / len(stem_set.stems)
            audio_io.write_wav(source, mix, SR, subtype="FLOAT")
            options = pipeline.AnalysisOptions(
                source=source, output=target, bpm=120.0,
                model="htdemucs_6s", keep_stems=True, reuse_stems=False,
                sample_instruments=True,
            )
            with patch.object(pipeline, "availability", return_value=(True, "")), \
                 patch.object(pipeline, "separate", return_value=stem_set):
                result = pipeline.analyze(options)

            report = json.loads((target / "analysis.json").read_text("utf-8"))
            self.assertTrue(report["exact_replay"])
            self.assertTrue(list((target / "beds").glob("*.wav")))
            self.assertTrue(list((target / "samples").glob("*_b*.wav")))
            # 精确回放不做自动增益/EQ/母带压缩
            self.assertFalse(report["master_effects"])
            self.assertTrue(all(abs(t["gain_db"]) < 1e-6 for t in report["tracks"]))

            project = load_project(result.project_path)
            stamped = [note for track in project.tracks for note in track.notes
                       if note.src_time is not None]
            self.assertTrue(stamped)   # srcTime 必须能往返读写
            rendered = render_project(project)
            ref = sum(stem_set.stems.values())
            n = min(ref.shape[1], rendered.audio.shape[1])
            for ch in range(2):
                a = rendered.audio[ch, :n].astype(np.float64)
                b = ref[ch, :n].astype(np.float64)
                self.assertGreater(float(np.corrcoef(a, b)[0, 1]), 0.99)

    def test_audio_tracks_mode(self):
        from musicgen.analysis import pipeline
        from musicgen.core.render import render_project
        from unittest.mock import patch

        stem_set = self._stems()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Audio"
            source = Path(temp) / "source.wav"
            mix = sum(stem_set.stems.values()) / len(stem_set.stems)
            audio_io.write_wav(source, mix, SR, subtype="FLOAT")
            options = pipeline.AnalysisOptions(
                source=source,
                output=target,
                bpm=120.0,
                model="htdemucs_6s",
                keep_stems=True,
                reuse_stems=False,
                audio_tracks=True,
            )
            with patch.object(pipeline, "availability", return_value=(True, "")), \
                 patch.object(pipeline, "separate", return_value=stem_set):
                result = pipeline.analyze(options)

            project = load_project(result.project_path)
            self.assertEqual(len(project.tracks), 6)
            self.assertTrue(all(track.audio_path for track in project.tracks))
            self.assertEqual(result.note_count, 0)
            self.assertTrue(all(len(track.notes) == 0 for track in project.tracks))
            report = json.loads((target / "analysis.json").read_text("utf-8"))
            self.assertEqual(report["mode"], "audio")
            rendered = render_project(project)
            self.assertGreater(rendered.peak, 0.1)
            self.assertTrue(all(stat.kind == "audio" for stat in rendered.track_stats))

    def test_pipeline_rejects_existing_output(self):
        from musicgen.analysis import pipeline
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "Reverse"
            target.mkdir()
            (target / "Reverse.musicproj").write_text("<musicproj/>", encoding="utf-8")
            options = pipeline.AnalysisOptions(
                source=ROOT / "examples" / "demo" / "demo.musicproj",
                output=target, bpm=120.0, model="htdemucs_6s",
            )
            with patch.object(pipeline, "availability", return_value=(True, "")):
                with self.assertRaises(pipeline.AnalysisError):
                    pipeline.analyze(options)



class RegionTests(unittest.TestCase):
    """段落切分：三段不同织体，边界应落在换段处。"""

    def _signal(self) -> np.ndarray:
        sr = SR
        first = _tone(48, 6.0, harmonics=2, vibrato=0.0) * 0.5
        second = sum(_tone(m, 6.0, harmonics=5) for m in (60, 64, 67)) * 0.3
        third = np.zeros(int(6.0 * sr), dtype=np.float32)
        t = np.arange(third.size, dtype=np.float32) / sr
        third += (0.25 * np.sin(2 * np.pi * 900 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)))
        return np.concatenate([first, second, third]).astype(np.float32)

    def test_detects_boundaries(self):
        from musicgen.analysis import regions as rg

        signal = self._signal()
        found = rg.detect_regions(signal, SR, min_seconds=4.0, max_regions=6)
        self.assertGreaterEqual(len(found), 2)
        starts = [region.start for region in found]
        self.assertEqual(starts[0], 0.0)
        self.assertAlmostEqual(found[-1].end, signal.size / SR, places=2)
        # 至少有一个边界落在 6s / 12s 附近
        boundaries = starts[1:]
        self.assertTrue(any(abs(boundary - 6.0) < 2.0 or abs(boundary - 12.0) < 2.0
                            for boundary in boundaries), boundaries)
        for region in found:
            self.assertGreater(region.seconds, 0)
            self.assertLessEqual(region.rms_db, 0.0)

    def test_short_input_returns_single_region(self):
        from musicgen.analysis import regions as rg

        found = rg.detect_regions(_tone(60, 3.0), SR, min_seconds=6.0)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].label, "A")

    def test_attach_stem_levels(self):
        from musicgen.analysis import regions as rg

        found = rg.detect_regions(_tone(60, 8.0), SR, min_seconds=6.0)
        stems = {"loud": np.vstack([_tone(60, 8.0)] * 2),
                 "quiet": np.vstack([_tone(60, 8.0) * 0.01] * 2)}
        rg.attach_stem_levels(found, stems, SR)
        self.assertGreater(found[0].stems["loud"], found[0].stems["quiet"])


class LayerTests(unittest.TestCase):
    def _note(self, midi: int, start: float, duration: float = 0.5):
        from musicgen.analysis.pitch import NoteEvent

        return NoteEvent(start=start, duration=duration, midi=midi, confidence=0.7)

    def test_splits_two_registers(self):
        from musicgen.analysis.layers import split_layers

        notes = [self._note(midi, index * 0.5) for index, midi in enumerate([40, 43, 45, 47, 48, 50])]
        notes += [self._note(midi, 4 + index * 0.5) for index, midi in enumerate([76, 79, 81, 83, 84, 86])]
        parts = split_layers(notes, max_layers=3, min_notes=4)
        self.assertEqual(len(parts), 2)
        self.assertEqual([part.index for part in parts], [1, 2])
        self.assertLess(max(part.high for part in parts[:1]), min(part.low for part in parts[1:]))
        self.assertEqual(sum(len(part.notes) for part in parts), len(notes))

    def test_continuous_range_stays_single_layer(self):
        from musicgen.analysis.layers import split_layers

        notes = [self._note(60 + index, index * 0.3) for index in range(20)]
        parts = split_layers(notes, max_layers=3, min_notes=6)
        self.assertEqual(len(parts), 1)

    def test_sustain_ratio(self):
        from musicgen.analysis.layers import split_layers

        notes = [self._note(60, i * 0.6, 0.9) for i in range(8)]
        parts = split_layers(notes)
        self.assertAlmostEqual(parts[0].sustain_ratio, 1.0)


class DecisionTests(unittest.TestCase):
    def _drafts(self):
        from musicgen.analysis.pitch import NoteEvent
        from musicgen.analysis.rebuild import TrackDraft

        def draft(name, instrument, stem, midis, layer=0):
            notes = [NoteEvent(start=i * 0.5, duration=0.4, midi=m, confidence=0.6)
                     for i, m in enumerate(midis)]
            return TrackDraft(name=name, instrument=instrument, notes=notes, stem=stem,
                              layer=layer, layer_count=1 if layer else 0)

        return [
            draft("钢琴", "piano", "piano", [60, 64, 67]),
            draft("小提琴", "violin", "other", [72, 76], layer=2),
            draft("吉他", "guitar", "guitar", [55, 59]),
        ]

    def test_apply_retimbre_delete_and_patch(self):
        from musicgen.analysis import decisions as dc

        plan = dc.Decisions.from_dict({
            "version": 1,
            "regions": [{"index": 1, "label": "intro"}],
            "synth_patches": {
                "lead": {"name": "lead", "oscillators": ["saw"],
                         "amp": {"attack": 0.01, "release": 0.2}},
            },
            "tracks": [
                {"track": "钢琴", "instrument": "synth", "patch": "lead",
                 "name": "主音", "gain_db": -6, "effects": [{"type": "eq", "low": 2}]},
                {"track": "小提琴", "action": "delete"},
                {"track": "吉他", "action": "keep", "pan": 0.3},
            ],
        })
        with tempfile.TemporaryDirectory() as temp:
            regions = [type("R", (), {"index": 1, "label": "A"})()]
            kept, messages = dc.apply_decisions(
                self._drafts(), plan, output_dir=Path(temp), regions=regions
            )
            self.assertEqual(len(kept), 2)
            self.assertEqual(regions[0].label, "intro")
            lead = kept[0]
            self.assertEqual(lead.name, "主音")
            self.assertEqual(lead.instrument, "synth")
            self.assertIsNotNone(lead.patch_path)
            self.assertTrue(lead.patch_path.is_file())
            self.assertEqual(lead.gain_db, -6.0)
            self.assertEqual([spec.type for spec in lead.effects], ["eq"])
            self.assertAlmostEqual(kept[1].pan, 0.3)
            self.assertTrue(any("删除" in message for message in messages))

    def test_numeric_target_uses_stable_id(self):
        """id 必须在排序/分层后保持稳定：id 1 永远指同一条轨。"""
        from musicgen.analysis import decisions as dc

        drafts = self._drafts()
        for index, draft in enumerate(drafts, 1):
            draft.id = index
        # 按增益重排（简报是在排序后写出的，决策却在排序前应用）
        drafts.sort(key=lambda item: (item.instrument, item.name))
        by_id = {draft.id: draft.name for draft in drafts}
        plan = dc.Decisions.from_dict({"tracks": [{"track": 1, "action": "delete"}]})
        with tempfile.TemporaryDirectory() as temp:
            kept, messages = dc.apply_decisions(drafts, plan, output_dir=Path(temp), regions=[])
        self.assertEqual(len(kept), 2)
        self.assertNotIn(by_id[1], [draft.name for draft in kept])
        self.assertTrue(any(f"删除「{by_id[1]}」" in message for message in messages))

    def test_invalid_decisions_raise(self):
        from musicgen.analysis import decisions as dc

        with self.assertRaises(dc.DecisionError):
            dc.Decisions.from_dict({"tracks": [{"action": "rewrite"}]})
        with self.assertRaises(dc.DecisionError):
            dc.Decisions.from_dict({"version": 9, "tracks": []})
        with self.assertRaises(dc.DecisionError):
            dc.Decisions.from_dict({"synth_patches": {"x": {"oscillators": [{"wave": "nope"}]}}})

        # 引用了不存在的脚本：解析阶段允许，应用阶段报错
        plan = dc.Decisions.from_dict(
            {"tracks": [{"track": "钢琴", "patch": "missing_patch"}]}
        )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(dc.DecisionError):
                dc.apply_decisions(self._drafts(), plan, output_dir=Path(temp), regions=[])

    def test_unknown_track_is_ignored(self):
        from musicgen.analysis import decisions as dc

        plan = dc.Decisions.from_dict({"tracks": [{"track": "不存在", "gain_db": -3}]})
        with tempfile.TemporaryDirectory() as temp:
            kept, messages = dc.apply_decisions(
                self._drafts(), plan, output_dir=Path(temp), regions=[]
            )
        self.assertEqual(len(kept), 3)
        self.assertEqual(messages, [])


class BriefTests(unittest.TestCase):
    def test_brief_structure_and_candidates(self):
        from musicgen.analysis import brief
        from musicgen.analysis.pitch import NoteEvent
        from musicgen.analysis.rebuild import TrackDraft
        from musicgen.analysis.timbre import TimbreBank

        notes = [NoteEvent(start=i * 0.5, duration=0.4, midi=60 + i, confidence=0.6)
                 for i in range(6)]
        drafts = [
            TrackDraft(name="钢琴", instrument="piano", notes=notes, stem="piano",
                       gain_db=-6, pan=0.1, confidence=0.5),
            TrackDraft(name="鼓组", instrument="drums", stem="drums",
                       notes=[NoteEvent(start=0.0, duration=0.2, midi=36, confidence=0.9)],
                       gain_db=-2, confidence=0.8),
        ]
        stems = {
            "piano": np.vstack([_tone(72, 3.0, harmonics=4)] * 2),
            "drums": np.vstack([_kick(0.3)] * 2),
        }
        bank = TimbreBank(sample_rate=SR, instruments=["synth_pad", "synth_lead", "piano"])
        bank.build()
        payload = brief.build_brief(
            drafts=drafts, regions=[], stems=stems, sample_rate=SR,
            bpm=120.0, key="C major", duration=3.0, source="test.wav", bank=bank,
        )
        self.assertIn("tracks", payload)
        self.assertEqual(len(payload["tracks"]), 2)
        piano, drums = payload["tracks"]
        self.assertEqual(piano["instrument"], "piano")
        self.assertIn("features", piano)
        self.assertIn("centroid_hz", piano["features"])
        self.assertTrue(piano["candidates"])
        self.assertEqual(drums["candidates"], [])
        self.assertIn("note", drums)
        self.assertIn("synth_presets", payload["global"])
        self.assertTrue(any(item["patch"] == "warm_pad" for item in payload["global"]["synth_presets"]))
        self.assertIn("decisions_schema", payload["instructions"])
        json.dumps(payload)  # 必须可序列化



class SampleExtractTests(unittest.TestCase):
    """原曲采样：从分轨切音做采样乐器，让音色就是原曲的音色。"""

    def _tone(self, freq: float, seconds: float, sr: int) -> np.ndarray:
        t = np.arange(int(seconds * sr)) / sr
        return (0.6 * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    def test_extract_melodic_samples(self):
        from musicgen.analysis.pitch import NoteEvent
        from musicgen.analysis import sample_extract

        sr = 22050
        pitches = [48, 55, 62, 69]
        pieces = []
        notes = []
        for index, pitch in enumerate(pitches):
            freq = 440.0 * 2 ** ((pitch - 69) / 12)
            piece = self._tone(freq, 0.8, sr)
            notes.append(NoteEvent(start=index * 1.0, duration=0.7, midi=pitch))
            pieces.append(np.pad(piece, (0, int(0.2 * sr))))
        mono = np.concatenate(pieces)[: int(4.0 * sr)]

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "src"
            written = sample_extract.extract_track_samples(mono, sr, notes, directory)
            self.assertGreaterEqual(written, 2)
            files = sorted(directory.glob("*.wav"))
            self.assertEqual(len(files), written)
            from musicgen.core.notes import parse_pitch
            for path in files:
                parse_pitch(path.stem)   # 文件名必须能被采样器解析

    def test_registry_uses_samples_for_custom_key(self):
        from musicgen.analysis import sample_extract
        from musicgen.analysis.pitch import NoteEvent
        from musicgen.core.instruments import InstrumentRegistry
        from musicgen.core.sampler import SampleInstrument

        sr = 22050
        mono = np.concatenate([self._tone(220.0, 0.8, sr),
                               np.zeros(int(0.2 * sr), dtype=np.float32)])
        notes = [NoteEvent(start=0.0, duration=0.7, midi=57)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "instruments"
            written = sample_extract.extract_track_samples(mono, sr, notes, root / "src_test")
            self.assertGreaterEqual(written, 1)
            instrument = InstrumentRegistry(cache_root=root, sample_rate=sr, base_dir=".").get("src_test")
            self.assertIsInstance(instrument, SampleInstrument)
            self.assertTrue(instrument.raw)      # 原曲采样不做电平补偿与力度低通
            out = instrument.render_note(57, 0.8, 0.4)
            self.assertIsNotNone(out)
            self.assertGreater(float(np.max(np.abs(out))), 0.01)

    def test_event_samples_are_isolated_per_track(self):
        """两条轨共用 samples/ 时，采样名必须不同且内容各归各轨。

        回归：以前每条轨都从 e00001 起编号，后写的轨覆盖先写的轨，
        鼓/贝斯的音符会播到吉他或合成层的片段，整首伴奏听起来是乱的。
        """
        from musicgen.analysis.pitch import NoteEvent
        from musicgen.analysis import sample_extract

        sr = 22050

        def signal(freqs):
            parts = []
            for freq in freqs:
                parts.append(self._tone(freq, 0.5, sr))
                parts.append(np.zeros(int(0.5 * sr), dtype=np.float32))
            return np.concatenate(parts)

        low_freqs = [220.0, 277.18, 329.63]
        high_freqs = [880.0, 1108.73, 1318.51]
        notes = [NoteEvent(start=i * 1.0, duration=0.4, midi=60) for i in range(3)]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "samples"
            low_map = sample_extract.extract_event_samples(
                signal(low_freqs), sr, notes, root, prefix="t01_")
            high_map = sample_extract.extract_event_samples(
                signal(high_freqs), sr, notes, root, prefix="t02_")

            self.assertTrue(set(low_map.values()).isdisjoint(high_map.values()))
            files = sorted(root.glob("*.wav"))
            self.assertEqual(len(files),
                             len(set(low_map.values())) + len(set(high_map.values())))

            def dominant(path):
                loaded = audio_io.load(path, sample_rate=None)
                mono = np.mean(loaded.samples, axis=0)
                mag = np.abs(np.fft.rfft(mono * np.hanning(mono.size)))
                return float(np.argmax(mag) * loaded.sample_rate / mono.size)

            for name in low_map.values():
                self.assertLess(dominant(root / f"{name}.wav"), 500.0)
            for name in high_map.values():
                self.assertGreater(dominant(root / f"{name}.wav"), 700.0)


class EditabilityTests(unittest.TestCase):
    """可编辑性：鼓件拆轨、同轨去重叠、指标计算。"""

    def test_drum_families_split(self):
        from musicgen.analysis.pipeline import DRUM_FAMILIES, _group_drums
        from musicgen.analysis.pitch import NoteEvent

        # 底鼓/军鼓/踩镲各 20 下，再来 3 个吊镲（小家族应并进镲片）
        notes = []
        for i in range(20):
            for pitch in (36, 38, 42):
                notes.append(NoteEvent(start=i * 0.5, duration=0.1, midi=pitch))
        for i in range(3):
            notes.append(NoteEvent(start=i * 2.0, duration=0.1, midi=49))
        buckets = _group_drums(notes)
        self.assertEqual(set(buckets), {"底鼓", "军鼓", "踩镲", "镲片"})
        self.assertEqual(len(buckets["底鼓"]), 20)
        self.assertEqual(len(buckets["镲片"]), 3)
        self.assertEqual(len(DRUM_FAMILIES), 5)

    def test_trim_overlaps_keeps_chords(self):
        from musicgen.analysis.pipeline import _trim_overlaps
        from musicgen.analysis.pitch import NoteEvent

        # 和弦（同一起音）都要保留，不能互相裁掉
        chord = [NoteEvent(start=0.0, duration=1.0, midi=60),
                 NoteEvent(start=0.0, duration=1.0, midi=64),
                 NoteEvent(start=0.0, duration=1.0, midi=67)]
        nxt = NoteEvent(start=0.5, duration=0.4, midi=72)
        trimmed = _trim_overlaps(chord + [nxt], minimum=0.05)
        self.assertEqual(trimmed, 3)
        for note in chord:
            self.assertAlmostEqual(note.duration, 0.5, places=3)
        self.assertAlmostEqual(nxt.duration, 0.4, places=3)

    def test_merge_tiny_drafts(self):
        from musicgen.analysis.pipeline import _merge_tiny_drafts
        from musicgen.analysis.pitch import NoteEvent
        from musicgen.analysis.rebuild import TrackDraft

        big = TrackDraft(name="合成垫", instrument="synth_pad", stem="other",
                         notes=[NoteEvent(start=i * 0.1, duration=0.1, midi=60) for i in range(40)])
        tiny = TrackDraft(name="竖笛", instrument="recorder", stem="other",
                          notes=[NoteEvent(start=i * 0.5, duration=0.1, midi=72) for i in range(5)])
        keep = TrackDraft(name="大提琴", instrument="cello", stem="other",
                          notes=[NoteEvent(start=i * 0.5, duration=0.1, midi=45) for i in range(30)])
        mono = np.zeros(SR, dtype=np.float32)
        out = _merge_tiny_drafts([big, tiny, keep], lambda _stem: mono, SR)
        names = {draft.name for draft in out}
        self.assertIn("合成垫", names)
        self.assertIn("大提琴", names)      # 30 音不算碎片，保留
        self.assertNotIn("竖笛", names)     # 5 音并进主轨
        self.assertEqual(len(big.notes), 45)

    def test_editability_metrics(self):
        from musicgen.analysis.pipeline import _editability
        from musicgen.analysis.pitch import NoteEvent

        notes = [NoteEvent(start=i * 0.5, duration=0.25, midi=60 + i % 3) for i in range(8)]
        info = _editability(notes, bpm=120.0, span=4.0, grid_seconds=0.125)
        self.assertEqual(info["notes"], 8)
        self.assertAlmostEqual(info["mean_beats"], 0.5, places=2)
        self.assertLessEqual(info["overlap"], 1.0)
        self.assertEqual(info["on_grid"], 1.0)
        self.assertEqual(info["too_short"], 0.0)


class CliTests(unittest.TestCase):
    def test_analyze_parser(self):
        from musicgen.cli import build_parser

        args = build_parser().parse_args(["analyze", "song.mp3", "--bpm", "-120"])
        self.assertEqual(args.command, "analyze")
        self.assertEqual(args.bpm, -120.0)  # 负数走 _normalize_argv 的等号改写
        self.assertEqual(args.quantize, "1/16")
        self.assertFalse(args.audio_tracks)
        args = build_parser().parse_args(["analyze", "song.mp3", "--audio-tracks"])
        self.assertTrue(args.audio_tracks)

    def test_render_track_selection(self):
        from musicgen.cli import _parse_track_selection

        project = load_project(ROOT / "examples" / "demo" / "demo.musicproj")
        self.assertIsNone(_parse_track_selection(project, None))
        self.assertEqual(_parse_track_selection(project, "1,3"), [0, 2])
        self.assertEqual(
            _parse_track_selection(project, project.tracks[1].name), [1]
        )
        # 越界序号/不存在的轨名一律判为无效
        self.assertIsNone(_parse_track_selection(project, "99"))
        self.assertIsNone(_parse_track_selection(project, "不存在的轨"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
