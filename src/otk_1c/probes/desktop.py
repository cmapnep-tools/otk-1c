"""Клиент 1С на отдельном рабочем столе Windows: окно не появляется на экране и не забирает фокус.

Тонкий клиент с YAxUnit — обычное оконное приложение; просьбу запуститься свёрнутым (wShowWindow) 1С
не выполняет. Процесс, запущенный на своём рабочем столе (CreateDesktop + STARTUPINFO.lpDesktop), рисует
окна там, а на экране пользователя (раннер CI в его сеансе, хук pre-push) ничего не происходит.
Модальное окно ошибки на скрытом столе тоже невидимо — исполнители его и так распознают по журналу
регистрации и таймауту, а не по экрану.

popen(args) возвращает объект с poll/wait/kill, как у subprocess.Popen.

Процесс запускается в объекте задания (Job Object) с KILL_ON_JOB_CLOSE: клиент 8.5 перезапускает себя дочерним
процессом (`/O Normal /UseHwLicenses+ …`, без `/C`), и TerminateProcess родителя оставлял потомка — тот держал
копию ИБ («база занята» у всех следующих запусков). kill и завершение родителя закрывают всё дерево.

Linux: окнам 1С нужен X-сервер (без него Конфигуратор и клиент не стартуют) — свой Xvfb вместо скрытого стола;
вместо задания — своя группа процессов.
"""
import atexit
import ctypes
import os
import shutil
import signal
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

_GENERIC_ALL = 0x10000000
_INFINITE = 0xFFFFFFFF
_WAIT_TIMEOUT = 0x102


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("Read", "Write", "Other", "ReadBytes", "WriteBytes", "OtherBytes")]


