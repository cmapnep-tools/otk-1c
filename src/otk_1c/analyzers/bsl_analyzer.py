"""bsl-analyzer до и после: новые замечания в изменённых модулях с моделью всей конфигурации.

В отличие от BSL LS (bslls.py: частичная раскладка) bsl-analyzer видит всё дерево редакции: «после» — само
дерево, разбор ограничен изменёнными модулями (`--changed-files`); «до» — зеркало того же дерева, где каталоги
неизменённых объектов — ссылки (junction), а изменённые объекты скопированы и их модули возвращены к базовой
редакции. Так старое семантическое замечание (неизвестный метод, поле) остаётся старым. Замер (20 модулей):
ERP — зеркало 6,5 с (жёсткие ссылки на 122 тыс. файлов — 171 с), прогон 73 с холодный / 32 с с кэшем `-w`;
12 тыс. файлов — 9 / 4 с. Замечание сравнивается так же, как у BSL LS: (правило, текст, строка без пробелов).

Родные настройки анализатора пишет адаптер во временный каталог: основа — bsl-analyzer.toml или
.bsl-language-server.json репозитория, поверх — настройки otk.toml (минимальная платформа, предупреждения
совместимости); своего файла нет — встроенный набор (критические правила BSL LS и семантика).

Формат EDT не поддерживается (0.4.6): метаданные .mdo не загружаются (объектов метаданных 0), в проект
попадают одни модули, а защищённый модуль (не UTF-8) обрывает прогон.
"""
import collections
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

from ..sources import layout
from . import bsl_ls

# Семантика по модели конфигурации — то, чего нет у BSL LS по частичной раскладке. Пока сверка на контрольных
# дефектах не показала, какие из них не дают ложных, — warn (FAIL_CODES — общие с BSL LS)
SEMANTIC = ("UnresolvedMethodCall", "UnresolvedBareCall", "MismatchedArgCount", "UnresolvedField",
            "UnknownFieldInQuery", "QueryToMissingMetadata", "UnavailableInEnvironment", "TypeMismatch",
            "PlatformMemberNewerThanMinVersion",
            "PlatformMemberHiddenByCompatibilityMode")
COMPAT_RULE = "PlatformMemberHiddenByCompatibilityMode"
# поля родного файла, которые задаёт адаптер (состав исходников — аргументами командной строки)
OWN_SECTIONS = ("source",)
TIMEOUT = 1800
CHANGED_FILES_MAX = 24000   # символов списка --changed-files: с путями и ключами — в пределах командной строки
_NOT_FOUND = re.compile(r"не найден[а-я]* у '([^']+)'")


def rule_ids(exe):
    """Правила анализатора: `rules list` -> [id]."""
    r = subprocess.run([str(exe), "rules", "list"], capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    return re.findall(r"^\s+(\w+)\s+\[(?:enabled|disabled)\]", r.stdout.decode("utf-8", "replace"), re.M)


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)     # строка JSON — допустимая базовая строка TOML


def dump_toml(data, prefix=""):
    """Словарь -> текст TOML (скаляры, списки, вложенные таблицы)."""
    lines, tables = [], []
    for k, v in data.items():
        key = k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)
        (tables if isinstance(v, dict) else lines).append((key, v))
    out = [f"{k} = {_toml_value(v)}" for k, v in lines]
    for k, v in tables:
        name = f"{prefix}.{k}" if prefix else k
        out += ["", f"[{name}]", dump_toml(v, name)]
    return "\n".join(out).strip("\n")


def native_config(repo, env, rules):
    """Родные настройки: (словарь bsl-analyzer.toml, источники {настройка: откуда}). rules — все id анализатора."""
    repo = Path(repo)
    data, sources = {}, {}
    own_toml, own_json = repo / "bsl-analyzer.toml", repo / ".bsl-language-server.json"
    params = None
    if own_toml.exists():
        with open(own_toml, "rb") as f:
            data = {k: v for k, v in tomllib.load(f).items() if k not in OWN_SECTIONS}
        sources["основа"] = own_toml.name
    elif own_json.exists():
        try:
            legacy = json.loads(own_json.read_text(encoding="utf-8-sig")).get("diagnostics", {})
        except ValueError:
            legacy = {}
        given = legacy.get("parameters", {})
        # mode: only — включено только перечисленное; иначе перечисленное поверх умолчаний анализатора
        params = {r: False for r in rules} if legacy.get("mode") == "only" else {}
        params.update({k: v for k, v in given.items() if isinstance(v, (bool, dict))})
        sources["основа"] = f"{own_json.name} (diagnostics.parameters)"
    else:
        wanted = set(bsl_ls.CRITICAL) | set(SEMANTIC)
        params = {r: r in wanted for r in rules}
        sources["основа"] = "встроенный набор (критические правила и семантика)"
    if params is not None:
        data.setdefault("diagnostics", {})["parameters"] = params
    if env.get("minPlatform"):
        data["min_platform_version"] = str(env["minPlatform"])
        sources["min_platform_version"] = "otk.toml: environment.minPlatform"
    if env.get("compatWarnings", True) is False:
        data.setdefault("diagnostics", {}).setdefault("parameters", {})[COMPAT_RULE] = False
        sources[COMPAT_RULE] = "otk.toml: environment.compatWarnings = false"
    return data, sources


