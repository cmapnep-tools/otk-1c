"""Блокировка копии ИБ между процессами ОТК: два прогона (CI и локальный, два раннера) на одной базе идут по
очереди, а не вперемешку — иначе состояние загрузки (otk-state.json) и сама ИБ оказываются от разных редакций.

Берётся при первом обращении к базе (Platform.lock: перед чтением состояния сборки и перед каждой командой
платформы) и держится до конца процесса: ОС снимает её и при аварийном завершении. Файл блокировки файловой
базы — рядом с её каталогом (`<каталог>.otk-lock`: видят все пользователи машины и общего диска), серверной
(`/S…`) — в каталоге временных файлов пользователя по хешу строки соединения. Внутри файла — кто держит.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

from ..errors import InfraError

DEFAULT_TIMEOUT = 900
_held: dict[str, BinaryIO] = {}         # путь файла блокировки -> открытый файл (держит блокировку)


def lock_file(ib, connection=None):
    """Файл блокировки базы: ib — каталог файловой ИБ, иначе connection «/SСервер\\База»."""
    if ib is not None:
        p = Path(ib).resolve()
        return p.with_name(p.name + ".otk-lock")
    key = hashlib.sha1(connection.replace("/", "\\").lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "otk-locks" / f"{key}.lock"


def _try_lock(f):
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def holder(path):
    """Кто держит блокировку (из файла): текст для сообщения или ""."""
    try:
        with open(path, "rb") as f:
            f.seek(1)                     # байт 0 заблокирован (Windows), сведения — после него
            info = json.loads(f.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return ""
    return f"PID {info.get('pid')}, {info.get('since')}, {info.get('command')}" if info else ""


def _stderr(message):
    print(message, file=sys.stderr)       # stdout занят выводом команды (gate --json)


def acquire(path, timeout=DEFAULT_TIMEOUT, log=_stderr):
    """Взять блокировку (повторно в том же процессе — ничего не делает); не дождались — InfraError ib-busy."""
    key = str(path)
    if key in _held:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")                 # noqa: SIM115 — файл открыт, пока держится блокировка
    deadline = time.time() + timeout
    waited = False
    while not _try_lock(f):
        if time.time() >= deadline:
            f.close()
            who = holder(path)
            raise InfraError(f"копия ИБ занята другим прогоном ОТК{f' ({who})' if who else ''} дольше {timeout} с "
                             f"(environment.lockTimeout; блокировка — {path})", code="ib-busy")
        if not waited:
            who = holder(path)
            log(f"копию ИБ проверяет другой прогон ОТК{f' ({who})' if who else ''}: жду до {timeout} с")
            waited = True
        time.sleep(1)
    info = {"pid": os.getpid(), "since": time.strftime("%Y-%m-%d %H:%M:%S"), "command": " ".join(sys.argv[1:3])}
    f.seek(1)
    f.truncate()
    f.write(json.dumps(info, ensure_ascii=False).encode("utf-8"))
    f.flush()
    _held[key] = f


def release_all():
    """Отпустить все блокировки процесса (тесты; иначе их снимает завершение процесса)."""
    for f in _held.values():
        f.close()
    _held.clear()
