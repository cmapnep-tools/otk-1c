"""Внедрение сбоев окружения: каждый вызов файловой операции по очереди бросает OSError.

Сценарий прогоняется начисто со счётчиками (points), затем — по разу на каждый вызов каждой операции, и этот
вызов бросает ошибку (fail_at). Так проверяется, что сбой на любом шаге даёт понятную ошибку, а не трассировку,
и не оставляет состояние, которое следующий прогон примет за готовое.
"""
import contextlib
import errno
import os
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

# (владелец, имя) — операции, через которые код ОТК трогает файловую систему
FS_OPS = (
    (Path, "mkdir"), (Path, "read_text"), (Path, "read_bytes"), (Path, "write_text"), (Path, "write_bytes"),
    (Path, "replace"), (Path, "rename"), (Path, "unlink"), (Path, "glob"), (Path, "iterdir"),
    (Path, "is_file"), (Path, "is_dir"), (Path, "exists"), (Path, "stat"), (Path, "open"),
    (os, "fdopen"), (os, "replace"), (os, "scandir"), (os, "mkdir"),
    (tempfile, "mkstemp"), (tempfile, "TemporaryFile"),
    (tarfile, "bltn_open"),         # запись файла при извлечении архива: обрыв посреди дерева
)


def denied(name):
    """Ошибка внедрённого сбоя — отказ в доступе с путём-меткой."""
    return PermissionError(errno.EACCES, f"сбой внедрён: {name}", f"<{name}>")


def _wrap(owner, name, on_call):
    orig = getattr(owner, name)

    def wrapper(*args, **kwargs):
        on_call(owner, name)
        return orig(*args, **kwargs)
    return mock.patch.object(owner, name, wrapper)


def points(scenario, ops=FS_OPS):
    """Прогнать scenario() начисто и вернуть точки сбоя: [(владелец, имя, номер вызова)] по всем вызовам."""
    counts = {}

    def count(owner, name):
        counts[(owner, name)] = counts.get((owner, name), 0) + 1
    with contextlib.ExitStack() as stack:
        for owner, name in ops:
            stack.enter_context(_wrap(owner, name, count))
        scenario()
    return [(owner, name, n) for (owner, name), total in counts.items() for n in range(1, total + 1)]


@contextlib.contextmanager
def fail_at(owner, name, nth):
    """Вызов номер nth операции owner.name бросает denied(); остальные — как обычно."""
    seen = [0]

    def maybe_fail(_owner, _name):
        seen[0] += 1
        if seen[0] == nth:
            raise denied(f"{getattr(owner, '__name__', owner)}.{name}#{nth}")
    with _wrap(owner, name, maybe_fail):
        yield


def label(owner, name, nth):
    return f"{getattr(owner, '__name__', owner)}.{name}#{nth}"
