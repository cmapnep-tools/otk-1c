"""Рабочее место прогона: каталог прогона, выгрузка редакции git, каталог выгрузки плана."""
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

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
    if configured:
        _warn_inside(Path(configured), path)
    return base / f"{path.name}-{key}"


def _warn_inside(base, repo):
    """Предупреждение (раз за процесс), если runDir внутри рабочего дерева: пути извлечённых редакций длиннее
    на путь репозитория, а каталог прогона виден git как неотслеживаемый."""
    base = base.resolve()
    if base in _warned_inside or not base.is_relative_to(repo):
        return
    _warned_inside.add(base)
    print(f"runDir {base} — внутри рабочего дерева {repo}: на Windows пути извлечённых редакций могут превысить "
          f"{MAX_PATH} символов; задайте короткий каталог вне репозитория (OTK_RUN_DIR или environment.runDir)",
          file=sys.stderr)


def materialize(repo, sha, src, trees_dir):
    """Выгрузка в редакции sha (git archive) в каталоге прогона; повторно не извлекается. Готовность — маркер
    <начало sha>.done рядом с деревом: остаток неудачного извлечения (не удалился целиком) не принимается."""
    target = Path(trees_dir) / sha[:TREE_SHA]
    done = target.with_name(f"{target.name}.done")
    try:                                            # is_file() до Python 3.12 пропускает PermissionError
        ready = done.is_file() and layout.detect(target / src)
    except OSError as e:
        raise _env_error("готовая редакция", Path(e.filename or target), e) from e
    if ready:
        return target
    try:
        done.unlink(missing_ok=True)                # до очистки: оборвётся — остаток без маркера
    except OSError as e:
        raise _env_error("маркер редакции", done, e) from e
    shutil.rmtree(target, ignore_errors=True)       # остаток прошлого извлечения — заново
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
    if rc or extract_error or tar_error:
        shutil.rmtree(target, ignore_errors=True)   # маркера нет — остаток следующий прогон извлечёт заново
    if extract_error:
        raise gitdiff.GitError(f"извлечение редакции {sha[:TREE_SHA]} в {target}: {extract_error}"
                               + _long_path_hint(extract_error, target))
    if rc:
        raise gitdiff.GitError(f"git archive {sha} -- {src}: {stderr[-500:]}" + _lfs_hint(stderr, sha))
    if tar_error:
        raise gitdiff.GitError(f"извлечение редакции {sha[:TREE_SHA]} в {target}: {tar_error}")
    try:
        done.touch()
    except OSError as e:                      # дерево готово, но без маркера следующий прогон извлечёт заново
        raise _env_error("маркер редакции", done, e) from e
    return target


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
