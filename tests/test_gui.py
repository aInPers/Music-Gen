"""GUI 冒烟测试：打开/新建工程、切页、卷帘、参数回写、停靠、预览播放。

需要一个可用的图形环境（本机桌面）。没有 tkinter 时自动跳过。
运行：``py -3 -m unittest discover -s tests -v``
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试期间把界面布局写到临时文件，别动用户的 ~/.musicgen/ui.json
_UI_TMP = Path(tempfile.mkdtemp(prefix="musicgen_ui_"))
os.environ["MUSICGEN_UI"] = str(_UI_TMP / "ui.json")

try:
    import tkinter  # noqa: F401

    from musicgen.gui.app import MusicGenApp
    from musicgen.gui.newproject import NewProjectDialog

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
        shutil.rmtree(_UI_TMP, ignore_errors=True)

    def _pump(self, seconds: float = 0.2) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.update()
            time.sleep(0.01)

    # ---- 载入与布局 ---------------------------------------------------
    def test_project_loaded(self) -> None:
        self.assertIsNotNone(self.app.project)
        self.assertEqual(len(self.app.project.tracks), 4)
        explorer = self.app.dock_manager.panel("explorer")
        self.assertIsNotNone(explorer)
        self.assertEqual(len(explorer.track_items), 4)
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
        props = self.app.dock_manager.panel("properties")
        self.assertEqual(self.app.selected, 2)
        self.assertEqual(props.labels["name"].cget("text"), "Bass")
        self.assertEqual(props.labels["notes"].cget("text"), "16")
        self.assertAlmostEqual(props.vars["gain_db"].get(), -5.0)

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
        props = self.app.dock_manager.panel("properties")
        props.vars["gain_db"].set(-7.5)
        props.vars["pan"].set(-0.4)
        self.app.on_apply()
        self.app.update()
        text = (self.project_path.parent / "tracks" / "ostinato.track.xml").read_text(
            encoding="utf-8")
        self.assertIn('gain="-7.5dB"', text)
        self.assertIn('pan="-0.4"', text)
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.assertAlmostEqual(self.app.project.tracks[1].gain_db, -7.5)

    # ---- 新建工程 -----------------------------------------------------
    def test_new_project_dialog(self) -> None:
        target = Path(tempfile.mkdtemp(prefix="mp_new_"))
        dialog = NewProjectDialog(self.app, default_dir=target)
        self.app.update()
        dialog.name_var.set("测试曲")
        dialog.bpm_var.set("96")
        dialog.rate_var.set("48000")
        dialog.meter_var.set("3")
        dialog.template_var.set("empty")
        dialog._create()
        self.app.update()
        self.assertIsNotNone(dialog.result)
        self.assertTrue(dialog.result["path"].is_file())
        self.app.open_project(str(dialog.result["path"]))
        self.app.update()
        self.assertEqual(self.app.project.name, "测试曲")
        self.assertEqual(self.app.project.bpm, 96)
        self.assertEqual(self.app.project.sample_rate, 48000)
        self.assertEqual(self.app.project.beats_per_bar, 3)
        self.assertEqual(len(self.app.project.tracks), 1)
        self.assertAlmostEqual(self.app.project.compute_length_seconds(), 9.5, places=2)
        shutil.rmtree(target, ignore_errors=True)
        # 复位到示例工程，后面的用例继续用
        self.app.open_project(str(self.project_path))
        self.app.update()

    # ---- 停靠 ---------------------------------------------------------
    def test_docking(self) -> None:
        manager = self.app.dock_manager
        self.assertEqual(manager.area_of("output"), "bottom")
        self.app.dock_panel("output", "right")
        self.app.update()
        self.assertEqual(manager.area_of("output"), "right")
        self.assertTrue(manager.visible("output"))
        # 底部空了应该收起来，右侧面板仍在
        self.assertEqual(len(self.app.hsplit.panes()), 3)
        self.app.dock_panel("output", "bottom")
        self.app.update()
        self.assertEqual(manager.area_of("output"), "bottom")

        self.app.toggle_panel("properties")
        self.app.update()
        self.assertFalse(manager.visible("properties"))
        self.assertFalse(self.app._view_vars["properties"].get())
        self.app.toggle_panel("properties")
        self.app.update()
        self.assertTrue(manager.visible("properties"))

    def test_layout_saved(self) -> None:
        self.app.save_layout()
        self.app.update()
        path = Path(os.environ["MUSICGEN_UI"])
        self.assertTrue(path.is_file())
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("geometry", "docks", "sizes", "volume", "page"):
            self.assertIn(key, data)
        self.assertEqual(data["docks"]["output"], "bottom")

    # ---- 渲染与预览 ---------------------------------------------------
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
        self.assertTrue(self.app.transport.btn_toggle.enabled)

    def test_transport_playback(self) -> None:
        if self.app.result is None:
            self.skipTest("需要先渲染")
        if not self.app.player.available:
            self.skipTest("没有可用音频输出")
        self.app.on_seek(0.0)
        self.app.on_play()
        self._pump(0.45)
        self.assertTrue(self.app.player.playing)
        self.assertGreater(self.app.player.position, 0.1)
        self._pump(0.1)
        self.assertIsNotNone(self.app.wave.playhead)
        self.app.on_play()  # 暂停
        self.app.update()
        self.assertFalse(self.app.player.playing)
        paused_at = self.app.player.position
        self.app.on_seek(12.0)
        self.assertAlmostEqual(self.app.player.position, 12.0, delta=0.05)
        self.assertGreater(paused_at, 0.0)
        self.app.on_stop_playback()
        self.app.update()
        self.assertEqual(self.app.player.position, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
