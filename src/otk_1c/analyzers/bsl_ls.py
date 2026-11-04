"""BSL Language Server до и после: новые замечания в изменённых модулях (спецификация static-checks).

Редакции «до» и «после» раскладываются в два каталога и анализируются отдельными запусками (большие
прогоны — пакетами по байтам): BSL LS читает метаданные только одной конфигурации рабочего каталога, и в общем
запуске правила, которым нужен вид модуля (EventHandlerInvalidSignature), молчали бы у «до». Замечание
сравнивается по (код, текст, содержимое строки без пробелов) — номера строк сдвигаются правками.
Новое замечание из FAIL_CODES — error, остальные — warning: уровень Error
в BSL LS стоит и у стилистических правил. Конфигурация анализатора — .bsl-language-server.json проекта,
если есть, иначе встроенный набор критических диагностик CRITICAL: со всеми диагностиками
BSL LS на выгрузке ERP — 0,53 с/модуль и 45 тыс. замечаний, в основном стиль; с набором — 0,22 с/модуль.
"""
import collections
import fnmatch
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

from .. import lang
from ..sources import bsl, index as index_mod, layout


def diag_key(diag, lines):
    no = diag["range"]["start"]["line"]
    text = lines[no].strip() if no < len(lines) else ""
    return diag["code"], diag["message"], " ".join(text.split())


# Новая — error. ParseError и ProcedureReturnsValue не пропустит и компиляция
# платформы; UnknownPreprocessorSymbol платформа молча выкидывает вместе с блоком, ThisObjectAssign
# компилирует (проверено на 8.3.27) — их ловит только BSL LS. Встроенный список обоих анализаторов: раздел
# [analyzerSeverity] настроек его поправляет (analyzers.fail_codes)
FAIL_CODES = {"ParseError", "ProcedureReturnsValue", "UnknownPreprocessorSymbol", "ThisObjectAssign"}

# Встроенный набор (mode: only) — ошибки, а не стиль. Без MissingCommonModuleMethod и
# MissingEventSubscriptionHandler: по частичной раскладке анализатор не видит чужих общих модулей, а то же
# самое проверяет статика ОТК по индексу всей выгрузки (common-method, подписки). Без
# ServerSideExportFormMethod: нарушение стандарта, а не ошибка
CRITICAL = (
    "ParseError", "QueryParseError", "UnreachableCode", "FunctionShouldHaveReturn", "AllFunctionPathMustHaveReturn",
    "MissedRequiredParameter", "EventHandlerInvalidSignature", "SelfAssign", "SelfInsertion", "IdenticalExpressions",
    "IfElseDuplicatedCondition", "DeletingCollectionItem", "PairingBrokenTransaction",
    "WrongUseOfRollbackTransactionMethod", "CommitTransactionOutsideTryCatch", "BeginTransactionBeforeTryCatch",
    "DuplicatedInsertionIntoCollection", "IncorrectUseOfStrTemplate",
    "ProcedureReturnsValue", "UnknownPreprocessorSymbol", "CompilationDirectiveLost", "CompilationDirectiveNeedLess",
    "ThisObjectAssign", "MultilineStringInQuery", "WrongUseFunctionProceedWithCall", "WrongDataPathForFormElements",
    "WrongHttpServiceHandler", "WrongWebServiceHandler", "ReservedParameterNames", "UsingCancelParameter",
    "UsingModalWindows", "ExtraCommas", "UseLessForEach")

# Таймаут, с на файл (редакции «до» и «после» — отдельные файлы) по замеру ERP с запасом вдвое:
# встроенный набор — 0,22, своя конфигурация проекта может включать всё — 0,53
SECONDS_PER_FILE = {"builtin": 0.5, "project": 1.2}

# Бюджет пакета, байт текста модулей и метаданных обеих редакций: выгрузка ERP (1,4 ГБ) одним
# запуском роняет BSL LS с OutOfMemoryError при -Xmx4g дистрибутива. Замер на случайных выборках ERP: живая
# куча 0,46 ГБ на 5 МБ, 1,5 ГБ на 100 МБ, 2,5 ГБ на 200 МБ, на 400 МБ упирается в 4 ГБ; время — 1,1 с/МБ и ~60 с
# на запуск. 200 МБ — запас под 4 ГБ; на меньшей куче выручает деление пакета пополам при OutOfMemoryError
BATCH_BYTES = 200 * 2**20