class _JobLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION (в него вложена JOBOBJECT_BASIC_LIMIT_INFORMATION)."""
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
                ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_CREATE_SUSPENDED = 0x4
_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMITS = 9          # JobObjectExtendedLimitInformation


def _api():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.CreateDesktopW.restype = wintypes.HANDLE
    u32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p]
    u32.CloseDesktop.argtypes = [wintypes.HANDLE]
    k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInfo)]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    return k32, u32


class HiddenProcess:
    """Процесс в своём объекте задания и (hidden) на своём рабочем столе; стол и задание закрываются вместе с
    процессом — с ними завершаются и оставшиеся потомки."""

    def __init__(self, args, hidden=True):
        self._k32, self._u32 = _api()
        self.name = f"otk-{os.getpid()}"
        self._desk = None
        if hidden:
            self._desk = self._u32.CreateDesktopW(self.name, None, None, 0, _GENERIC_ALL, None)
            if not self._desk:
                raise OSError(ctypes.get_last_error(), "CreateDesktop")
        self._job = self._k32.CreateJobObjectW(None, None)
        limits = _JobLimits(LimitFlags=_KILL_ON_JOB_CLOSE)
        if not self._job or not self._k32.SetInformationJobObject(self._job, _EXTENDED_LIMITS, ctypes.byref(limits),
                                                                  ctypes.sizeof(limits)):
            err = ctypes.get_last_error()
            self._close()
            raise OSError(err, "CreateJobObject")
        si = _StartupInfo(cb=ctypes.sizeof(_StartupInfo), lpDesktop=self.name if hidden else None)
        pi = _ProcessInfo()
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in args]))
        # приостановленным: в задание до того, как процесс успеет запустить потомка
        if not self._k32.CreateProcessW(None, cmd, None, None, False, _CREATE_SUSPENDED, None, None,
                                        ctypes.byref(si), ctypes.byref(pi)):
            err = ctypes.get_last_error()
            self._close()
            raise OSError(err, f"CreateProcess {args[0]}")
        if not self._k32.AssignProcessToJobObject(self._job, pi.hProcess):
            err = ctypes.get_last_error()
            self._k32.TerminateProcess(pi.hProcess, 1)
            for h in (pi.hThread, pi.hProcess):
                self._k32.CloseHandle(h)
            self._close()
            raise OSError(err, "AssignProcessToJobObject")
        self._k32.ResumeThread(pi.hThread)
        self._k32.CloseHandle(pi.hThread)
        self._handle, self.pid, self.returncode = pi.hProcess, pi.dwProcessId, None
        self.args = args

    def _close(self):
        """Закрыть задание (KILL_ON_JOB_CLOSE завершает оставшихся в нём) и рабочий стол."""
        if self._job:
            self._k32.CloseHandle(self._job)
            self._job = None
        if self._desk:
            self._u32.CloseDesktop(self._desk)
            self._desk = None

    def _finish(self):
        code = wintypes.DWORD()
        self._k32.GetExitCodeProcess(self._handle, ctypes.byref(code))
        self.returncode = code.value
        self._k32.CloseHandle(self._handle)
        self._close()

    def poll(self):
        if self.returncode is None and self._k32.WaitForSingleObject(self._handle, 0) != _WAIT_TIMEOUT:
            self._finish()
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            ms = _INFINITE if timeout is None else int(timeout * 1000)
            if self._k32.WaitForSingleObject(self._handle, ms) == _WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self._finish()
        return self.returncode

    def kill(self):
        """Завершить процесс и всех его потомков (задание целиком)."""
        if self.returncode is None:
            self._k32.TerminateJobObject(self._job, 1)


class GroupProcess(subprocess.Popen):
    """Linux: процесс — лидер своей группы; kill и завершение лидера снимают группу целиком (потомки клиента не
    остаются — как задание на Windows)."""

    def __init__(self, args, env=None):
        super().__init__(args, env=env, start_new_session=True)
        self._reaped = False

    def _reap(self):
        if not self._reaped:
            self._reaped = True
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def poll(self):
        rc = super().poll()
        if rc is not None:
            self._reap()
        return rc

    def wait(self, timeout=None):
        rc = super().wait(timeout)
        self._reap()
        return rc

    def kill(self):
        if self.returncode is None:
            self._reap()
            self._reaped = False          # группа снята; после выхода лидера проверить ещё раз


class NoDisplay(Exception):
    """Linux: окнам 1С негде открыться — нет ни Xvfb, ни DISPLAY."""


_xvfb = None


def display_status():
    """Linux: (есть ли дисплей для окон 1С, пояснение) — для doctor; Xvfb не запускается."""
    xvfb = shutil.which("Xvfb")
    if xvfb:
        return True, f"Xvfb ({xvfb}): окна 1С — на своём дисплее"
    if os.environ.get("DISPLAY"):
        return True, f"DISPLAY={os.environ['DISPLAY']}: окна 1С — на экране (Xvfb не найден)"
    return False, "нет Xvfb и DISPLAY: Конфигуратор и клиент 1С без дисплея не запускаются (apt install xvfb)"


def display_env(hidden=True):
    """Окружение для процессов 1С с окнами (Конфигуратор, клиенты). Windows — None (своё окружение).
    Linux: hidden и есть Xvfb — свой Xvfb на время работы ОТК (окна не появляются на экране, как скрытый рабочий
    стол Windows); иначе DISPLAY сеанса; нет ни того, ни другого — NoDisplay."""
    if os.name == "nt":
        return None
    xvfb = shutil.which("Xvfb") if hidden or not os.environ.get("DISPLAY") else None
    if xvfb:
        return {**os.environ, "DISPLAY": _start_xvfb(xvfb)}
    if os.environ.get("DISPLAY"):
        return None
    raise NoDisplay(display_status()[1])


def _accepts(number):
    """Принимает ли X-сервер :number соединения (абстрактный сокет Linux или файл в /tmp/.X11-unix)."""
    # сокеты X-сервера, не временные файлы
    for address in (f"\0/tmp/.X11-unix/X{number}", f"/tmp/.X11-unix/X{number}"):  # noqa: S108
        with socket.socket(socket.AF_UNIX) as s:
            try:
                s.connect(address)
                return True
            except OSError:
                pass
    return False


def _start_xvfb(exe, wait=30):
    """Номер дисплея своего Xvfb (запускается один раз, гасится при выходе ОТК). Номер выбирается здесь, а не
    -displayfd: в WSLg /tmp/.X11-unix только для чтения, и Xvfb с -displayfd перебирает номера бесконечно — с явным
    номером он слушает абстрактный сокет."""
    global _xvfb
    if _xvfb and _xvfb[0].poll() is None:
        return _xvfb[1]
    for number in range(99, 199):
        if Path(f"/tmp/.X{number}-lock").exists() or _accepts(number):          # noqa: S108 — блокировка X
            continue
        proc = subprocess.Popen([exe, f":{number}", "-nolisten", "tcp", "-screen", "0", "1600x1200x24"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        deadline = time.time() + wait
        while proc.poll() is None and not _accepts(number) and time.time() < deadline:
            time.sleep(0.1)
        if proc.poll() is not None:           # номер заняли между проверкой и запуском — следующий
            continue
        if not _accepts(number):
            proc.kill()
            proc.wait()
            raise NoDisplay(f"Xvfb не запустился за {wait} с")
        _xvfb = (proc, f":{number}")
        atexit.register(_stop_xvfb)
        return _xvfb[1]
    raise NoDisplay("Xvfb: нет свободного номера дисплея (:99–:198)")


def _stop_xvfb():
    global _xvfb
    if _xvfb:
        _xvfb[0].terminate()
        try:
            _xvfb[0].wait(10)
        except subprocess.TimeoutExpired:
            _xvfb[0].kill()
        _xvfb = None


def popen(args, hidden=True):
    """Запустить процесс: на Windows — в своём задании, при hidden — и на своём рабочем столе; на Linux — в своей
    группе процессов, при hidden — на своём Xvfb."""
    if os.name == "nt":
        return HiddenProcess(args, hidden=hidden)
    return GroupProcess(args, env=display_env(hidden))
