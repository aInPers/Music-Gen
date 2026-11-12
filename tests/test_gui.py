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

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 注册表测试隔离：必须在 import musicgen 之前设置环境变量
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _registry_env  # noqa: F401
except ImportError:  # pragma: no cover - 某些运行方式下 tests/ 不在 sys.path
    pass

# 测试期间把界面布局写到临时文件，别动用户的 ~/.musicgen/ui.json
_UI_TMP = Path(tempfile.mkdtemp(prefix="musicgen_ui_"))
os.environ["MUSICGEN_UI"] = str(_UI_TMP / "ui.json")

try:
    import tkinter  # noqa: F401

    from musicgen.core.notes import midi_to_name
    from musicgen.core.project import load_project
    from musicgen.gui.analyzedialog import AnalyzeDialog
    from musicgen.gui.renderdialog import RenderDialog
    from musicgen.gui.app import MusicGenApp
    from musicgen.gui.newproject import NewProjectDialog
    from musicgen.gui.newtrack import NewTrackDialog

    _TK_OK = True
    _TK_ERR = ""
except Exception as exc:  # noqa: BLE001 - 无 tkinter / 无显示环境
    _TK_OK = False
    _TK_ERR = str(exc)

DEMO_DIR = ROOT / "examples" / "demo"


def _menu_index(menu, label: str) -> int:
    """按文字找菜单项序号（菜单增删项后不必改测试里的硬编码索引）。"""
    for index in range(menu.index("end") + 1):
        try:
            if menu.entrycget(index, "label") == label:
                return index
        except Exception:  # noqa: BLE001 - 分隔项没有 label
            continue
    raise AssertionError(f"菜单里找不到「{label}」")


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
        self.assertIn(self.project_path.name, self.app.title())
        self.assertEqual(len(self.app.menu_bar.buttons), 5)
        self.assertEqual(self.app.dock_manager.area_of("note"), "left")

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

    def test_effect_chain_add_and_save(self) -> None:
        from musicgen.core.effects import EffectSpec
        from musicgen.gui.effectdialog import EffectDialog

        self.app.open_project(str(self.project_path))
        self.app.select_track(1)
        self.app.update()
        props = self.app.dock_manager.panel("properties")
        track = self.app.project.tracks[1]
        track.effects.append(EffectSpec("echo", {"time": 350, "mix": 0.4}))
        track.effects.append(EffectSpec("立体", {"width": 1.8}))
        props.set_track(track)
        self.app.update()
        self.assertEqual(len(props.effect_list.winfo_children()), 2)
        self.assertEqual([s.type for s in track.effects], ["echo", "stereo"])

        props._select_effect(1)
        self.assertEqual(props.btn_effect_edit.cget("state"), "normal")
        props._remove_effect()
        self.assertEqual(len(track.effects), 1)

        self.app.on_apply()
        self.app.update()
        text = (self.project_path.parent / "tracks" / "ostinato.track.xml").read_text(
            encoding="utf-8")
        self.assertIn('<effect type="echo"', text)

        # 对话框控件由注册表参数表生成，只回写与默认值不同的参数
        dialog = EffectDialog(self.app, effect_type="echo", params={"time": 450}, mode="edit")
        self.app.update()
        self.assertEqual(dialog.values(), ("echo", {"time": 450}))
        dialog._cancel()

        self.app.open_project(str(self.project_path))
        self.app.update()
        self.assertEqual([s.type for s in self.app.project.tracks[1].effects], ["echo"])

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

    def test_drum_track_shows_builtin_and_pad_names(self) -> None:
        source = ROOT / "examples" / "band"
        if not source.exists():
            self.skipTest("乐队示例不存在")
        target = Path(self.tmp) / "band"
        if not target.exists():  # 拷一份，避免用例改写仓库里的示例
            shutil.copytree(source, target)

        self.app.open_project(str(target / "band.musicproj"))
        self.app.select_track(2)
        self.app.update()

        props = self.app.dock_manager.panel("properties")
        self.assertEqual(props.instrument.get(), "drums")
        self.assertIn("内置", props.labels["samples"].cget("text"))

        self.app.select_note(2, 1)  # 第 1 小节的底鼓
        self.app.update()
        panel = self.app.dock_manager.panel("note")
        self.assertEqual(panel.vars["pitch"].get(), "底鼓")

        # 鼓件名能解析、能写回
        self.app.on_note_apply({"time": "0", "dur": "0.25", "pitch": "底鼓,snare", "velocity": 0.8})
        self.app.update()
        self.assertEqual(self.app.project.tracks[2].notes[1].pitches, [36, 38])

        self.app.open_project(str(self.project_path))  # 复原，后面的用例继续用示例工程

    def test_toolbar_states_and_commands(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.update()
        bar = self.app.toolbar

        # 打开工程后：编辑/渲染类可用，播放/停止/导出还不行（没有渲染结果）
        self.assertTrue(bar.btn_apply.enabled)
        self.assertTrue(bar.btn_render.enabled)
        self.assertTrue(bar.btn_refresh.enabled)
        self.assertFalse(bar.btn_play.enabled)
        self.assertFalse(bar.btn_stop.enabled)
        self.assertFalse(bar.btn_export.enabled)
        self.assertEqual(bar.btn_play.icon, "play")

        # 有结果且在播放：播放键变暂停图标
        bar.set_state(has_project=True, has_result=True, busy=False, playing=True)
        self.assertTrue(bar.btn_play.enabled)
        self.assertTrue(bar.btn_export.enabled)
        self.assertEqual(bar.btn_play.icon, "pause")
        self.assertTrue(bar.playing)

        # 按钮真的能触发命令；禁用时不触发
        fired: list[str] = []
        bar.btn_new.set_command(lambda: fired.append("new"))
        bar.btn_new.invoke()
        bar.btn_new.set_enabled(False)
        bar.btn_new.invoke()
        self.assertEqual(fired, ["new"])

        bar.set_state(has_project=True, has_result=False, busy=False, playing=False)
        self.app._sync_toolbar()

    def test_all_toolbar_buttons_wired(self) -> None:
        """命令条不能出现「看着能点、点了没反应」的按钮。"""
        bar = self.app.toolbar
        names = ("btn_new", "btn_new_track", "btn_delete_track", "btn_open", "btn_apply",
                 "btn_render", "btn_play", "btn_stop", "btn_view_track", "btn_isolate",
                 "btn_export", "btn_samples", "btn_instruments", "btn_refresh")
        for name in names:
            with self.subTest(button=name):
                button = getattr(bar, name)
                self.assertIsNotNone(button._command, f"{name} 没有绑定命令")

    def test_menubar_buttons_have_working_dropdowns(self) -> None:
        """Tk 9 的 tk::MbPost 会拒绝弹出「不是 Menubutton 后代」的菜单。"""
        bar = self.app.menu_bar
        self.assertEqual(len(bar.buttons), 5)
        for button, key in zip(bar.buttons, ("file", "edit", "view", "render", "help")):
            with self.subTest(menu=key):
                menu = self.app.menus[key]
                self.assertIs(bar.menu_of(button), menu)
                self.assertTrue(
                    str(menu).startswith(str(button) + "."),
                    f"{key} 菜单必须以按钮为 master，否则点击静默失效",
                )
                self.assertGreater(menu.index("end"), 0)
                self.assertTrue(button.bind("<ButtonPress-1>"))

    def test_menubar_alt_keys_and_commands(self) -> None:
        for key in "fevrh":
            with self.subTest(key=key):
                self.assertTrue(self.app.bind(f"<Alt-Key-{key}>"),
                                f"Alt+{key.upper()} 未绑定")
        self.app.goto_page("roll")
        self.app.menus["view"].invoke(_menu_index(self.app.menus["view"], "波形"))
        self.assertEqual(self.app.page, "wave")
        self.app.menus["view"].invoke(_menu_index(self.app.menus["view"], "分轨卷帘"))
        self.assertEqual(self.app.page, "tracks")
        self.app.menus["edit"].invoke(_menu_index(self.app.menus["edit"], "清空输出"))
        self.app.goto_page("roll")

    def test_audio_track_project_opens(self) -> None:
        from musicgen.core.wavio import write_wav

        tmp = Path(tempfile.mkdtemp(prefix="musicgen_audio_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "tracks").mkdir()
        (tmp / "stems").mkdir()
        write_wav(tmp / "stems" / "a.wav", np.zeros((2, 4410), dtype=np.float32), 44100)
        (tmp / "Audio.musicproj").write_text(
            '<musicproj name="Audio" bpm="120" sampleRate="44100" length="0.1s">'
            '<track file="tracks/a.track.xml"/></musicproj>',
            encoding="utf-8",
        )
        (tmp / "tracks" / "a.track.xml").write_text(
            '<track name="A" instrument="audio"><audio file="../stems/a.wav"/></track>',
            encoding="utf-8",
        )
        self.app.open_project(str(tmp / "Audio.musicproj"))
        self.app.update()
        self.assertIsNotNone(self.app.project)
        self.assertIsNotNone(self.app.project.tracks[0].audio_path)
        self.app.select_track(0)
        self.app.update()
        props = self.app.dock_manager.panel("properties")
        self.assertIn("音频", props.labels["notes"].cget("text"))

    def test_synth_patch_track_shows_script(self) -> None:
        from musicgen.core.patch import load_patch

        tmp = Path(tempfile.mkdtemp(prefix="musicgen_patch_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "tracks").mkdir()
        (tmp / "synths").mkdir()
        load_patch("warm_pad").save(tmp / "synths" / "pad.synth.json")
        (tmp / "Pad.musicproj").write_text(
            '<musicproj name="Pad" bpm="120" sampleRate="44100" length="1s">'
            '<track file="tracks/a.track.xml"/></musicproj>',
            encoding="utf-8",
        )
        (tmp / "tracks" / "a.track.xml").write_text(
            '<track name="Pad" instrument="synth" patch="../synths/pad.synth.json">'
            '<note time="0" dur="1" pitch="C4"/></track>',
            encoding="utf-8",
        )
        self.app.open_project(str(tmp / "Pad.musicproj"))
        self.app.update()
        self.assertIsNotNone(self.app.project.tracks[0].patch_path)
        self.app.select_track(0)
        self.app.update()
        props = self.app.dock_manager.panel("properties")
        self.assertIn("合成音脚本", props.labels["samples"].cget("text"))
        self.assertFalse(props.instrument._enabled)

    def test_analyze_dialog_defaults(self) -> None:
        from musicgen.gui.analyzedialog import AnalyzeDialog

        dialog = AnalyzeDialog(self.app)
        try:
            self.assertEqual(dialog.model_var.get(), "htdemucs_6s")
            self.assertEqual(dialog.quant_var.get(), "1/16")
            self.assertTrue(dialog.stems_var.get())
            self.assertFalse(dialog.audio_var.get())
            self.assertFalse(dialog.force_var.get())
        finally:
            dialog._cancel()

    def test_instrument_registry_dialog_refreshes_dropdown(self) -> None:
        from musicgen.gui import instrumentregistry as ir
        from musicgen.samples import fetch, registry

        self.app.open_project(str(self.project_path))
        self.app.update()
        dialog = ir.InstrumentRegistryDialog(
            self.app, on_changed=self.app.refresh_instrument_views
        )
        try:
            self.assertEqual(len(dialog.tree.get_children()), len(registry.defaults()))
            registry.add(registry.load(), {
                "key": "flute", "label": "长笛", "aliases": ["长笛"],
                "kind": "sample", "gm": "flute", "midi_low": 60,
                "midi_high": 96, "gain": 8.0,
            })
            dialog._changed("测试添加")
            self.app.update()
            self.assertIn("flute", fetch.instrument_keys())
            props = self.app.dock_manager.panel("properties")
            self.assertIn("flute", [value for value, _ in props.instrument._items])

            original = ir.messagebox.askyesno
            ir.messagebox.askyesno = lambda *_a, **_k: True
            try:
                dialog.tree.selection_set("flute")
                dialog._remove()
            finally:
                ir.messagebox.askyesno = original
            self.app.update()
            self.assertNotIn("flute", fetch.instrument_keys())
            self.assertNotIn("flute", [value for value, _ in props.instrument._items])
        finally:
            registry.reset()
            fetch.reload_registry()
            dialog.destroy()
            self.app.refresh_instrument_views()

    def test_tracks_page_lanes_and_playhead(self) -> None:
        """分轨卷帘：一轨一条泳道，播放头与轨头电平条能更新。"""
        self.app.goto_page("tracks")
        self._pump(0.3)
        page = self.app.tracks_page
        self.assertEqual(len(page._lanes), len(self.app.project.tracks))
        self.assertEqual(len(page._meters), len(self.app.project.tracks))
        page.set_playhead(1.0)
        page.set_levels({self.app.project.tracks[0].name: 0.5})
        self._pump(0.15)
        self.assertIsNotNone(page._playhead_item)

    def test_render_dialog_selects_tracks(self) -> None:
        dialog = RenderDialog(self.app, self.app.project, 1)
        self.app.update()
        self.assertEqual(len(dialog._vars), len(self.app.project.tracks))
        self.assertTrue(all(var.get() for var in dialog._vars))
        dialog._only_current()
        dialog._accept()
        self.assertEqual(dialog.result, [1])

    def test_track_management_menu_entries(self) -> None:
        self.assertIsNotNone(_menu_index(self.app.menus["edit"], "乐器注册表…"))
        self.assertIsNotNone(getattr(self.app.toolbar, "btn_instruments")._command)
        self.assertIsNotNone(_menu_index(self.app.menus["edit"], "删除选中音轨…"))
        self.assertIsNotNone(_menu_index(self.app.menus["view"], "单独查看这条音轨…"))
        self.assertIsNotNone(_menu_index(self.app.menus["view"], "只看选中音轨"))
        for key in ("btn_delete_track", "btn_view_track", "btn_isolate"):
            with self.subTest(button=key):
                self.assertIsNotNone(getattr(self.app.toolbar, key)._command)

    def test_isolate_mode_syncs_everywhere(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.update()
        try:
            self.app.set_only_selected(True)
            self.assertTrue(self.app.only_selected_track)
            self.assertTrue(self.app.roll.only_selected)
            self.assertTrue(self.app._isolate_var.get())
            self.assertEqual(self.app.toolbar.btn_isolate.icon, "focus_on")
            self.app.on_toggle_isolate()
            self.assertFalse(self.app.only_selected_track)
            self.assertFalse(self.app.roll.only_selected)
            self.assertFalse(self.app._isolate_var.get())
            self.assertEqual(self.app.toolbar.btn_isolate.icon, "focus")
        finally:
            self.app.set_only_selected(False)

    def test_track_window_table_and_jump(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.select_track(0)
        self.app.update()
        self.app.on_view_track()
        for _ in range(5):
            self.app.update()
        window = self.app._track_windows.get(0)
        self.assertIsNotNone(window)
        try:
            rows = window.tree.get_children()
            self.assertEqual(len(rows), len(self.app.project.tracks[0].notes))
            window.tree.selection_set(rows[2])
            window._on_row_select()
            self.app.update()
            self.assertEqual(self.app.note_sel, (0, 2))
            self.assertEqual(self.app.roll.selected_note, (0, 2))
            window._toggle_isolate()
            self.assertTrue(self.app.only_selected_track)
            self.assertIn("只看这一轨", window.btn_isolate.cget("text"))
        finally:
            window.close()
            self.app.set_only_selected(False)
            self.app.update()
        self.assertNotIn(0, self.app._track_windows)

    def test_delete_track_from_gui(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="musicgen_deltrack_"))
        try:
            target = Path(tmp) / "demo"
            shutil.copytree(DEMO_DIR, target)
            project_path = target / "demo.musicproj"
            self.app.open_project(str(project_path))
            before = len(self.app.project.tracks)
            self.assertTrue(self.app._delete_track(1))
            self.assertEqual(len(self.app.project.tracks), before - 1)
            text = self.app.project.path.read_text(encoding="utf-8")
            self.assertEqual(text.count("<track "), before - 1)
            self.assertTrue((target / "tracks" / "ostinato.track.xml").is_file())
            self.assertEqual(self.app.selected, 1)
        finally:
            self.app.open_project(str(self.project_path))
            shutil.rmtree(tmp, ignore_errors=True)

    def test_new_track_entries_and_dialog(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.assertTrue(self.app.toolbar.btn_new_track.enabled)

        labels = []
        menu = self.app.menus["file"]
        for index in range(menu.index("end") + 1):
            try:
                labels.append(menu.entrycget(index, "label"))
            except Exception:  # noqa: BLE001 - 分隔项没有 label
                pass
        self.assertIn("新建轨道…", labels)
        explorer = self.app.dock_manager.panel("explorer")
        self.assertIsNotNone(explorer.on_new_track)

        dialog = NewTrackDialog(self.app, len(self.app.project.tracks))
        for _ in range(6):
            dialog.update()
        self.assertEqual(dialog.name_var.get(), "Track 5")
        self.assertEqual(dialog.instrument.get(), "piano")
        self.assertEqual(dialog.template_var.get(), "blank")
        self.assertGreater(dialog.winfo_width(), 200)
        self.assertGreater(dialog.instrument.winfo_width(), 100)
        dialog.grab_release()
        dialog.destroy()

    def test_create_track_writes_project_and_refreshes(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="musicgen_newtrack_"))
        try:
            target = Path(tmp) / "demo"
            shutil.copytree(DEMO_DIR, target)
            project_path = target / "demo.musicproj"
            self.app.open_project(str(project_path))
            before = len(load_project(project_path).tracks)

            ok = self.app._create_track(
                {"name": "Guitar", "instrument": "guitar", "template": "note"}
            )
            self.assertTrue(ok)
            project = load_project(project_path)
            self.assertEqual(len(project.tracks), before + 1)
            self.assertEqual(project.tracks[-1].name, "Guitar")
            self.assertEqual(project.tracks[-1].instrument, "guitar")
            self.assertEqual(len(project.tracks[-1].notes), 1)
            self.assertEqual(self.app.selected, before)
            self.assertEqual(self.app._current_track().name, "Guitar")
            self.assertIn("5 音轨", self.app.toolbar.info.cget("text"))
        finally:
            self.app.open_project(str(self.project_path))
            shutil.rmtree(tmp, ignore_errors=True)

    def test_drag_docking(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.dock_panel("properties", "right")
        self.app.update()

        host = self.app.hsplit
        width, height = host.winfo_width(), host.winfo_height()
        if width < 200 or height < 200:
            self.skipTest("窗口还没有实际尺寸")
        hx, hy = host.winfo_rootx(), host.winfo_rooty()
        # 用例之间共享同一个窗口，结束时把底部区还原到原来的分栏位置
        sash_before = (list(self.app.center_host.sash_coord(0))
                       if len(self.app.center_host.panes()) >= 2 else None)

        drag = self.app.drag_dock
        drag.start("properties", "属性", hx + width - 40, hy + 30)
        drag.move(hx + 12, hy + height // 2)   # 拖到左边缘
        area = drag.finish(hx + 12, hy + height // 2)
        self.assertEqual(area, "left")
        self.app.update()
        self.assertEqual(self.app.dock_manager.area_of("properties"), "left")
        self.assertIsNotNone(self.app.dock_manager.panel("properties"))
        self.assertIsNone(drag._ghost)          # 浮窗已清理
        self.assertIsNone(drag._zone)

        # 拖到中间不该吸附
        self.assertIsNone(drag.area_at(hx + width // 2, hy + height // 2))
        # 拖到窗口外也不吸附
        self.assertIsNone(drag.area_at(hx - 60, hy + 10))

        # 已经在这一区时，再拖回同一区不算变化（按真实拖拽时序：start -> move -> finish）
        drag.start("properties", "属性", hx + 60, hy + 20)
        drag.move(hx + 12, hy + height // 2)
        self.assertIsNone(drag.area_at(hx + 12, hy + height // 2))
        self.assertIsNone(drag.finish(hx + 12, hy + height // 2))
        self.app.update()
        self.assertEqual(self.app.dock_manager.area_of("properties"), "left")

        # 拖到底部
        drag.start("properties", "属性", hx + 60, hy + 20)
        drag.move(hx + width // 2, hy + height - 8)
        self.assertEqual(drag.finish(hx + width // 2, hy + height - 8), "bottom")
        self.app.update()
        self.assertEqual(self.app.dock_manager.area_of("properties"), "bottom")
        # 底部区默认只有 150px，拖入第二个面板后应自动撑大，不能把两个都压成窄条
        self.app.update_idletasks()
        bottom = self.app.dock_manager.containers["bottom"]
        self.assertGreater(bottom.winfo_height(), 200)
        self.assertGreater(self.app.dock_manager.panel("properties").winfo_height(), 120)
        self.assertGreater(self.app.dock_manager.panel("output").winfo_height(), 80)

        self.app.dock_panel("properties", "right")
        if sash_before is not None and len(self.app.center_host.panes()) >= 2:
            self.app.center_host.sash_place(0, 0, int(sash_before[1]))
        self.app.update()

    def test_switch_instrument(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.select_track(1)
        self.app.update()
        props = self.app.dock_manager.panel("properties")
        self.assertEqual(props.instrument.get(), "piano")
        self.assertIn("采样", props.labels["samples"].cget("text"))

        props.instrument.set("violin")
        props._refresh_sample_state()
        self.app.on_apply()
        self.app.update()
        self.assertEqual(self.app.project.tracks[1].instrument, "violin")
        text = (self.project_path.parent / "tracks" / "ostinato.track.xml").read_text(
            encoding="utf-8")
        self.assertIn('instrument="violin"', text)

        # 改回钢琴，后面的用例继续用示例工程
        props.instrument.set("piano")
        self.app.on_apply()
        self.app.update()
        self.assertEqual(self.app.project.tracks[1].instrument, "piano")

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

    # ---- 音符（每一小段）属性 ------------------------------------------
    def test_select_note_shows_properties(self) -> None:
        self.app.open_project(str(self.project_path))
        self.app.update()
        self.app.goto_page("roll")
        self.app.select_note(1, 2)
        self.app.update()
        panel = self.app.dock_manager.panel("note")
        note = self.app.project.tracks[1].notes[2]
        self.assertEqual(self.app.note_sel, (1, 2))
        self.assertEqual(panel.labels["track"].cget("text"), "Ostinato")
        self.assertEqual(panel.labels["pos"].cget("text").split("·")[-1].strip(), "3/8")
        self.assertAlmostEqual(float(panel.vars["time"].get()), note.time)
        self.assertEqual(panel.vars["pitch"].get(),
                         ",".join(midi_to_name(p) for p in note.pitches))
        self.assertGreater(len(self.app.roll.canvas.find_withtag("n:1:2")), 0)

    def test_note_click_selects_segment(self) -> None:
        self.app.goto_page("roll")
        self.app.roll.reset_view()
        self.app.roll.redraw()
        self.app.update()
        canvas = self.app.roll.canvas
        items = canvas.find_withtag("n:2:0")
        self.assertTrue(items, "卷帘里应有可点的音符")
        x1, y1, x2, y2 = canvas.coords(items[0])
        canvas.event_generate("<Button-1>", x=int((x1 + x2) / 2), y=int((y1 + y2) / 2))
        self.app.update()
        self.assertEqual(self.app.note_sel, (2, 0))
        self.assertEqual(self.app.selected, 2)

    def test_note_apply_writes_xml(self) -> None:
        self.app.select_note(3, 0)
        self.app.update()
        panel = self.app.dock_manager.panel("note")
        panel.vars["time"].set("0.5")
        panel.vars["dur"].set("1.5")
        panel.vars["pitch"].set("E5,G5")
        panel.velocity.set(0.55)
        self.app.on_note_apply(panel.values())
        self.app.update()
        note = self.app.project.tracks[3].notes[0]
        self.assertAlmostEqual(note.time, 0.5)
        self.assertAlmostEqual(note.dur, 1.5)
        self.assertEqual(len(note.pitches), 2)
        self.assertAlmostEqual(note.velocity, 0.55)
        text = (self.project_path.parent / "tracks" / "melody.track.xml").read_text(
            encoding="utf-8")
        self.assertIn('pitch="E5,G5"', text)
        self.assertIn('time="0.5"', text)
        # 复位工程，后面的用例不受影响
        self.app.open_project(str(self.project_path))
        self.app.update()

    def test_note_duplicate_and_delete(self) -> None:
        self.app.select_note(0, 0)
        self.app.update()
        count = len(self.app.project.tracks[0].notes)
        self.app.on_note_duplicate()
        self.app.update()
        self.assertEqual(len(self.app.project.tracks[0].notes), count + 1)
        self.assertEqual(self.app.note_sel, (0, 1))
        self.app.on_note_delete()
        self.app.update()
        self.assertEqual(len(self.app.project.tracks[0].notes), count)
        self.app.on_note_step(1)
        self.app.update()
        self.assertEqual(self.app.note_sel, (0, 2))
        self.app.open_project(str(self.project_path))
        self.app.update()

    # ---- 卷帘滚动 -----------------------------------------------------
    def test_roll_scroll_gestures(self) -> None:
        self.app.goto_page("roll")
        self.app.roll.reset_view()
        self.app.update()
        roll = self.app.roll
        canvas = roll.canvas
        top0 = roll.pitch_top

        canvas.event_generate("<MouseWheel>", delta=-120, x=400, y=200)
        self.app.update()
        self.assertLess(roll.pitch_top, top0)          # 向下滚 → 看更低的音

        canvas.event_generate("<MouseWheel>", delta=120, x=400, y=200)
        self.app.update()
        self.assertAlmostEqual(roll.pitch_top, top0, places=3)

        zoom0 = roll.zoom
        canvas.event_generate("<Control-MouseWheel>", delta=120, x=400, y=200)
        self.app.update()
        self.assertGreater(roll.zoom, zoom0)           # Ctrl → 横向放大
        self.assertGreater(roll._pixels_per_beat(), 0)

        canvas.event_generate("<Alt-MouseWheel>", delta=-120, x=400, y=200)
        self.app.update()
        self.assertGreater(roll.scroll_beats, 0.0)     # Alt → 向右滚

        span0 = roll.pitch_span
        canvas.event_generate("<Shift-MouseWheel>", delta=120, x=400, y=200)
        self.app.update()
        self.assertLess(roll.pitch_span, span0)        # Shift → 行高变大（可见半音变少）

        first, last = roll.hbar.get()
        self.assertGreaterEqual(first, 0.0)
        self.assertLessEqual(last, 1.0)
        roll.reset_view()
        self.app.update()
        self.assertEqual(roll.zoom, 1.0)
        self.assertEqual(roll.scroll_beats, 0.0)

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


class AnalyzeDialogTests(unittest.TestCase):
    """「从音频重建工程」：选中的文件要进输入框，不能和「音频轨」开关共用一个变量。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="musicgen_analyze_")
        cls.root = tkinter.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self.dialog = AnalyzeDialog(self.root, default_dir=Path(self.tmp))

    def tearDown(self) -> None:
        try:
            self.dialog.destroy()
        except tkinter.TclError:
            pass

    def _fake_choice(self, name: str):
        audio = Path(self.tmp) / name
        audio.write_bytes(b"RIFF")
        from unittest import mock

        from musicgen.gui import analyzedialog

        return audio, mock.patch.object(
            analyzedialog.filedialog, "askopenfilename", return_value=str(audio)
        )

    def test_browse_fills_entry_and_auto_fields(self) -> None:
        audio, patch = self._fake_choice("song.mp3")
        with patch:
            self.dialog._browse_audio()
        self.assertEqual(self.dialog.audio_var.get(), str(audio))
        self.assertEqual(self.dialog.audio_entry.get(), str(audio))
        self.assertEqual(self.dialog.name_var.get(), "song")
        self.assertEqual(self.dialog.dir_var.get(), str(Path(self.tmp) / "song-musicproj"))

    def test_audio_track_switch_keeps_path(self) -> None:
        audio, patch = self._fake_choice("song.wav")
        with patch:
            self.dialog._browse_audio()
        self.dialog.audio_tracks_var.set(True)
        # 勾「音频轨」不能把路径变量顶掉（曾经的 bug：两个控件共用 audio_var）
        self.assertEqual(self.dialog.audio_var.get(), str(audio))
        self.dialog.dir_var.set(str(Path(self.tmp) / "out"))
        self.dialog._start()
        self.assertIsNotNone(self.dialog.result)
        self.assertEqual(self.dialog.result["source"], audio)
        self.assertTrue(self.dialog.result["audio_tracks"])
        self.assertEqual(self.dialog.result["name"], "song")


if __name__ == "__main__":
    unittest.main(verbosity=2)
