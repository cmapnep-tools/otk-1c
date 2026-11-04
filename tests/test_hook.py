"""Хук pre-push: установка, диапазоны проверки, коды выхода, настоящий git push во временный bare-репозиторий."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.integrations import hook
from otk_1c.sources import gitdiff


def git(repo, *args, stdin=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=stdin, text=True)
    if r.returncode:
        raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def commit(repo, name, text):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-hook-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "master")
        git(self.repo, "config", "user.email", "t@example.org")
        git(self.repo, "config", "user.name", "t")
        self.a = commit(self.repo, "readme.txt", "a")

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_install_uninstall(self):
        path = hook.install(self.repo)
        self.assertEqual(path, hook.hook_path(self.repo))
        self.assertIn(hook.MARKER, path.read_text(encoding="utf-8"))
        hook.install(self.repo)                                 # повторная установка своего хука
        self.assertEqual(hook.uninstall(self.repo), path)
        self.assertFalse(path.exists())
        self.assertIsNone(hook.uninstall(self.repo))

    def test_foreign_hook_kept(self):
        path = hook.hook_path(self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            hook.install(self.repo)
        with self.assertRaises(FileExistsError):
            hook.uninstall(self.repo)
        hook.install(self.repo, force=True)
        self.assertIn(hook.MARKER, path.read_text(encoding="utf-8"))

    def test_hooks_path(self):
        git(self.repo, "config", "core.hooksPath", ".githooks")
        self.assertEqual(hook.install(self.repo), self.repo / ".githooks" / "pre-push")

    def test_ranges(self):
        b = commit(self.repo, "b.txt", "b")
        git(self.repo, "switch", "-q", "-c", "feature")
        c = commit(self.repo, "c.txt", "c")
        z = hook.ZERO
        lines = [
            f"refs/heads/master {b} refs/heads/master {self.a}",   # обновление ветки
            f"refs/heads/feature {c} refs/heads/feature {z}",      # новая ветка: от merge-base с master
            f"(delete) {z} refs/heads/old {self.a}",               # удаление
            f"refs/heads/same {b} refs/heads/same {b}",            # без изменений
        ]
        self.assertEqual(hook.ranges(self.repo, lines, "master"),
                         [("refs/heads/master", self.a, b), ("refs/heads/feature", b, c)])

    def test_unknown_remote_sha(self):
        b = commit(self.repo, "b.txt", "b")
        missing = "1" * 40                                      # ревизии удалённой стороны нет локально
        self.assertEqual(hook.ranges(self.repo, [f"refs/heads/x {b} refs/heads/x {missing}"], "master"),
                         [])                                    # merge-base(b, master) = b: проверять нечего
        with self.assertRaises(gitdiff.GitError):
            hook.ranges(self.repo, [f"refs/heads/x {b} refs/heads/x {hook.ZERO}"], "нет/такой")

    def _run(self, code, toml=""):
        if toml:
            (self.repo / "otk.toml").write_text(toml, encoding="utf-8")
        self.n = getattr(self, "n", 0) + 1
        prev = git(self.repo, "rev-parse", "HEAD")
        b = commit(self.repo, "b.txt", f"b{self.n}")
        calls = []

        def gate(repo, cfg, run_dir, opts, log):
            calls.append((opts.base, opts.head, {"static_only": opts.static_only, "fast": opts.fast, "log": log}))
            return {"code": code, "verdict": "x"}

        out = []
        rc = hook.run(self.repo, [f"refs/heads/master {b} refs/heads/master {prev}"], log=out.append, gate=gate)
        return rc, calls, out

    def test_run_codes(self):
        from otk_1c.gate import report
        orig = report.markdown
        report.markdown = lambda s: f"вердикт {s['code']}"
        try:
            rc, calls, out = self._run(1)
            self.assertEqual(rc, 1)
            self.assertEqual(calls[0][2], {"static_only": False, "fast": False, "log": calls[0][2]["log"]})
            self.assertTrue(any("push отменён" in m for m in out))
            self.assertEqual(self._run(0)[0], 0)
            self.assertEqual(self._run(2)[0], 2)
            rc, calls, _ = self._run(2, '[hook]\ninfraFail = "warn"\nmode = "static"\n')
            self.assertEqual(rc, 0)
            self.assertTrue(calls[0][2]["static_only"])
        finally:
            report.markdown = orig

    def test_missing_parent(self):
        """Нет выгрузки основной конфигурации (git worktree адаптера) — понятная причина, не трассировка."""
        (self.tmp / "runs").mkdir()
        (self.repo / "otk.toml").write_text(
            f'[project]\nsource = "src/cfe/X"\nparent = "src/cf"\nextension = "X"\n'
            f'[environment]\nrunDir = "{(self.tmp / "runs").as_posix()}"\n', encoding="utf-8")
        prev = git(self.repo, "rev-parse", "HEAD")
        b = commit(self.repo, "src/cfe/X/CommonModules/М/Ext/Module.bsl", "Процедура П() Экспорт\nКонецПроцедуры\n")
        out = []
        rc = hook.run(self.repo, [f"refs/heads/master {b} refs/heads/master {prev}"], log=out.append)
        self.assertEqual(rc, 2)
        self.assertTrue(any("parent = src/cf: нет файла конфигурации" in m for m in out), out)
        (self.repo / "otk.local.toml").write_text('[hook]\ninfraFail = "warn"\n', encoding="utf-8")
        self.assertEqual(hook.run(self.repo, [f"refs/heads/master {b} refs/heads/master {prev}"], log=out.append), 0)

    def test_real_push(self):
        """Хук срабатывает на настоящем git push: без изменений выгрузки — push проходит; сбой — отменён."""
        remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(remote))
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "-q", "origin", "master")
        (self.tmp / "runs").mkdir()
        (self.repo / "otk.local.toml").write_text(
            f'[environment]\nrunDir = "{(self.tmp / "runs").as_posix()}"\n', encoding="utf-8")
        hook.install(self.repo)
        commit(self.repo, "notes.txt", "без изменений выгрузки")
        r = subprocess.run(["git", "-C", str(self.repo), "push", "origin", "master"], capture_output=True)
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("otk pre-push", out)

        git(self.repo, "switch", "-q", "-c", "feature")
        commit(self.repo, "more.txt", "x")
        (self.repo / "otk.toml").write_text('[hook]\nbase = "нет/такой"\n', encoding="utf-8")
        r = subprocess.run(["git", "-C", str(self.repo), "push", "origin", "feature"], capture_output=True)
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("push отменён", out)
        r = subprocess.run(["git", "-C", str(self.repo), "push", "--no-verify", "origin", "feature"],
                           capture_output=True)
        self.assertEqual(r.returncode, 0)
        # журнал срабатываний: два запуска хука, --no-verify хук не запускает
        rows = [json.loads(x) for x in (self.tmp / "runs" / "history.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(x["source"], x["action"], x["code"] != 0) for x in rows],
                         [("hook", "pre-push", False), ("hook", "pre-push", True)])


if __name__ == "__main__":
    unittest.main()
