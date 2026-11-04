"""Сценарные тесты (спецификация scenario-tests): тесты YAxUnit, которые пишет пользователь.

Раскладка — как у VS Code «1C: Platform Tools»: тестовые расширения в `tests/cfe/<имя>` проекта (выгрузка
Конфигуратора), каталог — [scenarios] path. Привязка к коду — тег YAxUnit с полным именем метода:
`ИмяОбщегоМодуля.Метод` (для прочих модулей — владелец плана, например `Справочник.Клиенты.Метод`),
тег модуля — его имя (`ИмяОбщегоМодуля`, `Справочник.Клиенты`). Теги задаются `.Тег("…")` у теста,
набора или модуля либо третьим параметром `ДобавитьТест("Имя", "Представление", "Тег1,Тег2")`.

Запускает тесты YAxUnit с фильтром tags (теги изменённых методов); статический разбор здесь — чтобы
показать в плане и сводке, какие сценарии выбраны, и не запускать клиент, если выбирать нечего.
"""
import re
from pathlib import Path

DEFAULT_PATH = "tests/cfe"
ENGINE = "YAXUNIT"              # расширение-движок: Platform Tools «Добавить YAxUnit» кладёт его рядом с тестами
_CALL = re.compile(r"\.\s*(ДобавитьТест|ДобавитьКлиентскийТест|ДобавитьСерверныйТест|ДобавитьТестовыйНабор|Тег)\s*\(",
                   re.I)


def discover(repo, path=DEFAULT_PATH):
    """Тестовые расширения: [{name, dir, tests: [{module, name, tags}]}]. Движок YAxUnit и расширения без
    тестов пропускаются: загружать их в копию ИБ незачем (движок ОТК ставит сам)."""
    root = Path(repo) / path
    out = []
    if not root.is_dir():
        return out
    for cfg in sorted(root.glob("*/Configuration.xml")):
        text = cfg.read_text(encoding="utf-8-sig", errors="replace")
        m = re.search(r"<Name>([^<]+)</Name>", text)
        ext_dir = cfg.parent
        if (m.group(1) if m else ext_dir.name).upper() == ENGINE:
            continue
        tests = []
        for bsl in sorted(ext_dir.glob("CommonModules/*/Ext/Module.bsl")):
            tests += parse_module(bsl.parent.parent.name, bsl.read_text(encoding="utf-8-sig", errors="replace"))
        if not tests:
            continue
        out.append({"name": m.group(1) if m else ext_dir.name, "dir": ext_dir.relative_to(repo).as_posix(),
                    "tests": tests})
    return out


def _args(text, start):
    """Параметры вызова, начиная со скобки в позиции start: строковый литерал — его значение, иначе
    (пропущен или выражение) — "". Делятся по запятым верхнего уровня."""
    depth, i, args, cur, literal = 0, start, [], [], None
    while i < len(text):
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < len(text):
                if text[j] == '"' and text[j + 1:j + 2] == '"':
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            if depth == 1:
                literal = text[i + 1:j].replace('""', '"')
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == "(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append(literal if literal is not None and "".join(cur).strip().startswith('"') else "")
                break
        elif ch == "," and depth == 1:
            args.append(literal if literal is not None and "".join(cur).strip().startswith('"') else "")
            cur, literal, i = [], None, i + 1
            continue
        cur.append(ch)
        i += 1
    return args


def _tags(value):
    return [t.strip() for t in value.split(",") if t.strip()]


def parse_module(module, text):
    """Тесты модуля ИсполняемыеСценарии с тегами. Тег относится к последнему добавленному тесту, до
    первого теста набора — к набору, до первого набора и теста — к модулю; тесты наследуют теги
    модуля и своего набора."""
    m = re.search(r"(?:Процедура|Procedure)\s+ИсполняемыеСценарии\s*\(.*?(?:КонецПроцедуры|EndProcedure)",
                  text, re.S | re.I)
    if not m:
        return []
    body = m.group(0)
    tests, module_tags, suite_tags, current, in_suite = [], [], [], None, False
    for call in _CALL.finditer(body):
        kind = call.group(1).lower()
        args = _args(body, call.end() - 1)
        if kind == "тег":
            target = current["tags"] if current else suite_tags if in_suite else module_tags
            target.extend(_tags(args[0]) if args else [])
        elif kind == "добавитьтестовыйнабор":
            current, in_suite = None, True
            suite_tags = _tags(args[2]) if len(args) > 2 else []
        elif args:
            current = {"module": module, "name": args[0],
                       "tags": module_tags + suite_tags + (_tags(args[2]) if len(args) > 2 else [])}
            tests.append(current)
    return tests


def tags_for(plan):
    """Теги изменённых методов и модулей плана."""
    out = set()
    for m in plan.get("modules", []):
        if m.get("reason") != "changed":
            continue
        owner = m["owner"]
        name = owner.split(".", 1)[1] if owner.startswith("ОбщийМодуль.") else owner
        out.add(name)
        for meth in m.get("changedMethods", []) + m.get("removedMethods", []):
            out.add(f"{name}.{meth['name'] if isinstance(meth, dict) else meth}")
    return out


def select(plan, extensions, all_scenarios=False):
    """[{extension, module, name, tags, matched}] — выбранные тесты (all_scenarios — все)."""
    wanted = {t.lower() for t in tags_for(plan)}
    out = []
    for ext in extensions:
        for t in ext["tests"]:
            matched = sorted(tag for tag in t["tags"] if tag.lower() in wanted)
            if all_scenarios or matched:
                out.append({"extension": ext["name"], "module": t["module"], "name": t["name"], "tags": t["tags"],
                            "matched": matched})
    return out
