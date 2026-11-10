"""Рабочее место прогона: каталог прогона, выгрузка редакции git, каталог выгрузки плана."""
import errno
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from . import gitdiff, layout

# фильтр извлечения — с Python 3.11.4; раньше архив своего git извлекается без него
SAFE_EXTRACT = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}


MAX_PATH = 260                  # предел длины пути Windows без LongPathsEnabled
TREE_SHA = 12                   # каталог редакции — trees/<начало sha>: длинные пути выгрузки ERP и так у предела
_warned_inside = set()


def run_dir(cfg, repo):
    """Каталог прогона: <runDir>/<имя репозитория>-<хеш пути> — одноимённые рабочие деревья и клоны не делят
    планы и сводки; состояние копии ИБ лежит рядом с ней (build.state_path)."""
    configured = cfg["environment"].get("runDir")
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "otk"
    path = Path(repo).resolve()
    key = hashlib.sha1(str(path).lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    _warn_inside(base, path)
    return base / f"{path.name}-{key}"


def _warn_inside(base, repo):
    """Предупреждение (раз за процесс), если runDir внутри рабочего дерева: пути извлечённых редакций длиннее
    на путь репозитория, а каталог прогона виден git как неотслеживаемый."""
    try:
        base = base.resolve()
    except (OSError, RuntimeError):         # цикл ссылок (RuntimeError до Python 3.13) — без предупреждения
        return
    if base in _warned_inside or not base.is_relative_to(repo):
        return
    _warned_inside.add(base)
    print(f"runDir {base} — внутри рабочего дерева {repo}: на Windows пути извлечённых редакций могут превысить "
          f"{MAX_PATH} символов; задайте короткий каталог вне репозитория (OTK_RUN_DIR или environment.runDir)",
          file=sys.stderr)


def materialize(repo, sha, src, trees_dir):
    """Источник src в редакции sha (git archive) в общем дереве редакции trees/<начало sha>: источник плана и
    расширения сценариев извлекаются в одно дерево, каждый — один раз. Готовность — маркер на пару редакция +
    src рядом с деревом (done_marker) или маркер объемлющего источника: остаток неудачного извлечения не
    принимается, а извлекается заново поверх — содержимое редакции неизменно, перезапись даёт полное дерево,
    поэтому ничего не удаляется. Сбой извлечения снимает и маркеры пересекающихся источников: недописанный файл
    мог попасть в их поддерево."""
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha or ""):
        raise gitdiff.GitError(f"редакция {sha}: нужен sha коммита")
    rel = PurePosixPath(Path(src).as_posix())
    if Path(src).anchor or rel.is_absolute() or ".." in rel.parts:
        raise gitdiff.GitError(f"источник {src}: нужен относительный путь внутри репозитория")
    target = Path(trees_dir) / sha[:TREE_SHA]
    done = done_marker(target, src)
    try:
        ready = _covered(target, rel) and layout.detect(target / src)
    except OSError as e:
        raise _env_error("готовая редакция", Path(e.filename or target), e) from e
    if ready:
        return target
    _drop_markers(target, rel)                      # до извлечения: оборвётся — остаток без маркера
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise _env_error("каталог редакции", target, e) from e
    # проект EDT: экспорт 1cedtcli требует ещё .project и DT-INF рядом с src
    proj = Path(src).parent.as_posix()
    extra = [p for p in ((f"{proj}/{n}" if proj != "." else n) for n in (".project", "DT-INF"))
             if Path(src).name == "src"
             and gitdiff.git(repo, "rev-parse", "--verify", "-q", f"{sha}:{p}", check=False).strip()]
    extract_error = tar_error = None
    try:
        err = tempfile.TemporaryFile()        # stderr — в файл: канал мог бы заполниться, пока читаем stdout
    except OSError as e:                      # без gettempdir(): каталога могло не найтись вовсе, он сам бросит
        raise gitdiff.GitError(f"временный файл для git archive: {e}") from e
    with err:
        proc = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", sha, "--", src, *extra],
                                stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL)
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                tar.extractall(target, **SAFE_EXTRACT)  # noqa: S202 — фильтр data, где он есть
        except tarfile.TarError as e:         # git оборвал архив (причина в stderr) или фильтр отклонил запись
            tar_error = e
        except OSError as e:                  # не записался файл: чаще всего путь длиннее MAX_PATH на Windows
            extract_error = e
        # tarfile останавливается на маркере конца архива, а git ещё пишет выравнивание: без дочитывания
        # git блокируется на записи в канал (на Windows буфер мал), а мы — на wait()
        while proc.stdout.read(1 << 16):
            pass
        proc.stdout.close()
        rc = proc.wait()
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace").strip()
    if rc or extract_error or tar_error:            # маркера нет — остаток следующий прогон извлечёт заново
        try:
            _drop_markers(target, rel)              # пересекающиеся маркеры могли появиться во время извлечения
        except gitdiff.GitError:
            pass                                    # не скрывать причину сбоя извлечения
    if extract_error:
        raise gitdiff.GitError(f"извлечение редакции {sha[:TREE_SHA]} в {target}: {extract_error}"
                               + _long_path_hint(extract_error, target))
    if rc:
        raise gitdiff.GitError(f"git archive {sha} -- {src}: {stderr[-500:]}" + _lfs_hint(stderr, sha))
    if tar_error:
        raise gitdiff.GitError(f"извлечение редакции {sha[:TREE_SHA]} в {target}: {tar_error}")
    # атомарно: оборванная запись не оставит маркер с префиксом src; временный файл свой у каждого вызова —
    # параллельные прогоны не подменяют и не удаляют его друг у друга
    try:
        fd, tmp = tempfile.mkstemp(prefix=done.name + ".", suffix=".tmp", dir=done.parent)
    except OSError as e:
        raise _env_error("маркер редакции", done, e) from e
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(rel.as_posix())                 # src в маркере — для поиска пересечений
        tmp.replace(done)
    except OSError as e:                      # дерево готово, но без маркера следующий прогон извлечёт заново
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass                              # не скрывать исходную ошибку
        raise _env_error("маркер редакции", done, e) from e
    return target