def _link_dir(src, dest):
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(src), str(dest))
    else:
        os.symlink(src, dest, target_is_directory=True)


def _link_file(src, dest):
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def mirror(root, dest, owners, skip=()):
    """Зеркало дерева root в dest: каталоги объектов — ссылки, owners — {(коллекция, имя)} — копии, skip — такие же
    пары, которых в зеркале нет; файлы — жёсткие ссылки (в них не пишем). Возвращает dest."""
    root, dest = Path(root), Path(dest)
    dest.mkdir(parents=True)
    colls = {c for c, _ in owners} | {c for c, _ in skip}
    for e in os.scandir(root):
        target = dest / e.name
        if e.is_dir() and e.name in colls:
            target.mkdir()
            for o in os.scandir(e.path):
                if (e.name, o.name) in skip:
                    continue
                if (e.name, o.name) in owners:
                    (shutil.copytree if o.is_dir() else shutil.copyfile)(o.path, target / o.name)
                elif o.is_dir():
                    _link_dir(o.path, target / o.name)
                else:
                    _link_file(o.path, target / o.name)
        elif e.is_dir():
            _link_dir(e.path, target)
        else:
            _link_file(e.path, target)
    return dest


def is_link(path):
    """Ссылка на каталог (junction или символьная): Path.is_junction — только с Python 3.12."""
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0)
                                            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def remove_mirror(dest):
    """Удаляет зеркало: ссылки на каталоги снимаются, их цели не трогаются."""
    dest = Path(dest)
    if not dest.exists():
        return
    for e in os.scandir(dest):
        p = Path(e.path)
        if is_link(p):
            try:
                os.rmdir(p)     # junction и символьная ссылка на каталог (Windows) — без захода внутрь
            except OSError:
                p.unlink()
        elif e.is_dir(follow_symlinks=False):
            remove_mirror(p)
        else:
            p.unlink()
    dest.rmdir()


def _write(path, text):
    """Новый файл вместо прежнего: прежний мог быть жёсткой ссылкой на исходник."""
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))


def _uri_path(uri):
    p = unquote(uri)
    for pre in ("file:///", "file://", "//?/", "\\\\?\\"):
        if p.startswith(pre):
            p = p[len(pre):]
    return p.replace("\\", "/")


def parse_sarif(report, root):
    """SARIF -> {rel: [{code, message, line}]} для файлов под root."""
    base = Path(root).resolve().as_posix().lower().rstrip("/") + "/"
    out = {}
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    for run in data.get("runs", []):
        for r in run.get("results", []):
            loc = (r.get("locations") or [{}])[0].get("physicalLocation", {})
            path = _uri_path(loc.get("artifactLocation", {}).get("uri", ""))
            if not path.lower().startswith(base):
                continue
            out.setdefault(path[len(base):], []).append(
                {"code": r["ruleId"], "message": r.get("message", {}).get("text", ""),
                 "line": loc.get("region", {}).get("startLine", 1) - 1})
    return out


def _analyze(exe, root, rels, cfg_path, cache, out, extension=None, parent=None, timeout=TIMEOUT):
    """Один прогон по дереву root, ограниченный модулями rels: ({rel: [замечания]}, None) или (None, причина)."""
    shutil.rmtree(out, ignore_errors=True)
    cmd = [str(exe), "analyze", "-q", "--incremental", "-s", str(root), "-c", str(cfg_path),
           "-w", str(cache), "-r", "sarif", "-o", str(out)]
    changed = ",".join(rels)
    if len(changed) <= CHANGED_FILES_MAX:   # длиннее — всё дерево: списка из файла анализатор не читает, а
        cmd += ["--changed-files", changed]   # командная строка Windows — до 32 767 символов (WinError 206)
    if extension:
        cmd += ["--extension", f"{extension}={root}"] + (["--configuration-root", str(parent)] if parent else [])
    else:
        cmd += ["--no-extensions", "--no-externals"]
    env = {**os.environ, "BSL_MCP_BROKER": "0"}
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, f"не уложился в {timeout} с (environment.bslAnalyzerTimeout)"
    report = Path(out) / "bsl-analyzer.sarif"
    if r.returncode or not report.exists():
        text = (r.stdout + r.stderr).decode("utf-8", "replace")
        errors = [ln.strip() for ln in text.splitlines() if ln.startswith("Error") or " ERROR " in ln]
        return None, "; ".join(errors[:2])[:500] or text[-300:]
    return parse_sarif(report, root), None


