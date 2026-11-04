"""Сбор изменений на синтетическом git-репозитории и (если задан) на истории настоящего.

Настоящий репозиторий — OTK_TEST_REPO (клон с историей выгрузки), каталог выгрузки в нём — OTK_TEST_REPO_SOURCE
(по умолчанию src); не задан — пропуск.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.gate import collect
from otk_1c.probes import build
from otk_1c.sources import gitdiff

CONFIG = '<Configuration><Properties><ScriptVariant>Russian</ScriptVariant></Properties></Configuration>'
FLAGS = ('<CommonModule><Properties><Global>false</Global><ClientManagedApplication>false</ClientManagedApplication>'
         '<Server>true</Server><ExternalConnection>true</ExternalConnection><ClientOrdinaryApplication>false'
         '</ClientOrdinaryApplication><ServerCall>false</ServerCall><Privileged>false</Privileged></Properties></CommonModule>')

BASE_MODULE = """Процедура Неизменная() Экспорт
	А = 1;
КонецПроцедуры

Функция Изменяемая(П) Экспорт
	Возврат П;
КонецФункции

Процедура Удаляемая()
КонецПроцедуры
"""

HEAD_MODULE = """Процедура Неизменная() Экспорт
	А = 1;
КонецПроцедуры

Функция Изменяемая(П, Новый = 0) Экспорт
	Т = "ВЫБРАТЬ Н.Ссылка ИЗ Справочник.Н КАК Н";
	Возврат П;
КонецФункции

