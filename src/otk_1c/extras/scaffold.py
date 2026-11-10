"""Заготовки сценарных тестов для изменённых методов (otk scaffold, задача 6.11).

Изменённым методам, у которых в тестовых расширениях проекта нет теста с их тегом (`Модуль.Метод`, см.
scenarios.py), добавляется каркас теста YAxUnit: регистрация в `ИсполняемыеСценарии` с тегом и процедура
теста с подсказкой вызова. Каркас помечен пропущенным (`ЮТест.Пропустить`), пока тест не написан, — он не
проходит ложно и виден в отчёте YAxUnit.

Тестовый модуль — общий модуль `<префикс расширения><имя проверяемого модуля>` в тестовом расширении
(`tests/cfe/<имя>`); нет расширения — создаётся. Существующие процедуры не перезаписываются: новый тест
дописывается в конец модуля и регистрируется отдельной строкой в `ИсполняемыеСценарии`.

Черновик теста (`[scaffold] draft`) — внешняя команда: на stdin — JSON о методе (модуль, имя, сигнатура,
текст метода, контексты), на stdout — тело процедуры теста. ОТК ИИ не встраивает: в команду можно
подставить любой инструмент; без неё — пустой каркас.
"""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from ..gate import scenarios
from ..probes import ib_tools
from ..probes.platform import find_platform
from ..sources import bsl, layout

DEFAULT_EXTENSION = "Тесты"
DEFAULT_PREFIX = "тст_"
_SKIP = 'ЮТест.Пропустить("заготовка: тест не написан");'


def owner_name(owner):
    """Имя модуля для тегов: «ОбщийМодуль.Х» -> «Х», «Справочник.Клиенты» без изменений."""
    return owner.split(".", 1)[1] if owner.startswith("ОбщийМодуль.") else owner


def targets(plan, extensions):
    """Изменённые методы без теста: [{entry, method, tag}] (псевдометод «тело модуля» не учитывается)."""
    have = {t.lower() for e in extensions for test in e["tests"] for t in test["tags"]}
    out = []
    for m in plan.get("modules", []):
        if m.get("reason") != "changed":
            continue
        for meth in m.get("changedMethods", []):
            if meth.get("kind") == "body":
                continue
            tag = f"{owner_name(m['owner'])}.{meth['name']}"
            if tag.lower() not in have:
                out.append({"entry": m, "method": meth, "tag": tag})
    return out


def module_name(owner, prefix, strip_prefix=""):
    """Имя тестового модуля: префикс тестового расширения + имя проверяемого (без префикса проекта)."""
    name = owner_name(owner).replace(".", "_")
    if strip_prefix and name.lower().startswith(strip_prefix.lower()):
        name = name[len(strip_prefix):]
    return prefix + name


def test_flags(entry):
    """Контекст тестового модуля по проверяемому: серверный — Сервер, только клиентский — клиент."""
    flags = entry.get("flags") or {}
    client_only = entry["kind"] == "CommonModule" and not flags.get("Server") and flags.get("ClientManagedApplication")
    return {"ClientManagedApplication": "true"} if client_only else {"Server": "true"}


def call_hint(entry, method):
    """Подсказка вызова проверяемого метода в теле теста (комментарии)."""
    params = ", ".join(method.get("params", []))
    if entry["kind"] == "CommonModule" and method.get("export"):
        call = f"{owner_name(entry['owner'])}.{method['name']}({params})"
        return [f"// Результат = {call};" if method.get("kind") == "function" else f"// {call};",
                "// ЮТест.ОжидаетЧто(Результат).Равно(Ожидаемое);"]
    if not method.get("export"):
        return [f"// {method['name']} не экспортный: проверьте его через экспортный метод модуля, "
                "который его вызывает."]
    return [f"// Вызовите {entry['owner']}: {method['name']}({params}) и проверьте результат (ЮТест.ОжидаетЧто)."]


def test_procedure(name, entry, method, body=None):
    lines = body.rstrip().splitlines() if body else [
        f"// TODO: тест метода {owner_name(entry['owner'])}.{method['name']}: подготовьте данные, вызовите метод,",
        "// проверьте результат; уберите ЮТест.Пропустить. Данные не оставляйте: .ВТранзакции() у теста.",
        *call_hint(entry, method), "", _SKIP]
    return "\n".join([f"Процедура {name}() Экспорт", "", *[f"\t{ln}" if ln else "" for ln in lines], "",
                      "КонецПроцедуры"])


