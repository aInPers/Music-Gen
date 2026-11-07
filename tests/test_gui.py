"""GUI 冒烟测试：打开工程、切页、卷帘绘制、参数保存、静音切换、后台渲染。

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

    # ---- 载入与布局 ---------------------------------------------------
    def test_project_loaded(self) -> None:
        self.assertIsNotNone(self.app.project)
        self.assertEqual(len(self.app.project.tracks), 4)
        self.assertEqual(len(self.app.explorer.track_items), 4)
        self.assertIn("已打开", self.app.status.message.cget("text"))
        self.assertEqual(len(self.app.menu_bar.buttons), 5)

    def test_document_pages(self) -> None:
        self.app.goto_page("info")
        self.app.update()
        self.assertEqual(self.app.page, "info")
        self.assertTrue(self.app.info.winfo_ismapped())
        self.app.goto_page("roll")
        self.app.update()
        self.assertTrue(self.app.roll.winfo_ismapped())
        self.assertFalse(self.app.info.winfo_ismapped())

    def test_piano_roll_drawn(self) -> None:
        self.app.goto_page("roll")
        self.app.roll.redraw()
        self.assertGreater(len(self.app.roll.canvas.find_all()), 60)
        self.assertGreater(len(self.app.roll.canvas.find_withtag("note:0")), 0)

    def test_select_track_updates_properties(self) -> None:
        self.app.select_track(2)
        self.app.update()
        self.assertEqual(self.app.selected, 2)
        self.assertEqual(self.app.props.labels["name"].cget("text"), "Bass")
        self.assertEqual(self.app.props.labels["notes"].cget("text"), "16")
        self.assertAlmostEqual(self.app.props.vars["gain_db"].get(), -5.0)

    def test_toggle_mute(self) -> None:
        before = self.app.project.tracks[0].mute
        self.app._toggle(0, "mute")
        self.app.update()
        self.assertNotEqual(self.app.project.tracks[0].mute, before)
        self.app._toggle(0, "mute")
        self.app.update()
        self.assertEqual(self.app.project.tracks[0].mute, before)

    # ---- 编辑回写 -----------------------------------------------------
    def test_apply_saves_xml(self) -> None:
        self.app.select_track(1)
        self.app.props.vars["gain_db"].set(-7.5)
        self.app.props.vars["pan"].set(-0.4)
        self.app.on_apply()
        self.app.update()
        text = (self.project_path.parent / "tracks" / "ostinato.track.xml").read_text(
            encoding="utf-8")
        self.assertIn('gain="-7.5dB"', text)
        self.assertIn('pan="-0.4"', text)
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.assertAlmostEqual(self.app.project.tracks[1].gain_db, -7.5)

    # ---- 渲染 ---------------------------------------------------------
    def test_render_produces_waveform(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.app.on_render()
        deadline = time.time() + 90
        while self.app.result is None and time.time() < deadline:
            self.app.update()
            time.sleep(0.02)
        self.assertIsNotNone(self.app.result, "渲染超时")
        self._pump(0.4)
        self.assertGreater(self.app.result.peak, 0.5)
        self.assertGreater(len(self.app.wave.canvas.find_all()), 100)
        self.assertIn("峰值", self.app.wave.summary)
        self.assertTrue(self.app.toolbar.btn_export.enabled)
        self.assertTrue(self.app.toolbar.btn_play.enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
