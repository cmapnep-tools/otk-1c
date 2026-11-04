"""Единый набор провальных исходов: код выхода run, пароль с обратным слэшем, .gitignore."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import cli, config, model
from otk_1c.gate import report

ROOT = Path(__file__).resolve().parent.parent


def probe(outcome):
    return {"executor": "scen", "kind": "scenario", "module": "m.bsl", "owner": None, "context": "server",
            "outcome": outcome, "message": "", "line": 1}


class TestOutcomes(unittest.TestCase):
    def test_one_set(self):
        self.assertIn("failed", model.FAIL)
        self.assertIs(report.PROBE_FAIL, model.FAIL)

    def run_cli(self, outcome):
        tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        try:
            plan = tmp / "plan.json"
            plan.write_text(json.dumps({"source": "src", "schema": model.PLAN_SCHEMA}), encoding="utf-8")
            out = {"results": [probe("ok"), probe(outcome)] if outcome != "ok" else [probe("ok")],
                   "notes": [], "timings": {}}
            with mock.patch("otk_1c.gate.runner.run", return_value=out), \
                    contextlib.redirect_stdout(io.StringIO()):
                return cli.main(["run", "--repo", str(tmp), "--plan", str(plan)])
        finally:
            rmtree_force(tmp)

    def test_run_exit_code(self):
        self.assertEqual(self.run_cli("failed"), 1)
        self.assertEqual(self.run_cli("compile-error"), 1)
        self.assertEqual(self.run_cli("ok"), 0)


class TestCiEnv(unittest.TestCase):
    def test_backslash_as_is(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        try:
            env = {"OTK_PASSWORD": 'a\\b"c', "OTK_IB": "C:\\x\\y", "OTK_USER": "п\tр\nс\x01"}
            got = config.load(tmp, env)["environment"]
            self.assertEqual(got["password"], 'a\\b"c')
            self.assertEqual(got["ib"], "C:\\x\\y")
            self.assertEqual(got["user"], "п\tр\nс\x01")
        finally:
            rmtree_force(tmp)


class TestGitignore(unittest.TestCase):
    def test_local_toml_ignored(self):
        r = subprocess.run(["git", "check-ignore", "otk.local.toml"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
