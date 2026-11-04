"""Консоль ИБ: запросы, проверка запросов и метаданные копии базы через внешнее соединение (COM).

Только чтение: язык запросов данные не меняет, исполнения произвольного кода нет. Соединение
держится между вызовами (подключение — секунды, запрос — миллисекунды) и отпускается по простою,
чтобы не мешать загрузке расширения и Конфигуратору.

Базы — псевдонимы в файле пользователя (OTK_BASES или ~/.otk/bases.toml, вне git):

    [bases.erp]
    connection = "/FC:/1C/Копии/ERP"          # или "/Sсервер\\база"
    user = "Администратор"
    password_cred = "otk:erp"             # Windows: Диспетчер учётных данных (cmdkey /generic:...)
    # password_env = "ERP_PASSWORD"            # или переменная окружения; открытым текстом пароль не хранится
    # progid = "V83.COMConnector"              # по умолчанию V85, затем V83

Параметры запроса (JSON): строка, число, булево, null; {"date": "2026-01-31T23:59:59"};
{"type": "CatalogRef.Номенклатура", "id": "<УИД>"}; {"type": "EnumRef.X", "id": "<имя значения>"}; список — массив.
Значения в ответе: ссылка — {"repr", "type", "id"} (тип и id годятся обратно в параметры);
NULL и НЕОПРЕДЕЛЕНО внешнее соединение отдаёт одинаково — null.
"""
import datetime
import gc
import os
import re
import time
import tomllib
from pathlib import Path

PROGIDS = ("V85.COMConnector", "V83.COMConnector")
COLLECTIONS = ("Константы", "Справочники", "Документы", "Перечисления", "ПланыВидовХарактеристик", "ПланыСчетов",
               "ПланыВидовРасчета", "РегистрыСведений", "РегистрыНакопления", "РегистрыБухгалтерии",
               "РегистрыРасчета", "БизнесПроцессы", "Задачи", "ПланыОбмена", "Обработки", "Отчеты", "ЖурналыДокументов",
               "ОбщиеМодули", "Роли", "Подсистемы")
FIELD_SETS = ("СтандартныеРеквизиты", "Измерения", "Ресурсы", "Реквизиты", "Графы")


class ConsoleError(Exception):
    """Ошибка консоли с понятным текстом (нет базы, нет соединения, ошибка запроса)."""


def _com_error():
    """Класс ошибки COM; без pywin32 (Linux) — исключение, которое никто не бросает."""
    try:
        import pythoncom
    except ImportError:
        return type("ComError", (Exception,), {})
    return pythoncom.com_error


# --- базы и учётные данные -------------------------------------------------------------------------

def bases_file():
    return Path(os.environ.get("OTK_BASES") or Path.home() / ".otk" / "bases.toml")


def load_bases(path=None):
    p = Path(path) if path else bases_file()
    if not p.exists():
        return {}
    with open(p, "rb") as f:
        return tomllib.load(f).get("bases", {})


def read_credential(target):
    """Пароль общей учётной записи Windows (cmdkey /generic:<target> /user:<имя> /pass) или None."""
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(pcred)):      # 1 = CRED_TYPE_GENERIC
        return None
    try:
        c = pcred.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        advapi.CredFree(pcred)


def connection_string(connection, user="", password=""):
    """Строка внешнего соединения из «/Fкаталог» или «/Sсервер\\база»."""
    c = connection.strip()
    if c[:2].upper() == "/S":
        server, _, ref = c[2:].replace("/", "\\").partition("\\")
        s = f'Srvr="{server}";Ref="{ref}";'
    else:
        s = f'File="{c[2:] if c[:2].upper() == "/F" else c}";'
    if user:
        s += f'Usr="{user}";'
    if password:
        s += f'Pwd="{password}";'
    return s


def resolve(base, bases=None):
    """Описание базы: псевдоним из файла баз или строка «/F…»/«/S…» напрямую (без пароля)."""
    bases = load_bases() if bases is None else bases
    if base in bases:
        b = dict(bases[base])
        password = ""
        if b.get("password_cred"):
            password = read_credential(b["password_cred"]) or ""
            if not password:
                raise ConsoleError(f"нет учётной записи «{b['password_cred']}» в Диспетчере учётных данных Windows")
        elif b.get("password_env"):
            password = os.environ.get(b["password_env"], "")
        return {"name": base, "connection": b["connection"], "user": b.get("user", ""), "password": password,
                "progid": b.get("progid")}
    if base[:2].upper() in ("/F", "/S"):
        return {"name": base, "connection": base, "user": "", "password": "", "progid": None}
    raise ConsoleError(f"база «{base}» не описана в {bases_file()}; есть: {', '.join(bases) or 'нет'}")


