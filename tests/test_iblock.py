"""Блокировка копии ИБ между процессами: второй прогон на той же базе ждёт, по тайм-ауту — ib-busy (код 2);
завершился держатель (и аварийно) — блокировка свободна."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.errors import InfraError
from otk_1c.probes import iblock

SRC = str(Path(__file__).resolve().parent.parent / "src")
HOLDER = """
import sys
from pathlib import Path
from otk_1c.probes import iblock
iblock.acquire(Path(sys.argv[1]), 5)
print("ready", flush=True)
sys.stdin.read()                         # держать, пока тест не закроет stdin
"""


class IbLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = iblock.lock_file(self.tmp / "ib")

    def tearDown(self):
        iblock.release_all()
        rmtree_force(self.tmp)

    def holder(self):
        env = {**os.environ, "PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", "")}
        p = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, env=env)
        self.assertEqual(p.stdout.readline().strip(), b"ready")
        return p

    def test_file_next_to_ib(self):
        self.assertEqual(self.path, (self.tmp / "ib.otk-lock").resolve())
        server = iblock.lock_file(None, "/Ssrv/Base")
        self.assertEqual(server, iblock.lock_file(None, "/SSRV\\base"))   # регистр и разделитель — та же база
        self.assertNotEqual(server, iblock.lock_file(None, "/Ssrv/Other"))

    def test_reentrant(self):
        iblock.acquire(self.path, 1)
        iblock.acquire(self.path, 1)     # тот же процесс — не ждёт сам себя

    def test_second_run_waits_then_busy(self):
        p = self.holder()
        try:
            waits = []
            with self.assertRaises(InfraError) as e:
                iblock.acquire(self.path, 1.5, log=waits.append)
            self.assertEqual(e.exception.code, "ib-busy")
            self.assertIn(f"PID {p.pid}", str(e.exception))
            self.assertEqual(len(waits), 1)
        finally:
            p.stdin.close()
            p.wait(timeout=30)
            p.stdout.close()
        iblock.acquire(self.path, 10)    # держатель завершился — свободно

    def test_killed_holder_releases(self):
        p = self.holder()
        p.kill()
        p.wait(timeout=30)
        p.stdin.close()
        p.stdout.close()
        iblock.acquire(self.path, 10)


if __name__ == "__main__":
    unittest.main()
