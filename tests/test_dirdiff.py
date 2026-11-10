"""Проверка без git: план из каталогов выгрузки совпадает с планом из git на тех же файлах."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from _fs import rmtree_force
from otk_1c import config
from otk_1c.errors import InfraError
from otk_1c.gate import collect, pipeline
from otk_1c.probes import build
from otk_1c.probes.platform import ConfigOps
from otk_1c.sources import dirdiff, gitdiff
from test_collect import BASE_MODULE, CONFIG, FLAGS, HEAD_MODULE, run, write

CONTEXTS = ["Server", "ThinClient", "ExternalConnection"]


def fill_base(src):
    write(src, "Configuration.xml", CONFIG)
    write(src, "CommonModules/М.xml", FLAGS)
    write(src, "CommonModules/М/Ext/Module.bsl", BASE_MODULE)
    write(src, "CommonModules/Удалённый.xml", FLAGS)
    write(src, "CommonModules/Удалённый/Ext/Module.bsl", "Процедура Э() Экспорт\nКонецПроцедуры\n")
    write(src, "Documents/Д/Forms/Ф/Ext/Form/Module.bsl", "&НаКлиенте\nПроцедура К()\nКонецПроцедуры\n")
    write(src, "Documents/Д/Forms/Ф/Ext/Form.xml", "<Form/>")


def fill_head(src):
    write(src, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
    (src / "CommonModules/Удалённый/Ext/Module.bsl").unlink()
    (src / "CommonModules/Удалённый.xml").unlink()
    write(src, "Documents/Д/Ext/ObjectModule.bsl", "Перем Х;\nПроцедура ПередЗаписью(Отказ)\nКонецПроцедуры\n")
    write(src, "Documents/Д/Forms/Ф/Ext/Form/Module.bsl",
          "&НаКлиенте\nПроцедура К()\n\t// комментарий\nКонецПроцедуры\n")


def body(plan):
    """Содержательная часть плана (без заголовка источника)."""
    return {k: plan[k] for k in ("modules", "removedModules", "metadata", "queries", "forms", "scriptVariant")}


class FakePlatform(ConfigOps, SimpleNamespace):
    """Платформа понарошку: designer и ibcmd — лямбды теста."""


class TestDirDiff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        cls.base, cls.head, cls.repo = cls.tmp / "было", cls.tmp / "стало", cls.tmp / "git"
        fill_base(cls.base)
        shutil.copytree(cls.base, cls.head)
        fill_head(cls.head)
        (cls.head / ".git").mkdir()                       # скрытые каталоги — не выгрузка
        (cls.head / ".git" / "мусор.bsl").write_text("x", encoding="utf-8")
        r = cls.repo
        run(r.parent, "init", "-q", "-b", "master", r.name)
        run(r, "config", "user.email", "t@t")
        run(r, "config", "user.name", "t")
        fill_base(r)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "base")
        fill_head(r)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "head")

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def test_same_plan_as_git(self):
        by_git = collect.collect(self.repo, "HEAD~1", "HEAD", ".", CONTEXTS)
        by_dirs = collect.collect_dirs(self.base, self.head, CONTEXTS)
        self.assertEqual(body(by_dirs), body(by_git))
        self.assertEqual((by_dirs["baseSha"], by_dirs["headDir"]), (None, str(self.head.resolve())))

    def test_whole_dir_like_all(self):
        by_git = collect.collect(self.repo, gitdiff.empty_commit(self.repo), "HEAD", ".", CONTEXTS)
        by_dir = collect.collect_dirs(None, self.head, CONTEXTS)
        self.assertEqual(body(by_dir), body(by_git))
        self.assertTrue(all(m["status"] == "A" for m in by_dir["modules"]))

    def test_schema_required_keys(self):
        schema = json.loads((Path(__file__).parent.parent / "src/otk_1c/schema/plan.schema.json")
                            .read_text(encoding="utf-8"))
        plan = collect.collect_dirs(None, self.head, CONTEXTS)
        for key in schema["required"]:
            self.assertIn(key, plan)

    def test_required_contexts_in_plan(self):
        """environment.requiredContexts попадает в план — report по файлам даёт тот же код, что и gate."""
        self.assertEqual(collect.collect_dirs(None, self.head, CONTEXTS)["requiredContexts"], [])
        plan = collect.collect_dirs(None, self.head, CONTEXTS, required_contexts=["Server"])
        self.assertEqual(plan["requiredContexts"], ["Server"])

    def test_ordinary_client_requires_ordinary_configuration(self):
        """Требовать OrdinaryClient у управляемой конфигурации нечем: такого контекста не будет ни у одного
        модуля, и требование молча считалось бы выполненным. Это ошибка конфигурации, а не код 0."""
        with self.assertRaises(InfraError) as e:
            collect.collect_dirs(None, self.head, CONTEXTS, required_contexts=["OrdinaryClient"])
        self.assertEqual(e.exception.code, "config-invalid")
        self.assertIn("OrdinaryClient", str(e.exception))

    def test_ordinary_client_of_extension_by_parent(self):
        """У расширения своего режима запуска может не быть — он берётся у основной конфигурации, как и флаги
        модулей: обычное приложение родителя делает OrdinaryClient проверяемым."""
        parent = self.tmp / "родитель"
        write(parent, "Configuration.xml",
              '<Configuration><Properties><ScriptVariant>Russian</ScriptVariant>'
              '<DefaultRunMode>OrdinaryApplication</DefaultRunMode></Properties></Configuration>')
        ext = self.tmp / "расширение"
        write(ext, "Configuration.xml",
              '<Configuration><Properties><ScriptVariant>Russian</ScriptVariant>'
              '<ConfigurationExtensionPurpose>AddOn</ConfigurationExtensionPurpose></Properties></Configuration>')
        write(ext, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        plan = collect.collect_dirs(None, ext, CONTEXTS, parent=parent, required_contexts=["OrdinaryClient"])
        self.assertEqual(plan["requiredContexts"], ["OrdinaryClient"])
        # у обычной конфигурации режим родителя не наследуется: --parent не делает её обычным приложением
        with self.assertRaises(InfraError):
            collect.collect_dirs(None, self.head, CONTEXTS, parent=parent, required_contexts=["OrdinaryClient"])

    def test_lines_like_git_diff(self):
        ch = gitdiff.FileChange("M", "м")
        dirdiff.fill_lines(ch, "а\nб\nв\nг\n", "а\nБ\nв\nновая\nг\n")
        self.assertEqual(ch.added_lines, {2, 4})
        ch = gitdiff.FileChange("M", "м")
        dirdiff.fill_lines(ch, "а\nб\nв\n", "а\nв\n")
        self.assertEqual((ch.added_lines, ch.deleted_at), (set(), {1, 2}))

    def test_not_a_dump(self):
        with self.assertRaises(InfraError):
            collect.collect_dirs(None, self.tmp, CONTEXTS)

    def test_gate_without_git(self):
        """gate --dir --base-dir: проект — каталог без git; без копии ИБ пробы — «не проверено», как с git."""
        project = self.tmp / "проект"
        project.mkdir(exist_ok=True)
        cfg = config._merge(config.DEFAULTS, {"checks": {"bslls": "off", "platform-api": "off"}})
        summary = pipeline.gate_run(project, cfg, self.tmp / "прогон",
                                    pipeline.GateOptions(head_dir=self.head, base_dir=self.base), log=lambda m: None)
        self.assertIn(summary["code"], (0, 1))
        self.assertTrue((self.tmp / "прогон" / "summary.json").exists())
        self.assertIn("probes-server", {i.get("check") for i in summary["notChecked"]})

    def test_load_list_contract(self):
        """Без git загружаются отличия от «было» (контракт: копия ИБ содержит base_dir); удаления — полная."""
        plan = collect.collect_dirs(self.base, self.head, CONTEXTS)
        self.assertIsNone(build.plan_load_dirs(plan))                  # удалён общий модуль
        head2 = self.tmp / "стало2"
        shutil.copytree(self.base, head2)
        write(head2, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        write(head2, "Documents/Д/Forms/Ф/Ext/Form/Module.bsl", "&НаКлиенте\nПроцедура К()\n\tА = 1;\nКонецПроцедуры\n")
        plan = collect.collect_dirs(self.base, head2, CONTEXTS)
        self.assertEqual(build.plan_load_dirs(plan), ["CommonModules/М/Ext/Module.bsl",
                                                      "Documents/Д/Forms/Ф/Ext/Form.xml",
                                                      "Documents/Д/Forms/Ф/Ext/Form/Module.bsl"])
        self.assertIsNone(build.plan_load_dirs(collect.collect_dirs(None, head2, CONTEXTS)))   # без «было»

    def test_build_dirs(self):
        """Частичная загрузка из каталога «стало»; в состоянии нет коммита — следующая сборка по git полная."""
        head2 = self.tmp / "стало3"
        shutil.copytree(self.base, head2)
        write(head2, "CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        plan = collect.collect_dirs(self.base, head2, CONTEXTS)
        ib, run_dir = self.tmp / "ib3", self.tmp / "прогон3"
        ib.mkdir()
        (ib / "1Cv8.1CD").write_bytes(b"x")                     # копия есть: пустой каталог ОТК создала бы
        run_dir.mkdir()
        calls = []
        platform = FakePlatform(ib=ib, connection=None, designer=lambda *a, **k: calls.append(a) or (0, 1.0, ""),
                                ibcmd=lambda *a, **k: calls.append(("ibcmd",) + a) or (0, 1.0, ""))
        res = build.build_dirs(plan, platform, run_dir)
        self.assertEqual((res["action"], res["files"]), ("partial", 1))
        self.assertEqual(calls[0][:2], ("/LoadConfigFromFiles", head2.resolve()))
        state = build.load_state(run_dir, ib)
        self.assertEqual((state["commit"], state["dir"]), (None, str(head2.resolve())))
        with mock.patch.object(build, "tree_hash", return_value="t"), \
                mock.patch.object(build, "plan_load", side_effect=AssertionError("частичная после сборки без git")):
            res = build.build(self.repo, "abc", ".", self.repo, platform, run_dir)
        self.assertEqual(res["action"], "full")


if __name__ == "__main__":
    unittest.main()