def new_module(registrations, procedures):
    return "\n".join(["// Сценарные тесты (YAxUnit). Тег теста — полное имя проверяемого метода: ОТК запускает",
                      "// тест, когда метод изменён. Заготовки созданы otk scaffold.", "",
                      "#Область СлужебныйПрограммныйИнтерфейс", "", "Процедура ИсполняемыеСценарии() Экспорт", "",
                      *registrations, "", "КонецПроцедуры", "", "#КонецОбласти", "", "#Область Тесты", "",
                      "\n\n".join(procedures), "", "#КонецОбласти", ""])


def add_to_module(text, registrations, procedures):
    """Дописать регистрации в конец ИсполняемыеСценарии и процедуры в конец модуля."""
    m = re.search(r"((?:Процедура|Procedure)\s+ИсполняемыеСценарии\s*\(.*?)(\n[ \t]*(?:КонецПроцедуры|EndProcedure))",
                  text, re.S | re.I)
    if not m:
        raise ValueError("в модуле нет процедуры ИсполняемыеСценарии")
    text = text[:m.end(1)] + "\n" + "\n".join(registrations) + text[m.end(1):]
    return text.rstrip() + "\n\n" + "\n\n".join(procedures) + "\n"


def register(name, title, tag):
    return f'\tЮТТесты.ДобавитьТест("{name}", "{title}", "{tag}");'


def ensure_extension(repo, path, extensions, config_root, name=None, bin_dir=None):
    """Тестовое расширение для заготовок: заданное, единственное или новое tests/cfe/Тесты. bin_dir — платформа,
    по ней версия формата файлов нового расширения у исходников EDT."""
    if name:
        found = [e for e in extensions if e["name"].lower() == name.lower()]
        if found:
            return Path(repo) / found[0]["dir"], False
    elif len(extensions) == 1:
        return Path(repo) / extensions[0]["dir"], False
    elif extensions:
        raise ValueError("тестовых расширений несколько: укажите --extension "
                         f"({', '.join(e['name'] for e in extensions)})")
    name = name or DEFAULT_EXTENSION
    target = Path(repo) / path / name
    ib_tools.generate_test_extension(target, ib_tools.base_properties(config_root, bin_dir), name=name,
                                     prefix=DEFAULT_PREFIX, modules={})
    return target, True


def preview(repo, plan, cfg, extension=None, use_draft=True):
    """План файлов scaffold без создания расширения, файлов или вызова команды черновика.
    draft — черновик придёт от внешней команды ([scaffold] draft и не --no-draft); сама команда не вызывается."""
    command = cfg.get("scaffold", {}).get("draft") if use_draft else None
    sc = cfg.get("scenarios", {})
    path = sc.get("path", scenarios.DEFAULT_PATH)
    extensions = scenarios.discover(repo, path)
    todo = targets(plan, extensions)
    if not todo:
        return {"extension": None, "createExtension": False, "files": [], "tests": [], "draft": bool(command)}
    name = extension or cfg.get("scaffold", {}).get("extension")
    found = next((e for e in extensions if e["name"].lower() == name.lower()), None) if name else None
    if found:
        ext_dir, ext_name, create_extension = Path(repo) / found["dir"], found["name"], False
    elif name:
        ext_name, ext_dir, create_extension = name, Path(repo) / path / name, True
    elif len(extensions) == 1:
        ext_dir, ext_name, create_extension = Path(repo) / extensions[0]["dir"], extensions[0]["name"], False
    elif extensions:
        raise ValueError("тестовых расширений несколько: укажите --extension "
                         f"({', '.join(e['name'] for e in extensions)})")
    else:
        ext_name, ext_dir, create_extension = DEFAULT_EXTENSION, Path(repo) / path / DEFAULT_EXTENSION, True
    prefix = _prefix(ext_dir) if (ext_dir / "Configuration.xml").exists() else DEFAULT_PREFIX
    project_root = Path(repo) / plan["source"]
    project_lay = layout.detect(project_root)
    project_prefix = project_lay.read_properties(project_root).prefix if project_lay else ""
    by_module = {}
    for target in todo:
        module = module_name(target["entry"]["owner"], prefix, project_prefix)
        by_module.setdefault(module, []).append(target)
    files = []
    for module, targets_ in by_module.items():
        rel = Path(path) / ext_name / "CommonModules" / module / "Ext" / "Module.bsl"
        module_file = ext_dir / "CommonModules" / module / "Ext" / "Module.bsl"
        files.append({"path": rel.as_posix(), "action": "append" if module_file.exists() else "create",
                      "tests": [target["tag"] for target in targets_]})
    if create_extension:
        files.insert(0, {"path": (Path(path) / ext_name / "Configuration.xml").as_posix(), "action": "create"})
    return {"extension": ext_name, "createExtension": create_extension, "files": files,
            "tests": [target["tag"] for target in todo], "draft": bool(command)}