#Если НЕ ВнешнееСоединение Тогда
Процедура Добавленная()
КонецПроцедуры
#КонецЕсли
"""


def run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))



class TestSourceAtRoot(unittest.TestCase):
    def test_root_source(self):
        """Выгрузка в корне репозитория (source = "."): пути модулей — без обрезки."""
        self.assertEqual(gitdiff.source_prefix("."), "")
        self.assertEqual(gitdiff.source_prefix("src/cf/"), "src/cf/")
        r = Path(tempfile.mkdtemp(prefix="otk-test-"))
        try:
            run(r, "init", "-q", "-b", "master")
            run(r, "config", "user.email", "t@t")
            run(r, "config", "user.name", "t")
            write(r, "Configuration.xml", CONFIG)
            write(r, "CommonModules/М.xml", FLAGS)
            write(r, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
            run(r, "add", "-A")
            run(r, "commit", "-q", "-m", "base")
            write(r, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
            run(r, "commit", "-q", "-am", "head")
            plan = collect.collect(r, "HEAD~1", "HEAD", ".", ["Server"])
            self.assertEqual([m["path"] for m in plan["modules"]], ["CommonModules/М/Ext/Module.bsl"])
            self.assertTrue(build.tree_hash(r, "HEAD", "."))
        finally:
            rmtree_force(r)

class TestCollectSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        r = cls.tmp
        run(r, "init", "-q", "-b", "master")
        run(r, "config", "user.email", "t@t")
        run(r, "config", "user.name", "t")
        src = r / "src" / "cf"
        write(src, "Configuration.xml", CONFIG)
        write(src, "CommonModules/М.xml", FLAGS)
        write(src, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        write(src, "CommonModules/Удалённый.xml", FLAGS)
        write(src, "CommonModules/Удалённый/Ext/Module.bsl", "Процедура Э() Экспорт\nКонецПроцедуры\n")
        write(src, "Documents/Д/Forms/Ф/Ext/Form/Module.bsl", "&НаКлиенте\nПроцедура К()\nКонецПроцедуры\n")
        write(src, "Documents/Д/Forms/Ф/Ext/Form.xml", "<Form/>")
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "base")
        write(src, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        (src / "CommonModules/Удалённый/Ext/Module.bsl").unlink()
        (src / "CommonModules/Удалённый.xml").unlink()
        write(src, "Documents/Д/Ext/ObjectModule.bsl", "Перем Х;\nПроцедура ПередЗаписью(Отказ)\nКонецПроцедуры\n")
        write(src, "Documents/Д/Forms/Ф/Ext/Form/Module.bsl",
              "&НаКлиенте\nПроцедура К()\n\t// комментарий\nКонецПроцедуры\n")
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "head")
        cls.plan = collect.collect(r, "HEAD~1", "HEAD", "src/cf", ["Server", "ThinClient", "ExternalConnection"])
        cls.mods = {m["path"]: m for m in cls.plan["modules"]}

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def test_changed_methods_and_signature(self):
        m = self.mods["CommonModules/М/Ext/Module.bsl"]
        names = {x["name"]: x for x in m["changedMethods"]}
        self.assertEqual(set(names), {"Изменяемая", "Добавленная"})
        self.assertTrue(names["Изменяемая"]["signatureChanged"])
        self.assertTrue(names["Добавленная"]["new"])
        self.assertEqual([x["name"] for x in m["removedMethods"]], ["Удаляемая"])
        self.assertEqual(m["contexts"], ["Server", "ExternalConnection"])

    def test_preprocessor(self):
        m = self.mods["CommonModules/М/Ext/Module.bsl"]
        self.assertTrue(m["needsPlainServerProbe"])
        self.assertIn("ExternalConnection", m["preprocessorContexts"])

    def test_added_object_module_with_body(self):
        m = self.mods["Documents/Д/Ext/ObjectModule.bsl"]
        self.assertEqual(m["status"], "A")
        self.assertIn("<тело модуля>", [x["name"] for x in m["changedMethods"]])
        self.assertEqual(m["probe"]["method"], "СоздатьДокумент")

    def test_form_loads_with_description(self):
        m = self.mods["Documents/Д/Forms/Ф/Ext/Form/Module.bsl"]
        self.assertEqual(m["loadWith"], ["Documents/Д/Forms/Ф/Ext/Form.xml"])

    def test_removed_module(self):
        self.assertEqual(self.plan["removedModules"][0]["exports"], ["Э"])
        self.assertIn({"path": "CommonModules/Удалённый.xml", "status": "D"}, self.plan["metadata"])

    def test_queries(self):
        self.assertEqual(len(self.plan["queries"]), 1)
        q = self.plan["queries"][0]
        self.assertEqual((q["id"], q["complete"], q["line"]), ("q1", True, 6))

    def test_deterministic(self):
        again = collect.collect(self.tmp, "HEAD~1", "HEAD", "src/cf", ["Server", "ThinClient", "ExternalConnection"])
        self.assertEqual(collect.dumps(self.plan), collect.dumps(again))

    def test_no_changes(self):
        plan = collect.collect(self.tmp, "HEAD", "HEAD", "src/cf", ["Server"])
        self.assertEqual((plan["modules"], plan["metadata"]), ([], []))

    def test_schema_required_keys(self):
        schema = json.loads((Path(__file__).parent.parent / "src/otk_1c/schema/plan.schema.json")
                            .read_text(encoding="utf-8"))
        for key in schema["required"]:
            self.assertIn(key, self.plan)
        for m in self.plan["modules"]:
            for key in schema["$defs"]["module"]["required"]:
                self.assertIn(key, m)


REPO = Path(os.environ.get("OTK_TEST_REPO") or "нет-OTK_TEST_REPO")
SOURCE = os.environ.get("OTK_TEST_REPO_SOURCE") or "src"


@unittest.skipUnless((REPO / ".git").exists(), "OTK_TEST_REPO не задана или это не git-репозиторий")
class TestCollectHistory(unittest.TestCase):
    def test_history(self):
        shas = gitdiff.git(REPO, "log", "--format=%H", "--", SOURCE).split()
        for sha in shas[:-1]:
            plan = collect.collect(REPO, f"{sha}~1", sha, SOURCE, ["Server", "ThinClient"])
            self.assertEqual(collect.dumps(plan), collect.dumps(
                collect.collect(REPO, f"{sha}~1", sha, SOURCE, ["Server", "ThinClient"])))
            for m in plan["modules"]:
                self.assertNotEqual(m["kind"], "Unknown", m["path"])


if __name__ == "__main__":
    unittest.main()
