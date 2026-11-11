"""Запуск платформы 1С: пакетный Конфигуратор, ibcmd, клиенты. Только свои процессы и своя копия ИБ.

Блокировки .cfl снимаются, только если файл удаётся удалить; занятый .cfl — база занята кем-то
другим: работа прекращается с InfraError (процессы не убиваются).
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..errors import InfraError
from . import com_extension, desktop, iblock

WINDOWS = sys.platform == "win32"
_VERSION = re.compile(r"^\d+(\.\d+){3}$")
# /Out Конфигуратора, когда база открыта другим сеансом (на Linux — единственный признак: блокировка не в .cfl)
BUSY = "Ошибка блокировки информационной базы"
IB_TOOLS = ("designer", "ibcmd")
# длина списка файлов в командной строке ibcmd (у Windows предел 32 767 символов на всю строку)
IBCMD_ARGS_MAX = 24000
# ibcmd: код возврата разбора командной строки — неизвестный ключ («Ошибка разбора параметра: --partial»)
# и пропущенный обязательный; ошибки данных, путей и импорта — -1 (проверено на 8.3.27)
IBCMD_PARSE_ERROR = 2
# файл для пробы поддержки --partial: заведомо отсутствует в любой выгрузке (нужен только разбор аргументов)
PARTIAL_PROBE = "otk-partial-probe.xml"
# тайм-аут полной загрузки Конфигуратором (без ibcmd): ERP — дольше 15 минут, втрое медленнее ibcmd (930 с)
FULL_LOAD_DESIGNER = 4 * 3600
# Режимы /CheckModules: ключ командной строки и контекст исполнения (None — расширенная проверка, не контекст);
# порядок — порядок режимов проверки всех модулей
CHECK_MODES = (("-ThinClient", "ThinClient"), ("-WebClient", "WebClient"), ("-Server", "Server"),
               ("-ExternalConnection", "ExternalConnection"), ("-ThickClientOrdinaryApplication", "OrdinaryClient"),
               ("-ExtendedModulesCheck", None))


def check_modes(ordinary=True):
    """Ключи режимов /CheckModules; ordinary=False — без толстого клиента обычного приложения."""
    return [key for key, ctx in CHECK_MODES if ordinary or ctx != "OrdinaryClient"]


def check_mode(context):
    """Ключ режима /CheckModules для контекста исполнения ('Server' -> '-Server')."""
    return next(key for key, ctx in CHECK_MODES if ctx == context)


def _version_key(name):
    return tuple(int(x) for x in name.split("."))


def install_roots():
    """Каталоги установки платформы (в них — <версия>/bin). Windows (руководство администратора 8.3.27, прил. 1.1.4):
    «для компьютера» — <Program Files>/1cv8 и <Program Files (x86)>/1cv8; «для пользователя» —
    %LOCALAPPDATA%/Programs/1cv8_x64, 1cv8_x86 (32-разрядная в 64-разрядной ОС), 1cv8 (в 32-разрядной ОС).
    Linux: /opt/1cv8/<arch>."""
    if not WINDOWS:
        return sorted(Path("/opt/1cv8").glob("*"))
    roots = [Path(os.environ.get(v, d)) / "1cv8" for v, d in
             (("ProgramFiles", r"C:/Program Files"), ("ProgramFiles(x86)", r"C:/Program Files (x86)"))]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots += [Path(local) / "Programs" / name for name in ("1cv8_x64", "1cv8_x86", "1cv8")]
    return roots


def find_platform(roots=None):
    """Каталог bin самой новой установленной платформы среди install_roots() или None.
    Windows: <корень>/<версия>/bin; Linux: /opt/1cv8/<arch>/<версия>."""
    if roots is None:
        roots = install_roots()
    found = []
    for root in roots:
        if not Path(root).is_dir():
            continue
        for d in Path(root).iterdir():
            if not _VERSION.match(d.name):
                continue
            bin_dir = d / "bin" if (d / "bin").is_dir() else d
            if (bin_dir / exe_name("1cv8")).exists():
                found.append((_version_key(d.name), bin_dir))
    return max(found)[1] if found else None


def exe_name(name):
    return f"{name}.exe" if WINDOWS else name


def ibcmd_installed(bin_dir):
    """ibcmd в каталоге платформы: ставится с компонентами сервера, в клиентской установке его нет."""
    return bool(bin_dir) and (Path(bin_dir) / exe_name("ibcmd")).is_file()


def platform_version(bin_dir):
    """'8.5.1.1529' из пути каталога платформы или None."""
    for part in reversed(Path(bin_dir).parts):
        if _VERSION.match(part):
            return part
    return None


def com_progid(bin_dir):
    """ProgID COM-соединителя по версии платформы: 8.3 -> V83.COMConnector, 8.5 -> V85.COMConnector."""
    v = platform_version(bin_dir)
    major_minor = "".join(v.split(".")[:2]) if v else "83"
    return f"V{major_minor}.COMConnector"


def find_bslls(configured=None):
    """BSL Language Server: настройка -> OTK_BSLLS -> PATH -> копия расширения VS Code «Language 1C (BSL)».
    None — не найден (шаг пропускается)."""
    if configured and Path(configured).exists():
        return Path(configured)
    found = shutil.which("bsl-language-server")
    return Path(found) if found else vscode_bslls()


def vscode_bslls(storages=None):
    """BSL LS, который скачивает расширение VS Code «Language 1C (BSL)» (1c-syntax.language-1c-bsl) в своё
    хранилище: <globalStorage>/1c-syntax.language-1c-bsl/bsl-language-server/v<версия>/bsl-language-server/ —
    самая новая версия; None — нет. storages — каталоги globalStorage (по умолчанию VS Code пользователя)."""
    if storages is None:
        home = Path.home()
        storages = [home / ".config/Code/User/globalStorage", home / ".vscode-server/data/User/globalStorage"]
        if os.environ.get("APPDATA"):
            storages.insert(0, Path(os.environ["APPDATA"]) / "Code/User/globalStorage")
    exe = exe_name("bsl-language-server")
    found = []
    for s in storages:
        for v in Path(s, "1c-syntax.language-1c-bsl", "bsl-language-server").glob("v*"):
            for p in (v / "bsl-language-server" / exe, v / "bsl-language-server" / "bin" / exe):   # Windows, Linux
                if re.fullmatch(r"v\d+(\.\d+)*", v.name) and p.is_file():
                    found.append(([int(x) for x in v.name[1:].split(".")], p))
    return max(found)[1] if found else None


def find_bsl_analyzer(configured=None):
    """bsl-analyzer: настройка -> OTK_BSL_ANALYZER -> PATH. None — не найден."""
    if configured and Path(configured).exists():
        return Path(configured)
    found = shutil.which(configured or "bsl-analyzer")
    return Path(found) if found else None


def find_edtcli(configured=None):
    """1cedtcli (экспорт исходников EDT в выгрузку Конфигуратора): настройка -> OTK_EDTCLI -> самая новая
    установка 1C:EDT пользователя (%LOCALAPPDATA%/1C/1cedtstart/installations/*/1cedt) -> PATH. None — не найден."""
    if configured and Path(configured).exists():
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA")
    installed = sorted(Path(local, "1C", "1cedtstart", "installations").glob(f"*/1cedt/{exe_name('1cedtcli')}"),
                       key=lambda p: [int(x) for x in re.findall(r"\d+", p.parts[-3])]) if local else []
    if installed:
        return installed[-1]
    found = shutil.which("1cedtcli")
    return Path(found) if found else None


