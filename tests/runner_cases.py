"""Синтетические прогоны шага run на заглушках исполнителей (этап 4 refactor-layers).

play(name) — результаты, примечания, решения реестра проверок и журнал вызовов исполнителей одного случая.
Эталон — runner_golden.json (`python tests/runner_cases.py`): снят кодом до реестра исполнителей, пересняты
намеренные изменения.
"""
import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from otk_1c import checks
from otk_1c.gate import runner
from otk_1c.probes import build
from otk_1c.probes.executors import com, result, yaxunit
from otk_1c.sources import layout

GOLDEN = Path(__file__).with_name("runner_golden.json")


def module(name, kind, strategy, contexts, **extra):
    probe = {"strategy": strategy, "target": f"Т.{name}"}
    if strategy == "create-object":
        probe["method"] = "Создать"
    return {"path": f"{name}/Module.bsl", "owner": f"ОбщийМодуль.{name}", "kind": kind, "probe": probe,
            "contexts": contexts, "reason": "changed", "status": "M", **extra}


MODULES = {
    "server": module("Сервер", "CommonModule", "missing-method", ["Server", "ExternalConnection"]),
    "plain": module("Плоский", "CommonModule", "missing-method", ["Server"], needsPlainServerProbe=True),
    "client": module("Клиент", "CommonModule", "missing-method", ["ThinClient"]),
    "form": module("Форма", "FormModule", "get-form", ["ThinClient", "Server"]),
    "object": module("Объект", "ObjectModule", "create-object", ["Server"],
                     uncovered=[{"context": "WebClient", "needs": "designer:WebClient", "reason": "только в CI"}]),
    "start": module("Сеанс", "SessionModule", "session-start", ["Server"]),
    "bad": module("Плохой", "CommonModule", "missing-method", ["Server", "ExternalConnection"]),
    "ecbranch": module("ВетвьВС", "CommonModule", "missing-method", ["Server", "ExternalConnection"],
                       preprocessorContexts=["ExternalConnection"]),
    "ecmodule": module("ВнешнееСоединение", "ExternalConnectionModule", "session-start", ["ExternalConnection"]),
    "econly": module("ТолькоВС", "CommonModule", "missing-method", ["ExternalConnection"]),
    "ordinary": module("Обычный", "CommonModule", "missing-method", ["Server", "OrdinaryClient"]),
}
QUERIES = [{"id": "q1", "module": "Сервер/Module.bsl", "line": 10, "text": "ВЫБРАТЬ 1", "complete": True},
           {"id": "q2", "module": "Сервер/Module.bsl", "line": 20, "text": "ВЫБРАТЬ", "complete": False}]

NO_EC = {"contexts": ["Server", "ThinClient"], "uncovered": True}

