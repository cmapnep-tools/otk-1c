"""gate --vendor: состояние поддержки, разбор полного отчёта /CompareCfg, восстановление текстов
поставщика и зеркало «было». Отчёт и выгрузка — синтетические, устроены как у Конфигуратора 8.3.27."""
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.analyzers import bsl_analyzer
from otk_1c.gate import vendor
from otk_1c.sources import dirdiff, module_kinds

VENDOR_M = "Процедура А()\r\n\tХ = 1;\r\nКонецПроцедуры\r\nПроцедура Б()\r\n\tУдалить();\r\nКонецПроцедуры\r\n"
MAIN_M = ("Процедура А()\r\n\tХ = 2;\r\n\tХ = 3;\r\n\tХ = 4;\r\nКонецПроцедуры\r\nПроцедура Б()\r\nКонецПроцедуры\r\n"
          "// новая\r\nПроцедура В()\r\nКонецПроцедуры\r\n")
VENDOR_SESSION = "Процедура УстановкаПараметровСеанса(Т)\r\nКонецПроцедуры\r\n"
MAIN_SESSION = "Процедура УстановкаПараметровСеанса(Т)\r\n\tДоработка();\r\nКонецПроцедуры\r\n"
DOT_FORM = "&НаКлиенте\r\nПроцедура К()\r\n\tС = \"·\";\r\nКонецПроцедуры\r\n"

# метки «<»/«>» в кусках «Изменено» ненадёжны: в первом куске одна строка основной помечена «<»
REPORT = """
\t***- Объект изменен
\t-->- Объект присутствует только в конфигурации поставщика
\t<--- Объект присутствует только в основной конфигурации
\t^- Порядок объекта изменен

\t- ***Конфигурация.Тест
\t\t- Модуль сеанса - Различаются значения
\t\t\tОбъект присутствует только в основной конфигурации: 2 - 2
\t\t\t"·Доработка();"
\t\t- <--ОбщийМодуль.Новый
\t\t- <--Роль.НоваяРоль
\t\t- -->Отчет.Удалённый
\t\t- ***Подсистема.П
\t\t\t- ***Состав
\t\t\t\t- <--ОбщийМодуль.Новый
\t\t- ***ОбщийМодуль.М
\t\t\t- Модуль - Различаются значения
\t\t\t\tИзменено: 2 - 4
\t\t\t\t< "·Х = 1;"
\t\t\t\t< "·Х = 2;"
\t\t\t\t> "·Х = 3;"
\t\t\t\t> "·Х = 4;"
\t\t\t\tОбъект присутствует только в конфигурации поставщика: 7 - 7
\t\t\t\t"·Удалить();"
\t\t\t\tОбъект присутствует только в основной конфигурации: 9 - 11
\t\t\t\t"// новая"
\t\t\t\t"Процедура В()"
\t\t\t\t"КонецПроцедуры"
\t\t- ***Справочник.С
\t\t\t- Длина кода - Различаются значения
\t\t\t\t"9"
\t\t\t\t"11"
\t\t\t- <--Справочник.С.Форма.НоваяФорма
\t\t\t- ***Справочник.С.Форма.Ф
\t\t\t\t- Модуль - Различаются значения
\t\t\t\t\tОбъект присутствует только в основной конфигурации: 3 - 3
\t\t\t\t\t"·С = "·";"
"""


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(("﻿" + text).encode("utf-8") if rel.endswith(".bsl") else text.encode("utf-8"))


def fill_head(root):
    write(root, "Configuration.xml", "<MetaDataObject><Configuration><Properties><Name>Тест</Name>"
                                     "<Version>1.0.0.1</Version></Properties></Configuration></MetaDataObject>")
    write(root, "Ext/SessionModule.bsl", MAIN_SESSION)
    write(root, "CommonModules/М.xml", "<М/>")
    write(root, "CommonModules/М/Ext/Module.bsl", MAIN_M)
    write(root, "CommonModules/Новый.xml", "<Новый/>")
    write(root, "CommonModules/Новый/Ext/Module.bsl", "Процедура Н() Экспорт\r\nКонецПроцедуры\r\n")
    write(root, "Roles/НоваяРоль.xml", "<Роль/>")
    write(root, "Catalogs/С.xml", "<С/>")
    write(root, "Catalogs/С/Forms/Ф.xml", "<Ф/>")
    write(root, "Catalogs/С/Forms/Ф/Ext/Form/Module.bsl", DOT_FORM)
    write(root, "Catalogs/С/Forms/НоваяФорма.xml", "<НФ/>")
    write(root, "Catalogs/С/Forms/НоваяФорма/Ext/Form/Module.bsl", "&НаКлиенте\r\nПерем Х;\r\n")


def snapshot(root):
    return {rel: p.read_bytes() for rel, p in dirdiff.files(root).items()}


