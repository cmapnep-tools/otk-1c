"""Шаг run на заглушках исполнителей совпадает с эталоном до реестра исполнителей (runner_cases.py)."""
import json
import unittest

import runner_cases
from otk_1c import checks
from otk_1c.gate import static
from otk_1c.probes import executors


class RunnerCases(unittest.TestCase):
    def test_golden(self):
        golden = json.loads(runner_cases.GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(golden), sorted(runner_cases.CASES))
        for name in runner_cases.CASES:
            with self.subTest(name):
                got = json.loads(json.dumps(runner_cases.play(name), ensure_ascii=False))
                self.assertEqual(got, golden[name])

    def test_no_external_connection(self):
        """Внешнее соединение вне environment.contexts: COM не трогается, по нему — только «не покрыто»."""
        for name, case in runner_cases.CASES.items():
            if "ExternalConnection" in case.get("contexts", ["ExternalConnection"]):
                continue
            with self.subTest(name):
                out = runner_cases.play(name)
                self.assertFalse([c for c in out["calls"] if c[0].startswith("com.")])
                self.assertFalse([r for r in out["results"]
                                  if r.get("context") == "ExternalConnection" and r["kind"] != "uncovered"])
                texts = [n["reason"] for n in out["notChecked"]] + [x["message"] for x in out["infra"]]
                self.assertFalse([i for i in texts if "COM" in i])


    def test_registries(self):
        names = [ex.name for ex in executors.EXECUTORS]
        self.assertEqual(len(names), len(set(names)))
        covered = {c for ex in executors.EXECUTORS for c in ex.checks}
        self.assertLessEqual(covered, set(checks.BY_NAME))
        in_base = {c.name for c in checks.CHECKS if "ib" in c.needs}
        self.assertEqual(covered, in_base)
        self.assertTrue(all(callable(r) for r in static.RULES + static.PLAN_RULES))
        # обход передаёт элементы исполнителю, который запускается позже; иначе они пропали бы молча
        for i, ex in enumerate(executors.EXECUTORS):
            if getattr(ex, "fallback", None):
                self.assertIn(ex.fallback, names[i + 1:], ex.name)


if __name__ == "__main__":
    unittest.main()
