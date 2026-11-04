"""Статические проверки изменённых строк по индексу метаданных (спецификация static-checks).

Находка: {"check", "severity" (error|warning), "module", "line", "message"}. error — только однозначные
случаи (нет объекта, значения перечисления, предопределённого, формы, экспортного метода, неверное
число аргументов, нет обработчика); warning — вероятные (неизвестный метод менеджера).
Неверное число аргументов в вызове общего модуля компилируется, но при исполнении строки всегда даёт
«Слишком много / Недостаточно фактических параметров» (проверено на 8.5) — Конфигуратор этого не ловит.
"""
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import lang
from ..sources import bsl, contexts, layout
from .platform_api import platform_names

_N = r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*"
EMPTY_REF = {"пустаяссылка", "emptyref"}


def local_names(parsed):
    """Имена, перекрывающие глобальные: параметры методов, Перем, цели присваивания."""
    names = {p.name.lower() for m in parsed.methods for p in m.params}
    for decl in re.findall(r"(?im)^[ \t]*(?:Перем|Var)[ \t]+([^;]+);", parsed.sanitized):
        names |= {n.lower() for n in re.findall(_N, decl) if n.lower() not in ("экспорт", "export")}
    names |= {n.lower() for n in re.findall(rf"(?m)^[ \t]*({_N})\s*=(?!=)", parsed.sanitized)}
    return names


_ASSIGNED = re.compile(rf"(?<![\wА-Яа-яЁё.&#~])({_N})\s*=(?!=)")    # «Х» и «=» бывают на разных строках
_EACH = re.compile(rf"(?<![\wА-Яа-яЁё])(?:Каждого|Each)\s+({_N})\s+(?:Из|In)(?![\wА-Яа-яЁё])", re.I)


def module_variables(sanitized):
    """Имена переменных модуля в любом месте текста: Перем, присваивание (`Х =`, в том числе `Для Х =` и
    сравнение — с запасом), `Для Каждого Х Из`. Для правила «Переменная не определена»: лишнее имя в списке
    лишь пропустит ошибку, недостающее — даст ложную."""
    names = {n.lower() for n in _ASSIGNED.findall(sanitized)} | {n.lower() for n in _EACH.findall(sanitized)}
    for decl in re.findall(r"(?im)^[ \t]*(?:Перем|Var)[ \t]+([^;]+);", sanitized):
        names |= {n.lower() for n in re.findall(_N, decl)}
    return names


def _after_dot(text, pos):
    """Перед позицией (через пробелы и переносы) стоит точка — это член другого выражения."""
    i = pos - 1
    while i >= 0 and text[i] in " \t\r\n":
        i -= 1
    return i >= 0 and text[i] == "."


def _suggest(name, candidates):
    close = difflib.get_close_matches(name.lower(), list(candidates), n=3, cutoff=0.75)
    return f" (похоже: {', '.join(close)})" if close else ""