def done_marker(target, src):
    """Маркер готовности источника src в дереве редакции target: <начало sha>-<хеш src>.done рядом с деревом."""
    key = hashlib.sha1(Path(src).as_posix().encode("utf-8"), usedforsecurity=False).hexdigest()  # полный: без коллизий
    return target.with_name(f"{target.name}-{key}.done")


def _covered(target, rel):
    """Источник rel извлечён целиком: есть его маркер или маркер объемлющего источника (расширение сценариев
    внутри источника плана готово вместе с ним — без повторного извлечения и снятия маркера источника).

    Маркер принимается, только если его имя посчитано от его же содержимого и содержимое непустое: пустой
    маркер иначе «покрывает» любой источник (его сегменты — (), префикс чего угодно), а чужой файл с подходящим
    именем выдаёт себя за готовое дерево. Испорченный маркер — как отсутствующий, дерево извлечётся заново."""
    mine = _parts(rel.as_posix())
    for m in target.parent.glob(f"{target.name}-*.done"):
        try:
            src = m.read_text(encoding="utf-8")     # как записано, без strip: имя сверяется с содержимым целиком
        except (FileNotFoundError, ValueError):     # снят параллельным прогоном или оборван посреди символа
            continue                                # недоступен по правам — ошибка окружения ниже, в materialize
        if not src or m.name != done_marker(target, src).name:
            continue
        other = _parts(src)
        if mine[:len(other)] == other:
            return True
    return False


