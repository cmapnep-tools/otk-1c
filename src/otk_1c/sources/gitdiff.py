"""Изменения между ревизиями через git: файлы со статусами и изменённые строки."""
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import layout

WORKTREE = None   # head = None — рабочее дерево (незакоммиченные изменения)


class GitError(Exception):
    pass


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if check and r.returncode:
        raise GitError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def empty_commit(repo):
    """Коммит с пустым деревом вне веток: база, от которой все файлы — добавленные (gate --all)."""
    return git(repo, "-c", "user.name=otk", "-c", "user.email=otk@otk.invalid", "commit-tree", EMPTY_TREE,
               "-m", "otk: пустая база (--all)").strip()


def rev(repo, ref):
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


@dataclass
class FileChange:
    status: str             # A, M, D, R
    path: str               # путь в head (для D — в base)
    old_path: str | None = None    # для R
    added_lines: set = field(default_factory=set)      # строки head, которые добавлены/изменены
    deleted_at: set = field(default_factory=set)       # строки head, рядом с которыми удалены строки


def _range_args(base, head):
    return [base] if head is WORKTREE else [base, head]


def source_prefix(source_dir):
    """Префикс путей выгрузки от корня репозитория со слэшем на конце; выгрузка в корне ("." или "") — пусто."""
    s = (source_dir or "").strip("/")
    return "" if s in ("", ".") else s + "/"


def changes(repo, base, head, prefix):
    """Изменённые файлы под prefix (source_prefix: путь от корня репозитория со слэшем на конце или пусто)."""
    out = git(repo, "diff", "--name-status", "-M", "-z", *_range_args(base, head), "--", prefix or ".")
    items = out.split("\0")
    result = []
    i = 0
    while i < len(items) and items[i]:
        status = items[i][0]
        if status == "R":
            result.append(FileChange("R", items[i + 2], items[i + 1]))
            i += 3
        else:
            result.append(FileChange(status, items[i + 1]))
            i += 2
    return result


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def fill_lines(repo, base, head, change):
    """Номера изменённых строк head для файла (git diff -U0)."""
    paths = [change.old_path, change.path] if change.status == "R" else [change.path]
    out = git(repo, "diff", "-U0", "-M", *_range_args(base, head), "--", *paths)
    for m in _HUNK.finditer(out):
        new_start = int(m.group(3))
        new_count = int(m.group(4)) if m.group(4) is not None else 1
        if new_count:
            change.added_lines.update(range(new_start, new_start + new_count))
        else:
            change.deleted_at.update({max(new_start, 1), new_start + 1})


def fill_lines_batch(repo, base, head, changes, prefix):
    """fill_lines для всех изменений одним git diff -U0 (а не процессом на файл)."""
    out = git(repo, "diff", "-U0", "-M", *_range_args(base, head), "--", prefix or ".")
    by_path = {c.path: c for c in changes}
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            current = by_path.get(line[6:]) if line.startswith("+++ b/") else None
        elif current is not None and line.startswith("@@"):
            m = _HUNK.match(line)
            if not m:
                continue
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            if new_count:
                current.added_lines.update(range(new_start, new_start + new_count))
            else:
                current.deleted_at.update({max(new_start, 1), new_start + 1})


def is_empty_tree(repo, ref):
    """База — пустое дерево (gate --all): все файлы добавлены, все строки новые."""
    return git(repo, "rev-parse", f"{ref}^{{tree}}").strip() == EMPTY_TREE


class Blobs:
    """Файлы ревизий одним процессом `git cat-file --batch` вместо `git show` на каждый файл: на больших
    репозиториях запуск git стоит ~0,1 с, а файлов в плане — десятки тысяч (выгрузка ERP)."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.proc = None

    def show(self, ref, path):
        if ref is WORKTREE:
            return show(self.repo, ref, path)
        if self.proc is None:
            self.proc = subprocess.Popen(["git", "-C", str(self.repo), "cat-file", "--batch"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.proc.stdin.write(f"{ref}:{path}\n".encode("utf-8"))
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode("utf-8", "replace").split()
        if len(header) != 3:                      # «<имя> missing» / «ambiguous»
            return None
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)                  # перевод строки после содержимого
        return layout.decode(data) if header[1] == "blob" else None

    def close(self):
        if self.proc is not None:
            self.proc.stdin.close()
            self.proc.wait()
            self.proc.stdout.close()
            self.proc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def show(repo, ref, path):
    """Содержимое файла в ревизии (ref = None — рабочее дерево); None, если файла нет."""
    if ref is WORKTREE:
        p = repo / path
        return layout.read_text(p)
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        return None
    return layout.decode(r.stdout)
