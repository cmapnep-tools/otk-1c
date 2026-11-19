"""Рабочее место прогона: каталог прогона, выгрузка редакции git, каталог выгрузки плана."""
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from . import gitdiff, layout

MAX_PATH = 260                  # предел длины пути Windows без LongPathsEnabled
TREE_SHA = 12                   # каталог редакции — trees/<начало sha>: длинные пути выгрузки ERP и так у предела
_warned_inside = set()

if os.name == "nt":
    _compare_string_ordinal = vars(ctypes)["windll"].kernel32.CompareStringOrdinal
    _compare_string_ordinal.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int]
    _compare_string_ordinal.restype = ctypes.c_int


def run_dir(cfg, repo):
    """Каталог прогона: <runDir>/<имя репозитория>-<хеш пути> — одноимённые рабочие деревья и клоны не делят
    планы и сводки; состояние копии ИБ лежит рядом с ней (build.state_path)."""
    configured = cfg["environment"].get("runDir")
    base = Path(configured) if configured else Path(tempfile.gettempdir()) / "otk"
    path = Path(repo).resolve()
    key = hashlib.sha1(str(path).lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    _warn_inside(base, path)
    return base / f"{path.name}-{key}"


def new_run(cfg, repo):
    """Атомарно выделить короткий каталог gate; незавершённые прогоны тоже сохраняются."""
    root = run_dir(cfg, repo)
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="r", dir=root))
    (run / ".otk-run").touch()
    return run


def latest_run(root):
    """Последний завершённый gate; старый collect остаётся запасным вариантом."""
    root = Path(root)
    completed = list(root.glob("r*/.completed"))
    return max(completed, key=lambda p: (p.stat().st_mtime_ns, p.parent.name)).parent if completed else root