def _drop_markers(target, rel):
    """Снять маркеры готовности источника rel и пересекающихся с ним (один внутри другого, «.» — со всеми; на
    Windows — без учёта регистра): извлечение rel пишет и в их поддеревья. Нечитаемый маркер снимается тоже."""
    try:
        markers = list(target.parent.glob(f"{target.name}-*.done"))
    except OSError as e:
        raise _env_error("маркеры редакции в", target.parent, e) from e
    mine = _parts(rel.as_posix())
    for m in markers:
        try:
            other = _parts(m.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):               # ValueError — оборванная запись посреди символа UTF-8
            other = ()
        n = min(len(other), len(mine))
        if other[:n] == mine[:n]:
            try:
                m.unlink(missing_ok=True)
            except OSError as e:
                raise _env_error("маркер редакции", m, e) from e


def _parts(src):
    """Сегменты пути источника для сравнения: «.» — пусто, на Windows — без учёта регистра."""
    src = src or "."
    return PurePosixPath(src.casefold() if os.name == "nt" else src).parts


def _env_error(what, path, e):
    """Ошибка окружения при подготовке редакции — сообщение с путём и подсказкой вместо трассировки."""
    return gitdiff.GitError(f"{what} {path}: {e}" + _long_path_hint(e, path))


def _long_path_hint(e, target):
    """Подсказка, если файл редакции не записался из-за длины пути (WinError 206, ENAMETOOLONG или путь на
    Windows не короче MAX_PATH — тогда ошибка «не найден путь»)."""
    path = os.path.abspath(e.filename or target)
    if not (getattr(e, "winerror", None) == 206 or e.errno == errno.ENAMETOOLONG
            or (os.name == "nt" and len(path) >= MAX_PATH)):
        return ""
    return (f" — путь {len(path)} символов: задайте короткий каталог прогонов вне репозитория (OTK_RUN_DIR или "
            "environment.runDir, например D:/otk) или включите длинные пути Windows (LongPathsEnabled)")


def _lfs_hint(stderr, sha):
    """Подсказка, если git archive упал на фильтре Git LFS: объектов LFS редакции нет в клоне."""
    if "lfs" not in stderr.lower():
        return ""
    return (f" — объекты Git LFS редакции {sha[:10]} не получены: git lfs fetch <remote> <ветка> "
            "(для текущей ветки — git lfs pull)")


LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
LFS_POINTER_MAX = 1024          # указатель Git LFS по спецификации меньше 1024 байт


def lfs_pointers(root, files=None):
    """Указатели Git LFS вместо содержимого (объекты LFS не получены) среди files (пути от root) или во всём
    дереве root: пути от root. Платформа такие файлы не загружает: «Ошибка формата потока»."""
    root = Path(root)
    entries = ((rel, root / rel) for rel in files) if files is not None else _small_files(root, "")
    found = []
    for rel, p in entries:
        try:
            if files is not None and p.stat().st_size >= LFS_POINTER_MAX:
                continue
            with open(p, "rb") as f:
                if f.read(len(LFS_POINTER)) == LFS_POINTER:
                    found.append(rel)
        except OSError:
            continue
    return found


def _small_files(d, prefix):
    """(путь от корня, путь) файлов меньше LFS_POINTER_MAX: размер — из записи каталога (на Windows без
    отдельного stat, выгрузка ERP — сотни тысяч файлов)."""
    try:
        it = os.scandir(d)
    except OSError:
        return
    with it:
        for e in it:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not e.name.startswith("."):         # .git, .otk — не выгрузка
                        yield from _small_files(e.path, f"{prefix}{e.name}/")
                elif e.stat().st_size < LFS_POINTER_MAX:
                    yield f"{prefix}{e.name}", Path(e.path)
            except OSError:
                continue


def tree_root(repo, plan, run_dir):
    if plan.get("headDir"):
        return Path(plan["headDir"])
    head_sha = plan.get("headSha")
    if head_sha and head_sha != gitdiff.rev(repo, "HEAD"):
        return materialize(repo, head_sha, plan["source"], run_dir / "trees")
    return repo


def plan_root(repo, plan_path, cfg):
    """Каталог выгрузки плана (для распознавания расширения): headDir или <проект>/<source>."""
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Path(repo) / cfg["project"]["source"]
    return Path(plan["headDir"]) if plan.get("headDir") else Path(repo) / plan.get("source", cfg["project"]["source"])
