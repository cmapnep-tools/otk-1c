"""Контрольные дефекты (спецификация control-defects).

Дефекты вносятся не в файлы, а в git-объекты: коммит с правками собирается через временный индекс
(hash-object / read-tree / update-index / write-tree / commit-tree) и ни на какую ветку не попадает —
рабочее дерево, индекс и ветки разработчика не меняются. Дальше обычный gate по этому коммиту.
Каждый дефект — в своём модуле; пойман, если в его файлах есть находка ожидаемого шага.
Дефект модуля сеанса ломает старт любого сеанса — отдельный прогон.

Каждый дефект строится от исходного текста независимо; дефекты раскладываются на прогоны («пакеты»)
так, чтобы в одном пакете они не делили файлы: платформа сообщает только первую ошибку компиляции модуля,
и второй дефект в том же модуле был бы не виден. Большой конфигурации хватает одного пакета, небольшому
проекту (расширению с парой серверных модулей) — нескольких.

Проект-расширение (project.extension): дефекты вносятся в модули расширения, а имена объектов
метаданных для них (справочники, предопределённые элементы) берутся из основной конфигурации
(project.parent); паритет — /CheckModules ... -Extension <имя>.

Паритет проекта-конфигурации — два эталона: /CheckModules основной конфигурации и тот же код,
заимствованный во временное расширение extcheck (/CheckModules -Extension otk_check, вывод
logs/extcheck-after.out того же пакета): ОТК опирается на второй, а сравнивается с первым.
"""
import collections
import copy
import datetime
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .. import analyzers as analyzers_mod, config
from ..analyzers import TITLES
from ..gate import pipeline
from ..probes import designer_check
from ..probes.executors import extended_check
from ..probes.platform import Platform, decode, find_platform, platform_version
from ..probes.syntax.index import GLOBAL, version_key, version_str
from ..sources import bsl, gitdiff, index, layout

SYN = ("\r\nФункция ОТККонтроль{n}()\r\n\tЕсли Истина Тогда\r\n\t\tВозврат 1;\r\n\tКонецЦикла;\r\n"
       "КонецФункции\r\n")


class Defect:
    """severity — уровень находки, которой дефект пойман: warning — достаточно предупреждения (код, который
    Конфигуратор пропускает, но на части платформ он не скомпилируется)."""
    def __init__(self, id_, title, expect, marker=None, severity="error"):
        self.id, self.title, self.expect, self.marker, self.severity = id_, title, expect, marker, severity
        self.files, self.owner, self.changes = [], None, {}


def _git_bytes(repo, *args, data=None, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True,
                       env={**os.environ, **(env or {})})
    if r.returncode:
        raise gitdiff.GitError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout


def make_commit(repo, base, changes, message):
    """Коммит base + changes ({путь: текст | None-удалить}) без изменения рабочего дерева и веток."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git_bytes(repo, "read-tree", base, env=env)
        for path, text in changes.items():
            if text is None:
                _git_bytes(repo, "update-index", "--force-remove", "--", path, env=env)
                continue
            # кодировка исходника сохраняется: BOM только если он был у блоба base (нового файла - BOM, как в выгрузке)
            bom = _had_bom(repo, base, path)
            blob = _git_bytes(repo, "hash-object", "-w", "--stdin",
                              data=(b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")).decode().strip()
            _git_bytes(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = _git_bytes(repo, "write-tree", env=env).decode().strip()
    ident = {"GIT_AUTHOR_NAME": "otk", "GIT_AUTHOR_EMAIL": "otk@local",
             "GIT_COMMITTER_NAME": "otk", "GIT_COMMITTER_EMAIL": "otk@local"}
    return _git_bytes(repo, "commit-tree", tree, "-p", base, "-m", message, env=ident).decode().strip()


def _had_bom(repo, rev, path):
    """Был ли у файла в ревизии BOM (gitdiff.show его срезает); файла нет - BOM (формат выгрузки Конфигуратора)."""
    try:
        return _git_bytes(repo, "cat-file", "blob", f"{rev}:{path}").startswith(b"\xef\xbb\xbf")
    except gitdiff.GitError:
        return True


def _read(repo, rev, path):
    return gitdiff.show(repo, rev, path)


def _nl(code, text):
    """Дописываемый код (написан с CRLF) - в переводах строк исходного текста: LF, если в нём есть строки и все с LF."""
    return code.replace("\r\n", "\n") if "\n" in text and "\r\n" not in text else code


def _pick(candidates, used, uses=None):
    """Ещё не задействованный кандидат; если все заняты и передан счётчик uses — наименее занятый
    (дефекты в одном модуле разойдутся по разным пакетам)."""
    for c in candidates:
        if c not in used:
            used.add(c)
            if uses is not None:
                uses[c] += 1
            return c
    if uses is not None and candidates:
        c = min(candidates, key=lambda x: (uses[x], x))
        uses[c] += 1
        return c
    return None


def batches(defects, src):
    """Пакеты [(дефекты, {путь: текст})]: в пакете дефекты не делят ни изменённых, ни проверяемых файлов."""
    out = []
    prefix = gitdiff.source_prefix(src)
    for d in defects:
        touch = {p[len(prefix):] if p.startswith(prefix) else p for p in d.changes} | set(d.files)
        for ds, changes, used in out:
            if not touch & used:
                ds.append(d)
                changes.update(d.changes)
                used |= touch
                break
        else:
            out.append(([d], dict(d.changes), set(touch)))
    return [(ds, changes) for ds, changes, _ in out]


# дефекты, которым в проекте может не найтись цели: id -> почему не построен
NOT_BUILT = {
    "removed-export": "нет экспортного метода серверного модуля с 1–3 вызывающими",
    "form-name": "нет клиентского общего модуля",
    "webclient-only": "нет клиентского общего модуля без внешнего соединения",
    "vopros-object": "нет своего справочника с модулем объекта",
    "client-form": "нет своей формы справочника",
    "handler-renamed": "нет формы справочника с обработчиком события",
    "form-missing-proc": "нет ещё одной управляемой формы справочника",
    "form-webclient-only": "нет ещё одной управляемой формы справочника",
    "object-missing-proc": "нет ещё одного своего справочника с модулем объекта",
    "form-open-error": "нет ещё одной формы справочника с обработчиком ПриОткрытии",
    "form-hang": "нет ещё одной формы справочника с обработчиком ПриОткрытии",
    "newer-line-method": "нет справки платформы, режим совместимости не задан или платформа проверки — "
                         "его линейки (нужна 8.5 при режиме 8.3.x)",
    "newer-method": "нет справки платформы, режим совместимости не задан или он новее серверных методов "
                    "своей линейки",
    "newer-ctor": "нет справки платформы, режим совместимости не задан или нет конструктора новее него",
    "session": "нет модуля сеанса",
}
# дефекты семантики анализаторов BSL (только controls --analyzers): клиентским нужен клиентский общий модуль
NOT_BUILT_ANALYZERS = {
    "client-server-type": "нет клиентского общего модуля",
    "client-modal": "нет клиентского общего модуля",
}
ANALYZER_EXPECT = ["static:bslls:", "static:bsla:"]


class _Targets:
    """Цели дефектов в выгрузке и построенные дефекты: свои модули (чужие и защищённые править нельзя), занятые
    цели (used, uses — см. _pick), формы справочников. Каждый дефект — от исходного текста файла (пакеты — batches)."""

    def __init__(self, repo, src, rev, parent, cache_dir, api):
        self.repo, self.src, self.rev, self.api = repo, src, rev, api
        self.root = root = Path(repo) / src
        self.lay = layout.detect(root) or layout.DESIGNER
        self.ix = index.build(root)
        self.meta = self.ix
        if parent:
            parent_ix = index.cached_dir(Path(parent), Path(cache_dir)) if cache_dir else index.build(Path(parent))
            self.meta = index.merge(self.ix, parent_ix)
        self.mods = {k: m for k, m in self.ix["commonModules"].items()
                     if m["source"] and self.editable(self.mpath("CommonModules", m["name"], "Module"))}
        skip = ("переопределяем", "глобальн", "повтисп", "вызовсервера", "клиентсервер")
        self.server = sorted(m["name"] for m in self.mods.values() if m["source"] and m["flags"]["Server"]
                             and not any(m["flags"][f] for f in ("Global", "ClientManagedApplication", "ServerCall"))
                             and not any(s in m["name"].lower() for s in skip))
        self.client = sorted(m["name"] for m in self.mods.values()
                             if m["source"] and m["flags"]["ClientManagedApplication"]
                             and not m["flags"]["Server"] and not m["flags"]["Global"]
                             and not any(s in m["name"].lower() for s in skip))
        catalogs = sorted(self.meta["objects"].get("Catalogs", {}).values(), key=lambda o: o["name"])
        self.cat = next(c["name"] for c in catalogs)
        self.cat_pre = next((c["name"] for c in catalogs if c["predefined"]), self.cat)
        self.own_catalogs = sorted(self.ix["objects"].get("Catalogs", {}).values(), key=lambda o: o["name"])
        self.forms = sorted(p for p in root.glob(self.mpath("Catalogs", "*", "FormModule", "*"))
                            if self.editable(p.relative_to(root).as_posix()))
        # константа и план обмена для заимствования рядом с catalog-typo
        self.first = {k: min((o["name"] for o in self.meta["objects"].get(k, {}).values()), default=None)
                      for k in ("Constants", "ExchangePlans")}
        self.used, self.uses, self.form_used, self.defects = set(), collections.Counter(), set(), []

    def mpath(self, coll, name, kind, sub=None):
        return self.lay.module_path(layout.Addr(coll, name, kind, sub))

    def editable(self, rel):              # защищённый модуль EDT (двоичный .bsl) целью дефекта быть не может
        if not self.lay.has_protected:
            return True
        try:
            with open(self.root / rel, "rb") as f:
                return not self.lay.protected(f.read(4))
        except OSError:
            return True

    def text_of(self, rel):
        return _read(self.repo, self.rev, f"{self.src}/{rel}")

    def put(self, defect, rel, code, text=None):
        """Дефект: исходный текст файла + дописываемый код (в переводах строк исходника)."""
        text = self.text_of(rel) if text is None else text
        defect.changes[f"{self.src}/{rel}"] = text + _nl(code, text)
        defect.files.append(rel)
        self.defects.append(defect)

    def append_server(self, defect, code):
        name = _pick(self.server, self.used, self.uses)
        defect.owner = f"ОбщийМодуль.{name}"
        self.put(defect, self.mpath("CommonModules", name, "Module"), code)

    def object_module(self):
        """Свой справочник с модулем объекта, ещё не занятый, или None."""
        obj = next((c["name"] for c in self.own_catalogs if c["name"] not in self.used
                    and (self.root / self.mpath("Catalogs", c["name"], "ObjectModule")).exists()
                    and self.editable(self.mpath("Catalogs", c["name"], "ObjectModule"))), None)
        if obj:
            self.used.add(obj)
        return obj

    def form_content(self, p):
        a = self.lay.parse(p.relative_to(self.root).as_posix())
        content = self.root / self.lay.form_files("Catalogs", a.name, a.sub)[1]
        return layout.read_text(content) if content.is_file() else ""

    def put_form(self, defect, p, code, text=None):
        """Дефект модуля формы; владелец — для поиска маркера в выводе Конфигуратора («Справочник.Х.Форма.Ф»)."""
        a = self.lay.parse(p.relative_to(self.root).as_posix())
        defect.owner = f"Справочник.{a.name}.Форма.{a.sub}"
        self.form_used.add(p)
        self.put(defect, p.relative_to(self.root).as_posix(), code, text)


def build_defects(repo, src, rev="HEAD", parent=None, cache_dir=None, api=None, analyzers=False):
    """Цели по индексу выгрузки и правки. Возвращает (основной набор, набор модуля сеанса, непостроенные), наборы —
    (список Defect, {путь: новый текст}), непостроенные — [(id, причина)] (NOT_BUILT и частичный catalog-typo).
    parent — выгрузка основной конфигурации (для расширения); api — (индекс справки платформы проверки, режим
    совместимости) для дефектов «новее режима»; analyzers — плюс дефекты семантики анализаторов BSL
    (controls --analyzers)."""
    t = _Targets(repo, src, rev, parent, cache_dir, api)
    for step in BUILDERS:                 # порядок важен: шаги занимают цели (used, uses)
        step(t)
    if analyzers:
        _analyzer_defects(t.append_server, t.client, t.used, t.uses, t.cat, t.mpath, t.put)
    session = _session_defect(t)
    built = {d.id for d in t.defects} | ({"session"} if session else set())
    skipped = [(i, why) for i, why in {**NOT_BUILT, **(NOT_BUILT_ANALYZERS if analyzers else {})}.items()
               if i not in built]
    if not (t.first["Constants"] and t.first["ExchangePlans"]):
        skipped.append(("catalog-typo", "заимствование " + " и ".join(
            name for k, name in (("Constants", "константы"), ("ExchangePlans", "плана обмена")) if not t.first[k])
            + " не проверено: таких объектов нет"))
    return batches(t.defects, src), session, skipped


def _server_defects(t):
    """Серверный общий модуль: ошибки компиляции, ссылок на метаданные и методы, запрос."""
    t.append_server(Defect("syntax-server", "синтаксическая ошибка в невызываемой функции серверного модуля",
                           ["static:bslls:ParseError", "run:com", "run:extcheck"], r"КонецЕсли|EndIf"),
                    SYN.format(n=1))
    # имя несуществующего общего модуля перед точкой: «Переменная не определена» Конфигуратора
    t.append_server(Defect("undeclared", "необъявленная переменная", ["static:platform-api", "run:com", "run:extcheck"],
                           "ОТКНеобъявленная"),
                    "\r\nФункция ОТККонтроль2() Экспорт\r\n\tВозврат ОТКНеобъявленная.Метод();\r\nКонецФункции\r\n")
    t.append_server(Defect("missing-proc", "вызов несуществующей процедуры модуля",
                           ["static:platform-api", "run:com", "run:extcheck"], "ОТКНетТакойПроцедуры"),
                    "\r\nПроцедура ОТККонтроль3() Экспорт\r\n\tОТКНетТакойПроцедуры();\r\nКонецПроцедуры\r\n")
    # верные ссылки на константу и план обмена рядом: их заимствование во временное расширение тоже проверяется —
    # не загрузится расширение, extcheck даст «не проверено», и дефект останется непойманным
    first = t.first
    adopted = ((f"\r\n\t\tК = Константы.{first['Constants']}.Получить();" if first["Constants"] else "")
               + (f"\r\n\t\tУ = ПланыОбмена.{first['ExchangePlans']}.ЭтотУзел();" if first["ExchangePlans"] else ""))
    t.append_server(Defect("catalog-typo", "Справочники.<опечатка> в неисполняемой ветке",
                           ["static:metadata-ref", "run:extcheck"], f"{t.cat}Опечатка"),
                    "\r\nПроцедура ОТККонтроль4() Экспорт\r\n\tЕсли Ложь Тогда"
                    f"\r\n\t\tХ = Справочники.{t.cat}Опечатка.ПустаяСсылка();{adopted}"
                    "\r\n\tКонецЕсли;\r\nКонецПроцедуры\r\n")
    t.append_server(Defect("predefined", "несуществующий предопределённый элемент", ["static:metadata-ref"],
                           "ОТКНетТакого"),
                    f"\r\nФункция ОТККонтроль5() Экспорт\r\n\tВозврат Справочники.{t.cat_pre}.ОТКНетТакого;"
                    "\r\nКонецФункции\r\n")
    t.append_server(Defect("no-ctor", "Новый для типа платформы без конструктора",
                           ["static:platform-api", "run:com", "run:extcheck"],
                           "Конструктор не найден"),   # без имени типа
                    "\r\nФункция ОТККонтрольКонструктор() Экспорт\r\n\tВозврат Новый WebSocketКлиентСоединение;"
                    "\r\nКонецФункции\r\n")
    t.append_server(Defect("query-field", "опечатка в поле запроса", ["run:com", "run:yaxunit"], "ОТКНетПоля"),
                    '\r\nФункция ОТККонтроль6() Экспорт\r\n\tЗапрос = Новый Запрос('
                    '"ВЫБРАТЬ Т.ОТКНетПоля КАК П '
                    f'ИЗ Справочник.{t.cat} КАК Т");\r\n\tВозврат Запрос;\r\nКонецФункции\r\n')
    # лишний параметр в вызове экспортного метода другого модуля
    target_mod = next(m for m in sorted(t.mods.values(), key=lambda m: m["name"])
                      if m["source"] and m["flags"]["Server"] and not m["flags"]["Global"] and m["exports"]
                      and not m["flags"]["ClientManagedApplication"])
    exp = sorted(target_mod["exports"].values())[0]
    args = ", ".join(["Неопределено"] * (exp[2] + 1))
    t.append_server(Defect("extra-arg", "лишний параметр в вызове экспортного метода", ["static:common-method"],
                           "фактических параметров"),
                    f"\r\nПроцедура ОТККонтроль7() Экспорт\r\n\t{target_mod['name']}.{exp[0]}({args});"
                    "\r\nКонецПроцедуры\r\n")


def _removed_export(t):
    """Удалён экспортный метод с вызывающими."""
    callers = _find_export_with_callers(t.root, t.mods, t.used, t.lay)
    if callers:
        name, method, caller_files = callers
        rel = t.mpath("CommonModules", name, "Module")
        d = Defect("removed-export", "удалён экспортный метод с вызывающими", ["static:dependent"], method)
        d.owner = f"ОбщийМодуль.{name}"
        d.changes[f"{t.src}/{rel}"] = _remove_method(t.text_of(rel), method)
        d.files = caller_files[:3]
        t.defects.append(d)


def _client_defects(t):
    """Клиентский общий модуль: несуществующая форма; метод, недоступный только в веб-клиенте (в тонком клиенте
    есть; доступность — по contexts.availability_table): ловит только проверка с -WebClient (extcheck), пробы
    веб-клиент не компилируют. Модуль и с внешним соединением не годится: там КомандаСистемы тоже недоступна —
    дефект не «только веб»."""
    name = _pick(t.client, t.used)
    if name:
        d = Defect("form-name", "несуществующая форма в ПолучитьФорму(\"…\")", ["static:form-name"], "ОТКНетФормы")
        d.owner = f"ОбщийМодуль.{name}"
        t.put(d, t.mpath("CommonModules", name, "Module"),
              '\r\nПроцедура ОТККонтроль8() Экспорт\r\n\tФ = '
              f'ПолучитьФорму("Справочник.{t.cat}.Форма.ОТКНетФормы");'
              "\r\nКонецПроцедуры\r\n")
    name = _pick([n for n in t.client if not t.mods[n.lower()]["flags"]["ExternalConnection"]], t.used, t.uses)
    if name:
        d = Defect("webclient-only", "метод, недоступный только в веб-клиенте (КомандаСистемы)", ["run:extcheck"],
                   "КомандаСистемы")
        d.owner = f"ОбщийМодуль.{name}"
        t.put(d, t.mpath("CommonModules", name, "Module"),
              '\r\nПроцедура ОТККонтроль12() Экспорт\r\n\tКомандаСистемы("cmd");'
              "\r\nКонецПроцедуры\r\n")


def _object_defect(t):
    """Модуль объекта справочника: клиентский Вопрос()."""
    obj = t.object_module()
    if obj:
        d = Defect("vopros-object", "Вопрос() в модуле объекта вне #Если", ["run:com", "run:yaxunit"],
                   "Вопрос")
        d.owner = f"Справочник.{obj}"
        t.put(d, t.mpath("Catalogs", obj, "ObjectModule"),
              "\r\nПроцедура ОТККонтроль9()\r\n\tВопрос(\"?\", РежимДиалогаВопрос.ДаНет);"
              "\r\nКонецПроцедуры\r\n")


def _form_defects(t):
    """Формы справочников: ошибка в клиентской процедуре и переименованный обработчик; модули управляемых форм
    во временном расширении extcheck: ловит только он — пробы компилируют форму в тонком клиенте при открытии,
    веб-клиент — никто, кроме него."""
    root = t.root
    if t.forms:
        t.put_form(Defect("client-form", "ошибка в клиентской процедуре модуля формы",
                          ["static:bslls:ParseError", "run:yaxunit"], r"КонецЕсли|EndIf"),
                   t.forms[0], "\r\n&НаКлиенте" + SYN.format(n=10))
    for p in (p for p in t.forms if t.lay.form_handlers(t.form_content(p))):
        if p in t.form_used:
            continue
        handler = t.lay.form_handlers(t.form_content(p))[0]
        text = t.text_of(p.relative_to(root).as_posix())
        new = _rename_method(text, handler, "ОТКПереименован")
        if new != text:
            # маркер — прежнее имя: так Конфигуратор назвал бы обработчик, которого нет в модуле
            t.put_form(Defect("handler-renamed", "переименован обработчик события формы", ["static:handler"],
                              rf"(?<![\wА-Яа-яЁё]){re.escape(handler)}(?![\wА-Яа-яЁё])"), p, "", new)
            break
    managed = [p for p in t.forms if p not in t.form_used
               and extended_check.managed_form(root, t.lay.parse(p.relative_to(root).as_posix()), t.lay)]
    for p, (did, title, marker, code) in zip(managed, (
            ("form-missing-proc", "вызов несуществующей процедуры в модуле формы", "ОТКНетПроцедурыФормы",
             "\r\n&НаКлиенте\r\nПроцедура ОТККонтроль13()\r\n\tОТКНетПроцедурыФормы();"
             "\r\nКонецПроцедуры\r\n"),
            ("form-webclient-only", "метод, недоступный только в веб-клиенте, в модуле формы", "КомандаСистемы",
             "\r\n&НаКлиенте\r\nПроцедура ОТККонтроль14()\r\n\tКомандаСистемы(\"cmd\");"
             "\r\nКонецПроцедуры\r\n")), strict=False):
        t.put_form(Defect(did, title, ["run:extcheck"], marker), p, code)


def _object_extcheck_defect(t):
    """Модуль объекта во временном расширении extcheck."""
    obj = t.object_module()
    if obj:
        d = Defect("object-missing-proc", "вызов несуществующей процедуры в модуле объекта", ["run:extcheck"],
                   "ОТКНетПроцедурыОбъекта")
        d.owner = f"Справочник.{obj}"
        t.put(d, t.mpath("Catalogs", obj, "ObjectModule"),
              "\r\nПроцедура ОТККонтроль15()\r\n\tОТКНетПроцедурыОбъекта();\r\nКонецПроцедуры\r\n")


def _form_open_defects(t):
    """Ошибка и зависание при открытии формы (обработчик ПриОткрытии): компиляция их не видит, ловит дымовой тест."""
    for p, (did, title, marker, code) in zip(
            [p for p in t.forms if p not in t.form_used and _on_open(t.form_content(p))], (
            ("form-open-error", "исключение в обработчике открытия формы", "ОТКОшибкаОткрытия",
             '\tВызватьИсключение "ОТКОшибкаОткрытия";\r\n'),
            ("form-hang", "зависание формы при открытии (бесконечный цикл в ПриОткрытии)", "ОТКЗависание",
             "\tОТКЗависание = Истина;\r\n\tПока ОТКЗависание Цикл\r\n\tКонецЦикла;\r\n")), strict=False):
        text = t.text_of(p.relative_to(t.root).as_posix())
        new = _prepend_body(text, _on_open(t.form_content(p)), _nl(code, text))
        if new != text:
            t.put_form(Defect(did, title, ["run:yaxunit"], marker), p, "", new)


def _newer_api_defects(t):
    """Методы и конструкторы платформы новее режима совместимости: метод более новой линейки Конфигуратор
    не определит, той же линейки и конструктор любой версии — пропустит (ОТК — предупреждение)."""
    for did, title, row, severity in _newer_api(t.api):
        name = row["name"]
        n = len(t.defects)
        if row["kind"] == "ctor":
            code = f"\r\nФункция ОТККонтрольВерсия{n}() Экспорт\r\n\tВозврат Новый {name}();\r\nКонецФункции\r\n"
        else:
            args = ", ".join(["Неопределено"] * (row["syntax"] or "").split("\n")[0].count("<"))
            code = f"\r\nПроцедура ОТККонтрольВерсия{n}() Экспорт\r\n\t{name}({args});\r\nКонецПроцедуры\r\n"
        expect = ["static:platform-api", "run:com", "run:extcheck"] if severity == "error" else ["static:platform-api"]
        t.append_server(Defect(did, f"{title}: {name} ({row['since']})", expect, rf"(?<![\wА-Яа-яЁё]){name}\b",
                               severity), code)


# шаги основного набора: каждый занимает свои цели, порядок задаёт и раскладку по пакетам
BUILDERS = (_server_defects, _removed_export, _client_defects, _object_defect, _form_defects,
            _object_extcheck_defect, _form_open_defects, _newer_api_defects)


def _session_defect(t):
    """Набор модуля сеанса — отдельно от основного (controls --no-session его не запускает) или None."""
    rel = t.mpath("", "", "SessionModule")
    text = _read(t.repo, t.rev, f"{t.src}/{rel}")
    if text is None or t.lay.protected(text):
        return None
    d = Defect("session", "ошибка в модуле сеанса", ["static:bslls:ParseError", "run:yaxunit"], r"КонецЕсли|EndIf")
    d.files = [rel]
    d.owner = "МодульСеанса"
    d.changes = {f"{t.src}/{rel}": text + _nl(SYN.format(n=11), text)}
    return [d], dict(d.changes)


def _analyzer_defects(append_server, client, used, uses, cat, mpath, put):
    """Дефекты, на которых сверяется семантика анализаторов BSL (обычный controls их не строит): компилятор их
    пропускает или ловит только при исполнении."""
    append_server(Defect("no-return", "функция без Возврат на одной из ветвей", ANALYZER_EXPECT),
                  "\r\nФункция ОТККонтрольВозврат(Флаг) Экспорт\r\n\tЕсли Флаг Тогда\r\n\t\tВозврат 1;"
                  "\r\n\tКонецЕсли;\r\nКонецФункции\r\n")
    append_server(Defect("type-method", "метод, которого нет у типа значения (Структура.Добавить)", ANALYZER_EXPECT),
                  "\r\nПроцедура ОТККонтрольТип() Экспорт\r\n\tОТКСтруктура = Новый Структура;"
                  "\r\n\tОТКСтруктура.Добавить(1);\r\nКонецПроцедуры\r\n")
    append_server(Defect("query-table", "запрос к несуществующей таблице", ANALYZER_EXPECT),
                  '\r\nФункция ОТККонтрольТаблица() Экспорт\r\n\tЗапрос = Новый Запрос('
                  f'"ВЫБРАТЬ Т.Ссылка КАК П ИЗ Справочник.{cat}ОТКНетТаблицы КАК Т");'
                  "\r\n\tВозврат Запрос;\r\nКонецФункции\r\n")
    for did, title, code in (
            ("client-server-type", "серверный тип в клиентском модуле (Новый Запрос)",
             '\r\nПроцедура ОТККонтрольКлиентЗапрос() Экспорт\r\n\tОТКЗапрос = Новый Запрос("ВЫБРАТЬ 1");'
             "\r\n\tОТКЗапрос.Выполнить();\r\nКонецПроцедуры\r\n"),
            ("client-modal", "модальный вызов в клиентском модуле (Вопрос)",
             '\r\nПроцедура ОТККонтрольМодально() Экспорт\r\n\tОТКОтвет = Вопрос("?", РежимДиалогаВопрос.ДаНет);'
             "\r\nКонецПроцедуры\r\n")):
        name = _pick(client, used, uses)
        if not name:
            continue
        d = Defect(did, title, ANALYZER_EXPECT)
        d.owner = f"ОбщийМодуль.{name}"
        put(d, mpath("CommonModules", name, "Module"), code)


def _find_export_with_callers(root, mods, used, lay=layout.DESIGNER):
    """Экспортный метод серверного общего модуля, который вызывают другие модули: (модуль, метод, файлы)."""
    candidates = sorted((m for m in mods.values() if m["source"] and m["flags"]["Server"] and not m["flags"]["Global"]
                         and m["exports"] and m["name"] not in used), key=lambda m: m["name"])
    texts = None
    for m in candidates[:80]:
        for exp in sorted(m["exports"].values()):
            pattern = re.compile(rf"(?<![\wА-Яа-яЁё.]){re.escape(m['name'])}\s*\.\s*{re.escape(exp[0])}\s*\(", re.I)
            if texts is None:
                texts = {p.relative_to(root).as_posix(): layout.read_text(p)
                         for p in root.rglob("*.bsl")}
                texts = {rel: t for rel, t in texts.items() if not lay.protected(t)}
            files = sorted(rel for rel, t in texts.items() if pattern.search(t)
                           and rel != lay.module_path(layout.Addr("CommonModules", m["name"], "Module")))
            if 0 < len(files) <= 3:
                used.add(m["name"])
                return m["name"], exp[0], files
    return None


def _rename_method(text, name, suffix):
    """Первый заголовок метода name -> name + suffix (любой вариант языка)."""
    def rename(m):
        cut = m.end("name") - m.start()
        return m.group(0)[:cut] + suffix + m.group(0)[cut:]
    return bsl.header_re(name).sub(rename, text, count=1)


def _remove_method(text, name):
    lines = text.split("\n")
    line = bsl.method_line(text, name)
    if line is None:
        raise ValueError(f"нет заголовка метода {name}")
    start = line - 1
    end = next(i for i in range(start, len(lines)) if bsl.end_re().match(lines[i]))
    return "\n".join(lines[:start] + lines[end + 1:])


def _on_open(form_text):
    """Имя обработчика события формы ПриОткрытии (OnOpen) в описании формы (выгрузка Конфигуратора или EDT)."""
    m = (re.search(r'<Event name="OnOpen"[^>]*>\s*([^<\s]+)\s*</Event>', form_text or "")
         or re.search(r"<handlers>\s*<event>OnOpen</event>\s*<name>([^<]+)</name>", form_text or ""))
    return m.group(1).strip() if m else None


def _prepend_body(text, name, code):
    """code — в начало тела метода name (после строки, где закрывается список параметров); нет метода — text."""
    m = bsl.header_re(name).search(text or "") if name else None
    if not m:
        return text
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    eol = text.find("\n", i)
    return text if eol < 0 else text[:eol + 1] + code + text[eol + 1:]


_SERVER = re.compile(r"(?:^|,\s*)сервер(?:\s*[,.]|\s*$)", re.I)


def _newer_api(api):
    """Цели дефектов «новее режима совместимости» по справке платформы проверки: [(id, заголовок, строка справки,
    уровень находки)]. Метод — глобального контекста, доступный на сервере; конструктор — без параметров.
    api — (индекс, режим совместимости[, warns_newer]): предупреждение, которое настройка скрывает, —
    без дефекта."""
    if not api or not api[0] or not api[1]:
        return []
    ix, compat = api[:2]
    warns = api[2] if len(api) > 2 else (lambda since: True)
    key = version_key(compat)
    if not key:
        return []
    line = version_str(compat).split(".")[:2]
    methods = [r for r in ix.db.execute(
        "SELECT * FROM entries WHERE kind = 'method' AND owner = ? AND since_key > ? ORDER BY since_key, name",
        (GLOBAL, key)).fetchall() if _SERVER.search(r["availability"] or "")]
    newer_line = [r for r in methods if r["since"].split(".")[:2] != line]
    same_line = [r for r in methods if r["since"].split(".")[:2] == line]
    ctors = [r for r in ix.db.execute(
        "SELECT * FROM entries WHERE kind = 'ctor' AND since_key > ? ORDER BY since_key, name", (key,)).fetchall()
        if re.fullmatch(r"\S+\s+\S+?(\(\))?", (r["syntax"] or "").split("\n")[0].strip())
        and any(_SERVER.search(t["availability"] or "") for t in ix.lookup(r["name"], kind="type"))]
    out = []
    for did, title, rows, severity in (
            ("newer-line-method", "метод более новой линейки платформы, чем режим совместимости", newer_line, "error"),
            ("newer-method", "метод новее режима совместимости той же линейки", same_line, "warning"),
            ("newer-ctor", "конструктор новее режима совместимости", ctors, "warning")):
        if severity == "warning":
            rows = [r for r in rows if warns(r["since"])]
        if rows:
            out.append((did, title, rows[0], severity))
    return out


def _parity_args(repo, cfg, full):
    """Ключи Конфигуратора для паритета: режимы /CheckModules или (full) флаги /CheckConfig, + -Extension."""
    project, env = cfg["project"], cfg["environment"]
    parent = project.get("parent")
    config_root = (Path(repo) / parent) if parent else Path(repo) / project["source"]
    ordinary, modes = env.get("ordinaryApplication", False), cfg.get("designerCheck", {}).get("modes")
    args = (designer_check.config_check_flags(config_root, ordinary, modes) if full
            else modes or designer_check.default_modes(config_root, ordinary))
    return args + (["-Extension", project["extension"]] if project.get("extension") else [])


def parity_modes(repo, cfg):
    """Режимы проверки всех модулей для паритета (+ -Extension для расширения)."""
    return _parity_args(repo, cfg, full=False)


def parity_config_flags(repo, cfg):
    """Флаги полной проверки /CheckConfig для паритета (+ -Extension для расширения)."""
    return _parity_args(repo, cfg, full=True)


def api_target(repo, cfg):
    """(индекс справки платформы проверки, режим совместимости проекта) для дефектов «новее режима» или None."""
    src = cfg["project"]["source"]
    lay = layout.detect(Path(repo) / src) or layout.DESIGNER
    checker, _ = pipeline.api_checker(cfg, _read(repo, "HEAD", f"{src}/{lay.config_file}"), lay)
    return (checker.ix, checker.compat, checker.warns_newer) if checker else None


def _mark(v):
    return "—" if v == "n/a" else "не проверяет" if v is None else ("да" if v else "нет")


def run(repo, cfg, run_dir, parity=False, session=True, log=print):
    """Каталог контрольных дефектов: коммит вне веток, gate, оценка (+ паритет с Конфигуратором).
    Возвращает (строки [(Defect, пойман, шаги)], паритет {id: True|False|None}, таблица Markdown).
    С паритетом пишет и vs-designer.md (document) в каталог прогона."""

    src = cfg["project"]["source"]
    parent = config.parent_dir(repo, cfg)
    parent = str(parent) if parent else None
    base = gitdiff.rev(repo, "HEAD")
    api = api_target(repo, cfg)
    packs, sess, skipped = build_defects(repo, src, parent=parent, cache_dir=Path(run_dir) / "cache", api=api)
    rows, hits, ext_hits, cfg_hits = [], {}, {}, {}
    times = {"gate": [], "modules": [], "config": []}      # секунды по пакетам: ОТК, /CheckModules, /CheckConfig
    for n, (defects, changes) in enumerate(packs, 1):
        sha = make_commit(repo, base, changes, f"otk: контрольные дефекты, пакет {n}")
        log(f"пакет {n}/{len(packs)}: дефектов {len(defects)}, коммит {sha[:10]} (вне веток)")
        rows += _run_pack(repo, cfg, run_dir, base, sha, defects, parity, hits, log, ext_hits, cfg_hits, times)
    if sess and session:
        sdefects, schanges = sess
        sha2 = make_commit(repo, base, schanges, "otk: дефект модуля сеанса")
        log(f"модуль сеанса: коммит {sha2[:10]}")
        rows += evaluate(sdefects, pipeline.gate_run(repo, cfg, run_dir, pipeline.GateOptions(base, sha2), log=log))
    two = bool(ext_hits) and not cfg["project"].get("extension")
    head = ("| Дефект | Ожидаемый шаг | Пойман | Шаги | /CheckModules |" + (" В расширении |" if two else "")
            + (" /CheckConfig |" if parity else ""))
    lines = [head, "|" + " --- |" * (head.count("|") - 1)]
    for d, ok, h in rows:
        lines.append(f"| {d.title} | {', '.join(d.expect)} | {'да' if ok else '**НЕТ**'} | {', '.join(h) or '—'} | "
                     f"{_mark(hits.get(d.id, 'n/a'))} |" + (f" {_mark(ext_hits.get(d.id, 'n/a'))} |" if two else "")
                     + (f" {_mark(cfg_hits.get(d.id, 'n/a'))} |" if parity else ""))
    if skipped:
        lines += [""] + [f"Контроль не построен: `{i}` — {why}" for i, why in skipped]
    table = "\n".join(lines)
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "controls.md").write_text(table + "\n", encoding="utf-8")
    (Path(run_dir) / "controls.json").write_text(json.dumps(
        [{"id": d.id, "title": d.title, "files": d.files, "expect": d.expect, "severity": d.severity, "caught": ok,
          "steps": h, "designer": hits.get(d.id, "n/a"), "designerExtension": ext_hits.get(d.id, "n/a"),
          "designerConfig": cfg_hits.get(d.id, "n/a")}
         for d, ok, h in rows], ensure_ascii=False, indent=1), encoding="utf-8")
    if parity:
        (Path(run_dir) / "vs-designer.md").write_text(
            document(rows, hits, ext_hits if two else None, skipped, conditions(repo, cfg, api), cfg_hits, times),
            encoding="utf-8")
    return rows, hits, table


def conditions(repo, cfg, api=None):
    """Условия прогона для vs-designer.md: конфигурация, версия, платформа, режим совместимости, режимы, коммит."""

    project, env = cfg["project"], cfg["environment"]
    root = config.parent_dir(repo, cfg) or Path(repo) / project["source"]
    lay = layout.detect(Path(root)) or layout.DESIGNER
    text = layout.read_text(Path(root) / lay.config_file) or ""
    name = re.search(r"<Synonym>.*?<(?:v8:)?content>([^<]+)<", text, re.S) or re.search(r"<Name>([^<]+)</Name>", text)
    version = re.search(r"<Version>([^<]+)</Version>", text) or re.search(r"<version>([^<]+)</version>", text)
    configured = env.get("platform")
    bin_dir = Path(configured) if configured else find_platform()
    return {"configuration": name.group(1).strip() if name else "?", "version": version.group(1) if version else "?",
            "extension": project.get("extension"), "platform": platform_version(bin_dir) if bin_dir else "?",
            "compat": api[1] if api else None, "modes": " ".join(parity_modes(repo, cfg)),
            "configFlags": " ".join(parity_config_flags(repo, cfg)),
            "commit": gitdiff.rev(repo, "HEAD")[:10], "date": datetime.date.today().isoformat()}


# почему эталоны расходятся (основная конфигурация и тот же код во временном расширении extcheck)
EXPLAIN = {
    "removed-export": "В расширение заимствуется только изменённый модуль; вызывающие его модули — нет, поэтому "
                      "удалённый метод там не ищется. ОТК находит вызывающих статикой (зависимые модули)",
}
# сноски к строкам: условия, при которых утверждение верно, и чем ОТК закрывает пропуск Конфигуратора
NOTES = {
    "handler-renamed": "`/CheckModules` не сверяет обработчики событий с описанием формы — это делает "
                       "`/CheckConfig -HandlersExistence`",
    "query-field": "текст запроса для компилятора — строка; ОТК разбирает запрос платформой (COM или тонкий клиент)",
    "extra-arg": "число параметров экспортного метода общего модуля при вызове через имя модуля компилятор "
                 "не проверяет",
    "form-open-error": "ошибка выполнения, а не компиляции: видна только при открытии формы (дымовой тест)",
    "form-hang": "ошибка выполнения: форма не закрылась за отведённое время (`[smoke] timeout`)",
    "newer-line-method": "режим совместимости ниже линейки платформы: Конфигуратор не определяет метод "
                         "более новой линейки",
    "newer-method": "метод своей линейки новее режима совместимости Конфигуратор пропускает; на платформе ниже "
                    "версии появления модуль не скомпилируется — ОТК предупреждает",
    "newer-ctor": "конструктор новее режима совместимости Конфигуратор пропускает в любой линейке — ОТК "
                  "предупреждает",
    "session": "дефект модуля сеанса — отдельный прогон без проверки Конфигуратором",
}


def _avg(xs):
    return f"{sum(xs) / len(xs):.0f}" if xs else "—"


def document(rows, hits, ext_hits, skipped, cond, cfg_hits=None, times=None):
    """docs/vs-designer.md: таблица «ОТК и проверка Конфигуратором» из прогона controls --parity.
    ext_hits — второй эталон синтаксического контроля (тот же код во временном расширении) или None
    (проект-расширение); cfg_hits — полная проверка /CheckConfig; times — секунды по пакетам (gate, modules,
    config) для строки времени."""
    c = cond
    full = cfg_hits is not None
    out = ["# ОТК и проверка Конфигуратором: контрольные дефекты", "",
           "Таблица сгенерирована командой `otk controls --parity --doc docs/vs-designer.md`; руками не править. "
           "Каждый дефект вносится в отдельный модуль коммитом вне веток, затем — `otk gate` по этому коммиту и "
           "проверка Конфигуратором в той же копии ИБ: синтаксический контроль всех модулей `/CheckModules`"
           + (" и полная проверка конфигурации `/CheckConfig`." if full else "."), "",
           f"- Конфигурация: {c['configuration']} {c['version']}"
           + (f", расширение `{c['extension']}`" if c.get("extension") else ""),
           f"- Платформа: {c['platform']}; режим совместимости: {c['compat'] or 'не задан'}",
           f"- Режимы `/CheckModules`: `{c['modes']}`"]
    if full and c.get("configFlags"):
        out.append(f"- Флаги `/CheckConfig`: `{c['configFlags']}`")
    out += [f"- Коммит: `{c['commit']}`, дата прогона: {c['date']}", "",
            "«Синтаксический контроль» — `/CheckModules` основной конфигурации" if ext_hits is not None else
            "«Синтаксический контроль» — `/CheckModules` расширения (`-Extension`)"]
    if ext_hits is not None:
        out[-1] += ("; «В расширении» — тот же код, заимствованный во временное расширение, как это делает шаг "
                    "`extcheck` ОТК (`/CheckModules … -Extension otk_check`)")
    if full:
        out[-1] += ("; «Полная проверка» — `/CheckConfig` со всеми применимыми к конфигурации флагами "
                    "(неиспользуемые процедуры и пустые обработчики на вердикт не влияют: он — по маркеру дефекта)")
    out[-1] += (". «да» — ошибка с маркером дефекта в выводе по его модулю, «нет» — её нет, «—» — не "
                "проверялось. «ОТК» — пойман ожидаемым шагом; шаги — все шаги ОТК с находкой в файлах дефекта.")
    # ключи запуска в заголовке — какая именно проверка (полные режимы и флаги — в условиях выше)
    ext = " -Extension" if ext_hits is None else ""
    head = (["Дефект", "ОТК", "Шаги ОТК", f"Конфигуратор: синтаксический контроль (`/CheckModules{ext}`)"]
            + (["В расширении (`/CheckModules -Extension otk_check`)"] if ext_hits is not None else [])
            + ([f"Конфигуратор: полная проверка (`/CheckConfig{ext}`)"] if full else []))
    out += ["", "| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    notes = []
    for d, ok, steps in rows:
        title = d.title
        if d.id in NOTES:
            notes.append(NOTES[d.id])
            title += f" [^{len(notes)}]"
        cells = [title, ("да" if ok else "**НЕТ**") + (" (предупреждение)" if d.severity == "warning" else ""),
                 ", ".join(f"`{s}`" for s in steps) or "—", _mark(hits.get(d.id, "n/a"))]
        if ext_hits is not None:
            cells.append(_mark(ext_hits.get(d.id, "n/a")))
        if full:
            cells.append(_mark(cfg_hits.get(d.id, "n/a")))
        out.append("| " + " | ".join(cells) + " |")
    if times and times.get("gate"):
        notes.append(f"среднее по пакетам дефектов ({len(times['gate'])}); ОТК — весь `otk gate` пакета (загрузка "
                     "изменений в копию ИБ, статика, пробы, extcheck, дымовой тест), Конфигуратор — проверка всей "
                     "конфигурации в уже загруженной копии")
        cells = [f"**время на пакет, с** [^{len(notes)}]", _avg(times["gate"]), "—", _avg(times.get("modules"))]
        if ext_hits is not None:
            cells.append("—")
        if full:
            cells.append(_avg(times.get("config")))
        out.append("| " + " | ".join(cells) + " |")
    if ext_hits is not None:
        diff = [(d, hits[d.id], ext_hits[d.id]) for d, _, _ in rows
                if d.id in hits and d.id in ext_hits and hits[d.id] != ext_hits[d.id]]
        out += ["", "## Расхождение эталонов", ""]
        out += ([f"- {d.title}: основная — {_mark(a)}, в расширении — {_mark(b)}. "
                 f"{EXPLAIN.get(d.id, 'Причина не разобрана — задача в pet/otk-1c.')}" for d, a, b in diff]
                or ["Нет: на всех дефектах проверка основной конфигурации и того же кода во временном расширении "
                    "дали одно и то же."])
    if skipped:
        out += ["", "## Не построены", ""] + [f"- `{i}` — {why}" for i, why in skipped]
    if notes:
        out += [""] + [f"[^{n}]: {t}" for n, t in enumerate(notes, 1)]
    return "\n".join(out) + "\n"


def _run_pack(repo, cfg, run_dir, base, sha, defects, parity, hits, log, ext_hits=None, cfg_hits=None, times=None):
    """gate по коммиту пакета, оценка, паритет (пока база в состоянии пакета), повтор без виновника сеанса.
    ext_hits — тот же код во временном расширении extcheck (его вывод в этом пакете); cfg_hits — полная проверка
    /CheckConfig; times — секунды пакета {gate, modules, config}."""

    ext_out = Path(run_dir) / "logs" / f"{extended_check.NAME}-after.out"
    ext_out.unlink(missing_ok=True)          # вывод прошлого пакета не должен попасть в этот
    t = time.time()
    summary = pipeline.gate_run(repo, cfg, run_dir, pipeline.GateOptions(base, sha), log=log)
    if times is not None:
        times["gate"].append(round(time.time() - t, 1))
    rows = evaluate(defects, summary)
    if ext_hits is not None and ext_out.exists():
        ext_hits.update(designer_hits(defects, decode(ext_out.read_bytes())))
    if parity:
        modes = parity_modes(repo, cfg)
        log(f"паритет: Конфигуратор {' '.join(modes)}")
        designer = Platform(cfg["environment"], run_dir).designer
        # журнал на пакет (по коммиту): вывод прошлого пакета остаётся доказательством его вердиктов
        rc, sec, out = designer("/CheckModules", *modes, label=f"parity-{sha[:10]}", timeout=6 * 3600)
        hits.update(designer_hits(defects, out))
        log(f"  Конфигуратор: {sec} c, rc={rc}")
        flags = parity_config_flags(repo, cfg)
        log(f"паритет: Конфигуратор /CheckConfig {' '.join(flags)}")
        rc2, sec2, out2 = designer("/CheckConfig", *flags, label=f"parity-config-{sha[:10]}", timeout=6 * 3600)
        if cfg_hits is not None:
            cfg_hits.update(designer_hits(defects, out2))
        if times is not None:
            times["modules"].append(sec)
            times["config"].append(sec2)
        log(f"  /CheckConfig: {sec2} c, rc={rc2}")
    culprits = session_culprits(summary)
    missed = [d for d, ok, _ in rows if not ok]
    if culprits and missed:
        # дефект, ломающий старт сеанса, скрывает пробы остальных — пропущенные перепроверяются без него
        blocked = {p for d in defects for p in d.changes
                   if any(p.endswith(c.split("/", 2)[-1]) or c.endswith(p) for c in culprits)}
        retry = [d for d in missed if not (set(d.changes) & blocked)]
        log(f"сеанс не стартовал из-за {', '.join(sorted(culprits))}; повтор без них: {', '.join(d.id for d in retry)}")
        if retry:
            changes2 = {}
            for d in retry:
                changes2.update(d.changes)
            sha_r = make_commit(repo, base, changes2, "otk: контрольные дефекты, повтор")
            rerun = pipeline.gate_run(repo, cfg, run_dir, pipeline.GateOptions(base, sha_r), log=log)
            again = {d.id: (ok, h) for d, ok, h in evaluate(retry, rerun)}
            rows = [(d, *again[d.id]) if d.id in again else (d, ok, h) for d, ok, h in rows]
    return rows


def step_key(source):
    return f"static:{source['code']}" if source["step"] == "static" else source["step"]


def evaluate(defects, summary):
    """[(defect, пойман, шаги)] по находкам сводки в файлах дефекта; шаги — все источники находки."""
    rows = []
    for d in defects:
        levels = {"error", d.severity}
        hits = sorted({step_key(s) for f in summary["findings"] for s in f.get("sources") or [f]
                       if f.get("severity") in levels and f.get("file") and
                       any(f["file"].endswith(x) for x in d.files)})
        ok = any(h == e or h.startswith(e) for h in hits for e in d.expect)
        rows.append((d, ok, hits))
    return rows


def designer_hits(defects, output):
    """Ловит ли полная проверка Конфигуратором: маркер дефекта в строке с его модулем."""
    lines = output.splitlines()
    res = {}
    for d in defects:
        if not d.marker:
            res[d.id] = None
            continue
        owners = {d.owner} if d.owner else set()
        if d.id == "removed-export":
            # ловится по вызывающим (d.files), а не по вызову внутри своего модуля: «.<имя объекта>.» в строке
            owners = {f".{f.split('/')[1]}." for f in d.files if f.count("/") >= 2}
        res[d.id] = any(re.search(d.marker, ln) and (not owners or any(o and o in ln for o in owners))
                        for ln in lines)
    return res


def session_culprits(summary):
    """Файлы, из-за которых не стартовал сеанс (COM или YAxUnit) — по находкам session/*."""
    return {f["file"] for f in summary["findings"] if f.get("file") and f["rule"].startswith("session/")}


# --- controls --analyzers: кто из анализаторов BSL что ловит ---

PREFIX = {"bslls": "bslls", "bsla": "bsl-analyzer"}


def analyzer_hits(defects, static):
    """{id дефекта: {"bslls": {правило: уровень}, "bsl-analyzer": {...}, "otk": [проверки]}} по находкам static.json
    в файлах дефекта. Находка обоих анализаторов (both) — у каждого из её списка analyzers; остальная статика —
    имя проверки."""
    res = {}
    for d in defects:
        hit = {"bslls": {}, "bsl-analyzer": {}, "otk": set()}
        for f in static.get("findings", []):
            if not (f.get("module") and any(f["module"].endswith(x) for x in d.files)):
                continue
            prefix, _, rule = f["check"].partition(":")
            if prefix not in PREFIX:
                hit["otk"].add(f["check"])
                continue
            for name in f.get("analyzers") or [PREFIX[prefix]]:
                if hit[name].get(rule) != "error":
                    hit[name][rule] = f["severity"]
        hit["otk"] = sorted(hit["otk"])
        res[d.id] = hit
    return res


def analyzer_status(static):
    """{анализатор: None — работал | причина, почему не проверено} по analyzerInfo static.json."""
    info = static.get("analyzerInfo") or {}
    if info.get("off"):
        return {name: "проверка bslls выключена ([checks])" for name in PREFIX.values()}
    out = {}
    for name in PREFIX.values():
        own = info.get(name) or {}
        out[name] = own.get("skipped") or own.get("error") or (info.get("skipped") if not own else None)
    return out


def tool_version(exe):
    """Номер версии из `<exe> --version` или «?»."""
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    m = re.search(r"\d+(?:\.\d+)+", (r.stdout or r.stderr).decode("utf-8", "replace"))
    return m.group(0) if m else "?"


def run_analyzers(repo, cfg, run_dir, session=True, log=print):
    """Каталог контрольных дефектов (с дефектами семантики анализаторов) статикой ОТК с обоими анализаторами
    BSL, без проб в базе. Возвращает (строки [(Defect, {анализатор: правила, otk: проверки})], статус анализаторов
    {имя: None | причина}, таблица Markdown); пишет analyzers.md, analyzers.json и vs-analyzers.md в каталог
    прогона."""

    cfg = copy.deepcopy(cfg)
    cfg["environment"]["analyzer"] = "both"
    src = cfg["project"]["source"]
    parent = config.parent_dir(repo, cfg)
    parent = str(parent) if parent else None
    base = gitdiff.rev(repo, "HEAD")
    packs, sess, skipped = build_defects(repo, src, parent=parent, cache_dir=Path(run_dir) / "cache",
                                         api=api_target(repo, cfg), analyzers=True)
    if sess and session:
        packs = packs + [sess]
    rows, status = [], {}
    for n, (defects, changes) in enumerate(packs, 1):
        sha = make_commit(repo, base, changes, f"otk: контрольные дефекты анализаторов, пакет {n}")
        log(f"пакет {n}/{len(packs)}: дефектов {len(defects)}, коммит {sha[:10]} (вне веток)")
        pipeline.gate_run(repo, cfg, run_dir, pipeline.GateOptions(base, sha, static_only=True), log=log)
        static = json.loads((Path(run_dir) / "static.json").read_text(encoding="utf-8"))
        hits = analyzer_hits(defects, static)
        rows += [(d, hits[d.id]) for d in defects]
        for name, why in analyzer_status(static).items():     # не проверено хоть в одном пакете — причина
            status[name] = status.get(name) or why
    env = cfg["environment"]
    exes = {name: analyzers_mod.find(name, env) for name in PREFIX.values()}
    cond = {**conditions(repo, cfg), "analyzers": {
        name: (tool_version(exe) if exe else None) for name, exe in exes.items()}}
    doc = analyzers_document(rows, status, skipped, cond)
    table = "\n".join(ln for ln in doc.splitlines() if ln.startswith("| "))
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "analyzers.md").write_text(table + "\n", encoding="utf-8")
    (Path(run_dir) / "vs-analyzers.md").write_text(doc, encoding="utf-8")
    (Path(run_dir) / "analyzers.json").write_text(json.dumps(
        [{"id": d.id, "title": d.title, "files": d.files, "expect": d.expect, **h} for d, h in rows],
        ensure_ascii=False, indent=1), encoding="utf-8")
    return rows, status, table


# сноски к строкам таблицы анализаторов: почему ячейка такая
ANALYZER_NOTES = {
    "vopros-object": "`ParseError` BSL LS — ложный: код дописан после `#КонецЕсли`, а в ветке `#Иначе` стоит оператор "
                     "тела модуля; BSL LS не учитывает препроцессор, платформа для сервера эту ветку не компилирует",
    "object-missing-proc": "`ParseError` BSL LS — ложный, как у `Вопрос()` в модуле объекта",
    "no-return": "`AllFunctionPathMustHaveReturn` BSL LS с параметрами по умолчанию не сообщает о `Если` без `Иначе`",
    "type-method": "ловит только исполнение: компилятор и анализаторы тип значения переменной здесь не выводят",
}


def _rules(rules, why):
    if why:
        return "не проверено"
    return ", ".join(f"`{r}` ({s})" for r, s in sorted(rules.items())) or "нет"


def analyzers_document(rows, status, skipped, cond):
    """docs/vs-analyzers.md: таблица «ОТК и анализаторы BSL» из прогона controls --analyzers."""
    c = cond
    versions = "; ".join(f"{analyzers_title(n)} — {v or 'не найден'}" for n, v in c["analyzers"].items())
    out = ["# ОТК и анализаторы BSL: контрольные дефекты", "",
           "Таблица сгенерирована командой `otk controls --analyzers --doc docs/vs-analyzers.md`; руками не править. "
           "Каждый дефект вносится в отдельный модуль коммитом вне веток, затем — статика `otk gate --static-only` "
           "по этому коммиту с обоими анализаторами (`environment.analyzer = \"both\"`). Анализатор находит "
           "только новые замечания — по правке, относительно базовой редакции.", "",
           f"- Конфигурация: {c['configuration']} {c['version']}"
           + (f", расширение `{c['extension']}`" if c.get("extension") else ""),
           f"- Анализаторы: {versions}",
           f"- Коммит: `{c['commit']}`, дата прогона: {c['date']}", "",
           "Ячейки анализаторов — правила новых замечаний в файлах дефекта и уровень находки ОТК (`error` — "
           "вердикт «не пропускать», `warning` — предупреждение), «нет» — замечаний нет, «не проверено» — анализатор "
           "не работал. «Статика ОТК» — остальные проверки статики с находкой в файлах дефекта. «Шаг ОТК» — "
           "ожидаемый шаг дефекта в `otk controls` (справочно: здесь пробы в базе не выполняются).", "",
           "| Дефект | Шаг ОТК | BSL LS | bsl-analyzer | Статика ОТК |", "| --- | --- | --- | --- | --- |"]
    notes = []
    for d, h in rows:
        expect = "анализаторы" if d.expect == ANALYZER_EXPECT else ", ".join(f"`{e}`" for e in d.expect)
        title = d.title
        if d.id in ANALYZER_NOTES:
            notes.append(ANALYZER_NOTES[d.id])
            title += f" [^{len(notes)}]"
        out.append(f"| {title} | {expect} | {_rules(h['bslls'], status.get('bslls'))} | "
                   f"{_rules(h['bsl-analyzer'], status.get('bsl-analyzer'))} | "
                   + (", ".join(f"`{x}`" for x in h["otk"]) or "—") + " |")
    caught = {n: sum(1 for _, h in rows if h[n]) for n in PREFIX.values()}
    only = [f"- {d.title}: только {analyzers_title(n)} ({', '.join(f'`{r}`' for r in sorted(h[n]))})"
            for d, h in rows for n, o in (("bslls", "bsl-analyzer"), ("bsl-analyzer", "bslls"))
            if h[n] and not h[o] and not status.get(o)]
    out += ["", "## Расхождения анализаторов", ""]
    out += only or ["Нет: все дефекты, пойманные одним анализатором, поймал и другой."]
    nobody = [d for d, h in rows if not (h["bslls"] or h["bsl-analyzer"] or h["otk"])]
    out += ["", "## Вывод", "",
            f"Из {len(rows)} дефектов BSL LS поймал {caught['bslls']}, bsl-analyzer — {caught['bsl-analyzer']}, "
            f"статика ОТК без анализаторов — {sum(1 for _, h in rows if h['otk'])}"
            + (f"; не поймала статика вовсе — {len(nobody)} (их ловят пробы в базе, extcheck и дымовой тест — "
               "`docs/vs-designer.md`)" if nobody else "") + ". "
            "Анализаторы — источник находок внутри ОТК (проверка `analyzers`), а не альтернатива ему: ни один не "
            "видит ошибок компиляции в контексте платформы, запросов в базе, открытия форм и сценарных тестов, "
            "а замечания до правки ОТК отсекает сравнением редакций."]
    for i, why in sorted(status.items()):
        if why:
            out.append(f"\n{analyzers_title(i)} не проверено: {why}")
    if skipped:
        out += ["", "## Не построены", ""] + [f"- `{i}` — {why}" for i, why in skipped]
    if notes:
        out += [""] + [f"[^{n}]: {t}" for n, t in enumerate(notes, 1)]
    return "\n".join(out) + "\n"


def analyzers_title(name):
    return TITLES[name]