# --- значения ------------------------------------------------------------------------------------

def _is_com(v):
    return hasattr(v, "_oleobj_")


def to_py(conn, v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, datetime.datetime):
        return v.replace(tzinfo=None).isoformat()   # дата 1С без часового пояса
    if _is_com(v):
        try:
            return {"repr": conn.String(v), "type": conn.XMLТипЗнч(v).ИмяТипа, "id": conn.XMLСтрока(v)}
        except Exception:                            # noqa: BLE001 — значение без XML-представления
            return {"repr": conn.String(v)}
    return str(v)


def to_1c(conn, v):
    if isinstance(v, list):
        arr = conn.NewObject("Массив")
        for x in v:
            arr.Добавить(to_1c(conn, x))
        return arr
    if isinstance(v, dict):
        if "date" in v:
            # «наивную» дату pywin32 считает местной и сдвигает на часовой пояс; дата 1С — без пояса
            return datetime.datetime.fromisoformat(v["date"]).replace(tzinfo=datetime.timezone.utc)
        if "type" in v:
            return conn.XMLЗначение(conn.ИзXMLТипа(v["type"], ""), v.get("id", ""))
        raise ConsoleError(f"параметр не распознан: {v}")
    return v


def _error_text(exc):
    info = getattr(exc, "excepinfo", None)
    text = str(info[2] if info and info[2] else exc)
    return " ".join(text.split())


def parse_query_error(text):
    """«{(строка, колонка)}: сообщение» из ошибки разбора запроса."""
    m = re.search(r"\{\((\d+),\s*(\d+)\)\}:\s*(.*?)(?:\s{2,}|$)", text)
    if not m:
        return {"message": text[:500]}
    msg = re.split(r"\s+(?=ВЫБРАТЬ|SELECT)", m.group(3), maxsplit=1, flags=re.I)[0]
    return {"line": int(m.group(1)), "col": int(m.group(2)), "message": msg.strip()}


# --- сеанс ---------------------------------------------------------------------------------------

