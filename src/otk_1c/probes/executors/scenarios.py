"""Исполнитель сценарных тестов (спецификация scenario-tests): тестовые расширения проекта в копии ИБ,
YAxUnit с фильтром по тегам изменённых методов.

Тестовые расширения (tests/cfe/<имя>) загружаются в свою копию ИБ ОТК и применяются; повторно —
только когда изменилось их содержимое (scenarios-state.json в каталоге прогона). Безопасный режим
снимается, как у тестового расширения проб: тесты обращаются к данным и внешним ресурсам по замыслу автора.
"""
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from ...errors import InfraError
from ...sources import bsl, layout, workspace
from .. import ib_tools
from .base import result
from .yaxunit import launch, resolving_conflicts

NAME = "scenarios"


def _digest(path):
    h = hashlib.sha1(usedforsecurity=False)
    for f in sorted(Path(path).rglob("*")):
        if f.is_file():
            h.update(f.relative_to(path).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def install(platform, run_dir, tree_root, extensions, log=print):
    """Загрузить и применить изменившиеся тестовые расширения. Возвращает число загруженных."""
    state_file = Path(run_dir) / "scenarios-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    loaded = 0
    for ext in extensions:
        src = Path(tree_root) / ext["dir"]
        digest = _digest(src)
        key = f"{platform.ib}|{ext['name']}"
        if state.get(key) == digest:
            continue
        for label, args in (("load", ["/LoadConfigFromFiles", src, "-Extension", ext["name"]]),
                            ("update", ["/UpdateDBCfg", "-Extension", ext["name"]])):
            rc, _, out = platform.designer(*args, label=f"scen-{label}")
            if rc:
                raise InfraError(f"тестовое расширение {ext['name']}: {label} rc={rc} {' '.join(out.split())[-300:]}",
                                 code="platform-failed")
        ib_tools.unsafe(platform, ext["name"])
        state[key] = digest
        loaded += 1
        log(f"  сценарии: загружено тестовое расширение {ext['name']}")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return loaded


def _line_of(path, method):
    """Строка объявления тестового метода в модуле (для перехода к упавшему тесту) или None."""
    text = layout.read_text(path) if path else None
    return bsl.method_line(text, method) if text is not None else None


def run(platform, run_dir, selected, extensions, all_scenarios=False, timeout=900, tree_root=None):
    """Выбранные сценарии через YAxUnit. Возвращает (результаты, примечание)."""
    if not selected:
        return [], ""
    ws = Path(run_dir) / "scenarios"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    names = sorted({s["extension"] for s in selected})
    filters = {"extensions": names}
    if not all_scenarios:
        filters["tags"] = sorted({t for s in selected for t in s["matched"]})
    report, code, errors, out_text = launch(platform, ws, filters, timeout)
    by_ext = {e["name"]: e for e in extensions}
    results = []
    if not report.exists():
        text = " ".join(("\n".join(errors) or out_text).split())[:400]
        return [result(NAME, "scenario", None, "ThinClient", "unknown",
                       f"нет отчёта YAxUnit (код клиента {code}): {text}")], "сценарии не выполнены"
    for tc in ET.parse(report).getroot().iter("testcase"):
        # jUnit YAxUnit: classname — «Модуль.ТестовыйМетод», name — представление теста, context — контекст
        module, _, method = (tc.get("classname") or "?").partition(".")
        title = tc.get("name") or method
        context = tc.get("context") or "Scenario"
        sel = next((s for s in selected if s["module"] == module and s["name"] == method), None)
        ext = by_ext.get(sel["extension"]) if sel else None
        entry = {"path": None, "owner": f"{module}.{method}"}
        bad = [c for c in tc if c.tag in ("failure", "error")]
        skipped = [c for c in tc if c.tag == "skipped"]
        if skipped and not bad:          # например, заготовка otk scaffold — тест ещё не написан
            text = " ".join((skipped[0].get("message") or skipped[0].text or "").split())[:300]
            r = result(NAME, "scenario", entry, context, "skipped", f"{module}.{method} ({title}): {text}")
        elif bad:
            text = " ".join((bad[0].get("message") or bad[0].text or "").split())[:500]
            r = result(NAME, "scenario", entry, context, "failed", f"{module}.{method} ({title}): {text}")
        else:
            r = result(NAME, "scenario", entry, context, "ok")
        if ext:
            rel = f"{ext['dir']}/CommonModules/{module}/Ext/Module.bsl"
            r["file"] = rel
            r["line"] = _line_of(Path(tree_root) / rel if tree_root else None, method)
        results.append(r)
    return results, ""


class Scenarios:
    """Сценарные тесты по изменённым методам из тестовых расширений проекта."""
    name = NAME
    checks = ("scenarios",)

    def select(self, ctx):
        return ctx.plan.get("scenarios") if ctx.plan.get("scenarios") and ctx.gate.ready("scenarios", ctx.have) \
            else None

    def run(self, ctx, items):
        plan, run_dir, tree_root = ctx.plan, Path(ctx.run_dir), Path(ctx.tree_root)
        if ctx.tools_conflict:            # YAxUnit не запустится — тестовые расширения не ставить
            return resolving_conflicts(ctx, None, self.checks, "сценарии не выполнены")
        ctx.ensure_tools()
        # тестовые расширения — из той же редакции, что и выгрузка (в извлечённом дереве только source)
        head = plan.get("headSha")
        for ext in plan["scenarioExtensions"]:
            if head and not (tree_root / ext["dir"] / "Configuration.xml").exists():
                workspace.materialize(ctx.repo, head, ext["dir"], run_dir / "trees")
        if ctx.session:                   # рабочая база: тестовые расширения — на время проверки
            for ext in plan["scenarioExtensions"]:
                ctx.session.guard(ext["name"])
            (run_dir / "scenarios-state.json").unlink(missing_ok=True)
        root = ctx.repo if plan.get("headDir") else tree_root   # без git тесты — в каталоге проекта
        install(ctx.platform, run_dir, root, plan["scenarioExtensions"], ctx.log)
        return resolving_conflicts(ctx, lambda: run(ctx.platform, run_dir, items, plan["scenarioExtensions"],
                                                    plan.get("allScenarios", False), tree_root=root),
                                   self.checks, "сценарии не выполнены")
