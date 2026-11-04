"""Изменения без git: каталог выгрузки целиком или сравнение двух каталогов «было» / «стало».

Ничего не хранится между прогонами: файлы сравниваются на месте (размер, затем содержимое), изменённые строки
модулей — difflib (аналог git diff -U0). Переименования не распознаются: удалён + добавлен."""
import difflib
import filecmp
import os
from pathlib import Path

from . import layout
from .gitdiff import FileChange


def files(root):
    """Файлы каталога: {путь от корня через «/»: Path}. Скрытые каталоги (.git, .otk…) пропускаются."""
    root = Path(root)
    out = {}
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        rel = Path(d).relative_to(root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        for n in names:
            out[prefix + n] = Path(d) / n
    return out


def read(root, rel):
    """Текст файла выгрузки или None, если файла нет."""
    p = Path(root) / rel
    return layout.read_text(p)


def fill_lines(change, base_text, head_text):
    """Номера изменённых строк head, как у gitdiff.fill_lines: добавленные/заменённые — added_lines,
    место удаления — deleted_at (строка перед удалением и следующая)."""
    a, b = base_text.splitlines(), head_text.splitlines()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            change.added_lines.update(range(j1 + 1, j2 + 1))
        elif tag == "delete":
            change.deleted_at.update({max(j1, 1), j1 + 1})


def changes(base_dir, head_dir, is_module):
    """Изменённые файлы head_dir относительно base_dir (None — все файлы добавлены). Строки — только у
    изменённых модулей (is_module(rel))."""
    head = files(head_dir)
    base = files(base_dir) if base_dir else {}
    out = []
    try:
        for rel in sorted(head.keys() | base.keys()):
            if rel not in base:
                out.append(FileChange("A", rel))
            elif rel not in head:
                out.append(FileChange("D", rel))
            # тот же файл (ссылка зеркала «было», gate --vendor) — без чтения содержимого
            elif not os.path.samefile(base[rel], head[rel]) and not filecmp.cmp(base[rel], head[rel], shallow=False):
                ch = FileChange("M", rel)
                if is_module(rel):
                    fill_lines(ch, read(base_dir, rel), read(head_dir, rel))
                out.append(ch)
    finally:
        filecmp.clear_cache()
    return out
