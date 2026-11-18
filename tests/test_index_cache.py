"""Кеш индекса каталогов вне git: стабильный ключ между процессами, правка модуля сбрасывает кеш --dir."""
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.sources import index, workspace
from test_collect import FLAGS, write

CONFIG = ('<Configuration><Properties><ScriptVariant>Russian</ScriptVariant></Properties>'
          '<ChildObjects><CommonModule>М</CommonModule></ChildObjects></Configuration>')


def touch_later(path, text):
    """Записать файл так, чтобы время изменения точно отличалось (грубые часы файловой системы)."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        os.utime(path, ns=(before + 10**9, before + 10**9))


class TestIndexCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        self.dump, self.cache = self.tmp / "выгрузка", self.tmp / "кеш"
        write(self.dump, "Configuration.xml", CONFIG)
        write(self.dump, "ConfigDumpInfo.xml", "<ConfigDumpInfo/>")
        write(self.dump, "CommonModules/М.xml", FLAGS)
        write(self.dump, "CommonModules/М/Ext/Module.bsl", "Процедура А() Экспорт\nКонецПроцедуры\n")

    def tearDown(self):
        rmtree_force(self.tmp)

    def exports(self, full):
        return set(index.cached_dir(self.dump, self.cache, full=full)["commonModules"]["м"]["exports"])

    def test_key_stable_between_processes(self):
        code = ("import sys; from pathlib import Path; from otk_1c.sources import index, workspace; "
                "index.cached_dir(Path(sys.argv[1]), Path(sys.argv[2])); "
                "print(*sorted(p.name for p in Path(sys.argv[2]).iterdir()))")
        env = {**os.environ, "PYTHONPATH": str(Path(index.__file__).parents[2])}
        names = [subprocess.run([sys.executable, "-c", code, str(self.dump), str(self.cache)], env=env,
                                capture_output=True, text=True, check=True).stdout.strip() for _ in range(2)]
        self.assertEqual(names[0], names[1])                   # второй процесс нашёл запись первого
        self.assertEqual(len(names[0].split()), 1)

    def test_extracted_cache_tracks_effect_of_local_git_attributes(self):
        git = ["git", "-C", str(self.dump)]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "dump"],
                       check=True)
        commit, tree = subprocess.check_output([*git, "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
        self.assertEqual(index.cache_key(self.dump, "."), tree)
        first = workspace.materialize(self.dump, commit, ".", self.tmp / "one")
        prior = index.cached_revision(first, self.cache)
        self.assertTrue(prior["commonModules"]["м"]["source"])

        write(self.dump, ".git/info/attributes", "CommonModules/**/Module.bsl export-ignore\n")
        second = workspace.materialize(self.dump, commit, ".", self.tmp / "two")
        current = index.cached_revision(second, self.cache)
        self.assertFalse(current["commonModules"]["м"]["source"])
        self.assertEqual(len(list(self.cache.glob("index-extracted-*.json"))), 2)

    def test_cache_hit(self):
        self.exports(True)
        with mock.patch.object(index, "build", side_effect=AssertionError("кеш не сработал")):
            self.assertEqual(self.exports(True), {"а"})

    def test_full_fingerprint_sees_module_edit(self):
        self.assertEqual(self.exports(True), {"а"})
        touch_later(self.dump / "CommonModules/М/Ext/Module.bsl", "Процедура Б() Экспорт\nКонецПроцедуры\n")
        self.assertEqual(self.exports(True), {"б"})
        self.assertEqual(len(list(self.cache.glob("index-dir-*.json"))), 2)

    def test_parallel_publication_keeps_previous_fingerprint(self):
        self.assertEqual(self.exports(True), {"а"})
        old, = self.cache.glob("index-dir-*.json")
        before = old.read_bytes()
        touch_later(self.dump / "CommonModules/М/Ext/Module.bsl", "Процедура Б() Экспорт\nКонецПроцедуры\n")
        build = index.build
        overlap = threading.Barrier(2)

        def build_together(*args):
            overlap.wait(timeout=10)
            return build(*args)

        with mock.patch.object(index, "build", side_effect=build_together):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = [executor.submit(self.exports, True) for _ in range(2)]
                self.assertEqual([result.result(timeout=30) for result in results], [{"б"}, {"б"}])
        self.assertEqual(old.read_bytes(), before)
        new, = set(self.cache.iterdir()) - {old}
        self.assertEqual(set(json.loads(new.read_text(encoding="utf-8"))["commonModules"]["м"]["exports"]), {"б"})

    def test_markers_for_parent(self):
        """Основная конфигурация: правка модуля без новой выгрузки кеш не сбрасывает, новая выгрузка — сбрасывает."""
        self.assertEqual(self.exports(False), {"а"})
        touch_later(self.dump / "CommonModules/М/Ext/Module.bsl", "Процедура Б() Экспорт\nКонецПроцедуры\n")
        self.assertEqual(self.exports(False), {"а"})
        touch_later(self.dump / "ConfigDumpInfo.xml", "<ConfigDumpInfo><x/></ConfigDumpInfo>")
        self.assertEqual(self.exports(False), {"б"})

    def test_hidden_dirs_ignored(self):
        before = index.dir_fingerprint(self.dump, True)
        write(self.dump, ".git/мусор", "x")
        self.assertEqual(index.dir_fingerprint(self.dump, True), before)

    def test_not_a_dump(self):
        with self.assertRaises(FileNotFoundError):
            index.cached_dir(self.tmp, self.cache)


if __name__ == "__main__":
    unittest.main()
