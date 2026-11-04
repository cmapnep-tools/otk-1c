"""Встроенная справка платформы: поиск установленных платформ, кэш пользователя, таблицы доступности."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from otk_1c.probes.syntax import index
from otk_1c.sources import contexts


class PlatformsTest(unittest.TestCase):
    def test_windows_linux_and_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            win = Path(tmp) / "1cv8" / "8.3.27.2342" / "bin"
            lin = Path(tmp) / "opt" / "x86_64" / "8.5.1.1529"
            extra = Path(tmp) / "custom" / "8.3.25.1000" / "bin"
            for d in (win, lin, extra):
                d.mkdir(parents=True)
                (d / index.SOURCES["cntx"]).write_bytes(b"")
            roots = [str(Path(tmp) / "1cv8"), str(Path(tmp) / "opt" / "x86_64")]
            with mock.patch.object(index, "_roots", return_value=roots), \
                    mock.patch.object(index, "EXTRA_BINS", []):
                index.add_platform(extra)
                found = index.installed_platforms()
        self.assertEqual(list(found), ["8.3.25.1000", "8.3.27.2342", "8.5.1.1529"])

    def test_cache_root(self):
        with mock.patch.dict(os.environ, {"OTK_CACHE": "X:/кэш"}):
            self.assertEqual(index.cache_root(), "X:/кэш")
            self.assertEqual(index.cache_dir(), os.path.join("X:/кэш", "syntax"))


class TablesTest(unittest.TestCase):
    def test_user_table_wins_and_nearest(self):
        with tempfile.TemporaryDirectory() as tmp:
            package, user = Path(tmp) / "package", Path(tmp) / "cache" / "availability"
            for d, versions in ((package, ("8.3.27", "8.5.1")), (user, ("8.5.1",))):
                d.mkdir(parents=True)
                for v in versions:
                    name = "МетодПользователя" if d == user else "МетодПоставки"
                    data = {"platform": v, "contexts": ["Server"], "methods": {name: ["Server"]},
                            "properties": {}, "english": {}}
                    (d / f"{v}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with mock.patch.dict(os.environ, {"OTK_CACHE": str(Path(tmp) / "cache")}), \
                    mock.patch.object(contexts, "_DATA", package):
                files = contexts.table_files()
                self.assertEqual(files["8.5.1"], user / "8.5.1.json")       # таблица пользователя важнее
                self.assertEqual(files["8.3.27"], package / "8.3.27.json")  # поставка остаётся
                self.assertEqual(contexts.nearest_table("8.5.6.100", files), "8.5.1")
                self.assertEqual(contexts.nearest_table("8.3.24", files), "8.3.27")
                contexts.availability_table.cache_clear()
                try:
                    self.assertIn("методпользователя", contexts.availability_table("8.5.1"))
                finally:
                    contexts.availability_table.cache_clear()

def _fake_build(version, bin_dir, db_path):
    """Вместо разбора .hbk: пустой индекс с meta, как у настоящего."""
    import sqlite3
    if os.path.exists(db_path):
        os.remove(db_path)
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    db.executemany("INSERT INTO meta VALUES(?,?)", [
        ("schema", index.SCHEMA), ("platform", version), ("bin", bin_dir), ("entries", "1"),
        ("sources", index.sources_signature(bin_dir))])
    db.commit()
    db.close()
    return 1


class FallbackTest(unittest.TestCase):
    """Справка никогда не «недоступна» молча: откат версии, индекс в кэше, helpDir, helpAuto, актуальность."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plats = self.root / "1cv8"
        self.builds = []

        def build(version, bin_dir, db_path):
            self.builds.append(version)
            return _fake_build(version, bin_dir, db_path)
        self.patches = [mock.patch.dict(os.environ, {"OTK_CACHE": str(self.root / "cache")}),
                        mock.patch.object(index, "_roots", return_value=[str(self.plats)]),
                        mock.patch.object(index, "EXTRA_BINS", []), mock.patch.object(index, "HELP_DIRS", []),
                        mock.patch.object(index, "AUTO", True), mock.patch.object(index, "_cache", {}),
                        mock.patch.object(index, "build", side_effect=build)]
        for pt in self.patches:
            pt.start()

    def tearDown(self):
        for ix, _ in index._cache.values():
            ix.db.close()
        for pt in reversed(self.patches):
            pt.stop()
        self.tmp.cleanup()

    def platform(self, version, parent=None):
        d = (parent or self.plats) / version / "bin"
        d.mkdir(parents=True)
        (d / index.SOURCES["cntx"]).write_bytes(b"x")
        return d

    def test_exact_then_nearest_same_line(self):
        for v in ("8.3.24.1000", "8.3.27.2342", "8.5.1.1529", "8.5.4.1878"):
            self.platform(v)
        ix, notes = index.locate("8.3.27")
        self.assertEqual((ix.version, notes), ("8.3.27.2342", []))
        ix, notes = index.locate("8.3.25")
        self.assertEqual(ix.version, "8.3.24.1000")          # ближе по номеру
        self.assertIn("платформа проверки 8.3.25", notes[0])
        self.assertIn("otk platform-help build", notes[0])
        self.assertEqual(index.locate("8.5.6.100")[0].version, "8.5.4.1878")
        with self.assertRaisesRegex(RuntimeError, "другие линейки: 8.3.24.1000"):
            index.locate("8.2.19")

    def test_cached_index_without_platform(self):
        d = self.platform("8.3.27.2342")
        index.locate("8.3.27")
        index._cache.pop("8.3.27.2342")[0].db.close()
        (d / index.SOURCES["cntx"]).unlink()                   # платформу удалили — индекс остался
        ix, notes = index.locate("8.3.27.2342")
        self.assertEqual((ix.version, notes), ("8.3.27.2342", []))
        self.assertEqual(index.status()[0]["index"], "без платформы")

    def test_help_dir(self):
        self.platform("8.5.1.1529", parent=self.root / "help")
        index.configure({"helpDir": str(self.root / "help")})
        self.assertEqual(index.locate("8.5.1")[0].version, "8.5.1.1529")

    def test_sources_changed_rebuilds(self):
        d = self.platform("8.3.27.2342")
        index.locate("8.3.27")
        index._cache.pop("8.3.27.2342")[0].db.close()
        (d / index.SOURCES["cntx"]).write_bytes(b"xx")         # переустановка той же версии
        self.assertEqual(index.status()[0]["index"], "устарел")
        index.locate("8.3.27")
        self.assertEqual(self.builds, ["8.3.27.2342", "8.3.27.2342"])

    def test_help_auto_off(self):
        d = self.platform("8.3.27.2342")
        index.configure({"helpAuto": False})
        with self.assertRaisesRegex(RuntimeError, "helpAuto = false.*help build --platform"):
            index.locate("8.3.27")
        index.configure({"helpAuto": True})
        index.locate("8.3.27")
        index._cache.pop("8.3.27.2342")[0].db.close()
        (d / index.SOURCES["cntx"]).write_bytes(b"xx")
        index.configure({"helpAuto": False})
        ix, notes = index.locate("8.3.27")                     # прежний индекс с предупреждением
        self.assertEqual(self.builds, ["8.3.27.2342"])
        self.assertIn("устарел", notes[0])

    def test_build_failure_falls_back(self):
        self.platform("8.5.1.1529")
        self.platform("8.5.4.1878")
        index.build.side_effect = lambda v, b, p: (_ for _ in ()).throw(ValueError("новый формат")) \
            if v == "8.5.4.1878" else _fake_build(v, b, p)
        ix, notes = index.locate("8.5.4")
        self.assertEqual(ix.version, "8.5.1.1529")
        self.assertTrue(any("новый формат" in n for n in notes))

    def test_build_from_version(self):
        d = self.platform("8.3.27.2342")
        self.assertEqual(index.build_from(str(d))[0], "8.3.27.2342")
        copy = self.root / "hbk"
        copy.mkdir()
        (copy / index.SOURCES["cntx"]).write_bytes(b"x")
        with self.assertRaisesRegex(RuntimeError, "--version"):
            index.build_from(str(copy))
        self.assertEqual(index.build_from(str(copy), "8.5.1.1529")[0], "8.5.1.1529")


if __name__ == "__main__":
    unittest.main()
