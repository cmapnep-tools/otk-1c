"""Изоляция процессных gate и выбор завершённого отчёта."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_iblock
from _fs import rmtree_force
from otk_1c import cli, config
from otk_1c.gate import pipeline
from otk_1c.probes import iblock
from otk_1c.sources import index, workspace
from test_collect import BASE_MODULE, CONFIG, FLAGS, HEAD_MODULE, run, write


class IsolatedRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-isolation-"))
        self.repo = self.tmp / "repo"
        run(self.tmp, "init", "-q", "-b", "master", "repo")
        run(self.repo, "config", "user.email", "t@t")
        run(self.repo, "config", "user.name", "t")
        write(self.repo, "Configuration.xml", CONFIG)
        write(self.repo, "CommonModules/М.xml", FLAGS)
        write(self.repo, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-q", "-m", "base")
        write(self.repo, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        run(self.repo, "commit", "-q", "-am", "head")
        self.cfg = {"environment": {"runDir": str(self.tmp / "runs")}}

    def tearDown(self):
        rmtree_force(self.tmp)

    def start(self, head):
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")}
        args = [sys.executable, "-m", "otk_1c", "gate", "--repo", str(self.repo), "--base", "HEAD~1",
                "--head", head, "--static-only", "--json", "--run-dir", str(self.tmp / "runs"),
                "--set", "checks.analyzers=off", "--set", "checks.platform-api=off"]
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def test_parallel_revisions_keep_plans_and_summaries(self):
        processes = [self.start(head) for head in ("HEAD~1", "HEAD")]
        results = []
        try:
            for proc in processes:
                stdout, stderr = proc.communicate(timeout=60)
                self.assertIn(proc.returncode, (0, 1), stderr.decode())
                results.append(json.loads(stdout))
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=10)
        dirs = [Path(result["runDir"]) for result in results]
        self.assertFalse(dirs[0].samefile(dirs[1]))
        plans = [json.loads((d / "otk.json").read_text(encoding="utf-8")) for d in dirs]
        self.assertEqual(len(plans[0]["modules"]), 0)
        self.assertEqual(len(plans[1]["modules"]), 1)
        self.assertNotEqual(plans[0]["headSha"], plans[1]["headSha"])
        for result, directory in zip(results, dirs, strict=True):
            self.assertTrue(Path(result["summaryPath"]).samefile(directory / "summary.json"))
            self.assertTrue((directory / ".completed").exists())
            summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["code"], result["code"])
            self.assertEqual(summary["findings"], result["findings"])

        other_before = {p.name: p.read_bytes() for p in dirs[1].iterdir() if p.is_file()}
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
               "OTK_RUN_DIR": str(self.tmp / "runs")}
        args = [sys.executable, "-m", "otk_1c", "report", "--repo", str(self.repo), "--json"]
        explicit = subprocess.run([*args, "--run-dir", str(dirs[0])], capture_output=True, env=env, timeout=30)
        self.assertTrue(Path(json.loads(explicit.stdout)["runDir"]).samefile(dirs[0]))
        self.assertEqual(other_before, {p.name: p.read_bytes() for p in dirs[1].iterdir() if p.is_file()})
        unfinished = workspace.new_run(self.cfg, self.repo)
        (unfinished / "otk.json").write_bytes((dirs[0] / "otk.json").read_bytes())
        default = subprocess.run(args, capture_output=True, env=env, timeout=30)
        self.assertTrue(Path(json.loads(default.stdout)["runDir"]).samefile(workspace.latest_run(dirs[0].parent)))

    def test_report_explicit_root_selects_its_own_plan(self):
        directories = []
        for head in ("HEAD~1", "HEAD"):
            process = self.start(head)
            stdout, stderr = process.communicate(timeout=60)
            self.assertIn(process.returncode, (0, 1), stderr.decode())
            directories.append(Path(json.loads(stdout)["runDir"]))
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")}
        (directories[-1].parent / "otk.json").write_bytes((directories[0] / "otk.json").read_bytes())
        for root, expected in ((self.tmp / "runs", directories[-1]),
                               (directories[-1].parent, directories[-1].parent)):
            result = subprocess.run([sys.executable, "-m", "otk_1c", "report", "--repo", str(self.repo),
                                     "--json", "--run-dir", str(root)], capture_output=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(Path(json.loads(result.stdout)["runDir"]).samefile(expected))

    def test_unfinished_run_does_not_replace_completed(self):
        root = workspace.run_dir(self.cfg, self.repo)
        root.mkdir(parents=True)
        self.assertTrue(workspace.latest_run(root).samefile(root))
        old = workspace.new_run(self.cfg, self.repo)
        (old / "otk.json").write_text("old", encoding="utf-8")
        (old / ".completed").touch()
        unfinished = workspace.new_run(self.cfg, self.repo)
        (unfinished / "otk.json").write_text("interrupted", encoding="utf-8")
        self.assertTrue(workspace.latest_run(root).samefile(old))
        retry = workspace.new_run(self.cfg, self.repo)
        self.assertFalse(any(retry.samefile(previous) for previous in (old, unfinished)))
        self.assertEqual((old / "otk.json").read_text(encoding="utf-8"), "old")
        self.assertEqual((unfinished / "otk.json").read_text(encoding="utf-8"), "interrupted")

    def test_static_and_run_select_plan_directory(self):
        root = workspace.run_dir(self.cfg, self.repo)
        common = ["--repo", str(self.repo)]
        options = ["--set", "checks.analyzers=off", "--set", "checks.platform-api=off"]
        env = mock.patch.dict(os.environ, {"OTK_RUN_DIR": str(self.tmp / "runs")})
        env.start()
        self.addCleanup(env.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["collect", *common, "--base", "HEAD~1", "--head", "HEAD~1"]), 0)
            self.assertEqual(cli.main(["static", *common, *options]), 0)
        self.assertTrue((root / "static.json").is_file())
        (root / "static.json").unlink()
        plan = (root / "otk.json").read_bytes()
        completed = workspace.new_run(self.cfg, self.repo)
        (completed / "otk.json").write_bytes(plan)
        (completed / ".completed").touch()
        unfinished = workspace.new_run(self.cfg, self.repo)
        (unfinished / "otk.json").write_text("unfinished", encoding="utf-8")
        explicit = self.tmp / "outside"
        explicit.mkdir()
        (explicit / "custom.json").write_bytes(plan)
        for directory, args in ((completed, []), (explicit, ["--plan", str(explicit / "custom.json")])):
            with self.subTest(directory=directory), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["static", *common, *options, *args]), 0)
                self.assertTrue((directory / "static.json").is_file())
                with mock.patch.object(cli.runner, "run", return_value={
                    "results": [], "timings": {}, "notes": [],
                }) as runner:
                    self.assertEqual(cli.main(["run", *common, *options, *args]), 0)
                self.assertTrue(runner.call_args.args[3].samefile(directory))
                self.assertTrue(runner.call_args.args[4].parent.parent.samefile(directory))
                self.assertIsNotNone(runner.call_args.kwargs["static"])
        self.assertFalse((root / "static.json").exists())
        self.assertFalse((unfinished / "static.json").exists())

    def test_static_and_run_prefer_fresh_collect_to_old_gate(self):
        common = ["--repo", str(self.repo)]
        options = ["--set", "checks.analyzers=off", "--set", "checks.platform-api=off"]
        completed = workspace.new_run(self.cfg, self.repo)
        cfg = config._merge(config.load(self.repo), self.cfg)
        cfg = config._merge(cfg, {"checks": {"analyzers": "off", "platform-api": "off"}})
        pipeline.gate_run(self.repo, cfg, completed, pipeline.GateOptions(base="HEAD~1", static_only=True),
                          log=lambda _: None)
        os.utime(completed / ".completed", (1_600_000_000, 1_600_000_000))
        before = {p.name: p.read_bytes() for p in completed.iterdir() if p.is_file()}
        root = completed.parent
        with mock.patch.dict(os.environ, {"OTK_RUN_DIR": str(self.tmp / "runs")}), (
            contextlib.redirect_stdout(io.StringIO())
        ):
            self.assertEqual(cli.main(["collect", *common, "--base", "HEAD"]), 0)
            self.assertEqual(cli.main(["static", *common, *options]), 0)
            with mock.patch.object(cli.runner, "run", return_value={"results": [], "timings": {}, "notes": []}) as run:
                self.assertEqual(cli.main(["run", *common, *options]), 0)
        self.assertTrue((root / "static.json").is_file())
        self.assertTrue(run.call_args.args[3].samefile(root))
        self.assertEqual(run.call_args.args[1]["modules"], [])
        self.assertIsNotNone(run.call_args.kwargs["static"])
        self.assertEqual({p.name: p.read_bytes() for p in completed.iterdir() if p.is_file()}, before)
        with mock.patch.dict(os.environ, {"OTK_RUN_DIR": str(self.tmp / "runs")}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIn(cli.main(["report", *common, "--json"]), (0, 1, 2))
        self.assertTrue(Path(json.loads(output.getvalue())["runDir"]).samefile(completed))
        with mock.patch.dict(os.environ, {"OTK_RUN_DIR": str(self.tmp / "runs")}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIn(cli.main(["report", *common, "--json", "--run-dir", str(root)]), (0, 1, 2))
        self.assertTrue(Path(json.loads(output.getvalue())["runDir"]).samefile(root))

    def test_historical_gate_reuses_project_index(self):
        cfg = config._merge(config.load(self.repo), self.cfg)
        cfg = config._merge(cfg, {"checks": {"analyzers": "off", "platform-api": "off"}})
        first, second = (workspace.new_run(cfg, self.repo) for _ in range(2))
        opts = pipeline.GateOptions(head="HEAD~1", static_only=True)
        pipeline.gate_run(self.repo, cfg, first, opts, log=lambda _: None)
        with mock.patch.object(index, "build", side_effect=AssertionError("кеш не сработал")) as build:
            result = pipeline.gate_run(self.repo, cfg, second, opts, log=lambda _: None)
            self.assertIn(result["code"], (0, 1), result)
            build.assert_not_called()
        cache = workspace.run_dir(cfg, self.repo) / "cache" / "metadata"
        self.assertEqual(len(list(cache.glob("index-*.json"))), 1)
        self.assertFalse(any((directory / "cache").exists() for directory in (first, second)))

    def test_head_gate_builds_index_from_commit_during_checkout_edit(self):
        cfg = config._merge(config.load(self.repo), self.cfg)
        cfg = config._merge(cfg, {"checks": {"analyzers": "off", "platform-api": "off"}})
        first, second = (workspace.new_run(cfg, self.repo) for _ in range(2))
        opts = pipeline.GateOptions(static_only=True)
        module = "CommonModules/М/Ext/Module.bsl"
        build = index.build

        def build_during_edit(source_root, *args):
            write(self.repo, module, BASE_MODULE)
            try:
                source = (source_root / module).read_text(encoding="utf-8-sig")
                self.assertEqual(source, HEAD_MODULE)
                return build(source_root, *args)
            finally:
                run(self.repo, "restore", "--", module)

        with mock.patch.object(index, "build", side_effect=build_during_edit) as first_build:
            result = pipeline.gate_run(self.repo, cfg, first, opts, log=lambda _: None)
            self.assertIn(result["code"], (0, 1), result)
            first_build.assert_called_once()
        with mock.patch.object(index, "build", side_effect=AssertionError("кеш не сработал")) as second_build:
            result = pipeline.gate_run(self.repo, cfg, second, opts, log=lambda _: None)
            self.assertIn(result["code"], (0, 1), result)
            second_build.assert_not_called()


class BusyGateTest(unittest.TestCase):
    setUp = test_iblock.IbLockTest.setUp
    tearDown = test_iblock.IbLockTest.tearDown
    holder = test_iblock.IbLockTest.holder

    def test_busy_gate_writes_own_infra_summary(self):
        holder = self.holder()
        try:
            fixture = IsolatedRunsTest()
            fixture.setUp()
            try:
                cfg = config._merge(config.load(fixture.repo), {
                    "environment": {"ib": str(self.tmp / "ib")},
                    "checks": {"analyzers": "off", "platform-api": "off"}})
                directory = workspace.new_run(fixture.cfg, fixture.repo)

                def busy(*_args, **_kwargs):
                    iblock.acquire(self.path, 0)

                with mock.patch.object(pipeline.runner, "run", side_effect=busy):
                    summary = pipeline.gate_run(fixture.repo, cfg, directory,
                                                pipeline.GateOptions("HEAD~1"), log=lambda _: None)
                self.assertEqual(summary["code"], 2)
                self.assertTrue(any(row["reasonCode"] == "ib-busy" for row in summary["infra"]))
                self.assertTrue((directory / "summary.json").is_file())
                self.assertTrue((directory / ".completed").is_file())
            finally:
                fixture.tearDown()
        finally:
            holder.stdin.close()
            holder.wait(timeout=10)
            holder.stdout.close()
