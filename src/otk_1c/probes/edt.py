"""Исходники 1С:EDT -> выгрузка Конфигуратора (этап 5): платформа EDT не читает, поэтому в копию ИБ
грузится экспорт `1cedtcli -data <workspace> -command export --project <проект> --configuration-files <каталог>`.

Каталог проекта — родитель src (там .project и DT-INF). Экспорт каталог проекта не меняет (проверено на типовой:
снимок путей и mtime до и после совпал), поэтому выполняется прямо с него. Экспорт дорог (небольшое расширение — ~80 с),
поэтому лежит в каталоге прогона под отпечатком проекта (index.dir_fingerprint): без изменений — повторно не делается.

Без экспорта: если метаданные в копии ИБ те же, что в проекте (изменены только модули), каталог в раскладке
выгрузки собирается из частичной выгрузки ИБ по -listFile (описания, Form.xml) и модулей EDT поверх (from_ib).
"""
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..checks import DEPS
from ..errors import InfraError
from ..sources import index, layout
from .platform import decode

CACHE = "edt-export"
IB_DIR = "edt-ib"
DEFAULT_TIMEOUT = 1800
BOM = b"\xef\xbb\xbf"
# каталог выгрузки -> вид объекта в строке -listFile выгрузки (Catalog.Валюты)
KINDS = {coll: kind for kind, coll in index.COLLECTIONS.items()} | {
    "CommonAttributes": "CommonAttribute", "Languages": "Language"}


class NoEdtcli(InfraError):
    """Нужен экспорт, а 1cedtcli не найден: пробы — «не проверено» с причиной, а не сбой инфраструктуры."""


def project_dir(source_dir):
    """Каталог проекта EDT по каталогу исходников (src)."""
    return Path(source_dir).resolve().parent


def pid_alive(pid):
    """Процесс pid ещё работает (чужие рабочие каталоги экспорта не трогаются). Сомнение — «работает»."""
    if pid == os.getpid():
        return True
    if os.name == "nt":                                     # os.kill(pid, 0) в Windows завершает процесс
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5             # ERROR_ACCESS_DENIED — процесс есть
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
            return not ok or code.value == 259              # STILL_ACTIVE
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def command(edtcli, workspace, project, out_dir, timeout=DEFAULT_TIMEOUT):
    """Аргументы запуска экспорта: -timeout и -vmargs — до -command (порядок ключей 1cedtcli)."""
    return [str(edtcli), "-data", str(workspace), "-timeout", str(timeout), "-command", "export",
            "--project", str(project), "--configuration-files", str(out_dir)]


def export(edtcli, source_dir, run_dir, timeout=DEFAULT_TIMEOUT):
    """Выгрузка Конфигуратора для исходников EDT source_dir: (каталог выгрузки, секунды; 0 — из кеша каталога прогона).
    Ошибка экспорта — InfraError с хвостом вывода."""
    if not edtcli:
        raise NoEdtcli("исходники EDT грузятся через экспорт, а 1cedtcli не найден: " + DEPS["edtcli"])
    project = project_dir(source_dir)
    if not (project / ".project").is_file():
        raise InfraError(f"{project}: не проект EDT (нет .project) — экспорт невозможен", code="no-source")
    fingerprint = index.dir_fingerprint(project, True, layout.EDT)
    key = hashlib.sha1((str(edtcli).lower() + fingerprint).encode(), usedforsecurity=False).hexdigest()[:16]
    cache = Path(run_dir) / CACHE
    target = cache / key
    if (target / "Configuration.xml").is_file():
        return target, 0.0
    # прежние отпечатки не копятся; рабочие каталоги — свои у каждого запуска (параллельный прогон на другую ИБ),
    # их остатки после аварийно завершённых процессов (`<ключ>.<pid>.ws|tmp`) удаляются
    for old in cache.glob("*") if cache.is_dir() else ():
        name = old.name.split(".")
        if (len(name) == 1 and old.name != key or
                len(name) == 3 and name[2] in ("ws", "tmp") and name[1].isdigit() and not pid_alive(int(name[1]))):
            shutil.rmtree(old, ignore_errors=True)
    tag = f"{key}.{os.getpid()}"
    work, out = cache / f"{tag}.ws", cache / f"{tag}.tmp"
    shutil.rmtree(out, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    t = time.time()
    try:
        r = subprocess.run(command(edtcli, work, project, out, timeout), stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=timeout + 120)
        text = decode(r.stdout + r.stderr).strip()
        # 1cedtcli возвращает 0 и при тихом сбое: результат — по наличию Configuration.xml
        if r.returncode or not (out / "Configuration.xml").is_file():
            raise InfraError(f"экспорт EDT {project}: rc={r.returncode} {' '.join(text.split())[-500:]}",
                             code="edt-failed")
    except subprocess.TimeoutExpired:
        shutil.rmtree(out, ignore_errors=True)              # частичный экспорт (ERP — до 6 ГБ)
        raise InfraError(f"экспорт EDT не завершился за {timeout} с (environment.edtTimeout)", code="timeout") from None
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        out.rename(target)
    except OSError:                                         # параллельный запуск успел раньше
        shutil.rmtree(out, ignore_errors=True)
        if not (target / "Configuration.xml").is_file():
            raise InfraError(f"экспорт EDT: не удалось переместить {out} в {target}", code="edt-failed") from None
    return target, round(time.time() - t, 1)


def list_name(collection, name, form=None):
    """Строка -listFile частичной выгрузки: Catalog.Валюты, Catalog.Валюты.Form.ФормаЭлемента, CommonForm.Ф.
    None — вид по каталогу не известен."""
    kind = KINDS.get(collection)
    if kind is None:
        return None
    return f"{kind}.{name}.Form.{form}" if form else f"{kind}.{name}"


def form_names(modules):
    """Пути модулей EDT -> строки -listFile форм, к модулям которых при загрузке нужен Form.xml (ib-build).
    None — форма неизвестного вида."""
    names = set()
    for rel in modules:
        a = layout.EDT.parse(rel)
        if a is not None and a.kind == "FormModule":
            name = list_name(a.collection, a.name, a.sub)
            if name is None:
                return None
            names.add(name)
    return sorted(names)


def from_ib(platform, source_dir, target, names=(), modules=(), label="edt-ib"):
    """Каталог в раскладке выгрузки Конфигуратора без экспорта: частичная выгрузка копии ИБ по -listFile
    (names) и поверх — модули EDT (modules — пути от source_dir) в путях выгрузки, с BOM, байты как есть (так их
    кладёт экспорт). Возвращает секунды выгрузки (0 — не выгружалось). InfraError — выгрузка не удалась или модуль
    защищён (в выгрузке это не .bsl)."""
    target = Path(target)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    sec = 0.0
    if names:
        lst = target.parent / f"{target.name}.lst"
        lst.write_text("\n".join(sorted(set(names))) + "\n", encoding="utf-8")
        rc, sec, out = platform.dump_files(target, lst, label=label)
        if rc:
            raise InfraError(f"частичная выгрузка копии ИБ ({label}, rc={rc}): {' '.join(out.split())[-300:]}",
                             code="platform-failed")
    for rel in modules:
        data = (Path(source_dir) / rel).read_bytes()
        if layout.EDT.protected(data):
            raise InfraError(f"{rel}: защищённый модуль — без экспорта EDT не загружается")
        dest = target / layout.EDT.to_designer(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data if data.startswith(BOM) else BOM + data)
    return sec
