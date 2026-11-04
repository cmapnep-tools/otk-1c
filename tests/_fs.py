"""Уборка временных каталогов тестов."""
import os
import shutil
import stat
import sys


def _writable(func, path, _exc):
    """Объекты git на Windows — только для чтения: rmtree на них падает; снять атрибут и повторить."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass                                    # уборка не роняет тест (как ignore_errors)


def rmtree_force(path):
    """shutil.rmtree, удаляющий и файлы только для чтения; нет каталога — ничего."""
    if not os.path.lexists(path):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_writable)
    else:
        shutil.rmtree(path, onerror=_writable)   # до 3.12 onexc нет
