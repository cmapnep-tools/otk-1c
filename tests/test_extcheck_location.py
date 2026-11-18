"""Регрессии: кратность сообщения не доказывает позицию в исходнике."""
import unittest

from otk_1c.probes.executors import extended_check


class ExtCheckLocationTests(unittest.TestCase):
    REL = "CommonModules/Test/Ext/Module.bsl"
    MSG = 'Возможно ошибочное свойство: "Standard"'

    def compare(self, source, line=None, mode="Сервер"):
        entry = {"path": self.REL, "owner": "CommonModule.Test", "kind": "CommonModule"}
        compiled = {self.REL: frozenset({"Server"})}
        old = (self.REL, None, self.MSG, mode)
        before = {"findings": [old], "compiled": compiled, "masked": {}}
        after = {"findings": [old, (self.REL, line, self.MSG, mode)],
                 "compiled": compiled, "masked": {}}
        results, _ = extended_check.compare(
            {self.REL: (None, entry)}, {self.REL: (None, entry, source)},
            {self.REL: (None, entry, source)}, after, before)
        return [r for r in results if r["outcome"] == "compile-error"]

    def test_increased_count_does_not_locate_old_reference_or_comment(self):
        for source in ("Old.Standard();\nNew.Standard();\n",
                       "// Standard\nOld.Standard();\nNew.Standard();\n"):
            with self.subTest(source=source):
                findings = self.compare(source)
                self.assertEqual(len(findings), 1)
                self.assertIsNone(findings[0].get("line"))
                self.assertIn(extended_check.COUNT_COMPARED, findings[0]["message"])
                self.assertNotIn(extended_check.UNCOMPARED, findings[0]["message"])

    def test_platform_position_is_preserved(self):
        finding, = self.compare("// Standard\nOld.Standard();\nNew.Standard();\n", line=3)
        self.assertEqual(finding["line"], 3)
        self.assertNotIn(extended_check.COUNT_COMPARED, finding["message"])

    def test_unknown_mode_does_not_claim_proven_count(self):
        finding, = self.compare("// Standard\nOld.Standard();\nNew.Standard();\n", mode="Мобильный клиент")
        self.assertIsNone(finding.get("line"))
        self.assertIn(extended_check.UNCOMPARED, finding["message"])
        self.assertNotIn(extended_check.COUNT_COMPARED, finding["message"])