def builtin_config(path, help_dir=None):
    """Пишет конфигурацию BSL LS со встроенным набором CRITICAL; возвращает путь. help_dir — см. analyzer_config."""
    data = {"language": "ru", "diagnostics": {"mode": "only", "parameters": {code: True for code in CRITICAL}}}
    if help_dir:
        data["v8platform"] = {"binPath": str(help_dir)}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return Path(path)


# правила BSL LS, которые без справки платформы молчат (сигнатуры и области обработчиков событий)
NEEDS_HELP = "EventHandlerInvalidSignature, EventHandlerOutsideEventRegion"


def analyzer_config(work, config, help_dir):
    """Путь конфигурации запуска: проектная, иначе встроенная (builtin_config). help_dir — каталог с
    shcntx_ru.hbk: без справки установленной платформы (Linux, контейнер) BSL LS молча выключает правила
    NEEDS_HELP, поэтому справка, известная ОТК, передаётся ключом v8platform.binPath — и в копию проектной
    конфигурации, если в ней своего ключа нет."""
    if not (config and Path(config).exists()):
        return builtin_config(work / "bsl-language-server.json", help_dir).resolve()
    if help_dir:
        try:
            data = json.loads(Path(config).read_text(encoding="utf-8-sig"))
        except ValueError:
            data = None                                   # нечитаемую конфигурацию пусть разбирает сам BSL LS
        if isinstance(data, dict) and not (data.get("v8platform") or {}).get("binPath"):
            data["v8platform"] = {**(data.get("v8platform") or {}), "binPath": str(help_dir)}
            path = work / "bsl-language-server.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            return path.resolve()
    return Path(config).resolve()


def excluded_by(config):
    """Предикат по excludePaths конфигурации BSL LS: модули раскладываются во временный каталог,
    поэтому сам анализатор шаблоны проекта к ним не применит."""
    patterns = []
    if config and Path(config).exists():
        try:
            patterns = json.loads(Path(config).read_text(encoding="utf-8-sig")).get("excludePaths", [])
        except ValueError:
            patterns = []
    globs = [p.replace("\\", "/").replace("**", "*") for p in patterns]
    return lambda rel: any(fnmatch.fnmatch("/" + rel, g) or fnmatch.fnmatch(rel, g) for g in globs)


def metadata_files(rel, lay=layout.DESIGNER):
    """Файлы метаданных, по которым BSL LS понимает вид модуля и его обработчики: корень конфигурации,
    описание объекта, описание и Form.xml формы. Без них обработчики событий формы и команды выглядят
    неиспользуемыми методами (UnusedLocalMethod), а реквизиты формы — переменными."""
    a = lay.parse(rel)
    out = [lay.config_file]
    if a is not None and a.collection:
        out.append(lay.object_file(a.collection, a.name))
        if a.kind == "FormModule":
            out += [f for f in lay.form_files(a.collection, a.name, a.sub) if f not in out]
    return out


def _owner(rel, lay=layout.DESIGNER):
    """Неделимая единица пакета — объект метаданных (модули объекта, менеджера и форм — с общими метаданными
    и формой рядом с её модулем); модуль корня конфигурации — сам по себе."""
    return lay.owner_key(rel)


def pack(sizes, budget):
    """Пакеты по байтам, first-fit decreasing: sizes — {единица: байты}. Единица больше бюджета — пакет из неё
    одной. Возвращает списки единиц, каждый — по убыванию размера."""
    bins = []
    for unit in sorted(sizes, key=lambda u: (-sizes[u], u)):
        for b in bins:
            if b[0] + sizes[unit] <= budget:
                b[0] += sizes[unit]
                b[1].append(unit)
                break
        else:
            bins.append([sizes[unit], [unit]])
    return [b[1] for b in bins]


def _link(src, dest):
    if src.exists() and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dest)
        except OSError:
            shutil.copyfile(src, dest)


def _analyze(exe, cfg_path, work, timeout):
    """Один запуск BSL LS по work/src: (отчёт, None) или (None, причина)."""
    out = work / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    # cwd — каталог работы: BSL LS считает пути относительно текущего каталога и падает на другом диске
    try:
        r = subprocess.run([str(exe), "analyze", "-q", "-c", str(cfg_path), "-s", "src", "-w", ".", "-r", "json",
                            "-o", "out"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=str(work))
    except subprocess.TimeoutExpired:
        return None, f"не уложился в {timeout} с (environment.bsllsTimeout)"
    report = out / "bsl-json.json"
    if r.returncode or not report.exists():
        text = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + r.stderr).decode("utf-8", "replace"))
        causes = [ln.strip() for ln in text.splitlines() if "Exception" in ln or ln.startswith("Caused")]
        return None, "; ".join(causes[:3]) or text[-300:]
    return json.loads(report.read_text(encoding="utf-8")), None


