"""Источник проверки: выгрузка в корне репозитория, указатели Git LFS вместо содержимого, BSL LS из VS Code."""
import contextlib
import errno
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import _faults
from _fs import rmtree_force
from otk_1c import config
from otk_1c.errors import InfraError
from otk_1c.gate import collect, pipeline, report
from otk_1c.probes import build, platform
from otk_1c.sources import gitdiff, revision, workspace
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

    def _assert_snapshot(self, repo, plan, run_dir, expected):
        """Статика, корень сборки/проб и строки отчёта читают один снимок."""
        rel = "CommonModules/М/Ext/Module.bsl"
        cfg = config._merge(config.load(repo), {"checks": {"analyzers": "off", "platform-api": "off"}})
        self.assertEqual(revision.read_head(repo, plan)(rel).splitlines(), expected.splitlines())
        with mock.patch.object(pipeline.static, "run", wraps=pipeline.static.run) as check:
            pipeline.static_step(repo, cfg, run_dir, run_dir / "otk.json")
        self.assertEqual(check.call_args.args[3](rel).splitlines(), expected.splitlines())
        tree = workspace.tree_root(repo, plan, run_dir)
        self.assertNotEqual(tree, repo)
        self.assertEqual((tree / rel).read_text(encoding="utf-8-sig").splitlines(), expected.splitlines())
        reader = report.head_reader(lambda: workspace.tree_root(repo, plan, run_dir), plan)
        self.assertEqual(reader(rel).splitlines(), expected.splitlines())
        self.assertEqual(workspace.plan_root(repo, run_dir / "otk.json", cfg), tree)

    def test_explicit_head_ignores_dirty_tree(self):
        repo = self._repo()
        run(repo, "commit", "-q", "-am", "head")
        write(repo, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        run_dir = self.tmp / "explicit"
        run_dir.mkdir()
        plan = collect.collect(repo, "HEAD~1", "HEAD", ".", ["Server"])
        (run_dir / "otk.json").write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(plan["modules"][0]["changedMethods"][0]["name"], "Изменяемая")
        self._assert_snapshot(repo, plan, run_dir, HEAD_MODULE)

    def test_head_snapshot_ignores_archive_attributes(self):
        rel = "CommonModules/М/Ext/Module.bsl"
        repo = self._repo()
        for attr in ("export-ignore", "export-subst"):
            with self.subTest(attr=attr):
                expected = HEAD_MODULE.replace("Возврат П;", f'Возврат "$Format:%H$ {attr}";')
                (repo / ".gitattributes").write_text(f"*.bsl -text {attr}\n", encoding="utf-8")
                write(repo, rel, expected)
                run(repo, "add", "-A")
                run(repo, "commit", "-q", "-m", attr)
                run_dir = self.tmp / attr
                plan = collect.collect(repo, "HEAD~1", "HEAD", ".", ["Server"])
                self.assertEqual([module["path"] for module in plan["modules"]], [rel])

                # Снимок от старой версии мог пропустить модуль или изменить его при экспорте.
                sha = plan["headSha"]
                stale = run_dir / "trees" / sha[:workspace.TREE_SHA]
                write(stale, "Configuration.xml", CONFIG)
                if attr == "export-subst":
                    write(stale, rel, expected.replace("$Format:%H$", sha))
                old_key = hashlib.sha1(b".", usedforsecurity=False).hexdigest()
                stale.with_name(f"{stale.name}-{old_key}.done").write_bytes(b".")

                (run_dir / "otk.json").write_text(json.dumps(plan), encoding="utf-8")
                self._assert_snapshot(repo, plan, run_dir, expected)
                self.assertEqual((stale / rel).read_bytes(), (repo / rel).read_bytes())

    def test_snapshot_failure_does_not_fall_back_to_worktree(self):
        repo = self._repo()
        plan = {"headSha": gitdiff.rev(repo, "HEAD"), "source": "."}
        with mock.patch.object(workspace, "materialize", side_effect=InfraError("snapshot unavailable")):
            with self.assertRaisesRegex(InfraError, "snapshot unavailable"):
                workspace.tree_root(repo, plan, self.tmp / "failed")

    def test_head_moves_after_plan(self):
        repo = self._repo()
        run(repo, "commit", "-q", "-am", "head")
        run_dir = self.tmp / "moving"
        run_dir.mkdir()
        plan = collect.collect(repo, "HEAD~1", "HEAD", ".", ["Server"])
        (run_dir / "otk.json").write_text(json.dumps(plan), encoding="utf-8")
        write(repo, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        run(repo, "commit", "-q", "-am", "later")
        self._assert_snapshot(repo, plan, run_dir, HEAD_MODULE)

    def test_worktree_snapshot_survives_later_edits(self):
        repo = self._repo()
        cfg = config._merge(config.load(repo), {"checks": {"bslls": "off", "platform-api": "off"}})
        run_dir = self.tmp / "worktree"
        run_dir.mkdir()
        plan, _ = pipeline._plan_step(repo, cfg, run_dir, pipeline.GateOptions("HEAD", worktree=True),
                                      pipeline.checks.Gate(cfg), None, {}, lambda _message: None)
        self.assertTrue(plan["modules"])
        write(repo, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        self._assert_snapshot(repo, plan, run_dir, HEAD_MODULE)

    def test_resolver_source_follows_plan_revision(self):
        repo = self._repo()
        run(repo, "commit", "-q", "-am", "head")
        plan = collect.collect(repo, "HEAD~1", "HEAD", ".", ["Server"])
        rel = "CommonModules/М/Ext/Module.bsl"
        plan["queries"] = [{"id": "q", "module": rel, "line": 2, "text": "ВЫБРАТЬ <?>", "complete": False}]
        write(repo, rel, BASE_MODULE)
        cfg = config._merge(config.load(repo), {"queries": {"resolvers": [{"command": ["fake"]}]}})

        def resolve(_resolver, payload):
            self.assertEqual((Path(payload["source"]) / rel).read_text(encoding="utf-8-sig").splitlines(),
                             HEAD_MODULE.splitlines())
            self.assertEqual(payload["modules"][rel].splitlines(), HEAD_MODULE.splitlines())
            return {}

        with mock.patch.object(pipeline.resolvers, "_call", side_effect=resolve) as call:
            pipeline.resolve_queries(plan, repo, cfg, self.tmp / "resolver", log=lambda _: None)
        call.assert_called_once()

    def test_scenarios_follow_plan_revision(self):
        repo = self._repo()
        rel = "tests/cfe/Tests/CommonModules/Tests/Ext/Module.bsl"
        registration = ('Процедура ИсполняемыеСценарии() Экспорт\n'
                        'ЮТТесты.ДобавитьТест("Committed");\nКонецПроцедуры\n')
        write(repo, "tests/cfe/Tests/Configuration.xml", "<Name>CommittedExtension</Name>")
        write(repo, rel, registration)
        run(repo, "add", "-A")
        run(repo, "commit", "-q", "-m", "scenarios")
        write(repo, rel, registration.replace("Committed", "Dirty"))
        write(repo, "tests/cfe/Tests/Configuration.xml", "<Name>DirtyExtension</Name>")
        paths = ("tests/cfe", str(repo / "tests" / "cfe"), str(Path("tests/cfe/../cfe")))
        for n, path in enumerate(paths):
            cfg = config._merge(config.load(repo), {"scenarios": {"path": path}})
            for worktree, name, extension in ((False, "Committed", "CommittedExtension"),
                                               (True, "Dirty", "DirtyExtension")):
                with self.subTest(path=path, worktree=worktree):
                    run_dir = self.tmp / f"scenarios-{n}-{worktree}"
                    run_dir.mkdir()
                    opts = pipeline.GateOptions("HEAD~1", head="HEAD", worktree=worktree, all_scenarios=True)
                    plan, _ = pipeline._plan_step(repo, cfg, run_dir, opts, pipeline.checks.Gate(cfg), None, {},
                                                  lambda _: None)
                    self.assertEqual([s["name"] for s in plan["scenarios"]], [name])
                    self.assertEqual(plan["scenarioExtensions"], [{"name": extension, "dir": "tests/cfe/Tests"}])

    def test_git_scenarios_path_outside_repo_is_rejected(self):
        repo = self._repo()
        cfg = config._merge(config.load(repo), {"scenarios": {"path": "../outside"}})
        run_dir = self.tmp / "outside-scenarios"
        run_dir.mkdir()
        opts = pipeline.GateOptions("HEAD", head="HEAD", all_scenarios=True)
        with mock.patch.object(pipeline.scen_mod, "discover") as discover:
            with self.assertRaisesRegex(InfraError, r"\[scenarios\] path.*внутри репозитория") as error:
                pipeline._plan_step(repo, cfg, run_dir, opts, pipeline.checks.Gate(cfg), None, {}, lambda _: None)
            self.assertEqual(error.exception.code, "config-invalid")
            discover.assert_not_called()

    def test_gate_retry_starts_fresh(self):
        """Повтор после прерывания заново строит план и не переиспользует файлы шагов прошлого запуска."""
        repo = self._repo()
        cfg = config._merge(config.load(repo), {"checks": {"bslls": "off", "platform-api": "off"}})
        run_dir = self.tmp / "retry-run"
        run_dir.mkdir()
        stale = [run_dir / name for name in ("static.json", "run.json", "designer.json")]
        for path in stale:
            path.write_text('{"plan":"old"}', encoding="utf-8")
        observations = []

        def probes(*_args, **_kwargs):
            observations.append([path.exists() for path in stale])
            return False, None

        with mock.patch.object(pipeline, "_probe_step", side_effect=probes), \
                mock.patch.object(pipeline.collect_mod, "collect", wraps=pipeline.collect_mod.collect) as collect:
            for _ in range(2):
                pipeline.gate_run(repo, cfg, run_dir, pipeline.GateOptions("HEAD", worktree=True, static_only=True),
                                  log=lambda _message: None)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(observations, [[False, False, False], [False, False, False]])
        self.assertTrue((run_dir / "otk.json").is_file())

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

    def test_lfs_pointer_inside_form(self):
        """Форма грузится описанием: указатель LFS в её содержимом или модуле тоже виден проверке."""
        for rel in ("CommonForms/Ф/Ext/Form.xml", "CommonForms/Ф/Ext/Form/Module.bsl"):
            d = self.tmp / rel.replace("/", "-")
            write(d, "Configuration.xml", CONFIG)
            pointer(d, rel)
            with self.assertRaisesRegex(InfraError, "указателей Git LFS"):
                build._check_lfs(d, ["CommonForms/Ф.xml"])
        d = self.tmp / "clean"
        write(d, "CommonForms/Ф.xml", "<form/>")
        build._check_lfs(d, ["CommonForms/Ф.xml"])                 # нет файлов формы — проверять нечего

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

    def test_git_snapshot_preserves_lfs_pointer_and_stops_load(self):
        repo = self._repo()
        rel = "Templates/Т/Ext/Template.bin"
        pointer(repo, rel)
        run(repo, "add", rel)
        run(repo, "commit", "-q", "-m", "pointer")
        sha = gitdiff.rev(repo, "HEAD")
        (repo / rel).write_bytes(b"hydrated in worktree")
        snapshot = workspace.materialize(repo, sha, ".", self.tmp / "trees")
        self.assertEqual((snapshot / rel).read_bytes(), POINTER.encode("ascii"))
        ib = self.tmp / "ib"
        ib.mkdir()
        (ib / "1Cv8.1CD").write_bytes(b"x")
        fake = mock.Mock(ib=ib, connection=None)
        with self.assertRaisesRegex(InfraError, r"в снимке Git-редакции .+через --dir"):
            build.build(repo, sha, ".", snapshot, fake, self.tmp / "run", full=True)
        self.assertEqual(fake.mock_calls, [mock.call.lock()])

    def test_git_tree_error_reason(self):
        r = self._repo()
        with self.assertRaisesRegex(gitdiff.GitError, "git ls-tree .+: .+"):
            workspace.materialize(r, "0" * 40, ".", self.tmp / "trees")

    def test_tree_dir_short_sha(self):
        """Каталог редакции — начало sha: путь выгрузки ERP короче на 28 символов."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        target = workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertEqual(target, self.tmp / "trees" / sha[:workspace.TREE_SHA])
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").is_file())
        # остаток неудачного извлечения без маркера не принимается за готовое дерево
        workspace.done_marker(target, ".").unlink()
        (target / "CommonModules/М/Ext/Module.bsl").unlink()
        workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").is_file())

    def test_tree_shared_by_sources(self):
        """Источник плана и расширения сценариев — в одном дереве редакции: следующий src не стирает прежние."""
        r = self._repo()
        for ext in ("ТестыА", "ТестыБ"):
            write(r, f"ext/{ext}/Configuration.xml", CONFIG)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "ext")
        sha = gitdiff.rev(r, "HEAD")
        trees = self.tmp / "trees"
        target = workspace.materialize(r, sha, "CommonModules", trees)
        for ext in ("ТестыА", "ТестыБ"):
            self.assertEqual(workspace.materialize(r, sha, f"ext/{ext}", trees), target)
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").is_file())
        self.assertTrue((target / "ext/ТестыА/Configuration.xml").is_file())
        # неудачное извлечение одного src снимает только его маркер, ничего не удаляет
        workspace.done_marker(target, "ext/ТестыБ").unlink()
        with mock.patch.object(workspace, "_extract_blobs", side_effect=OSError(errno.EIO, "I/O")):
            with self.assertRaises(gitdiff.GitError):
                workspace.materialize(r, sha, "ext/ТестыБ", trees)
        self.assertFalse(workspace.done_marker(target, "ext/ТестыБ").exists())
        self.assertTrue((target / "ext/ТестыА/Configuration.xml").is_file())
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").is_file())
        self.assertTrue(workspace.done_marker(target, "ext/ТестыА").is_file())
        # остаток извлекается заново поверх: файлы редакции неизменны
        (target / "ext/ТестыБ/Configuration.xml").write_text("оборвано", encoding="utf-8")
        workspace.materialize(r, sha, "ext/ТестыБ", trees)
        self.assertEqual((target / "ext/ТестыБ/Configuration.xml").read_text(encoding="utf-8-sig"), CONFIG)
        workspace.materialize(r, sha, ".", trees)   # «.» пересекается со всеми — их маркеры сняты
        self.assertFalse(workspace.done_marker(target, "ext/ТестыА").exists())
        # расширение внутри извлечённого источника готово по его маркеру: без извлечения и снятия маркера
        with mock.patch.object(workspace, "_extract_blobs", side_effect=AssertionError("извлечение")):
            self.assertEqual(workspace.materialize(r, sha, "ext/ТестыА", trees), target)
        self.assertTrue(workspace.done_marker(target, ".").is_file())
        # неполное расширение внутри «.» извлекается заново; сбой мог задеть дерево «.» — его маркер снят
        (target / "ext/ТестыБ/Configuration.xml").unlink()
        with mock.patch.object(workspace, "_extract_blobs", side_effect=OSError(errno.EIO, "I/O")):
            with self.assertRaises(gitdiff.GitError):
                workspace.materialize(r, sha, "ext/ТестыБ", trees)
        self.assertFalse(workspace.done_marker(target, ".").exists())
        workspace.materialize(r, sha, "ext/ТестыА", trees)       # без «.» — свой маркер
        self.assertTrue(workspace.done_marker(target, "ext/ТестыА").is_file())
        # маркер, оборванный посреди символа UTF-8, — нечитаемый: снимается, а не трассировка
        workspace.done_marker(target, "ext/ТестыА").write_bytes("ext/Т".encode("utf-8")[:-1])
        workspace.materialize(r, sha, "CommonModules", trees)
        self.assertFalse(workspace.done_marker(target, "ext/ТестыА").exists())
        if os.name == "nt":                         # пересечение источников на Windows — без учёта регистра
            self.assertEqual(workspace._parts("Src/Objects"), workspace._parts("src/objects"))

    def test_marker_must_match_name_and_content(self):
        """Испорченный маркер готовности (пустой, с чужим содержимым, с именем не от содержимого) — как
        отсутствующий: дерево извлекается заново, а не отдаётся неполным."""
        r = self._repo()
        write(r, "ext/ТестыА/Configuration.xml", CONFIG)
        write(r, "ext/ТестыА/README.md", "расширение")
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "ext")
        sha = gitdiff.rev(r, "HEAD")
        trees = self.tmp / "trees"
        target = workspace.materialize(r, sha, ".", trees)
        module = target / "CommonModules/М/Ext/Module.bsl"
        own = workspace.done_marker(target, ".")
        foreign = trees / f"{target.name}-{'0' * 40}.done"          # имя — не от этого содержимого
        variants = {"пустой": (own, ""),
                    "чужое имя при содержимом «.»": (foreign, "."),   # «.» покрывает любой источник
                    "лишние символы в содержимом": (own, ".\n")}
        for name, (marker, content) in variants.items():
            with self.subTest(name):
                for m in trees.glob("*.done"):
                    m.unlink()
                module.unlink()
                marker.write_text(content, encoding="utf-8")
                self.assertEqual(workspace.materialize(r, sha, ".", trees), target)
                self.assertTrue(module.is_file())
        # маркер «.» с путём объемлющего источника: по содержимому он источник покрывает, по имени — не его.
        # Без проверки имени дерево отдалось бы неполным: файл не восстановлен, формат источника на месте.
        readme = target / "ext/ТестыА/README.md"
        for m in trees.glob("*.done"):
            m.unlink()
        own.write_text("ext/ТестыА", encoding="utf-8")
        readme.unlink()
        self.assertEqual(workspace.materialize(r, sha, "ext/ТестыА", trees), target)
        self.assertTrue(readme.is_file())

    def test_drop_markers_compares_content_as_written(self):
        """Снятие пересекающихся маркеров сверяет содержимое так же, как приём: без strip. Иначе маркер источника
        `src/A ` (пробел — часть имени каталога на Linux) уцелел бы при сбое извлечения `src/A /ext`, и следующий
        вызов принял бы неполное дерево за готовое."""
        trees = self.tmp / "trees"
        trees.mkdir()
        target = trees / ("0" * workspace.TREE_SHA)
        parent = workspace.done_marker(target, "src/A ")
        parent.write_text("src/A ", encoding="utf-8")
        sibling = workspace.done_marker(target, "src/B")
        sibling.write_text("src/B", encoding="utf-8")
        workspace._drop_markers(target, PurePosixPath("src/A /ext"))
        self.assertFalse(parent.exists())
        self.assertTrue(sibling.is_file())          # не пересекается — маркер на месте

    def test_marker_content_compared_as_bytes(self):
        """Имя маркера сверяется с его байтами: read_text переводит CRLF в LF, и маркер с содержимым b"src\\r\\n",
        названный от "src\\n", прошёл бы за маркер пути с одним переводом строки (на Linux имя файла может его
        содержать). Такой маркер — как отсутствующий."""
        trees = self.tmp / "trees"
        trees.mkdir()
        target = trees / ("0" * workspace.TREE_SHA)
        workspace.done_marker(target, "src\n").write_bytes(b"src\r\n")
        self.assertFalse(workspace._covered(target, PurePosixPath("src\n")))

    def test_materialize_fault_injection(self):
        """Сбой любой файловой операции на любом шаге — один источник и общее дерево плана и расширений: наружу
        только GitError, маркер не стоит на неполном источнике, следующий прогон даёт полное дерево."""
        r = self._repo()
        for ext in ("ТестыА", "ТестыБ"):
            write(r, f"ext/{ext}/Configuration.xml", CONFIG)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "ext")
        sha = gitdiff.rev(r, "HEAD")
        expected = set(filter(None, gitdiff.git(r, "ls-tree", "-r", "-z", "--name-only", sha).split("\0")))

        def files(root):
            return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

        def want(src):
            return {f for f in expected if src == "." or f.startswith(src + "/")}

        def assert_markers_honest(trees, steps):
            """Маркер стоит только на полностью извлечённом источнике: свой и объемлющий (src в маркере)."""
            target = trees / sha[:workspace.TREE_SHA]
            have = files(target) if target.is_dir() else set()
            for s in steps:
                if workspace.done_marker(target, s).is_file():
                    self.assertEqual(want(s) - have, set(), f"маркер {s} на неполном дереве")
            for m in trees.glob("*.done"):
                src = m.read_text(encoding="utf-8").strip()
                self.assertEqual(want(src) - have, set(), f"маркер {src} на неполном дереве")

        # один источник; источник плана и расширения рядом (как у сценариев); вложенные и объемлющий
        sequences = ((".",), ("CommonModules", "ext/ТестыА", "ext/ТестыБ"), ("ext/ТестыА", "ext", "."))
        for seq, steps in enumerate(sequences):     # каталоги — свои у каждой последовательности: с чистого листа
            def scenario(trees, steps=steps):
                for s in steps:
                    workspace.materialize(r, sha, s, trees)
            clean = self.tmp / f"чисто{seq}"
            found = _faults.points(lambda: scenario(clean))  # noqa: B023 — вызывается здесь же
            self.assertTrue(found)
            for i, (owner, name, nth) in enumerate(found):
                with self.subTest(steps=steps, fault=_faults.label(owner, name, nth)):
                    trees = self.tmp / f"сбой{seq}-{i}"
                    self.assertFalse(trees.exists())
                    with _faults.fail_at(owner, name, nth):
                        try:
                            scenario(trees)
                        except gitdiff.GitError:
                            pass                            # понятная ошибка; любая другая — провал теста
                    assert_markers_honest(trees, steps)
                    target = trees / sha[:workspace.TREE_SHA]
                    for k, s in enumerate(steps):           # следующий прогон: каждый шаг не портит прежние
                        workspace.materialize(r, sha, s, trees)
                        have = files(target)
                        for p in steps[:k + 1]:
                            self.assertEqual(want(p) - have, set(), f"после {s} неполон {p}")

    def test_bad_head_sha_rejected(self):
        """headSha плана — не sha коммита (путь и т. п.): отказ до построения каталога редакции."""
        r = self._repo()
        for sha in (str(self.tmp / "чужое"), "../x", "", None):
            with self.subTest(sha=sha):
                with self.assertRaisesRegex(gitdiff.GitError, "нужен sha коммита"):
                    workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertFalse((self.tmp / "trees").exists())

    def test_source_outside_tree_rejected(self):
        """Источник плана — абсолютный путь или с «..»: отказ до извлечения, вне дерева редакции ничего не трогается."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        keep = self.tmp / "чужое"
        keep.mkdir()
        for src in (str(keep), "../чужое", "ext/../../чужое"):
            with self.subTest(src=src):
                with self.assertRaisesRegex(gitdiff.GitError, "относительный путь внутри репозитория"):
                    workspace.materialize(r, sha, src, self.tmp / "trees" / "x")
        self.assertTrue(keep.is_dir())

    def test_extract_long_path_error(self):
        """Файл редакции не записался — GitError с длиной пути и советом, неполное дерево без маркера."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        long = str(self.tmp / ("д" * 300))
        failure = OSError(errno.ENAMETOOLONG, "File name too long", long)
        with mock.patch.object(workspace, "_extract_blobs", side_effect=failure):
            with self.assertRaisesRegex(gitdiff.GitError, r"извлечение редакции .+ символов: .+OTK_RUN_DIR"):
                workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertFalse(workspace.done_marker(self.tmp / "trees" / sha[:workspace.TREE_SHA], ".").exists())
        self.assertEqual(workspace._long_path_hint(OSError(errno.EACCES, "denied", "C:/x"), "C:/x"), "")

    def test_extract_rejected_not_marked_done(self):
        """Git-ссылка на путь за пределами снимка не извлекается, даже если ОС не умеет создавать ссылки."""
        r = self._repo()
        link = self.tmp / "link-content"
        link.write_bytes(b"../../outside")
        oid = gitdiff.git(r, "hash-object", "-w", str(link)).strip()
        run(r, "update-index", "--add", "--cacheinfo", f"120000,{oid},external-link")
        run(r, "commit", "-q", "-m", "external link")
        sha = gitdiff.rev(r, "HEAD")
        target = self.tmp / "trees" / sha[:workspace.TREE_SHA]
        with self.assertRaisesRegex(gitdiff.GitError, "извлечение редакции .+ссылка за пределы дерева"):
            workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertFalse(workspace.done_marker(target, ".").exists())

    def test_extract_symlink_loop_error(self):
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        target = self.tmp / "trees" / sha[:workspace.TREE_SHA]
        with mock.patch.object(workspace.Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            with self.assertRaisesRegex(gitdiff.GitError, "извлечение редакции .+цикл ссылок"):
                workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertFalse(workspace.done_marker(target, ".").exists())

    def test_extract_directory_symlinks_after_their_targets(self):
        r = self._repo()
        (r / "z-dir").mkdir()
        (r / "z-dir" / "content.txt").write_text("content", encoding="utf-8")
        run(r, "add", "z-dir")
        for name, value in [("a-link", "b-link"), ("b-link", "z-dir")]:
            link = self.tmp / "link-content"
            link.write_bytes(value.encode())
            oid = gitdiff.git(r, "hash-object", "-w", str(link)).strip()
            run(r, "update-index", "--add", "--cacheinfo", f"120000,{oid},{name}")
        run(r, "commit", "-q", "-m", "directory links")
        sha = gitdiff.rev(r, "HEAD")

        created = []

        def create(dest, link, *, target_is_directory):
            # Stand in for a Windows directory symlink without requiring its privilege.
            created.append((dest.name, link, target_is_directory))
            dest.mkdir()

        with mock.patch.object(workspace.Path, "symlink_to", autospec=True, side_effect=create):
            workspace.materialize(r, sha, ".", self.tmp / "trees")
        self.assertEqual(created, [("b-link", "z-dir", True), ("a-link", "b-link", True)])

    def test_extract_directory_symlink_before_sibling_source(self):
        r = self._repo()
        for src in ("src/first", "src/second"):
            source = r / src
            source.mkdir(parents=True)
            (source / "Configuration.xml").write_text("<Configuration/>", encoding="utf-8")
        run(r, "add", "src")
        for name, value in [("src/first/link", "../second/alias"), ("src/second/alias", ".")]:
            link = self.tmp / "link-content"
            link.write_bytes(value.encode())
            oid = gitdiff.git(r, "hash-object", "-w", str(link)).strip()
            run(r, "update-index", "--add", "--cacheinfo", f"120000,{oid},{name}")
        run(r, "commit", "-q", "-m", "link across sources")
        sha = gitdiff.rev(r, "HEAD")
        trees = self.tmp / "trees"

        with mock.patch.object(workspace.Path, "symlink_to", autospec=True) as create:
            target = workspace.materialize(r, sha, "src/first", trees)
            self.assertFalse((target / "src/second").exists())
            create.assert_called_once()
            first_path, first_link = create.call_args.args
            self.assertTrue(first_path.parent.samefile(target / "src/first"))
            self.assertEqual(first_path.name, "link")
            self.assertEqual(first_link, "../second/alias")
            self.assertTrue(create.call_args.kwargs["target_is_directory"])

            workspace.materialize(r, sha, "src/second", trees)
            self.assertTrue((target / "src/second").is_dir())
            second_path, second_link = create.call_args.args
            self.assertTrue(second_path.parent.samefile(target / "src/second"))
            self.assertEqual(second_path.name, "alias")
            self.assertEqual(second_link, ".")
            self.assertTrue(create.call_args.kwargs["target_is_directory"])

    def test_extract_git_symlink_cycle_before_creating_links(self):
        r = self._repo()
        for name, value in [("a-link", "b-link"), ("b-link", "a-link")]:
            link = self.tmp / "link-content"
            link.write_bytes(value.encode())
            oid = gitdiff.git(r, "hash-object", "-w", str(link)).strip()
            run(r, "update-index", "--add", "--cacheinfo", f"120000,{oid},{name}")
        run(r, "commit", "-q", "-m", "cyclic links")
        sha = gitdiff.rev(r, "HEAD")
        trees = self.tmp / "trees"
        with mock.patch.object(workspace.Path, "symlink_to") as create:
            with self.assertRaisesRegex(gitdiff.GitError, "цикл ссылок"):
                workspace.materialize(r, sha, ".", trees)
        create.assert_not_called()
        self.assertFalse(workspace.done_marker(trees / sha[:workspace.TREE_SHA], ".").exists())

    def test_tree_dir_mkdir_error(self):
        """Не создался сам каталог редакции — GitError с подсказкой, а не трассировка."""
        r = self._repo()
        failure = OSError(errno.ENAMETOOLONG, "File name too long", str(self.tmp / ("д" * 300)))
        with mock.patch.object(workspace.Path, "mkdir", side_effect=failure):
            with self.assertRaisesRegex(gitdiff.GitError, "каталог редакции .+OTK_RUN_DIR"):
                workspace.materialize(r, gitdiff.rev(r, "HEAD"), ".", self.tmp / "trees")

    def test_done_marker_error(self):
        """Ошибки окружения вокруг извлечения (маркер готовности, проверка дерева, временный файл) — GitError
        с путём, а не трассировка."""
        r = self._repo()
        sha = gitdiff.rev(r, "HEAD")
        trees = self.tmp / "trees"
        denied13 = PermissionError(13, "Access is denied")
        with mock.patch.object(workspace.tempfile, "mkstemp", side_effect=denied13):    # запись маркера
            with self.assertRaisesRegex(gitdiff.GitError, r"маркер редакции .+\.done: .*Access is denied"):
                workspace.materialize(r, sha, ".", trees)
        with mock.patch.object(workspace.Path, "replace", side_effect=denied13):        # маркер — атомарно
            with self.assertRaisesRegex(gitdiff.GitError, r"маркер редакции .+\.done: .*Access is denied"):
                workspace.materialize(r, sha, ".", trees)
        self.assertEqual(list(trees.glob("*.done*")), [])                # ни маркера, ни временного файла
        target = workspace.materialize(r, sha, ".", trees)
        (target / "Configuration.xml").unlink()                     # дерево не готово, маркер есть — снимается
        with mock.patch.object(workspace.Path, "unlink", side_effect=denied13):
            with self.assertRaisesRegex(gitdiff.GitError, r"маркер редакции .+\.done: .*Access is denied"):
                workspace.materialize(r, sha, ".", trees)
        keep = target / "preserve"                                  # дерево не переизвлекается и не удаляется
        keep.write_bytes(b"preserve")
        denied = PermissionError(13, "Access is denied", str(self.tmp / "trees" / "x.done"))
        with mock.patch.object(workspace.Path, "read_bytes", side_effect=denied), \
                mock.patch.object(workspace, "_extract_blobs") as extract:
            with self.assertRaisesRegex(gitdiff.GitError, r"готовая редакция .+x\.done: .*Access is denied"):
                workspace.materialize(r, sha, ".", self.tmp / "trees")
        extract.assert_not_called()
        self.assertEqual(keep.read_bytes(), b"preserve")
        # каталог временных файлов не найден: и TemporaryFile, и gettempdir бросают FileNotFoundError
        none = FileNotFoundError(errno.ENOENT, "No usable temporary directory found")
        with mock.patch.object(workspace.tempfile, "TemporaryFile", side_effect=none), \
                mock.patch.object(workspace.tempfile, "gettempdir", side_effect=none):
            with self.assertRaisesRegex(gitdiff.GitError, "извлечение редакции .+No usable"):
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
        # runDir по умолчанию (<TEMP>/otk) проверяется так же
        with contextlib.redirect_stderr(out), mock.patch.object(workspace.tempfile, "gettempdir",
                                                                return_value=str(r / "tmp")):
            workspace.run_dir({"environment": {}}, r)
        self.assertEqual(out.getvalue().count("внутри рабочего дерева"), 2)
        # цикл символических ссылок в runDir (RuntimeError до Python 3.13) — без предупреждения и трассировки
        with mock.patch.object(workspace.Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            workspace._warn_inside(self.tmp / "петля", r)

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