class Checker:
    def __init__(self, index, variant="Russian", names=None):
        """names — словарь имён платформы (platform_api.platform_names): стандартные формы и методы менеджеров."""
        self.ix = index
        names_ = names or platform_names()
        self.standard_forms = names_["forms"]
        self.manager_prefixes = tuple(names_["managerMethods"])
        lang.dictionary(variant)              # вариант поддержан; ссылки в коде — на любом языке
        names = lang.metadata_names()
        self.by_manager, self.by_single = names["managers"], names["singles"]
        self.form_words = names["forms"]
        managers = "|".join(sorted(self.by_manager, key=len, reverse=True))
        self.re_manager = re.compile(
            rf"(?<![\wА-Яа-яЁё.])({managers})\s*\.\s*({_N})(\s*\()?(?:\s*\.\s*({_N})(\s*\()?)?", re.I)
        self.re_call = re.compile(rf"(?<![\wА-Яа-яЁё.])({_N})\s*\.\s*({_N})\s*\(", re.I)
        self.re_form_call = re.compile(
            r"(?<![\wА-Яа-яЁё.])(ПолучитьФорму|ОткрытьФорму|ОткрытьФормуМодально|GetForm|OpenForm)\s*\(\s*\"", re.I)
        self.re_predef_call = re.compile(
            r"(?<![\wА-Яа-яЁё.])(ПредопределенноеЗначение|PredefinedValue)\s*\(\s*\"", re.I)
        self.re_query_value = re.compile(rf"(?:ЗНАЧЕНИЕ|VALUE)\s*\(\s*({_N}(?:\.{_N}){{1,3}})\s*\)", re.I)

    def objects(self, collection):
        return self.ix["objects"].get(collection, {})

    # --- ссылки через точку ---------------------------------------------------------------
    def check_manager_refs(self, mod, text, lines, emit, locals_=frozenset()):
        for no in lines:
            ln = text[no - 1] if no - 1 < len(text) else ""
            for m in self.re_manager.finditer(ln):
                if m.group(1).lower() in locals_:
                    continue            # локальная переменная с именем менеджера
                if m.group(3):
                    continue            # метод менеджера коллекции: Справочники.ТипВсеСсылки()
                collection = self.by_manager[m.group(1).lower()]
                objs = self.objects(collection)
                obj = objs.get(m.group(2).lower())
                if obj is None:
                    emit("metadata-ref", "error", no, f"Объект не найден: {m.group(1)}.{m.group(2)}"
                         + _suggest(m.group(2), objs))
                    continue
                member, is_call = m.group(4), bool(m.group(5))
                if not member:
                    continue
                if not is_call and m.end() == len(ln.rstrip()):
                    is_call = _next_line_opens(text, no)       # «Менеджер.Метод\n\t(Параметры)» (ERP)
                low = member.lower()
                if collection == "Enums" and not is_call:
                    if low not in {v.lower() for v in obj["values"]} and low not in EMPTY_REF:
                        emit("metadata-ref", "error", no,
                             f"Нет значения перечисления: {m.group(1)}.{obj['name']}.{member}"
                             + _suggest(member, [v.lower() for v in obj["values"]]))
                elif collection in ("Catalogs", "ChartsOfCharacteristicTypes", "ChartsOfAccounts",
                                    "ChartsOfCalculationTypes") and not is_call:
                    if low not in {v.lower() for v in obj["predefined"]}:
                        emit("metadata-ref", "error", no,
                             f"Нет предопределённого элемента: {m.group(1)}.{obj['name']}.{member}"
                             + _suggest(member, [v.lower() for v in obj["predefined"]]))
                elif is_call and low not in obj["managerExports"] and not low.startswith(self.manager_prefixes):
                    emit("manager-method", "warning", no,
                         f"Возможно ошибочный метод менеджера: {m.group(1)}.{obj['name']}.{member}")

    # --- вызовы общих модулей -------------------------------------------------------------
    def check_common_calls(self, mod, text, sanitized, lines, emit, locals_=frozenset()):
        modules = self.ix["commonModules"]
        line_starts = [0]
        for ln in sanitized.split("\n")[:-1]:
            line_starts.append(line_starts[-1] + len(ln) + 1)
        for no in lines:
            if no - 1 >= len(line_starts):
                continue
            start = line_starts[no - 1]
            end = sanitized.find("\n", start)
            end = len(sanitized) if end < 0 else end
            for m in self.re_call.finditer(sanitized, start, end):
                cm = modules.get(m.group(1).lower())
                if cm is None or cm["flags"].get("Global") or not cm.get("source", True):
                    continue
                if m.group(1).lower() in locals_ or _after_dot(sanitized, m.start()):
                    continue
                exp = cm["exports"].get(m.group(2).lower())
                if exp is None:
                    emit("common-method", "error", no, f"Нет экспортного метода: {cm['name']}.{m.group(2)}"
                         + _suggest(m.group(2), cm["exports"]))
                    continue
                args = _count_args(sanitized, m.end())
                if args is not None and args > exp[2]:
                    emit("common-method", "error", no, f"Слишком много параметров: {cm['name']}.{exp[0]} "
                         f"принимает {exp[2]}, передано {args}")
                elif args is not None and args < exp[1]:
                    emit("common-method", "error", no, f"Недостаточно параметров: {cm['name']}.{exp[0]} "
                         f"требует {exp[1]}, передано {args}")

    # --- формы и предопределённые в строках ------------------------------------------------
    def check_strings(self, mod, parsed, lines, emit):
        s = parsed.sanitized
        literal_at = {lit.start: lit for lit in parsed.literals}
        for regex, kind in ((self.re_form_call, "form"), (self.re_predef_call, "predefined")):
            for m in regex.finditer(s):
                lit = literal_at.get(m.end() - 1)
                if lit is None or lit.line not in lines:
                    continue
                if s[lit.end:].lstrip()[:1] == "+":
                    continue            # имя собирается из частей
                if kind == "form":
                    self._check_form_name(lit.value, lit.line, emit)
                else:
                    self._check_value_path(lit.value, lit.line, emit, "ПредопределенноеЗначение")
        # заглушки шаблона запроса: литерал-имя, которое код подставляет (СтрЗаменить(Текст, "ИмяИсточника", …))
        placeholders = {lit.value.strip("&%#").lower() for lit in parsed.literals
                        if re.fullmatch(rf"[&%#]?{_N}", lit.value or "")}
        for lit in parsed.literals:
            if lit.line in lines:
                for m in self.re_query_value.finditer(lit.value):
                    if any(p.lower() in placeholders for p in m.group(1).split(".")[1:]):
                        continue
                    self._check_value_path(m.group(1), lit.line, emit, "ЗНАЧЕНИЕ")

    def _check_form_name(self, value, line, emit):
        parts = value.split(".")
        collection = self.by_single.get(parts[0].lower())
        if not collection or len(parts) < 2:
            return
        obj = self.objects(collection).get(parts[1].lower())
        if obj is None:
            emit("form-name", "error", line, f"Нет объекта для формы: {value}")
            return
        if collection == "CommonForms":
            return
        if len(parts) >= 4 and parts[2].lower() in self.form_words:
            if parts[3].lower() not in {f.lower() for f in obj["forms"]}:
                emit("form-name", "error", line,
                     f"Нет формы: {value}" + _suggest(parts[3], [f.lower() for f in obj["forms"]]))
        elif len(parts) == 3 and parts[2].lower() not in self.standard_forms and \
                parts[2].lower() not in {f.lower() for f in obj["forms"]}:
            emit("form-name", "error", line, f"Нет формы: {value}")

    def _check_value_path(self, value, line, emit, where):
        parts = value.split(".")
        if len(parts) != 3:
            return
        collection = self.by_single.get(parts[0].lower())
        if collection not in ("Enums", "Catalogs", "ChartsOfCharacteristicTypes", "ChartsOfAccounts",
                              "ChartsOfCalculationTypes"):
            return
        obj = self.objects(collection).get(parts[1].lower())
        if obj is None:
            emit("metadata-ref", "error", line, f"{where}: нет объекта {parts[0]}.{parts[1]}")
            return
        member = parts[2].lower()
        if member in EMPTY_REF:
            return
        pool = obj["values"] if collection == "Enums" else obj["predefined"]
        if member not in {v.lower() for v in pool}:
            emit("metadata-ref", "error", line, f"{where}: нет {value}" + _suggest(parts[2], [v.lower() for v in pool]))