# случай: модули, запросы, smoke, сценарии, настройки, поведение заглушек
CASES = {
    "com-all": {"modules": ["server", "plain", "client", "form", "object", "bad"], "queries": True, "smoke": True,
                "scenarios": True},
    "com-missing": {"modules": ["server", "plain", "client"], "queries": True, "com": "missing"},
    "com-fails": {"modules": ["server", "plain", "client"], "queries": True, "com": "unavailable"},
    "com-fails-no-yax": {"modules": ["server", "plain", "client"], "queries": True, "com": "unavailable",
                         "yaxunit": False},
    "no-tools": {"modules": ["server", "plain", "client", "form"], "queries": True, "smoke": True,
                 "scenarios": True, "com": "missing", "yaxunit": False},
    "no-yax-com-ok": {"modules": ["server", "plain", "client"], "queries": True, "yaxunit": False},
    "server-yaxunit": {"modules": ["server", "object"], "queries": True, "server": "yaxunit"},
    "session-start": {"modules": ["start"]},
    "session-start-off": {"modules": ["start"], "checks": {"probes-client": "off"}},
    "conflict": {"modules": ["client"], "conflict": True},
    "load-error": {"modules": ["server", "client"], "queries": True, "load": "error"},
    "extension": {"modules": ["server", "client"], "extension": "Расш"},
    "checks-off": {"modules": ["server", "client", "form"], "queries": True, "smoke": True, "scenarios": True,
                   "checks": {"probes-server": "off", "queries": "off", "extcheck": "off", "smoke": "off"}},
    "checks-on-missing": {"modules": ["client"], "smoke": True, "yaxunit": False,
                          "checks": {"probes-client": "on", "smoke": "on"}},
    "web-only": {"modules": ["client", "form"], "contexts": ["Server", "WebClient"]},
    "session": {"modules": ["server", "client"], "scenarios": True, "session": True},
    "session-ext": {"modules": ["server"], "session": True, "extension": "Расш"},
    # основная конфигурация рабочей базы не применена к БД: пробы исполнения — «не проверено», extcheck — да
    "session-db-changed": {"modules": ["server", "client"], "queries": True, "smoke": True, "session": True,
                           "dbChanged": True},
    "empty": {"modules": []},
    "conflict-exhausted": {"modules": ["client"], "conflict": 4},
    "conflict-exhausted-on": {"modules": ["client"], "conflict": 4, "checks": {"probes-client": "on"}},
    "conflict-exhausted-com": {"modules": ["server", "client"], "queries": True, "com": "missing", "conflict": 4},
    # конфликт имён YAxUnit в дымовом тесте и сценариях: повтор с переименованием, исчерпан — «не проверено»
    "conflict-smoke": {"modules": [], "smoke": True, "conflict": 1},
    "conflict-scenarios": {"modules": [], "scenarios": True, "conflict": 1},
    "conflict-exhausted-smoke-scen": {"modules": ["client"], "smoke": True, "scenarios": True, "conflict": 12},
    "com-missing-ec": {"modules": ["ecbranch", "ecmodule", "econly", "client"], "com": "missing"},
    "ec-no-tools": {"modules": ["ecmodule"], "com": "missing", "yaxunit": False},
    "com-ok-ec": {"modules": ["ecmodule"]},
    # внешнее соединение убрано из environment.contexts: COM доступен, но не запускается;
    # NO_EC — uncovered модулям, как пишет collect для недоступных контекстов
    "contexts-no-ec": {"modules": ["server", "ecbranch", "ecmodule", "econly", "client"], "queries": True, **NO_EC},
    "contexts-no-ec-no-yax": {"modules": ["server", "econly"], "queries": True, "yaxunit": False, **NO_EC},
    "contexts-no-ec-no-yax-on": {"modules": ["server"], "queries": True, "yaxunit": False, **NO_EC,
                                 "checks": {"probes-server": "on", "queries": "on"}},
    "contexts-ec-only-modules": {"modules": ["ecmodule", "econly"], **NO_EC},
    "contexts-no-ec-server-yaxunit": {"modules": ["server", "ecbranch", "econly"], "queries": True,
                                      "server": "yaxunit", **NO_EC},
    "contexts-no-ec-plain": {"modules": ["plain", "object", "start"], **NO_EC},
    "contexts-no-ec-probes-off": {"modules": ["server"], "queries": True, "checks": {"probes-server": "off"},
                                  **NO_EC},
    "contexts-no-ec-queries-off": {"modules": ["server"], "queries": True, "checks": {"queries": "off"}, **NO_EC},
    "no-git": {"modules": ["server", "client"], "scenarios": True, "headDir": True},
    # контекст OrdinaryClient у модуля: extcheck включает режим обычного клиента и снимает «не покрыто»
    "ordinary-client": {"modules": ["ordinary"], "uncovered": True,
                        "contexts": ["Server", "ThinClient", "ExternalConnection"]},
    "ordinary-client-ext": {"modules": ["ordinary"], "extension": "Расш", "uncovered": True,
                            "contexts": ["Server", "ThinClient", "ExternalConnection"]},
    "ordinary-client-no-ext": {"modules": ["ordinary"], "uncovered": True,
                               "contexts": ["Server", "ThinClient", "ExternalConnection"],
                               "checks": {"extcheck": "off"}},
}


class FakePlatform:
    bin = Path("bin")
    ib = "ib"
    user = ""
    password = ""

    def __init__(self, calls):
        self.calls = calls

    def unlock(self):
        self.calls.append(["unlock"])


