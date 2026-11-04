"""Снятие безопасного режима с расширения внешним соединением (COM) — когда ibcmd нет (клиентская установка
платформы): в пакетном Конфигураторе такого ключа нет. Модуль-лист: импортирует его platform.py, сам ничего из ОТК
не импортирует (исполнитель COM — executors/com.py — зависит от platform)."""
import sys
import time


class Unavailable(Exception):
    """COM нет: не Windows, нет pywin32 или коннектор не зарегистрирован."""


def set_unsafe(connection, name, progid):
    """Расширение name — без безопасного режима и защиты от опасных действий. connection — строка соединения COM
    с пользователем. (rc, секунды, текст); COM нет — Unavailable."""
    if sys.platform != "win32":
        raise Unavailable("COM недоступен: только Windows")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise Unavailable('COM недоступен: нет pywin32 (pip install "otk-1c[com]")') from None
    t = time.time()
    try:
        connector = win32com.client.Dispatch(progid)
    except Exception as e:  # noqa: BLE001 - коннектор не зарегистрирован или не грузится
        raise Unavailable(f"COM недоступен ({progid}): {e}") from None
    try:
        conn = connector.Connect(connection)
        found = conn.РасширенияКонфигурации.Получить(conn.NewObject("Структура", "Имя", name))
        if not found.Количество():
            return 1, round(time.time() - t, 1), f"расширения {name} в ИБ нет"
        ext = found.Получить(0)
        ext.БезопасныйРежим = False
        protection = conn.NewObject("ОписаниеЗащитыОтОпасныхДействий")
        protection.ПредупреждатьОбОпасныхДействиях = False
        ext.ЗащитаОтОпасныхДействий = protection
        ext.Записать()
    except pythoncom.com_error as e:
        info = getattr(e, "excepinfo", None)
        return 1, round(time.time() - t, 1), " ".join(str(info[2] if info and info[2] else e).split())
    return 0, round(time.time() - t, 1), ""