class _Stage:
    """Модули плана, разложенные по редакциям: <work>/src/before|after/<путь> вместе с метаданными (metadata_files).
    files — {(редакция, путь): номер модуля}, rels — обратно, sizes — байт по объекту-владельцу, units —
    {владелец: номера его модулей} (все модули объекта — в одном пакете)."""

    def __init__(self, root, modules, read_base, read_head, lay):
        self.root, self.lay = root, lay
        # каталог редакции внутри src: «after» или, для EDT, «after/src» (lay.project_src)
        self.tops = {s: f"{s}/{lay.project_src}".rstrip("/") for s in ("before", "after")}
        self.files, self.rels = {}, {}
        self.sizes, self.units = collections.Counter(), collections.defaultdict(set)
        for side, reader in (("before", read_base), ("after", read_head)):
            for n, entry in enumerate(modules):
                rel = entry["path"] if side == "after" else entry.get("renamedFrom") or entry["path"]
                text = None if side == "before" and entry.get("status") == "A" else reader(rel)
                # защищённая редакция EDT — как отсутствующая: при защищённой «до» все замечания «после» новые
                if text is None or (lay.has_protected and lay.protected(text)):
                    continue
                self._put(side, n, entry, rel, text, reader)

    def _put(self, side, n, entry, rel, text, reader):
        unit = _owner(entry["path"], self.lay)
        for meta in metadata_files(rel, self.lay):
            dest = self.path(side, meta)
            if not dest.exists() and (content := reader(meta)) is not None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                data = b"\xef\xbb\xbf" + content.encode("utf-8")
                dest.write_bytes(data)
                self.sizes[unit] += len(data) if meta != self.lay.config_file else 0
        p = self.path(side, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = b"\xef\xbb\xbf" + text.encode("utf-8")
        p.write_bytes(data)
        self.sizes[unit] += len(data)
        self.units[unit].add(n)
        self.files[(side, rel)] = n
        self.rels[(side, n)] = rel

    def path(self, side, rel):
        return self.root / self.tops[side] / rel

    def lines(self, side, n):
        return (self.path(side, self.rels[(side, n)]).read_text(encoding="utf-8-sig").splitlines()
                if (side, n) in self.rels else [])


def run(exe, work_dir, modules, read_base, read_head, timeout=None, config=None, batch_bytes=BATCH_BYTES,
        lay=layout.DESIGNER, help_dir=None):
    """modules — элементы плана; read_base/read_head(rel) -> текст файла выгрузки в редакции «до» / «после»
    или None. Модули раскладываются по своим путям вместе с метаданными (metadata_files) — как в выгрузке.
    Больше batch_bytes текста — анализ пакетами (pack): обе редакции модуля и все модули объекта — в одном
    пакете; пакет, упавший с OutOfMemoryError, делится пополам. Редакции пакета — отдельными запусками, каждая в
    своём каталоге со своими метаданными; «до» — только для модулей с замечаниями «после». timeout — секунды
    на запуск; по умолчанию растёт с числом файлов. help_dir — справка платформы (analyzer_config).
    Возвращает (findings, info)."""
    work = Path(work_dir) / "bslls"
    shutil.rmtree(work, ignore_errors=True)
    excluded = excluded_by(config)
    modules = [m for m in modules if not excluded(m["path"])]
    stage = _Stage(work / "src", modules, read_base, read_head, lay)
    if not stage.files:
        return [], {"skipped": "нет модулей"}
    own = bool(config and Path(config).exists())
    cfg_path = analyzer_config(work, config, help_dir)
    diags, failed, runs = _analyze_batches(exe, cfg_path, work, stage, batch_bytes, timeout, own)
    findings, dropped = _new_findings(modules, diags, stage)
    info = {"modules": len(modules), "config": str(config) if own else "встроенный набор"}
    try:
        used = json.loads(cfg_path.read_text(encoding="utf-8-sig")).get("v8platform", {}).get("binPath")
    except (ValueError, AttributeError):
        used = None
    if used:
        info["help"] = str(used)                          # справка платформы, с которой работал анализатор
    if runs > 1:
        info["batches"] = runs
    if dropped:
        info["dropped"] = dict(dropped)
    if failed:
        count = sum(c for c, _ in failed)
        info["error"] = (failed[0][1] if count == len(modules) else
                         f"не проверено модулей {count} из {len(modules)}: {failed[0][1]}")
    return findings, info


def _analyze_batches(exe, cfg_path, work, stage, batch_bytes, timeout, own):
    """Запуски по пакетам. Возвращает ({(редакция, номер модуля): замечания}, [(модулей, ошибка)], запусков)."""
    queue, diags, failed, runs = pack(stage.sizes, batch_bytes), collections.defaultdict(list), [], 0
    while queue:
        batch = queue.pop(0)
        nums = set().union(*(stage.units[u] for u in batch))
        keys = [k for k, n in stage.files.items() if n in nums]
        runs += 1
        found, error = collections.defaultdict(list), None
        for side in ("after", "before"):
            # «до» нужна только модулям с замечаниями «после»: чистая правка — один запуск
            side_keys = [k for k in keys if k[0] == side and (side == "after" or found[("after", stage.files[k])])]
            if not side_keys:
                continue
            bdir = work / f"batch{runs}-{side}"
            for _, rel in side_keys:
                for f in (rel, *metadata_files(rel, stage.lay)):
                    _link(stage.path(side, f), bdir / "src" / stage.tops[side] / f)
            limit = timeout or max(600, int(SECONDS_PER_FILE["project" if own else "builtin"] * len(side_keys)))
            data, error = _analyze(exe, cfg_path, bdir, limit)
            root = (bdir / "src").resolve().as_posix().lower()
            shutil.rmtree(bdir, ignore_errors=True)
            if error:
                break
            for fi in data["fileinfos"]:
                rel = _report_rel(fi["path"], root, stage.tops[side])
                if (side, rel) in stage.files:
                    found[(side, stage.files[(side, rel)])].extend(fi["diagnostics"])
        if error and "OutOfMemoryError" in error and len(batch) > 1:
            queue[:0] = [batch[::2], batch[1::2]]
        elif error:
            failed.append((len(nums), error))
        else:
            for k, v in found.items():
                diags[k].extend(v)
    return diags, failed, runs


def _report_rel(uri, root, top):
    """Путь файла отчёта BSL LS относительно каталога редакции top или пустая строка."""
    # file:///D:/x (Windows) -> D:/x, file:///home/x (Linux) -> /home/x
    path = unquote(re.sub(r"^file://", "", uri)).replace("\\", "/")
    path = re.sub(r"^/([A-Za-z]:/)", r"\1", path)
    tail = path[len(root) + 1:] if path.lower().startswith(root) else ""
    return tail[len(top) + 1:] if tail.startswith(top + "/") else ""


def _new_findings(modules, diags, stage):
    """Замечания «после», которых не было «до»; снятые ложные — счётчиком по коду. Возвращает (находки, снятые)."""
    findings, dropped = [], collections.Counter()
    for n, entry in enumerate(modules):
        if not diags[("after", n)]:
            continue
        old, new = stage.lines("before", n), stage.lines("after", n)
        kind = getattr(stage.lay.parse(entry["path"]), "kind", None)
        before = collections.Counter(diag_key(d, old) for d in diags[("before", n)])
        for d in diags[("after", n)]:
            k = diag_key(d, new)
            if before[k] > 0:
                before[k] -= 1
                continue
            if d["code"] == "ParseError" and dot_break(new, d["range"]["start"]["line"]):
                dropped["ParseError"] += 1
                continue
            if d["code"] == "ThisObjectAssign" and not this_object_property(kind, new, d["range"]["start"]["line"]):
                dropped["ThisObjectAssign"] += 1
                continue
            findings.append({"check": f"bslls:{d['code']}",
                             "severity": "error" if d["code"] in FAIL_CODES else "warning",
                             "module": entry["path"], "line": d["range"]["start"]["line"] + 1,
                             "message": d["message"]})
    return findings, dropped


def _code(line):
    """Строка без комментария и признак незакрытой строки-литерала."""
    quoted = False
    for i, c in enumerate(line):
        if c == '"':
            quoted = not quoted
        elif not quoted and line.startswith("//", i):
            return line[:i].rstrip(), False
    return line.rstrip(), quoted


def _method(lines, no):
    """Границы метода (номера строк с 0), в котором строка no; вне методов — от конца предыдущего метода до
    начала следующего."""
    header, end_line = bsl.header_re(paren=False), bsl.end_re()
    start = no
    while start > 0 and not header.match(lines[start]) and not (start < no and end_line.match(lines[start])):
        start -= 1
    end = no
    while end < len(lines) - 1 and not end_line.match(lines[end]) and not (end > no and header.match(lines[end])):
        end += 1
    return start, end


def dot_break(lines, no):
    """Известный дефект парсера BSL LS: перенос строки сразу после точки, имя члена — на
    следующей строке (`Модуль.` / `Метод()`). Платформа такой код компилирует, а BSL LS выдаёт ParseError
    на этой строке и каскадом до конца метода. True — в методе с ошибкой разбора есть строка кода,
    оканчивающаяся точкой после имени или скобки: замечание не выдаётся."""
    start, end = _method(lines, min(no, len(lines) - 1)) if lines else (0, -1)
    for line in lines[start:end + 1]:
        text, quoted = _code(line)
        if not quoted and not text.lstrip().startswith("|") and len(text) > 1 and text.endswith(".") \
                and (text[-2].isalnum() or text[-2] in "_)]"):
            return True
    return False


# Виды модулей, где ЭтотОбъект — свойство (присвоить нельзя); в общих модулях, модулях менеджера, команд и
# корня конфигурации это обычная переменная
THIS_OBJECT_KINDS = {"ObjectModule", "FormModule", "RecordSetModule", "ValueManagerModule"}
_THIS_OBJECT = r"(?:ЭтотОбъект|ThisObject)"
_PARAM = re.compile(r"[(,]\s*(?:Знач\s+|Val\s+)?" + _THIS_OBJECT + r"\s*[=,)]", re.I)
_VAR = re.compile(r"^\s*(?:Перем|Var)\s[^;]*(?<![\w.])" + _THIS_OBJECT + r"(?!\w)", re.I)


def this_object_property(kind, lines, no):
    """ThisObjectAssign — ошибка, только если ЭтотОбъект в этом месте — свойство модуля: вид
    модуля из THIS_OBJECT_KINDS и нет одноимённого параметра метода или переменной (Перем метода или модуля)."""
    if kind not in THIS_OBJECT_KINDS or not lines:
        return kind in THIS_OBJECT_KINDS
    no = min(no, len(lines) - 1)
    start, _ = _method(lines, no)
    if bsl.header_re(paren=False).match(lines[start]):
        header, depth = [], 0
        for line in lines[start:no + 1]:
            text = _code(line)[0]
            header.append(text)
            depth += text.count("(") - text.count(")")
            if depth <= 0 and "(" in "".join(header):
                break
        if _PARAM.search(" ".join(header)):
            return False
        local = lines[start + 1:no + 1]
    else:
        local = lines[:no + 1]
    module_vars = []
    for line in lines:
        if bsl.header_re(paren=False).match(line):
            break
        module_vars.append(line)
    return not any(_VAR.match(_code(line)[0]) for line in local + module_vars)


_QUOTED = re.compile(r'"([^".\s]+)\.([^".\s]+)')
# сообщение MissingCommonModuleMethod (ru, en): метод, общий модуль
_MISSING_METHOD = re.compile(r"Метод (\S+) общего модуля (\S+) не существует|The method (\S+) of (\S+) common module")


def drop_known(findings, index):
    """Замечания по частичной раскладке о том, чего в ней нет, а в индексе метаданных (своя выгрузка + основная
    конфигурация) есть. QueryToMissingMetadata: в раскладке только метаданные проверяемых модулей, и BSL LS
    считает отсутствующим любой другой объект из запроса. MissingCommonModuleMethod: заимствованный общий модуль
    расширения в раскладке — без методов основной конфигурации. Замечание остаётся, только если объекта или
    экспортного метода нет и в индексе."""
    types = {tag.lower(): coll for tag, coll in index_mod.COLLECTIONS.items()}
    for variant in ("Russian", "English"):
        for coll, info in lang.load(variant).get("metadataTypes", {}).items():
            if info.get("single"):
                types[info["single"].lower()] = coll
    modules = index.get("commonModules", {})
    out = []
    for f in findings:
        if f["check"] == "bslls:QueryToMissingMetadata":
            m = _QUOTED.search(f["message"])
            coll = types.get(m.group(1).lower()) if m else None
            if coll and m.group(2).lower() in index["objects"].get(coll, {}):
                continue
        elif f["check"] == "bslls:MissingCommonModuleMethod":
            m = _MISSING_METHOD.search(f["message"])
            method, module = (m.group(1) or m.group(3), m.group(2) or m.group(4)) if m else ("", "")
            if method.lower() in modules.get(module.lower(), {}).get("exports", {}):
                continue
        out.append(f)
    return out
