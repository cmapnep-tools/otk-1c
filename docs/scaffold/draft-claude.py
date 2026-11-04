"""Пример команды черновика для `otk scaffold` ([scaffold] draft): тело теста от Claude Code CLI.

Протокол: на stdin — JSON о методе ({module, path, method, kind, export, params, contexts, tag, testModule,
source}), на stdout — тело процедуры теста (строки встроенного языка без «Процедура … КонецПроцедуры»).
Код возврата не 0 или пустой вывод — ОТК оставит пустой каркас.

Подключение (otk.local.toml — команда своя у каждого разработчика):

    [scaffold]
    draft = "python docs/scaffold/draft-claude.py"

Нужен установленный и авторизованный `claude` (Claude Code). Подойдёт любой другой инструмент с тем же
протоколом: ОТК ИИ не встраивает.
"""
import json
import shutil
import subprocess
import sys

PROMPT = """Ты пишешь тест YAxUnit (1С:Предприятие 8.3, встроенный язык, русский синтаксис) для метода ниже.
Верни ТОЛЬКО тело процедуры теста — строки кода без объявления процедуры, без ``` и пояснений.
Используй ЮТест.ОжидаетЧто(...) для проверок. Если без данных базы проверить нельзя — подготовь данные
средствами YAxUnit (ЮТест.Данные()) или оставь комментарий TODO и строку ЮТест.Пропустить("черновик").

Модуль: {module} (контексты: {contexts}); метод: {method}({params}), {kind}, экспорт: {export}.
Тестовый модуль: {testModule}.

Текст метода:
{source}
"""


def main():
    info = json.loads(sys.stdin.read())
    exe = shutil.which("claude")
    if not exe:
        return 1
    prompt = PROMPT.format(**{**info, "params": ", ".join(info.get("params", [])),
                              "contexts": ", ".join(info.get("contexts", []))})
    r = subprocess.run([exe, "-p", prompt], capture_output=True, text=True, encoding="utf-8", timeout=280)
    body = r.stdout.strip().removeprefix("```bsl").removeprefix("```").removesuffix("```").strip()
    if r.returncode or not body:
        return 1
    sys.stdout.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
