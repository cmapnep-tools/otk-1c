"""Каталог контрольных дефектов на исходниках EDT == на выгрузке Конфигуратора (этап 6)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from otk_1c.extras import controls
from otk_1c.probes import build
from otk_1c.sources import layout

PROTECTED = b"\xff\xff\xff\x7f\x00\x01 binary"
SERV = "Функция Привет(Имя) Экспорт\n\tВозврат Имя;\nКонецФункции\n"
CALLER = "Процедура Работа()\n\tСерв.Привет(1);\nКонецПроцедуры\n"
CLIENT = "Процедура Показать() Экспорт\nКонецПроцедуры\n"
OBJ = "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n"
FORM = "&НаКлиенте\nПроцедура ПриОткрытии()\nКонецПроцедуры\n"
STUB = "Процедура Тут()\nКонецПроцедуры\n"
SESSION ="Процедура УстановкаПараметровСеанса()\nКонецПроцедуры\n"


def _texts(crlf_bom):
    if crlf_bom:
        return lambda t: b"\xef\xbb\xbf" + t.replace("\n", "\r\n").encode("utf-8")
    return lambda t: t.encode("utf-8")


def designer(conv):
    def wrap(tag, body):
        return f"<MetaDataObject><{tag}><Properties>{body}</Properties></{tag}></MetaDataObject>"
    return {
        "Configuration.xml": "<MetaDataObject><Configuration><ChildObjects><Catalog>Товары</Catalog>"
                             "<CommonModule>Серв</CommonModule><CommonModule>Вызов</CommonModule>"
                             "<CommonModule>Клиент</CommonModule><CommonModule>Защ</CommonModule>"
                             "</ChildObjects></Configuration></MetaDataObject>",
        "Catalogs/Товары.xml": wrap("Catalog", "<Name>Товары</Name>"),
        "Catalogs/Товары/Ext/ObjectModule.bsl": conv(OBJ),
        "Catalogs/Товары/Forms/Форма.xml": wrap("Form", "<Name>Форма</Name>"),
        "Catalogs/Товары/Forms/Форма/Ext/Form.xml":
            '<Form><Events><Event name="OnOpen">ПриОткрытии</Event></Events></Form>',
        "Catalogs/Товары/Forms/Форма/Ext/Form/Module.bsl": conv(FORM),
        "CommonModules/Серв.xml": wrap("CommonModule", "<Name>Серв</Name><Server>true</Server>"),
        "CommonModules/Серв/Ext/Module.bsl": conv(SERV),
        "CommonModules/Вызов.xml": wrap("CommonModule", "<Name>Вызов</Name><Server>true</Server>"),
        "CommonModules/Вызов/Ext/Module.bsl": conv(CALLER),
        "CommonModules/Клиент.xml": wrap("CommonModule", "<Name>Клиент</Name>"
                                         "<ClientManagedApplication>true</ClientManagedApplication>"),
        "CommonModules/Клиент/Ext/Module.bsl": conv(CLIENT),
        "CommonModules/Защ.xml": wrap("CommonModule", "<Name>Защ</Name><Server>true</Server>"),
        "CommonModules/Защ/Ext/Module.bin": PROTECTED,
        "Ext/SessionModule.bsl": conv(SESSION),
    }


def edt(conv):
    def wrap(tag, body):
        return (f'<?xml version="1.0"?><mdclass:{tag} xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">'
                f"{body}</mdclass:{tag}>")
    return {
        "Configuration/Configuration.mdo": wrap("Configuration", "<name>К</name><catalogs>Catalog.Товары</catalogs>"
            "<commonModules>CommonModule.Серв</commonModules><commonModules>CommonModule.Вызов</commonModules>"
            "<commonModules>CommonModule.Клиент</commonModules><commonModules>CommonModule.Защ</commonModules>"),
        "Catalogs/Товары/Товары.mdo": wrap("Catalog", "<name>Товары</name><forms><name>Форма</name></forms>"),
        "Catalogs/Товары/ObjectModule.bsl": conv(OBJ),
        "Catalogs/Товары/Forms/Форма/Form.form": '<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form">'
                                                 "<handlers><event>OnOpen</event><name>ПриОткрытии</name></handlers></form:Form>",
        "Catalogs/Товары/Forms/Форма/Module.bsl": conv(FORM),
        "CommonModules/Серв/Серв.mdo": wrap("CommonModule", "<name>Серв</name><server>true</server>"),
        "CommonModules/Серв/Module.bsl": conv(SERV),
        "CommonModules/Вызов/Вызов.mdo": wrap("CommonModule", "<name>Вызов</name><server>true</server>"),
        "CommonModules/Вызов/Module.bsl": conv(CALLER),
        "CommonModules/Клиент/Клиент.mdo": wrap("CommonModule", "<name>Клиент</name>"
                                                "<clientManagedApplication>true</clientManagedApplication>"),
        "CommonModules/Клиент/Module.bsl": conv(CLIENT),
        "CommonModules/Защ/Защ.mdo": wrap("CommonModule", "<name>Защ</name><server>true</server>"),
        "CommonModules/Защ/Module.bsl": PROTECTED,
        "Configuration/SessionModule.bsl": conv(SESSION),
    }


def add_modules(files, conv, edt_format):
    """Семь лишних серверных модулей без экспортов: первые дефекты занимают их, Серв остаётся для removed-export."""
    for i in range(1, 8):
        n = f"Мод{i}"
        if edt_format:
            files[f"CommonModules/{n}/{n}.mdo"] = files["CommonModules/Вызов/Вызов.mdo"].replace("Вызов", n)
            files[f"CommonModules/{n}/Module.bsl"] = conv(STUB)
            files["Configuration/Configuration.mdo"] = files["Configuration/Configuration.mdo"].replace(
                "</mdclass:Configuration>", f"<commonModules>CommonModule.{n}</commonModules></mdclass:Configuration>")
        else:
            files[f"CommonModules/{n}.xml"] = files["CommonModules/Вызов.xml"].replace("Вызов", n)
            files[f"CommonModules/{n}/Ext/Module.bsl"] = conv(STUB)
            files["Configuration.xml"] = files["Configuration.xml"].replace(
                "</ChildObjects>", f"<CommonModule>{n}</CommonModule></ChildObjects>")
    return files


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def make_repo(tmp, files):
    repo = Path(tmp)
    git(repo, "init", "-q")
    git(repo, "config", "core.autocrlf", "false")
    for rel, data in files.items():
        p = repo / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


def summary(repo, packs, sess):
    """{id: (файлы, {путь: байты})} по всем пакетам."""
    out = {}
    base = "HEAD"
    sets = list(packs) + ([sess] if sess else [])
    for defects, changes in sets:
        sha = controls.make_commit(repo, base, changes, "t")
        for d in defects:
            blobs = {p: git(repo, "show", f"{sha}:{p}") for p in d.changes}
            out[d.id] = (d.files, blobs)
    return out


class ControlsFormatTest(unittest.TestCase):
    def run_fmt(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = make_repo(tmp.name, files)
        packs, sess, self.skipped = controls.build_defects(repo, "src")
        return repo, summary(repo, packs, sess)

    def test_designer(self):
        repo, res = self.run_fmt(add_modules(designer(_texts(True)), _texts(True), False))
        self.assertEqual(res["syntax-server"][0], ["CommonModules/Вызов/Ext/Module.bsl"])
        self.assertEqual(res["form-name"][0], ["CommonModules/Клиент/Ext/Module.bsl"])
        self.assertEqual(res["webclient-only"][0], ["CommonModules/Клиент/Ext/Module.bsl"])
        self.assertEqual(res["vopros-object"][0], ["Catalogs/Товары/Ext/ObjectModule.bsl"])
        self.assertEqual(res["client-form"][0], ["Catalogs/Товары/Forms/Форма/Ext/Form/Module.bsl"])
        self.assertEqual(res["session"][0], ["Ext/SessionModule.bsl"])
        # формы с обработчиком, ещё форм и справочников, констант и планов обмена в выгрузке нет — видно в отчёте
        # справки платформы (api) нет — дефектов «новее режима совместимости» тоже
        self.assertEqual([i for i, _ in self.skipped], ["handler-renamed", "form-missing-proc", "form-webclient-only",
                                                        "object-missing-proc", "form-open-error", "form-hang",
                                                        "newer-line-method", "newer-method", "newer-ctor",
                                                        "catalog-typo"])
        self.assertIn("константы и плана обмена", self.skipped[-1][1])
        self.assertEqual(res["removed-export"][0], ["CommonModules/Вызов/Ext/Module.bsl"])
        self.assertEqual(list(res["removed-export"][1]), ["src/CommonModules/Серв/Ext/Module.bsl"])
        for _, blobs in res.values():
            for data in blobs.values():
                self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
                self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_extcheck_forms_and_objects_edt(self):
        """То же на исходниках EDT: признак обычной формы — formType в .mdo владельца."""
        files = add_modules(edt(_texts(False)), _texts(False), True)
        conv = _texts(False)
        forms = "".join(f"<forms><name>{f}</name></forms>" for f in ("Форма2", "Форма3")) + \
            "<forms><name>Обычная</name><formType>Ordinary</formType></forms>"
        files["Catalogs/Товары/Товары.mdo"] = files["Catalogs/Товары/Товары.mdo"].replace(
            "</mdclass:Catalog>", forms + "</mdclass:Catalog>")
        for f in ("Форма2", "Форма3", "Обычная"):
            files[f"Catalogs/Товары/Forms/{f}/Form.form"] = '<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form"/>'
            files[f"Catalogs/Товары/Forms/{f}/Module.bsl"] = conv(STUB)
        files["Configuration/Configuration.mdo"] = files["Configuration/Configuration.mdo"].replace(
            "</mdclass:Configuration>", "<catalogs>Catalog.Услуги</catalogs></mdclass:Configuration>")
        files["Catalogs/Услуги/Услуги.mdo"] = ('<?xml version="1.0"?><mdclass:Catalog xmlns:mdclass='
                                              '"http://g5.1c.ru/v8/dt/metadata/mdclass"><name>Услуги</name>'
                                              "</mdclass:Catalog>")
        files["Catalogs/Услуги/ObjectModule.bsl"] = conv(OBJ)
        _, res = self.run_fmt(files)
        self.assertEqual(res["form-missing-proc"][0], ["Catalogs/Товары/Forms/Форма2/Module.bsl"])
        self.assertEqual(res["form-webclient-only"][0], ["Catalogs/Товары/Forms/Форма3/Module.bsl"])
        self.assertEqual(res["object-missing-proc"][0], ["Catalogs/Услуги/ObjectModule.bsl"])

    def test_extcheck_forms_and_objects(self):
        """Ещё управляемые формы и справочник с модулем объекта — дефекты для extcheck."""
        files = add_modules(designer(_texts(True)), _texts(True), False)
        conv = _texts(True)
        for f in ("Форма2", "Форма3"):
            files[f"Catalogs/Товары/Forms/{f}.xml"] = f"<MetaDataObject><Form><Properties><Name>{f}</Name>" \
                                                      "<FormType>Managed</FormType></Properties></Form></MetaDataObject>"
            files[f"Catalogs/Товары/Forms/{f}/Ext/Form.xml"] = "<Form/>"
            files[f"Catalogs/Товары/Forms/{f}/Ext/Form/Module.bsl"] = conv(STUB)
        files["Catalogs/Товары/Forms/Обычная.xml"] = "<MetaDataObject><Form><Properties><Name>Обычная</Name>" \
                                                     "<FormType>Ordinary</FormType></Properties></Form></MetaDataObject>"
        files["Catalogs/Товары/Forms/Обычная/Ext/Form/Module.bsl"] = conv(STUB)
        files["Configuration.xml"] = files["Configuration.xml"].replace("</ChildObjects>",
                                                                        "<Catalog>Услуги</Catalog></ChildObjects>")
        files["Catalogs/Услуги.xml"] = "<MetaDataObject><Catalog><Properties><Name>Услуги</Name></Properties>" \
                                       "</Catalog></MetaDataObject>"
        files["Catalogs/Услуги/Ext/ObjectModule.bsl"] = conv(OBJ)
        _, res = self.run_fmt(files)
        self.assertEqual(res["form-missing-proc"][0], ["Catalogs/Товары/Forms/Форма2/Ext/Form/Module.bsl"])
        self.assertEqual(res["form-webclient-only"][0], ["Catalogs/Товары/Forms/Форма3/Ext/Form/Module.bsl"])
        self.assertEqual(res["object-missing-proc"][0], ["Catalogs/Услуги/Ext/ObjectModule.bsl"])
        self.assertNotIn("form-missing-proc", [i for i, _ in self.skipped])
        blob = res["form-webclient-only"][1]["src/Catalogs/Товары/Forms/Форма3/Ext/Form/Module.bsl"]
        self.assertIn("КомандаСистемы(\"cmd\");".encode(), blob)

    def test_form_open_defects(self):
        """Формы с обработчиком ПриОткрытии — ошибка и зависание при открытии в начале его тела."""
        files = add_modules(designer(_texts(True)), _texts(True), False)
        conv = _texts(True)
        for f in ("Форма2", "Форма3", "Форма4", "Форма5", "Форма6"):     # 3 и 4 занимают дефекты extcheck
            files[f"Catalogs/Товары/Forms/{f}.xml"] = f"<MetaDataObject><Form><Properties><Name>{f}</Name>" \
                                                      "<FormType>Managed</FormType></Properties></Form></MetaDataObject>"
            files[f"Catalogs/Товары/Forms/{f}/Ext/Form.xml"] = \
                '<Form><Events><Event name="OnOpen">ПриОткрытии</Event></Events></Form>'
            files[f"Catalogs/Товары/Forms/{f}/Ext/Form/Module.bsl"] = conv(
                "&НаКлиенте\nПроцедура ПриОткрытии(Отказ)\n\tТут = 1;\nКонецПроцедуры\n")
        _, res = self.run_fmt(files)
        self.assertEqual(res["handler-renamed"][0], ["Catalogs/Товары/Forms/Форма2/Ext/Form/Module.bsl"])
        self.assertEqual(res["form-open-error"][0], ["Catalogs/Товары/Forms/Форма5/Ext/Form/Module.bsl"])
        self.assertEqual(res["form-hang"][0], ["Catalogs/Товары/Forms/Форма6/Ext/Form/Module.bsl"])
        blob = res["form-open-error"][1]["src/Catalogs/Товары/Forms/Форма5/Ext/Form/Module.bsl"].decode("utf-8-sig")
        self.assertEqual(blob.split("\r\n")[1:4], ["Процедура ПриОткрытии(Отказ)",
                                                   '\tВызватьИсключение "ОТКОшибкаОткрытия";', "\tТут = 1;"])
        self.assertIn("Пока ОТКЗависание Цикл", res["form-hang"][1][
            "src/Catalogs/Товары/Forms/Форма6/Ext/Form/Module.bsl"].decode("utf-8-sig"))

    def test_catalog_typo_adopts_constant_and_exchange_plan(self):
        """Дефект для extcheck ссылается на существующие константу и план обмена: их заимствование тоже
        проверяется загрузкой временного расширения; без таких объектов — только опечатка."""
        def blob(res):
            files, blobs = res["catalog-typo"]
            return blobs[f"src/{files[0]}"].decode("utf-8-sig")

        files = add_modules(designer(_texts(True)), _texts(True), False)
        self.assertNotIn("Константы.", blob(self.run_fmt(files)[1]))
        files["Configuration.xml"] = files["Configuration.xml"].replace(
            "</ChildObjects>", "<ExchangePlan>Полный</ExchangePlan><Constant>Ставка</Constant></ChildObjects>")
        files["Constants/Ставка.xml"] = "<MetaDataObject><Constant><Properties><Name>Ставка</Name></Properties>" \
                                        "</Constant></MetaDataObject>"
        files["ExchangePlans/Полный.xml"] = "<MetaDataObject><ExchangePlan><Properties><Name>Полный</Name>" \
                                            "</Properties></ExchangePlan></MetaDataObject>"
        text = blob(self.run_fmt(files)[1])
        self.assertIn("Константы.Ставка.Получить();", text)
        self.assertIn("ПланыОбмена.Полный.ЭтотУзел();", text)
        self.assertLess(text.index("Опечатка"), text.index("Константы."))

    def test_edt(self):
        repo, res = self.run_fmt(add_modules(edt(_texts(False)), _texts(False), True))
        self.assertEqual(res["syntax-server"][0], ["CommonModules/Вызов/Module.bsl"])
        self.assertEqual(res["form-name"][0], ["CommonModules/Клиент/Module.bsl"])
        self.assertEqual(res["webclient-only"][0], ["CommonModules/Клиент/Module.bsl"])
        self.assertEqual(res["vopros-object"][0], ["Catalogs/Товары/ObjectModule.bsl"])
        self.assertEqual(res["client-form"][0], ["Catalogs/Товары/Forms/Форма/Module.bsl"])
        self.assertEqual(res["session"][0], ["Configuration/SessionModule.bsl"])
        self.assertEqual(res["removed-export"][0], ["CommonModules/Вызов/Module.bsl"])
        self.assertEqual(list(res["removed-export"][1]), ["src/CommonModules/Серв/Module.bsl"])
        for files, blobs in res.values():
            self.assertFalse(any("Защ" in f for f in files))
            for path, data in blobs.items():
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), path)
                self.assertNotIn(b"\r", data, path)
                self.assertTrue(data.endswith(b"\n") or not data.strip(), (path, data[-20:]))
        text = res["syntax-server"][1]["src/CommonModules/Вызов/Module.bsl"].decode("utf-8")
        self.assertTrue(text.startswith(CALLER) and "ОТККонтроль1" in text)

    def test_same_defects(self):
        _, d = self.run_fmt(add_modules(designer(_texts(True)), _texts(True), False))
        _, e = self.run_fmt(add_modules(edt(_texts(False)), _texts(False), True))
        self.assertEqual(sorted(d), sorted(e))

    def test_handler_renamed(self):
        # у единственной формы обработчик берёт client-form; вторая форма даёт переименование
        for files in (add_modules(designer(_texts(True)), _texts(True), False),
                      add_modules(edt(_texts(False)), _texts(False), True)):
            if "Configuration.xml" in files:
                files["Catalogs/Товары/Forms/Вторая/Ext/Form.xml"] = files["Catalogs/Товары/Forms/Форма/Ext/Form.xml"]
                files["Catalogs/Товары/Forms/Вторая/Ext/Form/Module.bsl"] = \
                    files["Catalogs/Товары/Forms/Форма/Ext/Form/Module.bsl"]
            else:
                files["Catalogs/Товары/Forms/Вторая/Form.form"] = files["Catalogs/Товары/Forms/Форма/Form.form"]
                files["Catalogs/Товары/Forms/Вторая/Module.bsl"] = files["Catalogs/Товары/Forms/Форма/Module.bsl"]
            _, res = self.run_fmt(files)
            self.assertIn("handler-renamed", res)
            (path, data), = res["handler-renamed"][1].items()
            self.assertIn("ПриОткрытииОТКПереименован".encode("utf-8"), data)


class FromDesignerTest(unittest.TestCase):
    def test_edt(self):
        e = layout.EDT
        self.assertEqual(e.from_designer("CommonModules/М/Ext/Module.bsl"), "CommonModules/М/Module.bsl")
        self.assertEqual(e.from_designer("Catalogs/К/Forms/Ф/Ext/Form/Module.bsl"), "Catalogs/К/Forms/Ф/Module.bsl")
        self.assertEqual(e.from_designer("Catalogs/К/Forms/Ф/Ext/Form.xml"), "Catalogs/К/Forms/Ф/Form.form")
        self.assertEqual(e.from_designer("CommonForms/Ф/Ext/Form.xml"), "CommonForms/Ф/Form.form")
        self.assertEqual(e.from_designer("Configuration.xml"), "Configuration/Configuration.mdo")
        self.assertEqual(e.from_designer("Ext/SessionModule.bsl"), "Configuration/SessionModule.bsl")
        self.assertEqual(e.from_designer("Catalogs/К.xml"), "Catalogs/К/К.mdo")
        self.assertEqual(e.from_designer("Catalogs/К/Forms/Ф.xml"), "Catalogs/К/К.mdo")
        self.assertEqual(e.from_designer("Catalogs/К/Ext/Predefined.xml"), "Catalogs/К/К.mdo")
        self.assertEqual(e.from_designer("Catalogs/К/Templates/Т.xml"), "Catalogs/К/К.mdo")
        self.assertIsNone(e.from_designer("Roles/Р/Ext/Rights.xml"))
        self.assertIsNone(e.from_designer("Ext/HomePageWorkArea.xml"))
        self.assertIsNone(e.from_designer("Ext/ClientApplicationInterface.xml"))
        self.assertIsNone(e.from_designer("D:/other/Catalogs/К/Forms/Ф/Ext/Form.xml"))

    def test_roundtrip(self):
        e = layout.EDT
        for p in ("CommonModules/М/Module.bsl", "Catalogs/К/Forms/Ф/Form.form", "Configuration/Configuration.mdo"):
            self.assertEqual(e.from_designer(e.to_designer(p)), p)

    def test_designer_identity(self):
        self.assertEqual(layout.DESIGNER.from_designer("Catalogs/К.xml"), "Catalogs/К.xml")

    def test_source_errors(self):
        out = ("[ERROR] Файл - D:/x/dump/CommonModules/М/Ext/Module.bsl: ошибка\n"
               "[ERROR] Файл - D:/x/dump/Roles/Р/Ext/Rights.xml: плохо\n")
        self.assertEqual(build.source_errors(out, "D:/x/dump", layout.EDT),
                         [("CommonModules/М/Module.bsl", "ошибка"),
                          ("Roles/Р/Ext/Rights.xml", build.EXPORT_MARK + "плохо")])
        self.assertEqual(build.source_errors(out, "D:/x/dump")[0][0], "CommonModules/М/Ext/Module.bsl")


if __name__ == "__main__":
    unittest.main()