class FakeSession:
    def __init__(self, calls):
        self.calls = calls
        self.platform = FakePlatform(calls)

    def load_project_extension(self, dump, name):
        self.calls.append(["session.load_project_extension", name])
        return {"action": "расширение проекта загружено"}

    def guard(self, name, tool=True):
        self.calls.append(["session.guard", name])


def play(name):
    case = CASES[name]
    calls = []
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        tmp = Path(tmp)
        cfe = tmp / "YAxUnit.cfe"
        if case.get("yaxunit", True):
            cfe.write_text("")
        modules = [MODULES[m] for m in case["modules"]]
        if case.get("uncovered"):        # как collect: контексты модуля, недоступные локально
            available = case["contexts"]
            modules = [{**m, "uncovered": m.get("uncovered", []) + [
                {"context": c, "needs": f"designer:{c}", "reason": "контекст недоступен локально"}
                for c in m["contexts"] if c not in available]}
                for m in modules]
        source = tmp / "tree" / "src"
        for m in modules:                 # тексты для лямбд расширенной проверки
            (source / m["path"]).parent.mkdir(parents=True, exist_ok=True)
            (source / m["path"]).write_text(m["owner"], encoding="utf-8")
            xml = source / layout.DESIGNER.object_file("CommonModules", m["owner"].split(".", 1)[1])
            xml.parent.mkdir(parents=True, exist_ok=True)
            xml.write_text("<xml/>", encoding="utf-8")
        plan = {"source": "src", "headSha": None if case.get("headDir") else "abc", "modules": modules,
                "sourceFormat": layout.DESIGNER.format,
                "queries": QUERIES if case.get("queries") else [],
                "contextsAvailable": case.get("contexts", ["Server", "ThinClient", "ExternalConnection"]),
                "smoke": [{"path": "Форма/Form.xml", "form": "Документ.Д.Форма.Ф"}] if case.get("smoke") else [],
                "scenarios": [{"id": "s1"}] if case.get("scenarios") else [],
                "scenarioExtensions": [{"name": "Тесты", "dir": "tests/Тесты"}] if case.get("scenarios") else []}
        if case.get("headDir"):
            plan["headDir"] = str(tmp / "tree")
        cfg = {"environment": {"comConnector": "V85.COMConnector", "yaxunit": str(cfe),
                               "executors": {"server": case.get("server", "auto")}},
               "project": {"extension": case.get("extension")}, "checks": case.get("checks", {})}
        conflicts = ["ОбщийМодуль"] * int(case.get("conflict", 0))

        def patch(target, fake):
            stack.enter_context(mock.patch(target, fake))

        def fake_build(repo, sha, src, tree_root, platform, run_dir, extension=None):
            calls.append(["build", sha, extension])
            if case.get("load") == "error":
                raise build.LoadError([("Форма/Form.xml", "неверный путь")])
            return {"action": "собрана"}

        def fake_build_dirs(plan, platform, run_dir, extension=None):
            calls.append(["build_dirs", extension])
            return {"action": "собрана из каталогов"}

        def fake_available(progid):
            calls.append(["com.available", progid])
            return (False, "COM недоступен (тест)") if case.get("com") == "missing" else (True, "")

        def fake_com_run(platform, plan, mods, queries, progid):
            calls.append(["com.run", [m["path"] for m in mods], [q["id"] for q in queries]])
            if case.get("com") == "unavailable":
                raise com.Unavailable("коннектор другой версии")
            out = [result("com", "probe", m, "Server", "compile-error" if "Плохой" in m["path"] else "ok",
                          "ошибка" if "Плохой" in m["path"] else "", 5 if "Плохой" in m["path"] else None)
                   for m in mods]
            out += [{**result("com", "query", {"path": q["module"], "owner": q["id"]}, "Server", "ok"),
                     "query": q["id"]} for q in queries]
            return out, "примечание COM"

        def fake_yax_run(platform, items, run_dir, timeout=600):
            calls.append(["yax.run", [spec["name"] for _, _, spec in items]])
            if conflicts:
                raise yaxunit.ToolsConflict(conflicts.pop())
            return [result("yaxunit", "query" if spec["kind"] == "query" else "probe", entry, ctx, "ok")
                    for entry, ctx, spec in items], "примечание YAxUnit"

        def fake_smoke(platform, items, run_dir, form_timeout=60, smoke_severity="error", timeout=600):
            calls.append(["yax.run_smoke", [spec["name"] for _, _, spec in items], form_timeout, smoke_severity])
            if conflicts:
                raise yaxunit.ToolsConflict(conflicts.pop())
            return [result("smoke", "smoke", entry, ctx, "ok") for entry, ctx, _ in items], ""

        def fake_install_tools(session, platform, run_dir, env, config_root, extra_renames=()):
            calls.append(["install_tools", list(extra_renames), session is not None])
            return "установлены"

        def fake_extcheck(platform, run_dir, config_root, entries, read_base, read_head, read_xml, lay=None,
                          ordinary=False):
            calls.append(["extcheck.run", [e["path"] for e in entries],
                          [[read_base(e), read_head(e), read_xml(e)] for e in entries], ordinary])
            return [result("extcheck", "extcheck", e, "Designer", "ok") for e in entries], ""

        def fake_extcheck_ext(platform, extension, entries, read_text, lay=None, ordinary=False):
            calls.append(["extcheck.run_extension", extension, [e["path"] for e in entries],
                          [read_text(e["path"]) for e in entries] + [read_text("нет/Module.bsl")], ordinary])
            return [], "примечание расширения"

        def fake_scen_install(platform, run_dir, tree_root, extensions, log=print):
            root = "repo" if Path(tree_root) == tmp else "tree"
            calls.append(["scen.install", [e["name"] for e in extensions], root])

        def fake_scen_run(platform, run_dir, selected, extensions, all_scenarios=False, timeout=900, tree_root=None):
            calls.append(["scen.run", [s["id"] for s in selected], all_scenarios])
            if conflicts:
                raise yaxunit.ToolsConflict(conflicts.pop())
            return [result("scenarios", "scenario", None, "ThinClient", "failed", "тест упал")], ""

        def fake_db_changed(platform, progid):
            calls.append(["db_config_changed", progid])
            return case.get("dbChanged", False)

        patch("otk_1c.gate.runner.Platform", lambda env, run_dir: FakePlatform(calls))
        patch("otk_1c.gate.runner.db_config_changed", fake_db_changed)
        patch("otk_1c.probes.build.build", fake_build)
        patch("otk_1c.probes.build.build_dirs", fake_build_dirs)
        patch("otk_1c.probes.executors.com.available", fake_available)
        patch("otk_1c.probes.executors.com.run", fake_com_run)
        patch("otk_1c.probes.executors.yaxunit.run", fake_yax_run)
        patch("otk_1c.probes.executors.yaxunit.run_smoke", fake_smoke)
        patch("otk_1c.gate.runner.install_tools", fake_install_tools)
        patch("otk_1c.probes.executors.extended_check.run", fake_extcheck)
        patch("otk_1c.probes.executors.extended_check.run_extension", fake_extcheck_ext)
        patch("otk_1c.probes.executors.scenarios.install", fake_scen_install)
        patch("otk_1c.probes.executors.scenarios.run", fake_scen_run)
        patch("otk_1c.sources.revision.read_base", lambda repo, plan: lambda rel: None)
        patch("otk_1c.sources.workspace.materialize", lambda repo, sha, rel, dest: calls.append(["materialize", rel]))
        gate = checks.Gate(cfg)
        session = FakeSession(calls) if case.get("session") else None
        out = runner._run(tmp, plan, cfg, tmp / "run", tmp / "tree", log=lambda *a: None, session=session,
                          gate=gate)
    return {"results": out["results"], "notes": out["notes"], **gate.take(), "calls": calls,
            "timings": sorted(out["timings"])}


def main():
    golden = {name: play(name) for name in CASES}
    GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{GOLDEN.name}: {len(golden)} случаев")


if __name__ == "__main__":
    sys.exit(main())
