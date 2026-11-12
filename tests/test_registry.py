"""乐器注册表：JSON 持久化、增删查改、CLI 命令与运行时表同步。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _registry_env  # noqa: F401
except ImportError:  # pragma: no cover
    pass

from musicgen import cli  # noqa: E402
from musicgen.core.sampler import SampleInstrument  # noqa: E402
from musicgen.samples import fetch, registry  # noqa: E402

FLUTE = {
    "key": "flute", "label": "长笛", "aliases": ["长笛", "长笛声部"],
    "kind": "sample", "gm": "flute", "midi_low": 60, "midi_high": 96,
    "gain": 8.5, "title": "FluidR3 GM — Flute",
}


class _RegistryCase(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("MUSICGEN_INSTRUMENTS")
        self.tmp = Path(tempfile.mkdtemp(prefix="mgregtest_"))
        os.environ["MUSICGEN_INSTRUMENTS"] = str(self.tmp / "instruments.json")
        registry.reset()
        fetch.reload_registry()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("MUSICGEN_INSTRUMENTS", None)
        else:
            os.environ["MUSICGEN_INSTRUMENTS"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)
        fetch.reload_registry()


class RegistryTests(_RegistryCase):
    def test_defaults_written_on_first_load(self) -> None:
        file = registry.path()
        file.unlink()  # setUp 的 reset 已写过一次，这里模拟「首次运行」
        self.assertFalse(file.is_file())
        entries = registry.load()
        self.assertEqual(len(entries), len(registry.defaults()))
        self.assertTrue(file.is_file())
        data = json.loads(file.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], registry.VERSION)
        self.assertEqual([e["key"] for e in data["instruments"]][0], "piano")
        self.assertEqual(fetch.instrument_keys(),
                         [entry["key"] for entry in registry.defaults()])
        self.assertIn("drums", fetch.BUILTIN)
        self.assertEqual(fetch.resolve("大提琴"), "cello")
        self.assertEqual(fetch.display("violin"), "小提琴 (violin)")

    def test_old_version_gets_new_synth_entries(self) -> None:
        """v2 的旧注册表只缺新条目：加载时补齐 bass/vocal/合成音色，用户参数不动。"""
        file = registry.path()
        file.write_text(json.dumps({
            "version": 2,
            "instruments": [{
                "key": "piano", "label": "我的钢琴", "kind": "sample",
                "gm": "acoustic_grand_piano", "midi_low": 21, "midi_high": 108,
                "gain": 12.5,
            }],
        }), encoding="utf-8")
        entries = registry.load()
        keys = {entry["key"] for entry in entries}
        self.assertIn("piano", keys)
        self.assertIn("bass", keys)
        self.assertIn("vocal", keys)
        self.assertIn("synth_pad", keys)
        piano = next(entry for entry in entries if entry["key"] == "piano")
        self.assertEqual(piano["label"], "我的钢琴")
        self.assertEqual(piano["gain"], 12.5)
        fetch.reload_registry(entries)
        self.assertEqual(fetch.patch_spec("synth_pad"), "warm_pad")
        self.assertTrue(fetch.is_synth("合成垫"))

    def test_add_sample_instrument(self) -> None:
        entry = registry.add(registry.load(), dict(FLUTE))
        fetch.reload_registry()
        self.assertEqual(entry["key"], "flute")
        self.assertIn("flute", fetch.instrument_keys())
        self.assertIn("flute", fetch.SOURCES)
        self.assertEqual(fetch.resolve("长笛声部"), "flute")
        self.assertEqual(fetch.label("flute"), "长笛")
        self.assertEqual(fetch.instrument_gain("flute"), 8.5)
        self.assertEqual(SampleInstrument("flute").instrument_gain, 8.5)
        source = fetch.SOURCES["flute"]
        self.assertEqual((source.midi_low, source.midi_high), (60, 96))
        self.assertEqual(source.mirror_count, 4)
        self.assertIn("{name}", source.url_templates[0])
        self.assertEqual(fetch.instrument_status("flute"), (0, 37))

    def test_custom_urls(self) -> None:
        spec = dict(FLUTE)
        spec.pop("gm")
        spec["urls"] = ["https://example.test/{name}.{ext}"]
        registry.add(registry.load(), spec)
        fetch.reload_registry()
        self.assertEqual(fetch.SOURCES["flute"].url_templates,
                         ["https://example.test/{name}.{ext}"])
        self.assertEqual(fetch.SOURCES["flute"].mirror_count, 1)

    def test_validation_errors(self) -> None:
        bad_specs = [
            {"key": "Bad Key", "label": "X", "gm": "x"},
            {"key": "x", "label": "X"},                      # 没有 gm / urls
            {"key": "x", "label": "X", "gm": "x", "midi_low": 90, "midi_high": 30},
            {"key": "x", "label": "X", "gm": "x", "gain": -1},
            {"key": "x", "label": "X", "kind": "nonsense"},
        ]
        for spec in bad_specs:
            with self.subTest(spec=spec), self.assertRaises(registry.RegistryError):
                registry.add(registry.load(), spec)

    def test_duplicate_and_force(self) -> None:
        registry.add(registry.load(), dict(FLUTE))
        with self.assertRaises(registry.RegistryError):
            registry.add(registry.load(), dict(FLUTE))
        updated = dict(FLUTE, label="短笛")
        registry.add(registry.load(), updated, force=True)
        fetch.reload_registry()
        self.assertEqual(fetch.LABELS["flute"], "短笛")
        self.assertEqual(len(registry.load()), len(registry.defaults()) + 1)

    def test_remove_builtin_and_reset(self) -> None:
        registry.remove(registry.load(), "drums")
        fetch.reload_registry()
        self.assertNotIn("drums", fetch.BUILTIN)
        self.assertNotIn("drums", fetch.instrument_keys())
        registry.reset()
        fetch.reload_registry()
        self.assertEqual(len(registry.load()), len(registry.defaults()))
        self.assertIn("drums", fetch.BUILTIN)
        with self.assertRaises(registry.RegistryError):
            registry.remove(registry.load(), "不存在")

    def test_remove_keeps_sample_cache(self) -> None:
        cache = self.tmp / "samples" / "flute"
        cache.mkdir(parents=True)
        (cache / "C4.mp3").write_bytes(b"fake")
        registry.add(registry.load(), dict(FLUTE))
        registry.remove(registry.load(), "flute")
        self.assertTrue((cache / "C4.mp3").is_file())

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        file = registry.path()
        file.write_text("{ this is not json", encoding="utf-8")
        entries = registry.load()
        self.assertEqual(len(entries), len(registry.defaults()))
        self.assertEqual(file.read_text(encoding="utf-8"), "{ this is not json")

    def test_environment_override(self) -> None:
        self.assertEqual(registry.path(), self.tmp / "instruments.json")
        self.assertEqual(registry.path().name, "instruments.json")


class CliRegistryTests(_RegistryCase):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self) -> None:
        base = len(registry.defaults())
        code, out, _ = self.run_cli("instrument", "list")
        self.assertEqual(code, 0)
        self.assertIn(f"{base} 件", out)
        self.assertIn("架子鼓", out)

        code, out, _ = self.run_cli(
            "instrument", "add", "flute", "--name", "长笛", "--gm", "flute",
            "--low", "60", "--high", "96", "--alias", "长笛声部,长笛",
            "--gain", "8.5", "--range-label", "C4–C7",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("已注册乐器 flute", out)
        self.assertIn("flute", fetch.SOURCES)
        self.assertEqual(fetch.resolve("长笛声部"), "flute")

        code, out, _ = self.run_cli("instrument", "list")
        self.assertIn(f"{base + 1} 件", out)
        code, out, _ = self.run_cli("instrument", "path")
        self.assertEqual(code, 0)
        self.assertIn("instruments.json", out)

        code, out, _ = self.run_cli("instrument", "remove", "flute")
        self.assertEqual(code, 0, out)
        self.assertNotIn("flute", fetch.SOURCES)

        registry.add(registry.load(), dict(FLUTE))
        code, out, _ = self.run_cli("instrument", "reset")
        self.assertEqual(code, 0)
        self.assertIn(f"{base} 件", out)
        self.assertEqual(len(registry.load()), base)

    def test_add_errors_exit_one(self) -> None:
        code, _, err = self.run_cli("instrument", "add", "Bad Key", "--name", "X",
                                    "--gm", "x")
        self.assertEqual(code, 1)
        self.assertIn("注册表错误", err)
        code, _, err = self.run_cli("instrument", "remove", "nope")
        self.assertEqual(code, 1)
        self.assertIn("注册表", err)

    def test_registered_alias_works_in_track_add(self) -> None:
        project_dir = self.tmp / "demo"
        shutil.copytree(ROOT / "examples" / "demo", project_dir)
        project = project_dir / "demo.musicproj"
        self.run_cli("instrument", "add", "flute", "--name", "长笛", "--gm", "flute",
                     "--alias", "长笛声部")
        code, out, _ = self.run_cli("track", "add", str(project), "--name", "Flute",
                                    "--instrument", "长笛声部")
        self.assertEqual(code, 0, out)
        self.assertIn("长笛 (flute)", out)
        from musicgen.core.project import load_project

        self.assertEqual(load_project(project).tracks[-1].instrument, "flute")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
