"""Источник проверки: выгрузка в корне репозитория, указатели Git LFS вместо содержимого, BSL LS из VS Code."""
import contextlib
import errno
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import config
from otk_1c.errors import InfraError
from otk_1c.gate import collect, pipeline
from otk_1c.probes import build, platform
from otk_1c.sources import gitdiff, workspace
from test_collect import BASE_MODULE, CONFIG, FLAGS, HEAD_MODULE, run, write
from test_dirdiff import FakePlatform


def pointer(root, rel):
    """Указатель Git LFS — как его пишет git lfs: без BOM, LF."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(POINTER.encode("ascii"))


POINTER = ("version https://git-lfs.github.com/spec/v1\n"
           "oid sha256:c577b1df95b812448975d927bae93b52f1ec6f978cbcd6221bc8dbd803a3401c\nsize 23\n")


class TestSources(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))

    def tearDown(self):
        rmtree_force(self.tmp)

    def _repo(self):
        """Выгрузка в корне репозитория; модуль изменён в рабочем дереве, не закоммичен."""
        r = self.tmp / "repo"
        run(self.tmp, "init", "-q", "-b", "master", r.name)
        run(r, "config", "user.email", "t@t")
        run(r, "config", "user.name", "t")
        write(r, "Configuration.xml", CONFIG)
        write(r, "CommonModules/М.xml", FLAGS)
        write(r, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "base")
        write(r, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        return r

    def test_root_source_detected(self):
        r = self._repo()
        cfg = config.load(r)
        self.assertEqual((cfg["project"]["source"], cfg["project"].get("sourceDetected")), (".", True))
        (r / "otk.toml").write_text('[project]\nsource = "src/cf"\n', encoding="utf-8")
        self.assertEqual(config.load(r)["project"]["source"], "src/cf")      # задан явно — не подменяется

    def test_worktree_root_dump(self):
        """--worktree: незакоммиченная правка выгрузки в корне попадает в план."""
        r = self._repo()
        cfg = config._merge(config.load(r), {"checks": {"bslls": "off", "platform-api": "off"}})
        logs = []
        (self.tmp / "прогон").mkdir()
        plan, _ = pipeline._plan_step(r, cfg, self.tmp / "прогон", pipeline.GateOptions("HEAD", worktree=True),
                                      pipeline.checks.Gate(cfg), None, {}, logs.append)
        self.assertEqual([m["path"] for m in plan["modules"]], ["CommonModules/М/Ext/Module.bsl"])
        self.assertTrue(any("в корне репозитория" in m for m in logs))

    def test_changes_outside_source_hint(self):
        r = self._repo()
        run(r, "commit", "-q", "-am", "head")
        plan = collect.collect(r, "HEAD~1", "HEAD", "src/cf", ["Server"])
        self.assertEqual(plan["modules"], [])
        note = pipeline.changes_outside(r, plan, "src/cf")
        self.assertIn("--source", note)
        self.assertIn("CommonModules/М/Ext/Module.bsl", note)
        self.assertIsNone(pipeline.changes_outside(r, plan, "."))

    def test_lfs_pointers(self):
        d = self.tmp / "dump"
        write(d, "Configuration.xml", CONFIG)
        pointer(d, "CommonModules/М/Ext/Template.bin")
        (d / "Pictures").mkdir()
        (d / "Pictures" / "Big.bin").write_bytes(b"x" * 4096)
        pointer(d, ".git/lfs.bin")                                  # скрытые каталоги — не выгрузка
        self.assertEqual(workspace.lfs_pointers(d), ["CommonModules/М/Ext/Template.bin"])
        self.assertEqual(workspace.lfs_pointers(d, ["Configuration.xml"]), [])
        with self.assertRaisesRegex(InfraError, "git lfs pull"):
            build._check_lfs(d, None)

    def test_load_stops_on_lfs_pointer(self):
        """Указатель LFS в выгрузке — понятный сбой до вызова платформы."""
        d = self.tmp / "dump"
        write(d, "Configuration.xml", CONFIG)
        pointer(d, "Templates/Т/Ext/Template.bin")
        ib = self.tmp / "ib"
        ib.mkdir()
        (ib / "1Cv8.1CD").write_bytes(b"x")
        calls = []
        fake = FakePlatform(ib=ib, connection=None, designer=lambda *a, **k: calls.append(a) or (0, 1.0, ""),
                            ibcmd=lambda *a, **k: calls.append(a) or (0, 1.0, ""))
        with self.assertRaisesRegex(InfraError, "указателей Git LFS"):
            build._load(fake, self.tmp, d, None, None, None, {"ib": str(ib)})
        self.assertEqual(calls, [])

    def test_archive_error_reason(self):
        r = self._repo()
        with self.assertRaisesRegex(gitdiff.GitError, "git archive .+: .+"):
            workspace.materialize(r, "0" * 40, ".", self.tmp / "trees")
        self.assertIn("git lfs fetch", workspace._lfs_hint("fatal: smudge filter lfs failed", "abc"))
        self.assertEqual(workspace._lfs_hint("fatal: not a tree", "abc"), "")

    def test_tree_dir_short_sha(self):
        """Каталог редакции — начало sha: путь выгрузки ERP короче на 28 символов."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        target = workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertEqual(target, self.tmp / "trees" / sha[:workspace.TREE_SHA])
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").is_file())
        # остаток неудачного извлечения без маркера не принимается за готовое дерево
        (target.with_name(f"{target.name}.done")).unlink()
        (target / "CommonModules/М/Ext/Module.bsl").unlink()
        workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").is_file())

    def test_extract_long_path_error(self):
        """Файл редакции не записался — GitError с длиной пути и советом, неполное дерево удалено."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        long = str(self.tmp / ("д" * 300))
        failure = OSError(errno.ENAMETOOLONG, "File name too long", long)
        with mock.patch.object(workspace.tarfile.TarFile, "extractall", side_effect=failure):
            with self.assertRaisesRegex(gitdiff.GitError, r"извлечение редакции .+ символов: .+OTK_RUN_DIR"):
                workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertFalse((self.tmp / "trees" / sha[:workspace.TREE_SHA]).exists())
        self.assertEqual(workspace._long_path_hint(OSError(errno.EACCES, "denied", "C:/x"), "C:/x"), "")

    def test_extract_rejected_not_marked_done(self):
        """Фильтр tarfile отклонил запись при коде git 0 — сбой, маркер готовности не ставится."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        target = self.tmp / "trees" / sha[:workspace.TREE_SHA]
        with mock.patch.object(workspace.tarfile.TarFile, "extractall",
                               side_effect=workspace.tarfile.TarError("ссылка за пределы дерева")):
            with self.assertRaisesRegex(gitdiff.GitError, "извлечение редакции .+ссылка за пределы"):
                workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertFalse(target.with_name(f"{target.name}.done").exists())

    def test_tree_dir_mkdir_error(self):
        """Не создался сам каталог редакции — GitError с подсказкой, а не трассировка."""
        r = self._repo()
        failure = OSError(errno.ENAMETOOLONG, "File name too long", str(self.tmp / ("д" * 300)))
        with mock.patch.object(workspace.Path, "mkdir", side_effect=failure):
            with self.assertRaisesRegex(gitdiff.GitError, "каталог редакции .+OTK_RUN_DIR"):
                workspace.materialize(r, gitdiff.rev(r, "HEAD"), ".", self.tmp / "trees")

    def test_done_marker_error(self):
        """Маркер готовности не снялся или не записался — GitError с путём маркера, а не трассировка."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        for method in ("unlink", "touch"):
            with self.subTest(method=method), \
                    mock.patch.object(workspace.Path, method, side_effect=PermissionError(13, "Access is denied")):
                with self.assertRaisesRegex(gitdiff.GitError, r"маркер редакции .+\.done: .*Access is denied"):
                    workspace.materialize(r, sha, ".", self.tmp / "trees")

    def test_run_dir_inside_worktree_warning(self):
        r = self._repo()
        workspace._warned_inside.clear()
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            workspace.run_dir({"environment": {"runDir": str(self.tmp / "прогоны")}}, r)
            self.assertEqual(out.getvalue(), "")
            for _ in range(2):                                      # предупреждение — раз за процесс
                workspace.run_dir({"environment": {"runDir": str(r / ".agents" / ".artifacts")}}, r)
        self.assertEqual(out.getvalue().count("внутри рабочего дерева"), 1)
        self.assertIn("OTK_RUN_DIR", out.getvalue())

    def test_bslls_from_vscode(self):
        storage = self.tmp / "globalStorage"
        exe = platform.exe_name("bsl-language-server")
        base = storage / "1c-syntax.language-1c-bsl" / "bsl-language-server"
        for v in ("v0.24.0", "v1.0.7"):
            write(base / v / "bsl-language-server", exe, "")
        self.assertEqual(platform.vscode_bslls([storage]), base / "v1.0.7" / "bsl-language-server" / exe)
        self.assertIsNone(platform.vscode_bslls([self.tmp / "нет"]))
        with mock.patch.dict("os.environ", {"OTK_BSLLS": ""}), mock.patch("shutil.which", return_value=None), \
                mock.patch.object(platform, "vscode_bslls", return_value=Path("vs")):
            self.assertEqual(platform.find_bslls(), Path("vs"))


if __name__ == "__main__":
    unittest.main()
