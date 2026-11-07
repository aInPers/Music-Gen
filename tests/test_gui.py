"""GUI 冒烟测试：打开工程、渲染、波形绘制、参数保存、静音切换。

需要一个可用的图形环境（本机桌面）。没有 tkinter 时自动跳过。
运行：``py -3 -m unittest discover -s tests -v``
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import tkinter  # noqa: F401

    from musicgen.gui.app import MusicGenApp

    _TK_OK = True
    _TK_ERR = ""
except Exception as exc:  # noqa: BLE001 - 无 tkinter / 无显示环境
    _TK_OK = False
    _TK_ERR = str(exc)

DEMO_DIR = ROOT / "examples" / "demo"


@unittest.skipUnless(_TK_OK, f"图形环境不可用: {_TK_ERR}")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="musicgen_gui_")
        target = Path(cls.tmp) / "demo"
        shutil.copytree(DEMO_DIR, target)
        cls.project_path = target / "demo.musicproj"
        cls.app = MusicGenApp(None)
        cls.app.update()
        cls.app.open_project(str(cls.project_path))
        cls.app.update()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.destroy()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pump(self, seconds: float = 0.2) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.01)

    def test_project_loaded(self) -> None:
        self.assertIsNotNone(self.app.project)
        self.assertEqual(len(self.app.project.tracks), 4)
        self.assertEqual(len(self.app.track_holder.winfo_children()), 4)
        self.assertIn("已载入", self.app.lbl_status.cget("text"))

    def test_piano_roll_drawn(self) -> None:
        self.app.roll.redraw()
        self.assertGreater(len(self.app.roll.find_all()), 40)
        self.assertGreater(len(self.app.roll.find_withtag("note:0")), 0)

    def test_select_track_updates_panel(self) -> None:
        self.app.select_track(2)
        self.app.update()
        self.assertEqual(self.app.selected, 2)
        self.assertEqual(self.app.prop_labels["name"].cget("text"), "Bass")
        self.assertEqual(self.app.prop_labels["notes"].cget("text"), "16")

    def test_toggle_mute(self) -> None:
        before = self.app.project.tracks[0].mute
        self.app._toggle(0, "mute")
        self.app.update()
        self.assertNotEqual(self.app.project.tracks[0].mute, before)
        self.app._toggle(0, "mute")
        self.app.update()
        self.assertEqual(self.app.project.tracks[0].mute, before)

    def test_apply_saves_xml(self) -> None:
        self.app.select_track(1)
        self.app._vars["gain_db"].set(-7.5)
        self.app._vars["pan"].set(-0.4)
        self.app.on_apply()
        self.app.update()
        text = (self.project_path.parent / "tracks" / "ostinato.track.xml").read_text(encoding="utf-8")
        self.assertIn('gain="-7.5dB"', text)
        self.assertIn('pan="-0.4"', text)
        # 重新载入应保留新值（工程层覆盖已被清除）
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.assertAlmostEqual(self.app.project.tracks[1].gain_db, -7.5)

    def test_render_produces_waveform(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.app.on_render()
        deadline = time.time() + 60
        while self.app.result is None and time.time() < deadline:
            self.app.update()
            time.sleep(0.02)
        self.assertIsNotNone(self.app.result, "渲染超时")
        self.app.update()
        self.assertGreater(self.app.result.peak, 0.5)
        self.assertGreater(len(self.app.wave.find_all()), 100)
        self.assertEqual(str(self.app.btn_export["state"]), "normal")
        self.assertEqual(str(self.app.btn_play["state"]), "normal")
        self.assertIn("峰值", self.app.lbl_render_info.cget("text"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
