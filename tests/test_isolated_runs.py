"""Изоляция процессных gate и выбор завершённого отчёта."""
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
from otk_1c import config
from otk_1c.gate import pipeline
from otk_1c.probes import iblock
from otk_1c.sources import workspace
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
        self.assertNotEqual(*dirs)
        plans = [json.loads((d / "otk.json").read_text(encoding="utf-8")) for d in dirs]
        self.assertEqual(len(plans[0]["modules"]), 0)
        self.assertEqual(len(plans[1]["modules"]), 1)
        self.assertNotEqual(plans[0]["headSha"], plans[1]["headSha"])
        for result, directory in zip(results, dirs, strict=True):
            self.assertEqual(Path(result["summaryPath"]), directory / "summary.json")
            self.assertTrue((directory / ".completed").exists())
            summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["code"], result["code"])
            self.assertEqual(summary["findings"], result["findings"])

        other_before = {p.name: p.read_bytes() for p in dirs[1].iterdir() if p.is_file()}
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
               "OTK_RUN_DIR": str(self.tmp / "runs")}
        args = [sys.executable, "-m", "otk_1c", "report", "--repo", str(self.repo), "--json"]
        explicit = subprocess.run([*args, "--run-dir", str(dirs[0])], capture_output=True, env=env, timeout=30)
        self.assertEqual(json.loads(explicit.stdout)["runDir"], str(dirs[0]))
        self.assertEqual(other_before, {p.name: p.read_bytes() for p in dirs[1].iterdir() if p.is_file()})
        unfinished = workspace.new_run(self.cfg, self.repo)
        (unfinished / "otk.json").write_bytes((dirs[0] / "otk.json").read_bytes())
        default = subprocess.run(args, capture_output=True, env=env, timeout=30)
        self.assertEqual(json.loads(default.stdout)["runDir"], str(workspace.latest_run(dirs[0].parent)))

    def test_report_explicit_root_selects_latest_sequential_gate(self):
        directories = []
        for head in ("HEAD~1", "HEAD"):
            process = self.start(head)
            stdout, stderr = process.communicate(timeout=60)
            self.assertIn(process.returncode, (0, 1), stderr.decode())
            directories.append(Path(json.loads(stdout)["runDir"]))
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")}
        (directories[-1].parent / "otk.json").write_bytes((directories[0] / "otk.json").read_bytes())
        for root in (self.tmp / "runs", directories[-1].parent):
            result = subprocess.run([sys.executable, "-m", "otk_1c", "report", "--repo", str(self.repo),
                                     "--json", "--run-dir", str(root)], capture_output=True, env=env, timeout=30)
            self.assertEqual(json.loads(result.stdout)["runDir"], str(directories[-1]))

    def test_unfinished_run_does_not_replace_completed(self):
        root = workspace.run_dir(self.cfg, self.repo)
        self.assertEqual(workspace.latest_run(root), root)
        old = workspace.new_run(self.cfg, self.repo)
        (old / "otk.json").write_text("old", encoding="utf-8")
        (old / ".completed").touch()
        unfinished = workspace.new_run(self.cfg, self.repo)
        (unfinished / "otk.json").write_text("interrupted", encoding="utf-8")
        self.assertEqual(workspace.latest_run(root), old)
        retry = workspace.new_run(self.cfg, self.repo)
        self.assertNotIn(retry, (old, unfinished))
        self.assertEqual((old / "otk.json").read_text(encoding="utf-8"), "old")
        self.assertEqual((unfinished / "otk.json").read_text(encoding="utf-8"), "interrupted")


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
