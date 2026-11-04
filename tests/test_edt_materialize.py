"""git-режим для проекта EDT: редакция извлекается вместе с .project и DT-INF —
без них 1cedtcli не экспортирует."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.sources import workspace


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


class TestMaterializeEdt(unittest.TestCase):
    def test_project_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-mat-"))
        try:
            repo = tmp / "repo"
            for rel in ("proj/.project", "proj/DT-INF/PROJECT.PMF", "proj/src/Configuration/Configuration.mdo",
                        "proj/docs/readme.txt"):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text("x", encoding="utf-8")
            _git(repo.parent, "init", "-q", str(repo))
            _git(repo, "add", "-A")
            _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
            sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            target = workspace.materialize(repo, sha, "proj/src", tmp / "trees")
            self.assertTrue((target / "proj/.project").is_file())
            self.assertTrue((target / "proj/DT-INF/PROJECT.PMF").is_file())
            self.assertTrue((target / "proj/src/Configuration/Configuration.mdo").is_file())
            self.assertFalse((target / "proj/docs").exists())
        finally:
            rmtree_force(tmp)


if __name__ == "__main__":
    unittest.main()