def latest_plan(root):
    """План последнего завершённого gate или более свежего отдельного collect."""
    root = Path(root)
    collected = root / "otk.json"
    completed = latest_run(root)
    if completed == root:
        return collected
    try:
        if collected.stat().st_mtime_ns > (completed / ".completed").stat().st_mtime_ns:
            return collected
    except FileNotFoundError:
        pass
    return completed / "otk.json"


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
    """Исходные Git blobs источника src в редакции sha в общем дереве редакции trees/<начало sha>: источник плана и
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
    try:
        _extract_blobs(repo, sha, rel.as_posix(), extra, target)
    except (OSError, gitdiff.GitError) as e:
        try:
            _drop_markers(target, rel)
        except gitdiff.GitError:
            pass                                    # не скрывать причину сбоя извлечения
        hint = _long_path_hint(e, target) if isinstance(e, OSError) else ""
        raise gitdiff.GitError(f"извлечение редакции {sha[:TREE_SHA]} в {target}: {e}" + hint) from e
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


def _tree_entries(cmd, sha, paths, *, recursive=True):
    options = ["-r", "-t", "-z"] if recursive else ["-z"]
    listing = subprocess.run(
        [*cmd, "--literal-pathspecs", "ls-tree", *options, sha, "--", *paths],
        capture_output=True, stdin=subprocess.DEVNULL,
    )
    if listing.returncode:
        stderr = listing.stderr.decode("utf-8", "replace").strip()
        raise gitdiff.GitError(f"git ls-tree {sha} -- {paths}: {stderr[-500:]}")
    entries = []
    for record in listing.stdout.split(b"\0")[:-1]:
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split(b" ")
        entries.append((os.fsdecode(name), mode, kind, oid))
    return entries


def git_tree_path(repo: Path, sha: str, path: str) -> str | None:
    """Находит точный путь каталога по Git-дереву, включая ссылки в пути."""
    cmd = ["git", "-C", str(repo)]
    listings: dict[bytes, list[tuple[str, bytes, bytes, bytes]]] = {}
    active: set[tuple[str, ...]] = set()

    def walk(parts: tuple[str, ...], parents: list[tuple[str, bytes]], link_name: str = "") -> bool:
        for part in parts:
            if part == "..":
                if len(parents) == 1:
                    if link_name:
                        raise gitdiff.GitError(f"ссылка за пределы дерева редакции: {link_name}")
                    raise gitdiff.GitError(f"путь за пределы дерева редакции: {path}")
                parents.pop()
                continue
            tree = parents[-1][1]
            if tree not in listings:
                listings[tree] = _tree_entries(cmd, tree.decode("ascii"), [], recursive=False)
            matches = [entry for entry in listings[tree] if _same_part(entry[0], part)]
            if not matches:
                return False
            if len(matches) > 1:
                prefix = [name for name, _ in parents[1:]]
                first, second = ("/".join([*prefix, name]) for name, *_ in matches[:2])
                raise gitdiff.GitError(f"пути Git совпадают на Windows: {first} и {second}")
            name, mode, kind, oid = matches[0]
            if kind == b"tree":
                parents.append((name, oid))
                continue
            if mode != b"120000":
                return False
            key = (*[parent for parent, _ in parents[1:]], name)
            dest = "/".join(key)
            if key in active:
                raise gitdiff.GitError(f"цикл ссылок в пути редакции: {dest}")
            value = _read_git_link(cmd, oid, dest)
            if not value:
                return False
            link = Path(value)
            if link.anchor:
                raise gitdiff.GitError(f"ссылка за пределы дерева редакции: {dest}")
            active.add(key)
            if not walk(link.parts, parents, dest):
                return False
            active.remove(key)
        return True

    parents = [("", sha.encode("ascii"))]
    try:
        if walk(PurePosixPath(path).parts, parents):
            return PurePosixPath(*(name for name, _ in parents[1:])).as_posix()
    except RecursionError as e:
        raise gitdiff.GitError("слишком длинная цепочка ссылок в редакции") from e
    return None


def _extract_blobs(repo, sha, src, extra, target):
    """Список путей и запросы по OID отделены от потока байтов файлов."""
    cmd = ["git", "-C", str(repo)]
    entries = _tree_entries(cmd, sha, [src, *extra] if src != "." else [])
    if src != "." and not any(name == src for name, *_ in entries):
        raise gitdiff.GitError(f"источник {src} отсутствует в Git-редакции {sha}")
    if os.name == "nt":
        _reject_windows_path_collisions(entries, target)

    links = {}
    with tempfile.TemporaryFile() as requests, tempfile.TemporaryFile() as errors:
        requests.writelines(oid + b"\n" for _, _, kind, oid in entries if kind == b"blob")
        requests.seek(0)
        with subprocess.Popen([*cmd, "cat-file", "--batch"], stdin=requests,
                              stdout=subprocess.PIPE, stderr=errors) as proc:
            try:
                for name, mode, kind, oid in entries:
                    link = _write_entry(proc.stdout, target, name, mode, kind, oid)
                    if link is not None:
                        links[name] = link
            except BaseException:
                proc.kill()
                raise
            if proc.wait():
                errors.seek(0)
                stderr = errors.read().decode("utf-8", "replace").strip()
                raise gitdiff.GitError(f"git cat-file {sha} -- {src}: {stderr[-500:]}")
    if links:
        commit_entries = entries if src == "." else _tree_entries(cmd, sha, [])
        _write_symlinks(target, links, cmd, commit_entries)


def _reject_windows_path_collisions(entries, root):
    """Отказаться от Git-дерева, которое Windows не может представить без потери файлов."""
    seen = {}
    existing = {}
    has_existing = any(root.iterdir())
    for name, *_ in entries:
        parts = PurePosixPath(name).parts
        for end in range(1, len(parts) + 1):
            exact = parts[:end]
            folded = tuple(part.casefold() for part in exact)
            candidates = seen.setdefault(folded, {})
            if exact not in candidates:
                for previous in candidates:
                    if all(_same_part(a, b) for a, b in zip(previous, exact, strict=True)):
                        raise gitdiff.GitError(
                            f"пути Git совпадают на Windows: {'/'.join(previous)} и {'/'.join(exact)}"
                        )
                candidates[exact] = None
            if not has_existing:
                continue
            parent_parts = exact[:-1]
            if parent_parts not in existing:
                parent = root.joinpath(*parent_parts)
                names = {}
                if parent.is_dir():
                    with os.scandir(parent) as items:
                        for item in items:
                            names.setdefault(item.name.casefold(), []).append(item.name)
                existing[parent_parts] = names
            for actual in existing[parent_parts].get(exact[-1].casefold(), []):
                if actual != exact[-1] and _same_part(actual, exact[-1]):
                    raise gitdiff.GitError(
                        f"пути Git совпадают на Windows: {'/'.join(exact)} и "
                        f"{'/'.join((*exact[:-1], actual))}"
                    )


def _write_entry(stream, root, name, mode, kind, oid):
    path = Path(name)
    if path.anchor or path.as_posix() != name or ".." in path.parts or (
        os.name == "nt" and any(_reserved_windows_name(part) for part in path.parts)
    ):
        raise gitdiff.GitError(f"недопустимый путь Git: {name}")
    dest = root / path
    try:
        inside = dest.parent.resolve().is_relative_to(root.resolve())
    except RuntimeError as e:                   # цикл ссылок в Python до 3.13
        raise gitdiff.GitError(f"цикл ссылок в пути редакции: {name}") from e
    if not inside:
        raise gitdiff.GitError(f"путь за пределами дерева редакции: {name}")
    if kind in (b"tree", b"commit"):              # gitlink даёт пустой каталог, как при экспорте Git
        if dest.is_symlink():
            raise gitdiff.GitError(f"ссылка вместо каталога редакции: {name}")
        dest.mkdir(parents=True, exist_ok=True)
        return

    header = stream.readline().split()
    if len(header) != 3 or header[:2] != [oid, b"blob"]:
        raise gitdiff.GitError(f"не удалось прочитать Git blob {oid.decode('ascii')} для {name}")
    size = int(header[2])
    dest.parent.mkdir(parents=True, exist_ok=True)
    link = None
    if mode == b"120000":
        link = os.fsdecode(stream.read(size))
    else:
        if dest.is_symlink():
            raise gitdiff.GitError(f"ссылка вместо файла редакции: {name}")
        with dest.open("wb") as f:
            while size:
                data = stream.read(min(size, 1 << 16))
                if not data:
                    raise gitdiff.GitError(f"неполный Git blob для {name}")
                f.write(data)
                size -= len(data)
            if os.name != "nt":
                os.fchmod(f.fileno(), 0o755 if mode == b"100755" else 0o644)
    if stream.read(1) != b"\n":
        raise gitdiff.GitError(f"неполный Git blob для {name}")
    return link


def _reserved_windows_name(name):
    if hasattr(os.path, "isreserved"):
        return os.path.isreserved(name)
    return bool(re.search(r'[\x00-\x1f<>:"|?*]|[. ]$', name)) or Path(name).is_reserved()


def _git_object_for_path(root, objects, folded_objects, path):
    parts = path.relative_to(root).parts
    obj = objects.get(PurePosixPath(*parts).as_posix())
    if obj is not None:
        return path, obj
    if os.name == "nt":
        for actual, obj in folded_objects.get(tuple(part.casefold() for part in parts), []):
            if _same_prefix(parts, actual):
                return root.joinpath(*actual), obj
    return path, None


def _read_git_link(cmd, oid: bytes, name: Path | str) -> str:
    result = subprocess.run([*cmd, "cat-file", "blob", oid.decode("ascii")],
                            capture_output=True, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise gitdiff.GitError(f"не удалось прочитать Git blob ссылки: {name}")
    return os.fsdecode(result.stdout)


def _write_symlinks(root: Path, links: dict[str, str], cmd, entries) -> None:
    """Определяем тип по коммиту, включая ещё не извлечённые соседние источники."""
    resolved_root = root.resolve()
    # Path на Windows может считать разные имена Git равными.
    pending = links.copy()
    objects = {name: (mode, kind, oid) for name, mode, kind, oid in entries}
    folded_objects: dict[tuple[str, ...], list[tuple[tuple[str, ...], tuple[bytes, bytes, bytes]]]] = {}
    if os.name == "nt":
        for name, obj in objects.items():
            parts = PurePosixPath(name).parts
            folded_objects.setdefault(tuple(part.casefold() for part in parts), []).append((parts, obj))
    resolved: dict[str, Path] = {}
    active: set[str] = set()

    def resolve(name: str, link: str) -> Path:
        dest = resolved_root / name
        if name in active:
            raise gitdiff.GitError(f"цикл ссылок в пути редакции: {dest}")
        if Path(link).anchor:
            raise gitdiff.GitError(f"ссылка за пределы дерева редакции: {dest}")
        active.add(name)
        target = dest.parent
        for part in Path(link).parts:
            target = target.parent if part == ".." else target / part
            if not target.is_relative_to(resolved_root):
                raise gitdiff.GitError(f"ссылка за пределы дерева редакции: {dest}")
            _resolve_link_target(target, resolved_root, dest)
            target, obj = _git_object_for_path(resolved_root, objects, folded_objects, target)
            if obj and obj[0] == b"120000":
                target = follow(target.relative_to(resolved_root).as_posix(), obj[2])
        active.remove(name)
        resolved[name] = target
        return target

    def follow(name: str, oid: bytes) -> Path:
        if name in resolved:
            return resolved[name]
        if name in pending:
            return write(name)
        return resolve(name, _read_git_link(cmd, oid, resolved_root / name))

    def write(name: str) -> Path:
        dest = resolved_root / name
        link = pending[name]
        target = resolve(name, link)
        target, obj = _git_object_for_path(resolved_root, objects, folded_objects, target)
        is_directory = target == resolved_root or (obj is not None and obj[1] in (b"tree", b"commit"))
        if not (dest.is_symlink() and os.readlink(dest) == link):
            dest.symlink_to(link, target_is_directory=is_directory)
        del pending[name]
        return target

    try:
        for name in list(pending):
            if name in pending:
                write(name)
    except RecursionError as e:
        raise gitdiff.GitError("слишком длинная цепочка ссылок в редакции") from e


def _resolve_link_target(target: Path, root: Path, dest: Path) -> None:
    try:
        resolved = target.resolve()
    except RuntimeError:                         # циклы ссылок в Python до 3.13
        raise gitdiff.GitError(f"цикл ссылок в пути редакции: {dest}") from None
    if not resolved.is_relative_to(root):
        raise gitdiff.GitError(f"ссылка за пределы дерева редакции: {dest}")


def done_marker(target, src):
    """Маркер готовности источника src в дереве редакции target: <начало sha>-<хеш src>.done рядом с деревом."""
    identity = f"git-blobs-v2:{Path(src).as_posix()}".encode("utf-8")
    key = hashlib.sha1(identity, usedforsecurity=False).hexdigest()
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
            src = _marker_text(m)                   # как записано, без strip: имя сверяется с содержимым целиком
        except (FileNotFoundError, ValueError):     # снят параллельным прогоном или оборван посреди символа
            continue                                # недоступен по правам — ошибка окружения ниже, в materialize
        if not src or m.name != done_marker(target, src).name:
            continue
        other = _parts(src)
        if _same_prefix(mine, other):
            return True
    return False


def _drop_markers(target, rel):
    """Снять маркеры готовности источника rel и пересекающихся с ним (один внутри другого, «.» — со всеми; на
    Windows — без учёта регистра): извлечение rel пишет и в их поддеревья. Нечитаемый маркер снимается тоже.
    Содержимое сверяется как записано, без strip, — так же, как маркер принимается в _covered: иначе маркер
    источника `src/A ` (пробел — часть имени на Linux) не снялся бы при сбое извлечения `src/A /ext`."""
    try:
        markers = list(target.parent.glob(f"{target.name}-*.done"))
    except OSError as e:
        raise _env_error("маркеры редакции в", target.parent, e) from e
    mine = _parts(rel.as_posix())
    for m in markers:
        try:
            other = _parts(_marker_text(m))
        except (OSError, ValueError):               # ValueError — оборванная запись посреди символа UTF-8
            other = ()
        n = min(len(other), len(mine))
        if _same_prefix(other[:n], mine[:n]):
            try:
                m.unlink(missing_ok=True)
            except OSError as e:
                raise _env_error("маркер редакции", m, e) from e


def _marker_text(m):
    """Содержимое маркера как записано: байты, затем UTF-8. read_text перевёл бы CRLF в LF, и маркер с парой
    CRLF прошёл бы за маркер пути с одним переводом строки — имя перестало бы соответствовать содержимому."""
    return m.read_bytes().decode("utf-8")


def _parts(src):
    """Сегменты пути источника для сравнения: «.» — пусто, на Windows — без учёта регистра."""
    src = src or "."
    return PurePosixPath(src).parts


def _same_prefix(parts, prefix):
    return len(parts) >= len(prefix) and all(_same_part(a, b) for a, b in zip(parts, prefix, strict=False))


def _same_part(a, b):
    if a == b:
        return True
    if os.name != "nt" or a.casefold() != b.casefold():
        return False
    return _compare_string_ordinal(a, -1, b, -1, True) == 2


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


def cleanup_trees(run_dir, log=print):
    """Удалить только временные Git-деревья своего gate после формирования отчётов.

    Отчёты, план и общий кеш индекса сохраняются. Последующий отдельный шаг при необходимости
    заново извлечёт headSha; параллельные gate используют другие каталоги.
    """
    trees = Path(run_dir) / "trees"
    try:
        shutil.rmtree(trees)
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"не удалось удалить временные исходники {trees}: {e}")


def tree_root(repo, plan, run_dir):
    """Один снимок Git для статики, загрузки и строк отчёта; --dir читает заданную выгрузку."""
    if plan.get("headDir"):
        return Path(plan["headDir"])
    head_sha = plan.get("headSha")
    if head_sha:
        return materialize(repo, head_sha, plan["source"], run_dir / "trees")
    return repo


def plan_root(repo, plan_path, cfg):
    """Каталог выгрузки плана для распознавания расширения: headDir или снимок Git-редакции."""
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Path(repo) / cfg["project"]["source"]
    return tree_root(Path(repo), plan, Path(plan_path).parent) / plan.get("source", cfg["project"]["source"])