def protected_modules(root):
    """Имена общих модулей без исходников (Ext/Module.bin): вызовы их методов анализатор не разрешает."""
    common = Path(root) / "CommonModules"
    if not common.is_dir():
        return set()
    return {d.name.lower() for d in common.iterdir()
            if (d / "Ext" / "Module.bin").exists() and not (d / "Ext" / "Module.bsl").exists()}


def run(exe, work_dir, cache_dir, root, modules, read_base, read_head, env, repo, lay=layout.DESIGNER,
        extension=None, parent=None, excluded=None):
    """root — дерево проверяемой редакции (каталог выгрузки), modules — изменённые модули плана, read_base /
    read_head(rel) -> текст. extension — имя расширения (root — его выгрузка), parent — основная конфигурация.
    Возвращает (findings, info)."""
    if lay.format != layout.DESIGNER.format:
        return [], {"skipped": "формат EDT bsl-analyzer не поддерживает (метаданные .mdo не загружаются)"}
    excluded = excluded or (lambda rel: False)
    modules = [m for m in modules if not excluded(m["path"]) and (Path(root) / m["path"]).exists()]
    if not modules:
        return [], {"skipped": "нет модулей"}
    work = Path(work_dir) / "bsla"
    remove_mirror(work / "before")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    data, sources = native_config(repo, env, rule_ids(exe))
    cfg_path = work / "bsl-analyzer.toml"
    cfg_path.write_text(dump_toml(data) + "\n", encoding="utf-8")
    key = hashlib.sha1(str(Path(root).resolve()).lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    cache = Path(cache_dir) / "bsla" / key
    timeout = int(env.get("bslAnalyzerTimeout") or TIMEOUT)
    rels = [m["path"] for m in modules]
    after, error = _analyze(exe, root, rels, cfg_path, cache / "after", work / "out-after", extension, parent,
                            timeout)
    info = {"modules": len(modules), "config": sources}
    if error:
        info["error"] = error
        return [], info
    texts = {}
    for m in modules:
        if m.get("status") != "A":
            texts[m["path"]] = read_base(m.get("renamedFrom") or m["path"])
    before = {}
    if any(after.get(r) for r in rels):
        owners = {tuple(r.split("/")[:2]) if r.count("/") >= 2 else tuple(r.split("/")) for r in rels}
        mroot = mirror(root, work / "before", owners)
        try:
            for rel in rels:
                if texts.get(rel) is None:
                    (mroot / rel).unlink(missing_ok=True)
                else:
                    _write(mroot / rel, texts[rel])
            had = [r for r in rels if texts.get(r) is not None]
            if had:
                before, error = _analyze(exe, mroot, had, cfg_path, cache / "before", work / "out-before",
                                         extension, parent, timeout)
                if error:
                    info["error"] = f"редакция «до»: {error}"
                    return [], info
        finally:
            remove_mirror(mroot)
    hidden = protected_modules(parent or root) | protected_modules(root)
    findings, dropped = [], collections.Counter()
    for m in modules:
        rel = m["path"]
        old = (texts.get(rel) or "").splitlines()
        new = (read_head(rel) or "").splitlines()
        kind = getattr(lay.parse(rel), "kind", None)
        seen = {}
        for d in before.get(rel, []):
            k = bsl_ls.diag_key({"code": d["code"], "message": d["message"], "range": {"start": {"line": d["line"]}}},
                                old)
            seen[k] = seen.get(k, 0) + 1
        for d in after.get(rel, []):
            k = bsl_ls.diag_key({"code": d["code"], "message": d["message"], "range": {"start": {"line": d["line"]}}},
                                new)
            if seen.get(k):
                seen[k] -= 1
                continue
            hit = _NOT_FOUND.search(d["message"]) if d["code"] == "UnresolvedMethodCall" else None
            if hit and hit.group(1).lower() in hidden:
                dropped["UnresolvedMethodCall (модуль без исходников)"] += 1
                continue
            # ЭтотОбъект — локальная переменная или параметр (общий модуль, менеджер): присваивание законно
            if d["code"] == "ThisObjectAssign" and not bsl_ls.this_object_property(kind, new, d["line"]):
                dropped["ThisObjectAssign"] += 1
                continue
            findings.append({"check": f"bsla:{d['code']}",
                             "severity": "error" if d["code"] in bsl_ls.FAIL_CODES else "warning",
                             "module": rel, "line": d["line"] + 1, "message": d["message"]})
    if dropped:
        info["dropped"] = dict(dropped)
    return findings, info