def _next_line_opens(text, no):
    """Следующая непустая строка начинается со скобки: вызов, перенесённый на новую строку."""
    for ln in text[no:no + 3]:
        if ln.strip():
            return ln.lstrip().startswith("(")
    return False


def _count_args(sanitized, open_paren):
    depth, i, commas, content = 1, open_paren, 0, False
    while i < len(sanitized) and depth:
        ch = sanitized[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth == 0:
                break
        elif ch == "," and depth == 1:
            commas += 1
        elif not ch.isspace():
            content = True
        i += 1
    if depth:
        return None
    return commas + 1 if content or commas else 0


def _form_lay(form_xml, lay):
    """Формат содержимого формы: заданный lay или по тексту (Form.form EDT — пространство имён form)."""
    return lay or layout.form_layout(form_xml)


def form_attributes(form_xml, lay=None):
    """Реквизиты и параметры формы (Form.xml или Form.form): перекрывают глобальные имена в модуле формы."""
    return {n.lower() for n in _form_lay(form_xml, lay).form_attributes(form_xml)}


def form_handlers(form_xml, lay=None):
    """Имена процедур-обработчиков из Form.xml / Form.form (события формы и элементов)."""
    return sorted(set(_form_lay(form_xml, lay).form_handlers(form_xml)))


def changed_lines(entry, parsed):
    """Строки изменённых методов модуля (и тела модуля)."""
    names = {x["name"] for x in entry["changedMethods"]}
    lines = set()
    for m in parsed.methods:
        if m.name in names:
            lines.update(range(m.start, m.end + 1))
    if any(x.get("kind") == "body" for x in entry["changedMethods"]):
        lines.update(parsed.code_lines)
    return lines


@dataclass
class ModuleContext:
    """Модуль плана для правил статики: разобранный текст, изменённые строки, локальные имена, emit(check,
    severity, line, message) — находка. Общее для прогона: индекс, Checker, api (методы платформы или None),
    read_head/read_parent, формат исходников lay, экспорты глобальных модулей."""
    entry: dict
    parsed: object
    lines: set
    san_lines: list
    locals: set
    emit: Callable
    index: dict
    checker: Checker
    read_head: Callable
    lay: object
    api: object = None
    read_parent: Callable | None = None
    global_exports: frozenset = frozenset()


def manager_refs(m):
    m.checker.check_manager_refs(m.entry, m.san_lines, sorted(m.lines), m.emit, m.locals)


def common_calls(m):
    m.checker.check_common_calls(m.entry, m.san_lines, m.parsed.sanitized, sorted(m.lines), m.emit, m.locals)


def strings(m):
    m.checker.check_strings(m.entry, m.parsed, m.lines, m.emit)


def platform_api(m):
    """Методы и типы платформы (проверка platform-api): только при справке платформы."""
    if m.api is None:
        return
    known = {n.lower() for n in bsl.declared(m.parsed.sanitized)} | m.global_exports | m.locals
    # имена перед точкой, известные без справки: переменные модуля, общие модули, менеджеры (Справочники),
    # экспортные переменные модулей приложения
    variables = (module_variables(m.parsed.sanitized) | set(m.index["commonModules"]) | set(m.checker.by_manager)
                 | set(m.index.get("globalVars", ())))
    if m.entry["kind"] == "ManagerModule":
        # в модуле менеджера предопределённые элементы доступны без менеджера: «РФ.ПолучитьОбъект()»
        a = m.lay.parse(m.entry["path"])
        obj = m.index["objects"].get(a.collection, {}).get(a.name.lower(), {})
        variables |= {n.lower() for n in obj.get("predefined", [])}
    base =m.read_parent(m.entry["path"]) if m.read_parent else None
    if base:
        base_text = bsl.lex(base)[0]
        known |= {n.lower() for n in bsl.declared(base_text)}
        variables |= module_variables(base_text)
    m.api.check(m.entry["kind"], m.san_lines, sorted(m.lines), m.emit, known, m.parsed.conditions,
                contexts.line_contexts(m.entry, m.parsed), variables=variables)


def missing_form_handlers(m):
    """Обработчики из Form.xml есть в модуле формы."""
    if m.entry["kind"] != "FormModule" or not m.entry.get("loadWith"):
        return
    procs = {x.name.lower() for x in m.parsed.methods}
    for handler in form_handlers(m.read_head(m.entry["loadWith"][0]), m.lay):
        if handler.lower() not in procs:
            m.emit("handler", "error", 1, f"Обработчик из Form.xml не найден в модуле формы: {handler}")


# правила по модулю (порядок — порядок находок) и по плану целиком; новое правило — функция и запись здесь
RULES = (manager_refs, common_calls, strings, platform_api, missing_form_handlers)


def module_locals(entry, parsed, index, read_head, lay):
    """Имена, перекрывающие глобальные в модуле: локальные, реквизиты формы, реквизиты и табличные части объекта."""
    names = local_names(parsed)
    if entry["kind"] == "FormModule" and entry.get("loadWith"):
        names |= form_attributes(read_head(entry["loadWith"][0]), lay)
    if entry["kind"] in ("ObjectModule", "RecordSetModule"):
        # реквизиты и табличные части объекта перекрывают глобальные имена (табличная часть «Пользователи»
        # в модуле объекта — не общий модуль «Пользователи»)
        a = lay.parse(entry["path"])
        obj = index["objects"].get(a.collection, {}).get(a.name.lower(), {})
        names |= {n.lower() for n in obj.get("fields", [])} | {n.lower() for n in obj.get("tabular", {})}
    return names


def run(repo, plan, index, read_head, api=None, read_parent=None, lay=layout.DESIGNER, metadata=True, names=None):
    """Статические проверки плана: правила RULES по каждому модулю, затем PLAN_RULES. read_head(rel) — текст
    файла выгрузки в проверяемой редакции; api — apicheck.ApiChecker (методы и типы платформы) или None;
    names — словарь имён платформы, когда api нет (иначе — api.names);
    read_parent(rel) — модуль основной конфигурации с тем же путём (у расширения его методы вызываются без
    точки); lay — формат исходников. metadata=False ([checks] metadata = "off") — только platform_api: она
    решается своим режимом."""
    rules = RULES if metadata else (platform_api,)
    if not metadata and api is None:
        return []
    checker = Checker(index, plan["scriptVariant"], getattr(api, "names", None) or names)
    global_exports = frozenset(k.lower() for k in index["globalExports"])
    findings = []
    for entry in plan["modules"]:
        text = read_head(entry["path"])
        if text is None:
            continue
        parsed = bsl.parse(text)

        def emit(check, severity, line, message, _p=entry["path"]):
            findings.append({"check": check, "severity": severity, "module": _p, "line": line, "message": message})

        m = ModuleContext(entry, parsed, changed_lines(entry, parsed), parsed.sanitized.split("\n"),
                          module_locals(entry, parsed, index, read_head, lay), emit, index, checker, read_head, lay,
                          api, read_parent, global_exports)
        for rule in rules:
            rule(m)
    for rule in PLAN_RULES if metadata else ():
        findings.extend(rule(plan, index, lay))
    return findings


def check_handler_refs(plan, index, lay=layout.DESIGNER):
    """Подписки и регламентные задания, ссылающиеся на удалённые/неэкспортные методы общих модулей."""
    findings = []
    touched = {m["owner"].split(".", 1)[1].lower() for m in plan["modules"] if m["kind"] == "CommonModule"}
    touched |= {m["owner"].split(".", 1)[1].lower() for m in plan["removedModules"] if m["kind"] == "CommonModule"}
    changed_xml = {x["path"].lower() for x in plan["metadata"]}
    for h in index["handlers"]:
        parts = h["ref"].split(".")
        if len(parts) != 3 or parts[0] != "CommonModule":
            continue
        module, method = parts[1].lower(), parts[2].lower()
        xml_path = lay.object_file(h["kind"], h["object"])
        if module not in touched and xml_path.lower() not in changed_xml:
            continue
        cm = index["commonModules"].get(module)
        if cm is not None and not cm.get("source", True):
            continue                    # модуль без исходного текста (Module.bin): экспорт не проверить
        if cm is None or method not in cm["exports"]:
            findings.append({"check": "handler", "severity": "error", "module": xml_path,
                             "line": 1, "message": f"Обработчик {h['ref']} не найден среди экспортных методов"})
    return findings


PLAN_RULES = (check_handler_refs,)


def dependents(repo_source, plan, index):
    """Модули, вызывающие удалённые экспорты или экспорты со сменённой сигнатурой.
    Возвращает ({путь: [строки вызовов]}, находки по удалённым методам)."""
    targets = {}
    for m in plan["modules"]:
        if m["kind"] != "CommonModule":
            continue
        name = m["owner"].split(".", 1)[1]
        for x in m["removedMethods"]:
            if x["export"]:
                targets[(name.lower(), x["name"].lower())] = ("removed", name, x["name"])
        for x in m["changedMethods"]:
            if x.get("signatureChanged") and x.get("export"):
                targets[(name.lower(), x["name"].lower())] = ("signature", name, x["name"])
    for m in plan["removedModules"]:
        if m["kind"] == "CommonModule":
            name = m["owner"].split(".", 1)[1]
            for e in m["exports"]:
                targets[(name.lower(), e.lower())] = ("removed", name, e)
    if not targets:
        return {}, []
    alt = "|".join(sorted({re.escape(t[1]) for t in targets.values()}, key=len, reverse=True))
    rough = re.compile(rf"(?<![\wА-Яа-яЁё.])({alt})\s*\.\s*({_N})\s*\(", re.I)
    changed = {m["path"] for m in plan["modules"]}
    found, findings = {}, []
    root = Path(repo_source)
    for path in root.rglob("*.bsl"):
        rel = path.relative_to(root).as_posix()
        raw = layout.read_text(path)
        if not rough.search(raw):
            continue
        parsed = bsl.parse(raw)
        for m in rough.finditer(parsed.sanitized):
            key = (m.group(1).lower(), m.group(2).lower())
            if key not in targets:
                continue
            line = parsed.sanitized.count("\n", 0, m.start()) + 1
            found.setdefault(rel, []).append(line)
            kind, mod_name, meth = targets[key]
            if kind == "removed":
                findings.append({"check": "dependent", "severity": "error", "module": rel, "line": line,
                                 "message": f"Вызов удалённого экспортного метода {mod_name}.{meth}"})
            else:
                exp = index["commonModules"].get(key[0], {}).get("exports", {}).get(key[1])
                args = _count_args(parsed.sanitized, m.end())
                if exp and args is not None and (args > exp[2] or args < exp[1]):
                    findings.append({"check": "dependent", "severity": "error", "module": rel, "line": line,
                                     "message": f"Вызов {mod_name}.{exp[0]} с {args} параметрами, "
                                                f"после изменения — от {exp[1]} до {exp[2]}"})
    return {k: v for k, v in found.items() if k not in changed}, findings