class Session:
    def __init__(self, base):
        self.base = base
        self.conn = None
        self.last = 0.0
        self.connect_seconds = None

    def connect(self):
        if self.conn is not None:
            return self.conn
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            raise ConsoleError("консоль ИБ работает через COM-коннектор 1С: только Windows, нужен pywin32") from None
        pythoncom.CoInitialize()
        errors = []
        for progid in ([self.base["progid"]] if self.base.get("progid") else PROGIDS):
            try:
                connector = win32com.client.Dispatch(progid)
            except Exception as e:                   # noqa: BLE001 — коннектор не зарегистрирован
                errors.append(f"{progid}: {e}")
                continue
            t = time.time()
            try:
                self.conn = connector.Connect(connection_string(self.base["connection"], self.base["user"],
                                                                self.base["password"]))
            except pythoncom.com_error as e:
                raise ConsoleError(f"подключение к {self.base['name']} не удалось: {_error_text(e)[:500]}") from None
            self.connect_seconds = round(time.time() - t, 1)
            self.last = time.time()
            return self.conn
        raise ConsoleError("COM-коннектор 1С недоступен: " + "; ".join(errors))

    def close(self):
        if self.conn is not None:
            self.conn = None
            gc.collect()                                  # сеанс 1С закрывается с последней ссылкой

    def query(self, text, params=None, limit=200):
        com_error = _com_error()
        conn = self.connect()
        self.last = time.time()
        q = conn.NewObject("Запрос", text)
        try:
            for name, value in (params or {}).items():
                q.УстановитьПараметр(name, to_1c(conn, value))
            t = time.time()
            batch = q.ВыполнитьПакет()
            seconds = round(time.time() - t, 3)
        except com_error as e:
            err = parse_query_error(_error_text(e))
            where = f" (строка {err['line']}, колонка {err['col']})" if "line" in err else ""
            raise ConsoleError(f"ошибка запроса{where}: {err['message']}") from None
        results = []
        for k in range(batch.Количество()):
            tab = batch.Получить(k).Выгрузить()
            cols = [tab.Колонки.Получить(i) for i in range(tab.Колонки.Количество())]
            total = tab.Количество()
            rows = []
            for r in range(min(total, limit)):
                row = tab.Получить(r)
                rows.append([to_py(conn, row.Получить(i)) for i in range(len(cols))])
            results.append({"columns": [c.Имя for c in cols], "rows": rows, "total": total,
                            "truncated": total > limit})
        self.last = time.time()
        return {"base": self.base["name"], "seconds": seconds, "results": results}

    def check(self, text):
        com_error = _com_error()
        conn = self.connect()
        self.last = time.time()
        try:
            params = conn.NewObject("Запрос", text).НайтиПараметры()
            names = [p.Имя for p in params]         # ОписаниеПараметровЗапроса: без Получить(), только обход
            return {"ok": True, "parameters": names}
        except com_error as e:
            return {"ok": False, **parse_query_error(_error_text(e))}

    def metadata(self, name):
        conn = self.connect()
        self.last = time.time()
        md = conn.Метаданные
        if name in COLLECTIONS:
            coll = getattr(md, name)
            return {"collection": name, "objects": [f"{o.Имя} — {o.Синоним}" if o.Синоним and o.Синоним != o.Имя
                                                    else o.Имя for o in coll]}
        obj = md.НайтиПоПолномуИмени(name)
        if obj is None:
            raise ConsoleError(f"объект метаданных «{name}» не найден")

        def fields(coll):
            out = []
            for a in coll:
                types = []
                for t in a.Тип.Типы():
                    m = md.НайтиПоТипу(t)
                    types.append(m.ПолноеИмя() if m is not None else conn.String(t))
                out.append(f"{a.Имя}: {' | '.join(types)}")
            return out

        res = {"name": obj.ПолноеИмя(), "synonym": obj.Синоним}
        for fs in FIELD_SETS:
            try:
                res[fs] = fields(getattr(obj, fs))
            except Exception:                        # noqa: BLE001, S110 — у объекта нет такой коллекции
                pass
        try:
            res["ТабличныеЧасти"] = {ts.Имя: fields(ts.Реквизиты) for ts in obj.ТабличныеЧасти}
        except Exception:                            # noqa: BLE001, S110 — у объекта нет такой коллекции
            pass
        try:
            res["ЗначенияПеречисления"] = [v.Имя for v in obj.ЗначенияПеречисления]
        except Exception:                            # noqa: BLE001, S110 — у объекта нет такой коллекции
            pass
        return {k: v for k, v in res.items() if v not in ([], {}, None)}

    def eventlog(self, flt=None, limit=200, group_by=None, scan=100000):  # noqa: C901 — отборы по полям ЖР
        """Журнал регистрации (ВыгрузитьЖурналРегистрации во внешнем соединении, только чтение).

        flt: from/to (дата ISO), level (Информация|Предупреждение|Ошибка|Примечание, список), user, event
        (системные имена `_$Data$_.Post`, список), metadata (полное имя, список), data ({type, id}),
        dataPresentation, comment, session, application, computer, transactionStatus
        (Зафиксирована|Отменена|НеЗавершена|НетТранзакции). Выгрузка идёт от начала периода вперёд.
        group_by — поля для подсчёта (user, event, metadata, level, application, computer, session, date:день|час)
        по первым scan записям; без него — первые limit записей."""
        conn = self.connect()
        self.last = time.time()
        flt = dict(flt or {})
        s = conn.NewObject("Структура")

        def arr(v, fn=lambda x: x):
            vals = v if isinstance(v, list) else [v]
            a = conn.NewObject("Массив")
            for x in vals:
                a.Добавить(fn(x))
            return a

        if flt.get("from"):
            s.Вставить("ДатаНачала", to_1c(conn, {"date": flt["from"]}))
        if flt.get("to"):
            s.Вставить("ДатаОкончания", to_1c(conn, {"date": flt["to"]}))
        if flt.get("level"):
            s.Вставить("Уровень", arr(flt["level"], lambda x: getattr(conn.УровеньЖурналаРегистрации, x)))
        if flt.get("transactionStatus"):
            s.Вставить("СтатусТранзакции", arr(flt["transactionStatus"],
                                                lambda x: getattr(conn.СтатусТранзакцииЗаписиЖурналаРегистрации, x)))
        if flt.get("metadata"):
            def md(name):
                m = conn.Метаданные.НайтиПоПолномуИмени(name)
                if m is None:
                    raise ConsoleError(f"объект метаданных «{name}» не найден")
                return m
            s.Вставить("Метаданные", arr(flt["metadata"], md))
        if flt.get("data") is not None:
            s.Вставить("Данные", to_1c(conn, flt["data"]))
        for key, name in (("user", "Пользователь"), ("event", "Событие"), ("application", "ИмяПриложения"),
                          ("computer", "Компьютер"), ("session", "Сеанс")):
            if flt.get(key) is not None:
                s.Вставить(name, arr(flt[key]))
        for key, name in (("dataPresentation", "ПредставлениеДанных"), ("comment", "Комментарий")):
            if flt.get(key):
                s.Вставить(name, flt[key])
        unknown = set(flt) - {"from", "to", "level", "transactionStatus", "metadata", "data", "user", "event",
                              "application", "computer", "session", "dataPresentation", "comment"}
        if unknown:
            raise ConsoleError(f"неизвестные поля отбора: {', '.join(sorted(unknown))}")
        cols = ("Дата,Уровень,ИмяПользователя,Событие,ПредставлениеСобытия,Метаданные,ПредставлениеДанных,Данные,"
                "Комментарий,СтатусТранзакции,Транзакция,Сеанс,ИмяПриложения,Компьютер")
        tab = conn.NewObject("ТаблицаЗначений")
        t = time.time()
        try:
            conn.ВыгрузитьЖурналРегистрации(tab, s, cols, None, scan if group_by else limit)
        except Exception as e:                       # noqa: BLE001 — ошибка платформы в отборе
            raise ConsoleError(f"журнал регистрации: {_error_text(e)[:400]}") from None
        seconds = round(time.time() - t, 3)
        names = [tab.Колонки.Получить(i).Имя for i in range(tab.Колонки.Количество())]
        idx = {n: i for i, n in enumerate(names)}
        total = tab.Количество()

        def val(row, name):
            v = row.Получить(idx[name])
            if name in ("Уровень", "СтатусТранзакции", "Метаданные") and _is_com(v):
                return conn.String(v)
            return to_py(conn, v)

        if group_by:
            fields = {"user": "ИмяПользователя", "event": "ПредставлениеСобытия", "metadata": "Метаданные",
                      "level": "Уровень", "application": "ИмяПриложения", "computer": "Компьютер",
                      "session": "Сеанс", "status": "СтатусТранзакции"}
            counts = {}
            for r in range(total):
                row = tab.Получить(r)
                key = []
                for g in group_by:
                    if g.startswith("date"):
                        d = str(val(row, "Дата"))
                        key.append(d[:13] if g.endswith("час") else d[:10])
                    elif g in fields:
                        key.append(str(val(row, fields[g])))
                    else:
                        raise ConsoleError(f"группировка по «{g}» не поддерживается: "
                                           f"{', '.join(fields)}, date:день|час")
                counts[tuple(key)] = counts.get(tuple(key), 0) + 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
            return {"base": self.base["name"], "seconds": seconds, "scanned": total, "truncated": total >= scan,
                    "group_by": group_by, "groups": [list(k) + [n] for k, n in top], "groups_total": len(counts)}
        rows = []
        for r in range(total):
            row = tab.Получить(r)
            rec = {n: val(row, n) for n in names}
            if isinstance(rec.get("Комментарий"), str) and len(rec["Комментарий"]) > 1000:
                rec["Комментарий"] = rec["Комментарий"][:1000] + "…"
            rows.append({k: v for k, v in rec.items() if v not in ("", None)})
        self.last = time.time()
        return {"base": self.base["name"], "seconds": seconds, "rows": rows, "total": total,
                "truncated": total >= limit}


class Console:
    """Сеансы по базам с отпусканием по простою (idle секунд без вызовов)."""

    def __init__(self, idle=120, bases=None):
        self.idle = idle
        self.bases = bases
        self.sessions = {}

    def session(self, base):
        if base not in self.sessions:
            self.sessions[base] = Session(resolve(base, self.bases))
        return self.sessions[base]

    def release_idle(self):
        now = time.time()
        for s in self.sessions.values():
            if s.conn is not None and now - s.last > self.idle:
                s.close()

    def close(self, base=None):
        for name, s in self.sessions.items():
            if base in (None, name):
                s.close()
