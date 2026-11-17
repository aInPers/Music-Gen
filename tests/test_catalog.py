"""GM 目录：完整性、名称解析、按需注册与目录搜索。"""

from __future__ import annotations

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

from musicgen.samples import fetch, gm_catalog, registry  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_full_gm_coverage(self) -> None:
        self.assertEqual(len(gm_catalog.PROGRAMS), 128)
        self.assertEqual([p.program for p in gm_catalog.PROGRAMS], list(range(128)))
        keys = [p.key for p in gm_catalog.PROGRAMS]
        self.assertEqual(len(set(keys)), 128, "注册键必须唯一")
        gms = [p.gm for p in gm_catalog.PROGRAMS]
        self.assertEqual(len(set(gms)), 128, "GM 包名必须唯一")

    def test_ranges_are_sane(self) -> None:
        for program in gm_catalog.PROGRAMS:
            self.assertLess(program.low, program.high, program.key)
            self.assertGreaterEqual(program.low, 0)
            self.assertLessEqual(program.high, 127)
            self.assertGreater(program.gain, 0, program.key)

    def test_lookup_by_every_name_kind(self) -> None:
        self.assertEqual(gm_catalog.lookup("flute").key, "flute")
        self.assertEqual(gm_catalog.lookup("长笛").key, "flute")
        self.assertEqual(gm_catalog.lookup("FLUTE").key, "flute")
        self.assertEqual(gm_catalog.lookup("萨克斯").key, "alto_sax")
        self.assertEqual(gm_catalog.lookup("中音萨克斯").key, "alto_sax")
        self.assertEqual(gm_catalog.lookup("church_organ").key, "church_organ")
        self.assertIsNone(gm_catalog.lookup("kazoo"))

    def test_existing_short_keys_are_preserved(self) -> None:
        # 旧工程里写 instrument="piano"/"guitar"/"bass"/"vocal" 必须继续有效
        self.assertEqual(gm_catalog.BY_GM["acoustic_grand_piano"].key, "piano")
        self.assertEqual(gm_catalog.BY_GM["acoustic_guitar_steel"].key, "guitar")
        self.assertEqual(gm_catalog.BY_GM["electric_bass_finger"].key, "bass")
        self.assertEqual(gm_catalog.BY_GM["voice_oohs"].key, "vocal")

    def test_search_hits_chinese_and_family(self) -> None:
        self.assertEqual([p.key for p in gm_catalog.search("萨克斯")],
                         ["soprano_sax", "alto_sax", "tenor_sax", "baritone_sax"])
        self.assertIn("pad_2_warm", [p.key for p in gm_catalog.search("温暖")])
        self.assertTrue(all(p.family == "弦乐" for p in gm_catalog.by_family("弦乐")))
        self.assertEqual(len(gm_catalog.by_family()), 128)

    def test_recorder_no_longer_owns_flute_alias(self) -> None:
        # v4 迁移：旧注册表把 "flute" 当竖笛别名，会导致长笛解析成竖笛
        entries = registry.defaults()
        recorder = next(e for e in entries if e["key"] == "recorder")
        self.assertNotIn("flute", [a.lower() for a in recorder["aliases"]])
        fetch.reload_registry(entries)
        self.assertEqual(fetch.resolve("flute"), "flute")
        self.assertEqual(fetch.resolve("竖笛"), "recorder")


class EnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = registry.defaults()

    def test_ensure_adds_catalog_instrument(self) -> None:
        added, unknown = registry.ensure(self.entries, ["alto_sax"], persist=False)
        self.assertEqual([e["key"] for e in added], ["alto_sax"])
        self.assertEqual(unknown, [])
        entry = added[0]
        self.assertEqual(entry["kind"], "sample")
        self.assertEqual(entry["gm"], "alto_sax")
        self.assertEqual((entry["midi_low"], entry["midi_high"]), (49, 81))
        # 中文名与英文名都进别名，便于工程里写中文
        self.assertIn("中音萨克斯", entry["aliases"])

    def test_ensure_is_idempotent(self) -> None:
        registry.ensure(self.entries, ["alto_sax"], persist=False)
        added, _ = registry.ensure(self.entries, ["alto_sax", "中音萨克斯"], persist=False)
        self.assertEqual(added, [])
        self.assertEqual(len([e for e in self.entries if e.get("gm") == "alto_sax"]), 1)

    def test_ensure_skips_already_registered_piano(self) -> None:
        # piano 已用短键名注册同一个 GM 包，不能再加一条重复的
        added, _ = registry.ensure(self.entries, ["piano", "acoustic_grand_piano"], persist=False)
        self.assertEqual(added, [])

    def test_ensure_reports_unknown(self) -> None:
        added, unknown = registry.ensure(self.entries, ["kazoo"], persist=False)
        self.assertEqual(added, [])
        self.assertEqual(unknown, ["kazoo"])

    def test_catalog_entry_roundtrip_through_normalize(self) -> None:
        spec = registry.catalog_entry("tenor_sax")
        self.assertIsNotNone(spec)
        entry = registry._normalize(spec)
        self.assertEqual(entry["key"], "tenor_sax")
        self.assertEqual(entry["label"], "次中音萨克斯")


class ResolutionTests(unittest.TestCase):
    def test_unregistered_catalog_name_still_resolves(self) -> None:
        fetch.reload_registry(registry.defaults())
        # 没注册也要能解析到正确的键，渲染时才有机会自动补注册
        self.assertEqual(fetch.resolve("双簧管"), "oboe")
        self.assertEqual(fetch.resolve("oboe"), "oboe")
        self.assertEqual(fetch.resolve("失真吉他"), "distortion_guitar")

    def test_sample_path_uses_mp3_for_catalog_only(self) -> None:
        cache = Path(tempfile.mkdtemp(prefix="musicgen_cache_"))
        path = fetch.sample_path("oboe", "C5", cache)
        self.assertEqual(path.suffix, ".mp3")
        # 原曲采样（src_*）仍是 wav
        self.assertEqual(fetch.sample_path("src_piano_4", "C5", cache).suffix, ".wav")


class CatalogStatusTests(unittest.TestCase):
    def test_catalog_rows_shape(self) -> None:
        cache = Path(tempfile.mkdtemp(prefix="musicgen_cache_"))
        rows = fetch.catalog(cache_root=cache)
        self.assertEqual(len(rows), 128)
        row = next(r for r in rows if r["key"] == "oboe")
        self.assertEqual(row["gm"], "oboe")
        self.assertEqual(row["zh"], "双簧管")
        self.assertFalse(row["ready"])
        self.assertEqual(row["want"], row["midi_high"] - row["midi_low"] + 1)

    def test_catalog_family_filter(self) -> None:
        rows = fetch.catalog(family="铜管")
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r["family"] == "铜管" for r in rows))


if __name__ == "__main__":
    unittest.main()
