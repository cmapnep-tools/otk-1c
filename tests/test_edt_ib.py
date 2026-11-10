"""Исходники EDT без экспорта: изменены только модули — загрузка из модулей EDT и частичной выгрузки
копии ИБ (Form.xml к модулям форм), описания для расширенной проверки — тоже частичной выгрузкой ИБ."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import checks
from otk_1c.errors import InfraError
from otk_1c.probes import build, edt
from otk_1c.probes.executors import RunContext
from otk_1c.probes.executors.extended_check import ExtCheck, ib_names
from otk_1c.sources import index, layout
from test_edt_index import edt_files, write
from test_edt_load import FakeDesigner

E = layout.EDT
BOM = b"\xef\xbb\xbf"
COLLS = {**index.COLLECTIONS, "CommonAttribute": "CommonAttributes", "Language": "Languages"}
FORM = "Catalogs/Товары/Forms/ФормаЭлемента/Module.bsl"


class FakeIb(FakeDesigner):
    """Конфигуратор с частичной выгрузкой: по строкам -listFile — описание объекта, у формы — описание, Form.xml и
    модуль ИБ; skip — строки, которых «нет в ИБ»."""

    def __init__(self, tmp, rc=0, skip=()):
        super().__init__(tmp)
        self.rc, self.skip = rc, set(skip)

    def designer(self, *args, label="", timeout=0):
        rc, sec, out = super().designer(*args, label=label, timeout=timeout)
        if args[0] != "/DumpConfigToFiles":
            return rc, sec, out
        if self.rc:
            return self.rc, 20.0, "Ошибка выгрузки"
        target = Path(args[1])
        for name in self.lists[-1]:
            if name in self.skip:
                continue
            if name == "Configuration":
                write(target, {"Configuration.xml": "<cfg/>"})
                continue
            parts = name.split(".")
            coll = COLLS[parts[0]]
            if len(parts) == 4:
                d = f"{coll}/{parts[1]}/Forms/{parts[3]}"
                write(target, {f"{d}.xml": "<form/>", f"{d}/Ext/Form.xml": "<Form/>", f"{d}/Ext/Form/Module.bsl": "ИБ"})
            else:
                write(target, {f"{coll}/{parts[1]}.xml": "<obj/>"})
        return 0, 20.0, ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-edtib-"))
        self.addCleanup(rmtree_force, self.tmp)
        self.src = self.tmp / "проект" / "src"
        write(self.src, edt_files())
        write(self.src, {FORM: "Процедура П()\nКонецПроцедуры\n"})
        (self.tmp / "прогон").mkdir()


class NamesTest(unittest.TestCase):
    def test_list_name(self):
        self.assertEqual(edt.list_name("Catalogs", "Валюты"), "Catalog.Валюты")
        self.assertEqual(edt.list_name("Catalogs", "Валюты", "ФормаЭлемента"), "Catalog.Валюты.Form.ФормаЭлемента")
        self.assertEqual(edt.list_name("CommonForms", "Ф"), "CommonForm.Ф")
        self.assertEqual(edt.list_name("Languages", "Русский"), "Language.Русский")
        self.assertEqual(edt.list_name("CommonAttributes", "ОбластьДанных"), "CommonAttribute.ОбластьДанных")
        self.assertIsNone(edt.list_name("Неизвестные", "Х"))

    def test_form_names(self):
        self.assertEqual(edt.form_names(["CommonModules/М/Module.bsl", "Catalogs/К/ObjectModule.bsl"]), [])
        self.assertEqual(edt.form_names([FORM, "CommonForms/Ф/Module.bsl", FORM]),
                         ["Catalog.Товары.Form.ФормаЭлемента", "CommonForm.Ф"])

    def test_edt_modules(self):
        mods = build.edt_modules
        self.assertEqual(mods([("M", "CommonModules/М/Module.bsl"), ("A", FORM)], E),
                         [FORM, "CommonModules/М/Module.bsl"])
        self.assertEqual(mods([("M", "Configuration/SessionModule.bsl")], E), ["Configuration/SessionModule.bsl"])
        self.assertIsNone(mods([("M", "CommonModules/М/Module.bsl")], layout.DESIGNER))       # выгрузка — не EDT
        self.assertIsNone(mods([("M", "CommonModules/М/Module.bsl")], E, extension="Расш"))  # расширение — экспорт
        self.assertIsNone(mods([("M", "Catalogs/Товары/Forms/ФормаЭлемента/Form.form")], E))
        self.assertIsNone(mods([("M", FORM), ("M", "Catalogs/Товары/Товары.mdo")], E))
        self.assertIsNone(mods([("D", "CommonModules/М/Module.bsl")], E))
        self.assertIsNone(mods([("R", "CommonModules/М/Module.bsl")], E))
        self.assertIsNone(mods([], E))


class FromIbTest(Base):
    def test_dump_and_overlay(self):
        p = FakeIb(self.tmp)
        target = self.tmp / "прогон" / "edt-ib" / "load"
        sec = edt.from_ib(p, self.src, target, ["Catalog.Товары.Form.ФормаЭлемента"],
                          [FORM, "CommonModules/М/Module.bsl"])
        self.assertEqual(sec, 20.0)
        self.assertEqual(p.calls[0][0], "/DumpConfigToFiles")
        self.assertIn("-listFile", p.calls[0])
        form = target / "Catalogs/Товары/Forms/ФормаЭлемента/Ext"
        self.assertTrue((form / "Form.xml").is_file())
        # модуль из EDT поверх модуля ИБ, с BOM, байты как есть (LF)
        self.assertEqual((form / "Form/Module.bsl").read_bytes(), BOM + "Процедура П()\nКонецПроцедуры\n".encode())
        self.assertTrue((target / "CommonModules/М/Ext/Module.bsl").read_bytes().startswith(BOM))

    def test_no_names_no_dump(self):
        p = FakeIb(self.tmp)
        self.assertEqual(edt.from_ib(p, self.src, self.tmp / "t", (), ["CommonModules/М/Module.bsl"]), 0.0)
        self.assertEqual(p.calls, [])

    def test_bom_not_doubled(self):
        (self.src / "CommonModules/М/Module.bsl").write_bytes(BOM + b"x")
        edt.from_ib(FakeIb(self.tmp), self.src, self.tmp / "t", (), ["CommonModules/М/Module.bsl"])
        self.assertEqual((self.tmp / "t/CommonModules/М/Ext/Module.bsl").read_bytes(), BOM + b"x")

    def test_failures(self):
        with self.assertRaises(InfraError):
            edt.from_ib(FakeIb(self.tmp, rc=1), self.src, self.tmp / "t", ["Catalog.Товары"], [])
        with self.assertRaises(InfraError):                       # защищённый модуль — не .bsl в выгрузке
            edt.from_ib(FakeIb(self.tmp), self.src, self.tmp / "t", (), ["CommonModules/Защ/Module.bsl"])


class LoadTest(Base):
    def load(self, p, changes):
        files = build._load_list(changes, E)
        modules = build.edt_modules(changes, E)
        state = {"ib": "x", "commit": None, "tree": None, "dir": "d", "extension": None}
        return build._load(p, self.tmp / "прогон", self.src, files, None, None, state, modules)

    def test_modules_without_export(self):
        p = FakeIb(self.tmp)
        with mock.patch.object(edt, "export", side_effect=AssertionError("экспорт")):
            res = self.load(p, [("M", FORM), ("M", "CommonModules/М/Module.bsl")])
        self.assertEqual((res["action"], res["ibDump"]), ("partial", 20.0))
        self.assertNotIn("dump", res)                 # каталог загрузки — не описания для extcheck
        self.assertEqual(p.lists[0], ["Catalog.Товары.Form.ФормаЭлемента"])
        load = next(c for c in p.calls if c[0] == "/LoadConfigFromFiles")
        self.assertEqual(Path(load[1]), self.tmp / "прогон" / "edt-ib" / "load")
        self.assertEqual(p.lists[1], ["Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml",
                                      "Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form/Module.bsl",
                                      "CommonModules/М/Ext/Module.bsl"])

    def test_common_module_no_dump(self):
        p = FakeIb(self.tmp)
        with mock.patch.object(edt, "export", side_effect=AssertionError("экспорт")):
            res = self.load(p, [("M", "CommonModules/М/Module.bsl")])
        self.assertEqual(res["ibDump"], 0.0)
        self.assertEqual([c[0] for c in p.calls], ["/LoadConfigFromFiles", "/UpdateDBCfg"])

    def test_no_form_xml_falls_back_to_export(self):
        """Модуль формы без Form.xml платформа грузит, стирая форму: не вышло выгрузить — экспорт."""
        dump = self.tmp / "прогон" / "edt-export" / "k"
        write(dump, {"Configuration.xml": "<x/>"})
        for p in (FakeIb(self.tmp, skip={"Catalog.Товары.Form.ФормаЭлемента"}), FakeIb(self.tmp, rc=1)):
            with mock.patch.object(edt, "export", return_value=(dump, 42.0)):
                res = self.load(p, [("M", FORM)])
            self.assertEqual(res["export"], 42.0)
            self.assertIn("без экспорта EDT не вышло", res["edtNote"])
            load = next(c for c in p.calls if c[0] == "/LoadConfigFromFiles")
            self.assertEqual(Path(load[1]), dump)

    def test_metadata_changed_exports(self):
        dump = self.tmp / "прогон" / "edt-export" / "k"
        write(dump, {"Configuration.xml": "<x/>"})
        p = FakeIb(self.tmp)
        with mock.patch.object(edt, "export", return_value=(dump, 42.0)):
            res = self.load(p, [("M", FORM), ("M", "Catalogs/Товары/Forms/ФормаЭлемента/Form.form")])
        self.assertEqual(res["export"], 42.0)
        self.assertFalse(any(c[0] == "/DumpConfigToFiles" for c in p.calls))


class ExtcheckTest(Base):
    def unit(self, path, kind, status="M"):
        return {"path": path, "kind": kind, "status": status, "reason": "changed"}

    def test_ib_names(self):
        write(self.src, {"CommonAttributes/ОбластьДанных/ОбластьДанных.mdo": "<x/>",
                         "CommonAttributes/Другой/Другой.mdo": "<x/>"})
        units = [self.unit(FORM, "FormModule"), self.unit("CommonModules/М/Module.bsl", "CommonModule")]
        texts = ["Перечисления.режимы.Первый; Справочники.Нет.Пустая(); ОбластьДанных"]
        self.assertEqual(ib_names(self.src, E, units, texts),
                         ["Catalog.Товары", "Catalog.Товары.Form.ФормаЭлемента", "CommonAttribute.ОбластьДанных",
                          "CommonModule.М", "Configuration", "Enum.Режимы", "Language.Русский"])

    def test_common_module_only_no_attributes_scan(self):
        write(self.src, {"CommonAttributes/ОбластьДанных/ОбластьДанных.mdo": "<x/>"})
        self.assertEqual(ib_names(self.src, E, [self.unit("CommonModules/М/Module.bsl", "CommonModule")],
                                  ["ОбластьДанных"]),
                         ["CommonModule.М", "Configuration", "Language.Русский"])

    def ctx(self, p, dump=None, gate=None):
        plan = {"source": "проект/src", "sourceFormat": "edt", "modules": []}
        return RunContext(self.tmp, plan, {"project": {}}, self.tmp / "прогон", self.tmp, self.src, p,
                          gate or checks.Gate({}), install=lambda r: "", log=lambda m: None, designer_dump=dump)

    def test_designer_root_from_ib(self):
        p = FakeIb(self.tmp)
        c = self.ctx(p)
        with mock.patch("otk_1c.probes.build.loadable", side_effect=AssertionError("экспорт")):
            root = c.designer_root(["CommonModule.М", "Configuration"])
        self.assertEqual(root, self.tmp / "прогон" / "edt-ib" / "extcheck")
        self.assertTrue((root / "CommonModules/М.xml").is_file())
        self.assertEqual(c.timings["ibDump"], 20.0)

    def test_designer_root_build_export_wins(self):
        c = self.ctx(FakeIb(self.tmp), dump=str(self.tmp / "export"))
        self.assertEqual(c.designer_root(["CommonModule.М"]), self.tmp / "export")

    def test_run_uses_ib_dump(self):
        p = FakeIb(self.tmp)
        c = self.ctx(p)
        items = [self.unit("CommonModules/М/Module.bsl", "CommonModule", status="A")]
        with mock.patch("otk_1c.probes.executors.extended_check.run", return_value=([], "")) as run, \
                mock.patch("otk_1c.probes.build.loadable", side_effect=AssertionError("экспорт")):
            ExtCheck().run(c, items)
        root = run.call_args.args[2]
        self.assertEqual(root, self.tmp / "прогон" / "edt-ib" / "extcheck")
        self.assertEqual(p.lists[0], ["CommonModule.М", "Configuration", "Language.Русский"])

    def test_run_ib_dump_error_registry(self):
        """Выгрузка ИБ для extcheck не удалась — проверка не выполнилась целиком: решает реестр проверок, а не
        per-module skipped с кодом 0: on — сбой инфраструктуры (код 2), auto — «не проверено» с check extcheck."""
        for mode, bucket in (("on", "infra"), ("auto", "not_checked")):
            with self.subTest(mode):
                gate = checks.Gate({"checks": {"extcheck": mode}})
                c = self.ctx(FakeIb(self.tmp, rc=1), gate=gate)
                items = [self.unit("CommonModules/М/Module.bsl", "CommonModule", status="A")]
                res, note = ExtCheck().run(c, items)
                self.assertEqual((res, note), ([], ""))
                self.assertEqual(len(getattr(gate, bucket)), 1)
                if mode == "auto":
                    self.assertEqual(gate.not_checked[0]["check"], "extcheck")
                    self.assertIn("выгрузка ИБ", gate.not_checked[0]["reason"])
                else:
                    self.assertIn('extcheck = "on"', gate.infra[0]["message"])
                    self.assertIn("выгрузка ИБ", gate.infra[0]["message"])


if __name__ == "__main__":
    unittest.main()
