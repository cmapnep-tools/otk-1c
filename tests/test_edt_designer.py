"""Исходники EDT в проверке Конфигуратором: пути находок, отрицательный контроль Б, проект-расширение, pt."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.integrations import platform_tools
from otk_1c.probes import designer_check
from otk_1c.probes.executors import extended_check
from otk_1c.probes.platform import ConfigOps
from otk_1c.sources import layout
from test_designer_check import OUTPUT, Platform2

BOM = b"\xef\xbb\xbf"


def mdo(tag, body):
    return (f'<?xml version="1.0"?><mdclass:{tag} xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">'
            f"{body}</mdclass:{tag}>")


def edt_tree(root):
    """Исходники EDT: клиентский, глобальный и серверный общие модули (модули — без BOM, LF)."""
    files = {"Configuration/Configuration.mdo": mdo("Configuration", "<name>К</name>"),
             "CommonModules/АКлиент/АКлиент.mdo": mdo("CommonModule", "<name>АКлиент</name>"
                                                     "<clientManagedApplication>true</clientManagedApplication>"),
             "CommonModules/БГлоб/БГлоб.mdo": mdo("CommonModule", "<name>БГлоб</name><global>true</global>"
                                                 "<server>true</server>"),
             "CommonModules/ВСервер/ВСервер.mdo": mdo("CommonModule", "<name>ВСервер</name><server>true</server>")}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for name in ("АКлиент", "БГлоб", "ВСервер"):
        (root / "CommonModules" / name / "Module.bsl").write_bytes("Процедура А()\nКонецПроцедуры\n".encode())


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src"
        edt_tree(self.src)

    def tearDown(self):
        rmtree_force(self.tmp)


class ParseTest(unittest.TestCase):
    def test_edt_paths(self):
        files = [f["file"] for f in designer_check.parse(OUTPUT, "src", layout.EDT)]
        self.assertEqual(files[:6], ["src/CommonModules/КассовыеСмены/Module.bsl",
                                     "src/Documents/Событие/Forms/ФормаДокумента/Module.bsl",
                                     "src/Catalogs/Клиенты/ManagerModule.bsl",
                                     "src/CommonForms/Настройки/Module.bsl",
                                     "src/Documents/Событие/Commands/Печать/CommandModule.bsl", None])


class ControlTest(Tmp):
    def test_control_module(self):
        self.assertEqual(designer_check.control_module(self.src), "ВСервер")

    def test_inline_on_edt(self):
        """Контроль Б на исходниках EDT: модуль грузится в пути выгрузки и восстанавливается из копии с BOM."""
        out = ("{ОбщийМодуль.ВСервер.Модуль(5,5)}: Ожидается выражение\r\n"
               "{Документ.Событие.Форма.ФормаДокумента.Форма(12,3)}: Ожидается выражение\r\n")
        p = Platform2({"designer-check": (101, 5.0, out)})
        p.ib = self.tmp / "ib"
        run = self.tmp / "run"
        with mock.patch.object(designer_check, "minimal_control", side_effect=designer_check.InfraError("нет")):
            res = designer_check.check(p, self.src, "src", ["-Server"], self.tmp / "b.json", run, log=lambda m: None)
        self.assertEqual(p.calls, ["control-load", "designer-check", "control-restore"])
        self.assertEqual((res["code"], res["control"]["ok"]), (1, True))
        self.assertEqual([f["file"] for f in res["findings"]],
                         ["src/Documents/Событие/Forms/ФормаДокумента/Module.bsl"])
        rel = "CommonModules/ВСервер/Ext/Module.bsl"
        original = BOM + (self.src / "CommonModules/ВСервер/Module.bsl").read_bytes()
        self.assertEqual((run / "control-restore" / rel).read_bytes(), original)
        self.assertTrue((run / "control" / rel).read_bytes().startswith(original))
        self.assertEqual((run / "control.list").read_text(encoding="utf-8-sig").strip(), rel)


class ExtensionTest(unittest.TestCase):
    def test_run_extension_edt(self):
        out = ("{demo ОбщийМодуль.demo_Сервер.Модуль(10,5)}: Ожидается выражение\r\n"
               "demo Справочник.demo_Т.Форма.Ф.Форма Возможно ошибочное свойство: \"Х\"\r\n")

        class P(ConfigOps):
            def designer(self, *args, label="", timeout=0):
                return 101, 1.0, out
        entries = [{"path": "CommonModules/demo_Сервер/Module.bsl", "owner": "ОбщийМодуль.demo_Сервер"}]
        texts = {"Catalogs/demo_Т/Forms/Ф/Module.bsl": "А = 1;\nБ = Х;\n"}
        res, _ = extended_check.run_extension(P(), "demo", entries, texts.get, layout.EDT)
        self.assertIs(res[0]["module"], entries[0]["path"])     # находка привязана к модулю плана
        self.assertEqual((res[1]["module"], res[1]["line"]), ("Catalogs/demo_Т/Forms/Ф/Module.bsl", 2))


class PtTest(Tmp):
    def test_cf_post_edt_not_checked(self):
        cfg = {"project": {"source": "src"}, "environment": {"runDir": str(self.tmp / "runs")}}
        conn = {"ibconnection": "/FD:/Демо"}
        d = platform_tools.state_dir(cfg, self.tmp, conn) / "static"
        d.mkdir(parents=True)
        plan = {"source": "src", "sourceFormat": "edt", "baseSha": "x",
                "modules": [{"path": "CommonModules/ВСервер/Module.bsl", "kind": "CommonModule", "reason": "changed",
                             "status": "M", "owner": "ОбщийМодуль.ВСервер"}]}
        (d / "otk.json").write_text(json.dumps(plan), encoding="utf-8")
        logs = []
        with mock.patch.object(platform_tools, "platform_for", side_effect=AssertionError("Конфигуратор не нужен")):
            self.assertEqual(platform_tools.cf_post(self.tmp, cfg, conn, logs.append, open_summary=False), 0)
        self.assertTrue(any("исходники EDT" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