class SupportState(unittest.TestCase):
    def test_headers(self):
        self.assertEqual(vendor.support_state('﻿{6,1,1,b6c7b199-189f,1,5ec8,"2.5"'), vendor.LOCKED)
        self.assertEqual(vendor.support_state('﻿{6,0,1,b6c7b199-189f,0,5ec8,"2.5"'), vendor.EDITABLE)
        self.assertEqual(vendor.support_state("﻿{6,0,0,0,1,0}"), vendor.NONE)
        self.assertEqual(vendor.support_state(""), vendor.NONE)

    def test_root_info(self):
        with tempfile.TemporaryDirectory() as d:
            fill_head(d)
            self.assertEqual(vendor.root_info(d), (vendor.NONE, "Тест", "1.0.0.1"))
            write(d, "Ext/ParentConfigurations.bin", "﻿{6,0,1,a,0,b}")
            self.assertEqual(vendor.root_info(d)[0], vendor.EDITABLE)


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "r.txt").write_bytes(REPORT.encode("utf-16"))      # Конфигуратор пишет UTF-16 с BOM
        self.report = vendor.parse(vendor.read_report(self.tmp / "r.txt"))

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_parse_objects(self):
        r = self.report
        self.assertEqual(r["added"], ["ОбщийМодуль.Новый", "Роль.НоваяРоль", "Справочник.С.Форма.НоваяФорма"])
        self.assertEqual(r["vendorOnly"], ["Отчет.Удалённый"])
        self.assertIn("ОбщийМодуль.М", r["changed"])
        self.assertEqual([(o, p) for o, p, _ in r["modules"]],
                         [("Конфигурация.Тест", "Модуль сеанса"), ("ОбщийМодуль.М", "Модуль"),
                          ("Справочник.С", "Длина кода"), ("Справочник.С.Форма.Ф", "Модуль")])

    def test_restore_exact(self):
        head = self.tmp / "head"
        fill_head(head)
        hunks = dict(((o, p), h) for o, p, h in self.report["modules"])
        got = vendor.restore(head / "CommonModules/М/Ext/Module.bsl", hunks[("ОбщийМодуль.М", "Модуль")])
        self.assertEqual(got, ("﻿" + VENDOR_M).encode("utf-8"))

    def test_dot_is_ambiguous(self):
        head = self.tmp / "head"
        fill_head(head)
        hunks = dict(((o, p), h) for o, p, h in self.report["modules"])
        with self.assertRaisesRegex(ValueError, "·"):
            vendor.restore(head / "Catalogs/С/Forms/Ф/Ext/Form/Module.bsl", hunks[("Справочник.С.Форма.Ф", "Модуль")])

    def test_before_mirror(self):
        head, before = self.tmp / "head", self.tmp / "before"
        fill_head(head)
        was = snapshot(head)
        info = vendor.build_before(head, before, self.report)
        self.assertEqual(sorted(info["restored"]), ["CommonModules/М/Ext/Module.bsl", "Ext/SessionModule.bsl"])
        self.assertEqual(len(info["whole"]), 1)
        self.assertEqual(info["skipped"], [])             # «Длина кода» — свойство, а не модуль
        self.assertEqual((before / "CommonModules/М/Ext/Module.bsl").read_bytes(), ("﻿" + VENDOR_M).encode())
        self.assertEqual((before / "Ext/SessionModule.bsl").read_bytes(), ("﻿" + VENDOR_SESSION).encode())
        for gone in ("CommonModules/Новый.xml", "CommonModules/Новый", "Roles/НоваяРоль.xml",
                     "Catalogs/С/Forms/НоваяФорма", "Catalogs/С/Forms/Ф/Ext/Form/Module.bsl"):
            self.assertFalse((before / gone).exists(), gone)
        self.assertEqual(snapshot(head), was)                  # «стало» не тронуто

        changes = {c.path: c for c in dirdiff.changes(before, head, module_kinds.is_module)}
        self.assertEqual(changes["CommonModules/М/Ext/Module.bsl"].status, "M")
        self.assertEqual(changes["CommonModules/М/Ext/Module.bsl"].added_lines, {2, 3, 4, 8, 9, 10})
        self.assertEqual(changes["CommonModules/Новый/Ext/Module.bsl"].status, "A")
        self.assertEqual(changes["Catalogs/С/Forms/Ф/Ext/Form/Module.bsl"].status, "A")
        self.assertEqual(changes["Catalogs/С/Forms/НоваяФорма/Ext/Form/Module.bsl"].status, "A")
        self.assertNotIn("Catalogs/С.xml", changes)
        self.assertNotIn("Configuration.xml", changes)

        bsl_analyzer.remove_mirror(before)
        self.assertFalse(before.exists())
        self.assertEqual(snapshot(head), was)


if __name__ == "__main__":
    unittest.main()
