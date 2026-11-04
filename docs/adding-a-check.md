# Как добавить проверку

Новая проверка — один модуль и записи в реестрах. `gate/pipeline`, `gate/report` и CLI не меняются: сводка, JUnit и Problems
строятся по полям результатов (`executor`, `check`, `outcome`), а решения о пропуске — по реестру проверок. `gate/runner`
меняется, только если у проверки новая зависимость (её обнаружение — в `ctx.have`).

## 1. Запись в реестре проверок

`src/otk_1c/checks.py`, `CHECKS`: имя (ключ в `[checks]` настроек), что проверяет, зависимости и что теряется без
неё. Зависимость — ключ `DEPS`; вложенный кортеж — достаточно любой из них:

```python
Check("my-check", "что проверяет", ("ib", "yaxunit"), "что теряется без проверки"),
```

После этого проверка видна в `otk doctor` и включается `[checks] my-check = "on" | "auto" | "off"`. Новая
зависимость — ключ в `DEPS` и её обнаружение в `gate/doctor.environment` (для `doctor`) и в `RunContext.have` (для шага run:
`ctx.have.update(...)` в `runner._run` или в `select` исполнителя, как `com` у COM).

## 2а. Проверка в базе — исполнитель

Модуль в `src/otk_1c/probes/executors/` с классом по протоколу `executors.Executor`, его импорт и запись в
`executors.EXECUTORS` в `probes/executors/__init__.py` (порядок кортежа — порядок запуска):

```python
from .base import result

NAME = "my-executor"


class MyExecutor:
    name = NAME                    # executor в результатах и ключ времени в run.json timings
    checks = ("my-check",)

    def select(self, ctx):
        """Что проверять. Решение реестра — ctx.gate.ready(...): «не проверено» и сбой в режиме on оно пишет само."""
        work = [m for m in ctx.modules if m["kind"] == "CommonModule"]
        return work if work and ctx.gate.ready("my-check", ctx.have) else None

    def run(self, ctx, items):
        """(результаты, примечание или пусто)."""
        ctx.ensure_tools()         # если нужен YAxUnit в базе
        return [result(NAME, "probe", m, "Server", "ok") for m in items], None
```

- `ctx` (`executors.RunContext`) — план, настройки, `platform` (копия ИБ: Конфигуратор, клиент), `run_dir`,
  `tree_root` (дерево проверяемой редакции), `config_root`, `session` (рабочая база пользователя или `None`),
  `gate`, `have` (зависимости: `ib`, `yaxunit`, `com`), `log`.
- `select` всех исполнителей выполняется до запуска первого — в порядке реестра; решения о пропуске из `select` идут
  в `run.json` в этом порядке, решения из `run` (COM не подключился, YAxUnit нет) — после них.
- Результат — `executors.result(...)`: `outcome` из `ok | compile-error | runtime-error | unknown | failed | skipped`;
  провалы — `model.FAIL`. Ошибки платформы разбирают `executors.classify` и `executors.query_result`.
- Обход: исполнитель, который не может выполнить своё (COM не подключился), передаёт элементы следующему —
  `ctx.hand(<имя исполнителя>, <ключ>, элементы)`; тот получает их в `ctx.handed[<имя>]` и запускается, даже если
  сам ничего не выбрал. Так COM передаёт серверные модули и запросы YAxUnit. Получатель стоит в реестре позже
  передающего (атрибут `fallback`, проверяет `test_registries`), иначе переданное пропадёт молча.
- Сбой инфраструктуры (не из-за кода проекта) — `errors.InfraError`: прогон завершится с кодом 2.

## 2б. Статическая проверка — правило

Функция в `src/otk_1c/gate/static.py` (или в своём модуле) и запись в `static.RULES`. Правило получает
`static.ModuleContext` одного модуля плана и сообщает находки через `emit`:

```python
def my_rule(m):
    for no in sorted(m.lines):                     # изменённые строки
        if "Сообщить(" in m.san_lines[no - 1]:    # текст без строк и комментариев
            m.emit("message-call", "warning", no, "Сообщить() в изменённой строке")
```

В `ModuleContext`: `entry` (модуль плана), `parsed` (`bsl.parse`: методы, условия, очищенный текст), `lines`,
`san_lines`, `locals` (имена, перекрывающие глобальные), `index` (индекс метаданных), `checker` (общие регулярные
выражения и поиск по индексу), `api` (справка платформы или `None`), `read_head`/`read_parent`, `lay` (формат
исходников). Находка: `check` — код находки (в сводке и Problems — `rule`), `severity` — `error`
(только однозначные случаи) или `warning`. Правила статики и поиск зависимых модулей — проверка `metadata`:
`[checks] metadata = "off"` выключает их все, кроме `platform-api` (у неё свой режим); правило, которому нужна
зависимость, — своя запись в `CHECKS` и
решение `gate` в `pipeline.static_step`, как у `platform-api`.
Правило по плану целиком (не по модулю) — функция `(plan, index, lay) -> [находки]` в `static.PLAN_RULES`.

## 3. Тесты и документы

- Исполнитель: случай в `tests/runner_cases.py` (заглушка исполнителя и ожидаемый журнал вызовов); эталон
  `tests/runner_golden.json` пересобирается `python tests/runner_cases.py` с `PYTHONPATH` из `src` и `tests`
  (разделитель `;` в Windows, `:` в Linux) — только когда поведение меняется намеренно, с разбором разницы.
- Правило: тест на фикстуре выгрузки в `tests/` по образцу `test_static.py`.
- Строка в таблице «Проверки и зависимости» в [checks.md](checks.md#проверки-и-зависимости).
- Новый код находки (правило статики, вид пробы) — раздел в [справочнике находок](findings.md): тест
  `FindingsDocTest` падает, если код не описан.
