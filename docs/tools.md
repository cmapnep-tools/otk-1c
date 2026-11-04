# Дополнительные инструменты

## Консоль ИБ (запросы и метаданные копии базы)

Для отладки и разбора учёта в копии базы (своей или заказчика): выполнить запрос и получить таблицу,
разобрать запрос без выполнения, посмотреть метаданные живой базы. Только чтение — язык запросов данные
не меняет, исполнения произвольного кода нет. Внешнее соединение (COM, `pywin32`).

```
otk console query --ib erp "ВЫБРАТЬ ПЕРВЫЕ 10 Н.Ссылка, Н.Код ИЗ Справочник.Номенклатура КАК Н"
otk console query --ib erp --params '{"Д": {"date": "2026-01-31"}}' -     # текст из stdin
otk console check --ib erp "ВЫБРАТЬ … ГДЕ Т.Поле = &П"                    # ошибка с позицией или параметры
otk console metadata --ib erp "РегистрНакопления.ТоварыНаСкладах"        # реквизиты, ТЧ, измерения с типами
otk console eventlog --ib erp --filter '{"from":"2026-10-01","event":"_$Data$_.Post"}'   # журнал регистрации
otk console eventlog --ib erp --filter '{"from":"2026-10-01","level":"Ошибка"}' --group event,user  # кто/что/сколько
```

**Журнал регистрации** (`eventlog`, MCP `ib_eventlog`): отбор — `from`/`to`, `level`, `user` (имя), `event` (системные
имена `_$Data$_.Post|Update|Delete`, `_$Session$_.*`, `_$PerformError$_` или имена событий конфигурации), `metadata`
(полное имя), `data` (ссылка `{type, id}` из ответа запроса — вся история объекта), `dataPresentation`, `comment`,
`session`, `application`, `computer`, `transactionStatus`. Без `--group` — первые записи (до `--limit`), с `--group` —
подсчёт по полям `user|event|metadata|level|application|computer|session|status|date:день|date:час`. Выгрузка идёт от
начала периода вперёд, поэтому задавайте `from`.

Базы — псевдонимы в файле пользователя (`OTK_BASES` или `~/.otk/bases.toml`, вне git):

```toml
[bases.erp]
connection = "/FC:/1C/Копии/ERP"        # или "/Sсервер\\база"
user = "Администратор"
password_cred = "otk:erp"           # Windows: cmdkey /generic:otk:erp /user:Администратор /pass
# password_env = "ERP_PASSWORD"          # или переменная окружения; пароль открытым текстом не хранится
```

Параметры запроса — JSON: строка, число, булево, `null`, `{"date": "…"}`, `{"type": "CatalogRef.Номенклатура",
"id": "<УИД>"}` (значение перечисления — `{"type": "EnumRef.X", "id": "<имя>"}`), список — массив. Ссылка в
ответе — `{"repr", "type", "id"}`; NULL и НЕОПРЕДЕЛЕНО внешнее соединение отдаёт одинаково (`null`).

**MCP** (`otk mcp`): `ib_query`, `ib_check`, `ib_metadata`, `ib_eventlog`, `ib_bases`, `ib_disconnect`. Подключение к
базе — секунды, поэтому сервер держит соединение между вызовами и отпускает его через `--idle` секунд простоя
(по умолчанию 120) или по `ib_disconnect`: открытое соединение — это сеанс 1С, он мешает загрузке расширения и
монопольным операциям с этой базой. Регистрация в клиенте MCP (Claude Code, `.mcp.json`):

```json
{"mcpServers": {"otk-console": {"command": "python", "args": ["-m", "otk_1c", "mcp"]}}}
```

## Анализ технологического журнала

`otk tj <отчёт> <каталог ТЖ>` (и MCP `tj_report`) — агрегаты по журналу на диске: сумма, количество, среднее,
максимум по группам, сортировка (`--sort sum|count|mean|max`), топ (`--top`), отбор по процессу (`--process rphost`)
и периоду (`--from`, `--to`). Многострочные события склеиваются, значения нормализуются (GUID → `{GUID}`, `(число)` →
`({NUM})`, временные таблицы → `{TempTable}`, строки параметров `p_N:` убираются); длительности приводятся к
микросекундам (8.3 — мкс, 8.2 — 1/10000 с).

| Отчёт | Что | Режимы |
| --- | --- | --- |
| `calls` | серверные вызовы `CALL` по контексту | `--value duration|memory|memorypeak|inbytes|outbytes|cpu` |
| `queries` | запросы СУБД (`DBMSSQL`, `DBPOSTGRS`, …) | `--mode sql|sql+context|context` |
| `sdbl` | запросы платформы `SDBL` | `--mode sql|sql+context|context` |
| `errors` | ошибки `EXCP` и ошибки запросов `QERR` по описанию (сортировка по количеству) | `--mode descr|descr+context` |
| `locks` | ожидания `TLOCK` с `WaitConnections` по пространству и контексту | |
| `timeouts` | `TTIMEOUT`, `TDEADLOCK` | |
| `events` | число и длительность по типам событий (доля СУБД во времени вызовов) | |

Скорость (ТЖ 1 ГБ, 12 ядер): файлы и большие файлы (кусками по 64 МБ по границам событий) разбираются параллельно —
запросы с контекстом за 12 с, вызовы и ошибки за 3 с; в один поток — 33 с и 10–11 с. Для сравнения
связки perl/gawk на тех же данных — 14–30 с; результаты групп совпадают.

Шаблоны `logcfg.xml` под отчёты — [calls](techlog/calls.xml), [queries](techlog/queries.xml), [locks](techlog/locks.xml),
[errors](techlog/errors.xml), [leaks](techlog/leaks.xml), [production](techlog/production.xml) ( — постоянный журнал рабочих серверов). Подход перенесён из bash/perl/gawk-скриптов разбора ТЖ.