def decode(raw, fallback="cp1251"):
    """Вывод платформы: UTF-8 (с BOM и без), UTF-16 — только с BOM или нулевыми байтами (иначе любой чётный по длине
    текст «декодируется» в мусор), иначе fallback — stderr ibcmd на Windows в кодировке консоли (cp866)."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw:
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", fallback):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


# ibcmd без --user в базе с пользователями бесконечно спрашивает имя (stdin закрыт — пустой ответ, снова вопрос)
_LOGIN_PROMPT = ("Имя пользователя:", "User name:")


def _ext(extension):
    return [f"--extension={extension}"] if extension else []


def _designer_ext(extension):
    return ["-Extension", extension] if extension else []


class ConfigOps:
    """Операции с конфигурацией ИБ: пакетный Конфигуратор или ibcmd (environment.ibTool). Нужны designer(),
    ibcmd(), ib и ib_tool — у Platform и у подделок платформы в тестах."""
    ib_tool = "designer"
    ib: Path | None = None
    has_ibcmd = True
    partial_supported: bool | None = None

    def lock(self):
        """Межпроцессная блокировка базы (Platform.lock); у подделок — нет."""

    def via_ibcmd(self):
        """Операции с конфигурацией — через ibcmd: так настроено (ibTool) и база файловая (серверной ibcmd нужен
        прямой доступ к СУБД — там всегда Конфигуратор)."""
        return self.ib_tool == "ibcmd" and self.ib is not None

    def ibcmd_usable(self):
        """ibcmd есть и база файловая: полная загрузка и снятие безопасного режима — им при любом ibTool."""
        return self.has_ibcmd and self.ib is not None

    def probe_ibcmd_partial(self):
        """Знает ли ibcmd платформы ключ --partial (у ConfigOps и подделок — знает); у Platform — проба."""
        return True

    def ibcmd_partial(self):
        """Частичная загрузка через ibcmd: ключ --partial есть у ibcmd платформы (проба один раз за прогон)."""
        if self.partial_supported is None:
            self.partial_supported = self.probe_ibcmd_partial()
        return self.partial_supported

    def full_load(self, dump, extension=None, label="load"):
        """Полная загрузка выгрузки. ibcmd втрое быстрее пакетного Конфигуратора (УМЦ, 12,9 тыс. файлов, в пустую
        ИБ: 56 с против 182 с; ERP Конфигуратором — дольше 15 минут) — им при любом ibTool, без него —
        Конфигуратором с запасом по времени."""
        if self.ibcmd_usable():
            return self.ibcmd("infobase", "config", "import", *_ext(extension), dump, timeout=7200, label=label)
        return self.designer("/LoadConfigFromFiles", dump, *_designer_ext(extension), timeout=FULL_LOAD_DESIGNER,
                             label=label)

    def extension_unsafe(self, name, label="unsafe"):
        """Расширение — без безопасного режима и защиты от опасных действий (YAxUnit пишет отчёт в файл). В пакетном
        Конфигураторе ключа нет: ibcmd, без него — внешним соединением (COM)."""
        if self.ibcmd_usable():
            return self.ibcmd("infobase", "config", "extension", "update", f"--name={name}", "--safe-mode=no",
                              "--unsafe-action-protection=no", label=label)
        return self.com_unsafe(name)

    def com_unsafe(self, name):
        raise InfraError(f"снять безопасный режим с расширения {name} нечем: нет ibcmd и COM", code="no-ibcmd")

    def load_files(self, dump, list_file, extension=None, label="load", timeout=3600):
        """Частичная загрузка файлов выгрузки dump по списку (пути относительно dump, по строке в list_file).
        ibcmd и Конфигуратор дают одинаковый результат; ibcmd — когда знает --partial (8.3.23 не знает)."""
        if self.via_ibcmd() and self.ibcmd_partial():
            files = [s.strip() for s in Path(list_file).read_text(encoding="utf-8-sig").splitlines() if s.strip()]
            if sum(len(f) + 3 for f in files) < IBCMD_ARGS_MAX:
                return self.ibcmd("infobase", "config", "import", "files", f"--base-dir={dump}", "--partial",
                                  *_ext(extension), *files, timeout=timeout, label=label)
            # список не помещается в командную строку (у ibcmd нет файла списка) — Конфигуратором
        return self.designer("/LoadConfigFromFiles", dump, "-listFile", list_file, "-Format", "Hierarchical",
                             *_designer_ext(extension), timeout=timeout, label=label)

    def load_dir(self, dump, extension=None, label="load-dir", timeout=3600):
        """Загрузка выгрузки целиком; расширения нет в ИБ — создаётся. Полная загрузка основной конфигурации
        проекта — full_load."""
        if self.via_ibcmd():
            return self.ibcmd("infobase", "config", "import", *_ext(extension), dump, timeout=timeout, label=label)
        return self.designer("/LoadConfigFromFiles", dump, *_designer_ext(extension), timeout=timeout, label=label)

    def dump_files(self, target, names_file=None, extension=None, label="dump", timeout=3600):
        """Выгрузка конфигурации в файлы: целиком или объекты из names_file (полные имена, по строке)."""
        if self.via_ibcmd():
            if names_file:
                names = [s.strip() for s in Path(names_file).read_text(encoding="utf-8-sig").splitlines()
                         if s.strip()]
                return self.ibcmd("infobase", "config", "export", "objects", f"--out={target}", *_ext(extension),
                                  *names, timeout=timeout, label=label)
            return self.ibcmd("infobase", "config", "export", *_ext(extension), target, timeout=timeout, label=label)
        listed = ["-listFile", names_file] if names_file else []
        return self.designer("/DumpConfigToFiles", target, *listed, "-Format", "Hierarchical",
                             *_designer_ext(extension), timeout=timeout, label=label)

    def update_db(self, extension=None, label="update", timeout=7200):
        """Обновление конфигурации базы данных (/UpdateDBCfg, ibcmd config apply)."""
        if self.via_ibcmd():
            return self.ibcmd("infobase", "config", "apply", *_ext(extension), "--force", timeout=timeout,
                              label=label)
        return self.designer("/UpdateDBCfg", *_designer_ext(extension), timeout=timeout, label=label)

    def save_extension(self, name, path, label="save-ext"):
        """Расширение в файл .cfe. Расширения нет — rc≠0, вывод содержит EXT_MISSING."""
        if self.via_ibcmd():
            return self.ibcmd("infobase", "config", "save", f"--extension={name}", path, label=label)
        return self.designer("/DumpCfg", path, "-Extension", name, label=label)

    def load_extension(self, name, path, label="load-ext"):
        """Расширение из файла .cfe (нет в ИБ — создаётся)."""
        if self.via_ibcmd():
            return self.ibcmd("infobase", "config", "load", f"--extension={name}", path, label=label)
        return self.designer("/LoadCfg", path, "-Extension", name, label=label)

    def delete_extension(self, name, label="delete-ext"):
        if self.via_ibcmd():
            return self.ibcmd("infobase", "config", "extension", "delete", f"--name={name}", label=label)
        return self.designer("/DeleteCfg", "-Extension", name, label=label)

    def disable_support(self, label="support-off"):
        """Снять конфигурацию своей копии с поддержки (вне зависимости от права изменения)."""
        if self.via_ibcmd():
            return self.ibcmd("infobase", "config", "support", "disable", "--force", label=label)
        return self.designer("/ManageCfgSupport", "-disableSupport", "-force", label=label)


def ib_path(env):
    """Каталог копии ИБ так же, как его видит Platform: connection «/FКаталог» — файловая ИБ, «/SСервер\\База» —
    сервер (каталога нет), иначе environment.ib. None — каталога копии нет (её нет или работа идёт по серверу)."""
    connection = env.get("connection")
    if connection:
        return Path(connection[2:]) if connection[:2].upper() == "/F" else None
    ib = env.get("ib")
    return Path(ib) if ib else None


class Platform(ConfigOps):
    def __init__(self, env, run_dir):
        configured = env.get("platform")
        found = Path(configured) if configured else find_platform()
        if not found:
            raise InfraError("платформа 1С не найдена: --platform, environment.platform в otk.local.toml "
                             "или OTK_PLATFORM", code="no-platform")
        self.bin = found
        # connection — строка соединения Конфигуратора («/FКаталог» или «/SСервер\База»): так задаётся
        # чужая (целевая) база хука Platform Tools; ib — своя файловая копия ОТК
        self.connection = env.get("connection")
        self.ib = ib_path(env)
        if self.ib is None and not self.connection:
            raise InfraError("не задана копия ИБ: --ib, environment.ib в otk.local.toml или OTK_IB", code="no-ib")
        self.edtcli = find_edtcli(env.get("edtcli"))
        self.edt_timeout = int(env.get("edtTimeout") or 1800)
        self.user = env.get("user", "")
        self.password = env.get("password", "")
        self.run_dir = Path(run_dir)
        self.data = self.run_dir / "ibdata"
        # клиент на скрытом рабочем столе Windows (desktop.py); false — окно видно (отладка)
        self.hidden_desktop = bool(env.get("hiddenDesktop", True))
        # загрузка, выгрузка, обновление БД, расширения, поддержка: Конфигуратор (по умолчанию — на Windows быстрее)
        # или ibcmd (без дисплея и лицензии на эти операции); результат одинаковый
        self.ib_tool = str(env.get("ibTool") or "designer").strip().lower()
        if self.ib_tool not in IB_TOOLS:
            raise InfraError(f"environment.ibTool = {self.ib_tool!r}: допустимо {' или '.join(IB_TOOLS)}",
                             code="config-invalid")
        # ibcmd ставится с компонентами сервера 1С: в клиентской установке его нет — всё через Конфигуратор
        self.has_ibcmd = ibcmd_installed(self.bin)
        if self.ib_tool == "ibcmd" and not self.has_ibcmd:
            raise InfraError(f"environment.ibTool = ibcmd, а ibcmd в {self.bin} нет: установите компоненты сервера "
                             "1С:Предприятия или выберите ibTool = designer", code="no-ibcmd")
        self.lock_timeout = float(env.get("lockTimeout") or iblock.DEFAULT_TIMEOUT)

    def lock(self):
        """Эта база — только этому прогону ОТК до конца процесса (iblock); другой прогон держит — ждать."""
        iblock.acquire(iblock.lock_file(self.ib, self.connection), self.lock_timeout)

    def unlock(self, wait=15):
        """Снять висячие .cfl своей копии; занятые дольше wait секунд — признак чужого сеанса
        (только что завершённый клиент отпускает блокировки не сразу). Сначала — блокировка ОТК (lock)."""
        self.lock()
        if self.ib is None:                      # серверная база: блокировки не файловые
            return
        deadline = time.time() + wait
        while True:
            busy = []
            for cfl in self.ib.glob("*.cfl"):
                try:
                    cfl.unlink()
                except PermissionError:
                    busy.append(cfl.name)
            if not busy or time.time() >= deadline:
                break
            time.sleep(1)
        if busy:
            raise InfraError(f"база {self.ib} занята (заблокированы {', '.join(busy)}); "
                             "закройте Конфигуратор и сеансы 1С:Предприятия с этой базой", code="ib-busy")

    def com_connection(self):
        """Строка соединения COM: File="…" для файловой базы, Srvr="…";Ref="…" для серверной (/Sсервер\\база)."""
        if self.connection and self.connection[:2].upper() == "/S":
            server, _, ref = self.connection[2:].replace("/", "\\").partition("\\")
            return f'Srvr="{server}";Ref="{ref}";'
        return f'File="{self.ib}";'

    def com_unsafe(self, name):
        """Снять безопасный режим расширения внешним соединением (без ibcmd): (rc, секунды, текст)."""
        self.unlock()
        user = f'Usr="{self.user}";Pwd="{self.password}";' if self.user else ""
        try:
            return com_extension.set_unsafe(self.com_connection() + user, name, com_progid(self.bin))
        except com_extension.Unavailable as e:
            raise InfraError(f"снять безопасный режим с расширения {name} нечем: нет ibcmd (ставится с компонентами "
                             f"сервера 1С:Предприятия), {e}; без этого пробы YAxUnit недоступны",
                             code="no-ibcmd") from None

    def ib_target(self):
        return self.connection or f"/F{self.ib}"

    def _auth(self):
        args = [f"/N{self.user}"] if self.user else []
        if self.password:
            args.append(f"/P{self.password}")   # пустой пароль не передаётся: /P съедает следующий аргумент
        return args

    def designer(self, *args, timeout=3600, label="designer"):
        """Пакетный Конфигуратор. Возвращает (rc, секунды, текст /Out)."""
        exe = self.bin / exe_name("1cv8")
        if not exe.is_file():
            # серверная установка платформы: ibcmd есть, клиентских компонентов нет — Конфигуратором нечем
            raise InfraError(f"в {self.bin} нет {exe.name}: пакетный Конфигуратор не установлен — установите "
                             "клиентские компоненты платформы 1С:Предприятия или выберите ibTool = ibcmd",
                             code="no-platform")
        self.unlock()
        logs = self.run_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = logs / f"{label}.out"
        out.unlink(missing_ok=True)
        env = self.display_env()
        t = time.time()
        try:
            rc = subprocess.run([str(exe), "DESIGNER", self.ib_target(), *self._auth(),
                                 "/DisableStartupDialogs", *[str(a) for a in args], f"/Out{out}"],
                                stdin=subprocess.DEVNULL, timeout=timeout, env=env).returncode
        except subprocess.TimeoutExpired:
            raise InfraError(f"Конфигуратор не завершился за {timeout} с ({label})", code="timeout") from None
        text = decode(out.read_bytes()).strip() if out.exists() else ""
        if rc and BUSY in text:
            # Linux: платформа блокирует базу не файлами .cfl — unlock занятость не видит, её сообщает Конфигуратор
            raise InfraError(f"база {self.ib or self.connection} занята ({text.splitlines()[0]}): "
                             "закройте Конфигуратор и сеансы 1С:Предприятия с этой базой", code="ib-busy")
        return rc, round(time.time() - t, 1), text

    def display_env(self):
        """Окружение процессов 1С с окнами: Linux — свой Xvfb (hiddenDesktop) или DISPLAY; Windows — None."""
        try:
            return desktop.display_env(self.hidden_desktop)
        except desktop.NoDisplay as e:
            raise InfraError(str(e), code="no-display") from None

    def create_file_ib(self, path, timeout=600):
        """Новая пустая файловая ИБ (CREATEINFOBASE, ibcmd infobase create). Возвращает (rc, текст /Out)."""
        if self.ib_tool == "ibcmd":
            rc, _, text = self.ibcmd("infobase", "create", timeout=timeout, label="create-ib", db=path)
            return rc, text
        logs = self.run_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = logs / "create-ib.out"
        out.unlink(missing_ok=True)
        # строка соединения File="…" — кавычки внутри аргумента; список аргументов в Windows их экранирует (\"),
        # и путь с пробелом 1cv8 не разбирает — там командная строка собирается как есть (проверено на 8.3.27)
        exe = str(self.bin / exe_name("1cv8"))
        cmd = (f'{subprocess.list2cmdline([exe, "CREATEINFOBASE"])} File="{path}" /DisableStartupDialogs /Out"{out}"'
               if WINDOWS else [exe, "CREATEINFOBASE", f'File="{path}"', "/DisableStartupDialogs", f"/Out{out}"])
        try:
            rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=timeout, env=self.display_env()).returncode
        except subprocess.TimeoutExpired:
            raise InfraError(f"CREATEINFOBASE не завершился за {timeout} с", code="timeout") from None
        return rc, decode(out.read_bytes()).strip() if out.exists() else ""

    def probe_ibcmd_partial(self):
        """Знает ли ibcmd ключ --partial: по версии не определить (8.3.24–8.3.26 не проверить), поэтому проба —
        та же команда import files на заведомо отсутствующем файле, с ключом и без него. Ключ не разобран — rc=2
        («Ошибка разбора параметра: --partial», 8.3.23). Отличать от rc=2 по другой причине (нет обязательного ключа)
        позволяет вторая проба: если код тот же, дело не в --partial — ключ есть, ошибку покажет сама загрузка."""
        probe = ("infobase", "config", "import", "files", f"--base-dir={self.run_dir}")
        rc, _, _ = self.ibcmd(*probe, "--partial", PARTIAL_PROBE, timeout=600, label="probe-partial")
        if rc != IBCMD_PARSE_ERROR:
            return True
        rc, _, _ = self.ibcmd(*probe, PARTIAL_PROBE, timeout=600, label="probe-partial-off")
        return rc == IBCMD_PARSE_ERROR

    def ibcmd(self, *args, timeout=3600, label=None, db=None):
        """ibcmd над своей файловой ИБ (db — другая файловая ИБ, например создаваемая). (rc, секунды, вывод);
        с label вывод пишется и в logs/<label>.out, как /Out Конфигуратора."""
        if not self.has_ibcmd:
            raise InfraError(f"ibcmd в {self.bin} нет ({label or args[0]}): установите компоненты сервера "
                             "1С:Предприятия", code="no-ibcmd")
        if db is None:
            self.unlock()
        self.data.mkdir(parents=True, exist_ok=True)
        auth = [f"--user={self.user}", f"--password={self.password}"] if self.user else []
        t = time.time()
        logs = self.run_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out_path, err_path = logs / "ibcmd.stdout", logs / "ibcmd.stderr"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            proc = subprocess.Popen([str(self.bin / exe_name("ibcmd")), *[str(a) for a in args],
                                     f"--db-path={db or self.ib}", f"--data={self.data}", *auth],
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            while proc.poll() is None:
                if time.time() - t > timeout:
                    proc.kill()
                    proc.wait()
                    raise InfraError(f"ibcmd не завершился за {timeout} с ({label or args[0]})", code="timeout")
                if sum(decode(out_path.read_bytes(), "cp866").count(q) for q in _LOGIN_PROMPT) > 1:
                    proc.kill()
                    proc.wait()
                    raise InfraError(f"ibcmd ({label or args[0]}) запрашивает имя пользователя: в базе есть "
                                     "пользователи — задайте environment.user и environment.password", code="ib-auth")
                time.sleep(0.5)
        text = (decode(out_path.read_bytes(), "cp866") + decode(err_path.read_bytes(), "cp866")).strip()
        if label:
            (logs / f"{label}.out").write_text(text, encoding="utf-8")
        return proc.returncode, round(time.time() - t, 1), text

    def client_args(self, thin=True):
        exe = exe_name("1cv8c" if thin else "1cv8")
        return [str(self.bin / exe), "ENTERPRISE", self.ib_target(), *self._auth()]

    def start_client(self, *args, thin=True):
        """Клиент 1С:Предприятия (процесс с poll/wait/kill); на Windows — на скрытом рабочем столе, на Linux — на своём
        Xvfb."""
        try:
            return desktop.popen(self.client_args(thin=thin) + [str(a) for a in args], hidden=self.hidden_desktop)
        except desktop.NoDisplay as e:
            raise InfraError(str(e), code="no-display") from None
