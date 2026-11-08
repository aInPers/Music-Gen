"""轨道编辑：``core.trackedit`` 与 CLI 的 ``track`` / ``note`` 命令。

所有用例都在临时副本上操作，不碰仓库里的示例工程。
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicgen import cli  # noqa: E402
from musicgen.core import trackedit as te  # noqa: E402
from musicgen.core.project import load_project  # noqa: E402


class _ProjectCopy(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mgtrk_"))
        self.project = self.tmp / "demo" / "demo.musicproj"
        shutil.copytree(ROOT / "examples" / "demo", self.project.parent)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self):
        return load_project(self.project)

    @staticmethod
    def inject_comment(path: Path, marker: str = "保留我") -> str:
        """在 <track> 根元素内部插一条注释，用来验证编辑不会冲掉注释。"""
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.lstrip().startswith("<track "):
                lines.insert(index + 1, f"  <!-- {marker} -->\n")
                break
        else:  # pragma: no cover - 工程文件总能找到 <track>
            raise AssertionError(f"没有找到 <track> 起始标签：{path}")
        path.write_text("".join(lines), encoding="utf-8")
        return marker


class TrackEditTests(_ProjectCopy):
    def test_add_track_writes_files_and_keeps_project_comments(self) -> None:
        before = self.project.read_text(encoding="utf-8")
        self.assertIn("每条音轨一个文件", before)

        ref = te.add_track(self.project, name="大提琴声部", instrument="cello",
                           gain_db=-4, notes=[(0, 2, [48], 0.7)])
        self.assertEqual(ref.index, 5)
        self.assertEqual(ref.relative, "tracks/大提琴声部.track.xml")
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("每条音轨一个文件", text)          # 原注释保留
        self.assertIn('file="tracks/大提琴声部.track.xml"', text)
        self.assertIn("音符：time", ref.path.read_text(encoding="utf-8"))

        project = self.load()
        self.assertEqual(len(project.tracks), 5)
        self.assertEqual(project.tracks[4].instrument, "cello")
        self.assertEqual(project.tracks[4].gain_db, -4.0)
        self.assertEqual(project.tracks[4].notes[0].pitches, [48])

    def test_add_track_avoids_name_collision(self) -> None:
        first = te.add_track(self.project, name="Lead")
        second = te.add_track(self.project, name="Lead")
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(second.path.name.endswith("_2.track.xml"))
        self.assertEqual(len(self.load().tracks), 6)

    def test_find_track_by_index_name_and_filename(self) -> None:
        project = self.load()
        for selector in ("2", "Ostinato", "ostinato", "ostinato.track.xml", "stin"):
            index, track = te.find_track(project, selector)
            self.assertEqual(index, 1)
            self.assertEqual(track.name, "Ostinato")
        with self.assertRaises(te.TrackEditError):
            te.find_track(project, "99")
        with self.assertRaises(te.TrackEditError):
            te.find_track(project, "不存在的轨")

    def test_edit_track_updates_attributes_and_clears_override(self) -> None:
        # demo 的 bass 轨在工程层有 pan="-0.05" 覆盖
        self.assertIn('pan="-0.05"', self.project.read_text(encoding="utf-8"))
        bass_path = self.load().tracks[2].path
        marker = self.inject_comment(bass_path)
        ref, applied = te.edit_track(self.project, "Bass", instrument="cello",
                                     gain_db=-8, name="Cello Bass", transpose=-12)
        self.assertEqual(ref.name, "Cello Bass")
        self.assertEqual({field for field, _, _ in applied},
                         {"instrument", "gain_db", "name", "transpose"})
        text = self.project.read_text(encoding="utf-8")
        self.assertNotIn('pan="-0.05"', text)      # 工程层覆盖已清掉
        track = self.load().tracks[2]
        self.assertEqual(track.instrument, "cello")
        self.assertEqual(track.gain_db, -8.0)
        self.assertEqual(track.transpose, -12)
        self.assertEqual(track.name, "Cello Bass")
        # 音轨文件原有的注释仍在
        self.assertIn(marker, track.path.read_text(encoding="utf-8"))

    def test_edit_track_reports_unknown_field(self) -> None:
        with self.assertRaises(te.TrackEditError):
            te.edit_track(self.project, 1, nonsense=1)

    def test_remove_track_keeps_file_by_default(self) -> None:
        ref = te.add_track(self.project, name="Temp")
        name, relative, deleted = te.remove_track(self.project, ref.index)
        self.assertEqual(name, "Temp")
        self.assertEqual(relative, ref.relative)
        self.assertFalse(deleted)
        self.assertTrue(ref.path.is_file())
        self.assertEqual(len(self.load().tracks), 4)

        te.add_track(self.project, name="Temp", file=ref.relative, force=True)
        _, _, deleted = te.remove_track(self.project, "Temp", delete_file=True)
        self.assertTrue(deleted)
        self.assertFalse(ref.path.exists())

    def test_remove_last_track_rejected(self) -> None:
        empty_dir = self.tmp / "empty"
        from musicgen.core.project import create_project

        project_file = create_project(empty_dir, template="empty")
        with self.assertRaises(te.TrackEditError):
            te.remove_track(project_file, 1)

    def test_note_add_set_delete_list_preserve_comments(self) -> None:
        ref = te.add_track(self.project, name="Notes", notes=[(0, 1, [60], 0.7)])
        index = te.add_note(self.project, "Notes", time=1, dur=0.5,
                            pitches=[64, 67], velocity=0.6)
        self.assertEqual(index, 2)
        applied = te.set_note(self.project, "Notes", 1, pitches=[62], velocity=0.9)
        self.assertEqual({field for field, _, _ in applied}, {"pitches", "velocity"})
        removed = te.delete_note(self.project, "Notes", 2)
        self.assertEqual(removed["pitch"], "E4,G4")
        notes = te.list_notes(self.project, "Notes")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][1].pitches, [62])
        self.assertEqual(notes[0][1].velocity, 0.9)
        # 新增轨道里的语法注释始终保留
        self.assertIn("音符：time", ref.path.read_text(encoding="utf-8"))

    def test_out_of_range_note_index(self) -> None:
        with self.assertRaises(te.TrackEditError):
            te.set_note(self.project, 1, 9999, velocity=0.5)
        with self.assertRaises(te.TrackEditError):
            te.delete_note(self.project, 1, 0)

    def test_drum_track_writes_pad_names(self) -> None:
        ref = te.add_track(self.project, name="Drums", instrument="drums",
                           notes=[(0, 1, [36, 42], 0.8)])
        text = ref.path.read_text(encoding="utf-8")
        self.assertIn('pitch="底鼓,闭镲"', text)
        self.assertEqual(te.list_notes(self.project, "Drums")[0][1].pitches, [36, 42])

    def test_parse_helpers(self) -> None:
        self.assertAlmostEqual(te.parse_beats("1/3"), 1 / 3)
        self.assertEqual(te.parse_gain("-3dB"), -3.0)
        self.assertEqual(te.parse_pitch_list("kick,snare"), [36, 38])
        with self.assertRaises(te.TrackEditError):
            te.parse_pitch_list("H9")


class CliTrackTests(_ProjectCopy):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_track_and_note_flow(self) -> None:
        code, out, _ = self.run_cli("track", "list", str(self.project))
        self.assertEqual(code, 0)
        self.assertIn("4 条音轨", out)

        code, out, _ = self.run_cli(
            "track", "add", str(self.project), "--name", "Guitar",
            "--instrument", "吉他", "--gain", "-4dB",
            "--note", "0 1 C4,E4 0.7", "--note", "1 1 D4 0.6",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("已新增音轨 5. Guitar", out)
        self.assertEqual(len(self.load().tracks), 5)

        code, out, _ = self.run_cli("track", "edit", str(self.project),
                                    "--track", "Guitar", "--name", "Guitar 2",
                                    "--gain", "-6dB", "--no-loop")
        self.assertEqual(code, 0)
        self.assertIn("gain_db", out)

        code, out, _ = self.run_cli("note", "list", str(self.project), "--track", "Guitar 2")
        self.assertEqual(code, 0)
        self.assertIn("2 个音符", out)

        code, out, _ = self.run_cli("note", "add", str(self.project),
                                    "--track", "Guitar 2", "--time", "2",
                                    "--dur", "1/2", "--pitch", "F#4", "--vel", "0.5")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("note", "set", str(self.project),
                                    "--track", "Guitar 2", "--index", "1",
                                    "--pitch", "C5", "--vel", "0.9")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("note", "del", str(self.project),
                                    "--track", "Guitar 2", "--index", "2")
        self.assertEqual(code, 0)
        notes = te.list_notes(self.project, "Guitar 2")
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0][1].pitches, [72])

        code, _, _ = self.run_cli("validate", str(self.project))
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("track", "remove", str(self.project), "--track", "Guitar 2")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.load().tracks), 4)

    def test_negative_gain_without_equals(self) -> None:
        """``--gain -4dB`` 不该被 argparse 当成新选项。"""
        code, out, err = self.run_cli("track", "edit", str(self.project),
                                      "--track", "1", "--gain", "-4dB")
        self.assertEqual(code, 0, err)
        self.assertIn("gain_db", out)
        code, _, _ = self.run_cli("track", "edit", str(self.project),
                                  "--track", "1", "--gain=-7dB")
        self.assertEqual(code, 0)
        self.assertEqual(self.load().tracks[0].gain_db, -7.0)

    def test_error_exit_codes(self) -> None:
        code, _, err = self.run_cli("track", "edit", str(self.project),
                                    "--track", "99", "--gain", "-3dB")
        self.assertEqual(code, 1)
        self.assertIn("音轨序号", err)
        code, _, err = self.run_cli("note", "add", str(self.project), "--track", "1",
                                    "--time", "0", "--dur", "1", "--pitch", "H9")
        self.assertEqual(code, 1)
        self.assertIn("音高", err)
        bad = self.tmp / "bad.musicproj"
        bad.write_text('<musicproj><track file="nope.xml"/></musicproj>', encoding="utf-8")
        code, _, err = self.run_cli("track", "list", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("工程文件有问题", err)

    def test_cli_edits_preserve_comments(self) -> None:
        first_path = self.load().tracks[0].path
        marker = self.inject_comment(first_path)
        self.run_cli("track", "edit", str(self.project), "--track", "2", "--gain", "-5dB")
        text = self.project.read_text(encoding="utf-8")
        self.assertIn("每条音轨一个文件", text)       # 工程文件注释
        self.run_cli("note", "add", str(self.project), "--track", "1",
                     "--time", "20", "--dur", "1", "--pitch", "C5")
        self.assertIn(marker, first_path.read_text(encoding="utf-8"))  # 音轨文件注释


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