def _prefix(ext_dir):
    m = re.search(r"<NamePrefix>([^<]*)</NamePrefix>", (ext_dir / "Configuration.xml").read_text(encoding="utf-8-sig"))
    return m.group(1) if m else ""


def _register_module(ext_dir, name):
    """Общий модуль в ChildObjects Configuration.xml расширения (после последнего общего модуля)."""
    cfg_path = ext_dir / "Configuration.xml"
    cfg = cfg_path.read_text(encoding="utf-8-sig")
    if f"<CommonModule>{name}</CommonModule>" in cfg:
        return
    item = f"<CommonModule>{name}</CommonModule>"
    last = cfg.rfind("</CommonModule>")
    cfg = (cfg[:last + len("</CommonModule>")] + item + cfg[last + len("</CommonModule>"):] if last >= 0
           else cfg.replace("</ChildObjects>", item + "</ChildObjects>", 1))
    ib_tools.write(cfg_path, cfg)


def draft(command, target, source_text, test_module, cwd=None):
    """Тело теста от внешней команды или None (команды нет, ошибка, пустой ответ)."""
    if not command:
        return None
    m, e = target["method"], target["entry"]
    lines = source_text.splitlines()
    start, end = m.get("lines", [1, len(lines)])
    payload = {"module": e["owner"], "path": e["path"], "method": m["name"], "kind": m.get("kind"),
               "export": m.get("export"), "params": m.get("params", []), "contexts": e.get("contexts", []),
               "tag": target["tag"], "testModule": test_module, "source": "\n".join(lines[start - 1:end])}
    try:
        r = subprocess.run(shlex.split(command, posix=False) if isinstance(command, str) else command,
                           input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
                           encoding="utf-8", timeout=300, cwd=cwd,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def scaffold(repo, plan, cfg, read_head, config_root, extension=None, use_draft=True, log=print):
    """Создать заготовки. Возвращает [{tag, module, test, file, draft}]."""
    sc = cfg.get("scenarios", {})
    path = sc.get("path", scenarios.DEFAULT_PATH)
    exts = scenarios.discover(repo, path)
    todo = targets(plan, exts)
    if not todo:
        return []
    ext_name = extension or cfg.get("scaffold", {}).get("extension")
    # у выгрузки Конфигуратора версия формата — у самой выгрузки; исходникам EDT она нужна от платформы
    bin_dir = None
    if config_root is not None and layout.detect(config_root) is layout.EDT:
        env = cfg.get("environment", {})
        bin_dir = Path(env["platform"]) if env.get("platform") else find_platform()
    ext_dir, created = ensure_extension(repo, path, exts, config_root, ext_name, bin_dir)
    if created:
        log(f"создано тестовое расширение {ext_dir.relative_to(repo).as_posix()}")
    prefix = _prefix(ext_dir)
    project_prefix = ""
    project_root = Path(repo) / plan["source"]
    project_lay = layout.detect(project_root)
    if project_lay:
        project_prefix = project_lay.read_properties(project_root).prefix
    command = cfg.get("scaffold", {}).get("draft") if use_draft else None
    by_module, done = {}, []
    for t in todo:
        by_module.setdefault(module_name(t["entry"]["owner"], prefix, project_prefix), []).append(t)
    for mod, items in by_module.items():
        bsl_path = ext_dir / "CommonModules" / mod / "Ext" / "Module.bsl"
        existing = bsl_path.read_bytes().decode("utf-8-sig") if bsl_path.exists() else None
        taken = {x.name.lower() for x in bsl.parse(existing).methods} if existing else set()
        regs, procs = [], []
        for t in items:
            name, n = t["method"]["name"], 1
            while name.lower() in taken:
                n += 1
                name = f"{t['method']['name']}_{n}"
            taken.add(name.lower())
            body = draft(command, t, read_head(t["entry"]["path"]) or "", mod, cwd=repo)
            regs.append(register(name, f"{t['method']['name']}: заготовка", t["tag"]))
            procs.append(test_procedure(name, t["entry"], t["method"], body))
            done.append({"tag": t["tag"], "module": mod, "test": name, "draft": body is not None,
                         "file": bsl_path.relative_to(repo).as_posix()})
        if existing is None:
            ib_tools.write_common_module(ext_dir, mod, test_flags(items[0]["entry"]), new_module(regs, procs))
            _register_module(ext_dir, mod)
        else:
            ib_tools.write(bsl_path, add_to_module(existing.replace("\r\n", "\n"), regs, procs))
    return done
