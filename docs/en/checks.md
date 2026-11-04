*Translated from the [Russian original](../checks.md); if they differ, the Russian page is authoritative.*

# Checks

## Checks and dependencies

Each check is enabled in `[checks]`: `on`, `auto` (the default) or `off`. If a dependency is missing:

- in the `on` mode the run ends with an infrastructure failure (code 2) naming what is missing;
- in the `auto` mode the check is not executed, and the summary, JUnit (`skipped`) and Problems write “not verified”
  with the reason and with what is lost;
- in the `off` mode the check is not executed, and one line “disabled by the setting” remains in the summary.

| Check | What | Needs |
| --- | --- | --- |
| `metadata` | references to metadata, forms, handlers, exported methods; dependent modules (calls of changed exports) — `off` disables their probes as well | — |
| `analyzers` | new remarks of the BSL analyzer for the change | BSL LS or bsl-analyzer (`environment.analyzer`) |
| `platform-api` | platform methods and types, availability by context | help of the installed platform |
| `probes-server` | compilation on the server and in the external connection | infobase copy; COM or YAxUnit |
| `probes-client` | compilation in the thin client | infobase copy, YAxUnit |
| `queries` | parsing of queries by the platform | infobase copy; COM or YAxUnit |
| `extcheck` | extended check of changed modules: common modules, in a Designer XML dump — and managed forms, objects, managers, record sets; all modes, including the web client | infobase copy |
| `scenarios` | scenario tests for the changed methods | infobase copy, YAxUnit |
| `smoke` | opening of the changed forms | infobase copy, YAxUnit |

`otk doctor` shows what has been found on the machine, which checks are available and what is lost without the rest.
If a check in the `on` mode is unavailable, `doctor` ends with code 2.

For `platform-api`, OTK indexes the syntax assistant help by itself from the installed platforms (a few seconds on the
first access; if the `.hbk` files have changed, the index is rebuilt). If there is no version of its own, the index
from the cache or the nearest version of the same line is used, and the substitution is noted in the summary. For a
server without a client (no `.hbk`) — a copy of the help in `environment.helpDir` (a directory named after the
version) or `otk platform-help build --from <каталог с .hbk> [--version 8.3.27.2342]`; `otk platform-help
status` shows which indexes exist and whether they are up to date. `helpAuto = false` — the index is not built by
itself (reproducible CI), a warning with the command is given instead.
The availability of methods, properties and types (`Новый …`) by context (server, client, external connection, web
client) is taken from the tables built by the Designer: the delivery contains 8.3.27, 8.5.1 and 8.5.4; for another
platform the nearest table of the same line is used. An exact one is built by `otk availability build`: a temporary
extension in the project's infobase copy, about a minute. The index and the tables are in the user cache: `OTK_CACHE`,
otherwise `%LOCALAPPDATA%/otk` or `~/.cache/otk`.

How to add your own check (an executor in the infobase or a static rule) — [adding-a-check.md](../adding-a-check.md)
(in Russian); finding codes and how to fix them — the [findings reference](../findings.md) (in Russian).

Without YAxUnit the full check works: the server and queries are checked through COM, while the thin client, forms
and scenarios get “not verified”.

## BSL analyzers

Which BSL LS remarks are errors and which are warnings — [“Summary and exit codes”](../report.md) (in Russian); what
each analyzer catches on the control defects — [comparison.md](../comparison.md#отк-и-анализаторы-bsl) (in Russian).

The `analyzers` check works through an adapter: `environment.analyzer = "bslls"` (BSL LS, the default),
  `"bsl-analyzer"` or `"both"` (`--analyzer`). bsl-analyzer sees the whole configuration: “after” is the whole dump
  tree (for an extension — with the main configuration from `parent`), the parsing is limited to the changed modules;
  “before” is a mirror of the tree on links in which the changed modules are returned to the base (the sources are not
  changed). Therefore, besides the general rules, it catches a new call of a nonexistent method, a wrong number of
  arguments, an unknown field, a query field, a server method on the client (`bsla:<Правило>`, warnings for now; to make
  one an error — [`[analyzerSeverity]`](configuration.md#analyzer-finding-level)), and it does not count old such
  remarks of a module as new. OTK writes the analyzer's native settings file by itself to a temporary directory: the
  basis is `bsl-analyzer.toml` or `.bsl-language-server.json` of the repository, on top of it — `minPlatform` and
  `compatWarnings` from `otk.toml`; if there is no file of its own — the critical rules and semantics. With `both`, one
  remark of both analyzers is one finding, and remarks of only one of them are counted in the notes. The choice can be
  set by dump format: `[environment.analyzer]` with the keys `designer-xml` and `edt` (a format that is not in the
  table — `bslls`; `--analyzer` sets the choice for all formats). OTK itself knows which formats an analyzer
  understands: bsl-analyzer 0.3.2 — only `designer-xml` (it does not read `.mdo` metadata and crashes on protected
  modules), so with `both` on EDT only BSL LS works (with a note), and an explicit `bsl-analyzer` for EDT is
  “not verified” with the reason.

The BSL LS rules on events (`EventHandlerInvalidSignature`, `EventHandlerOutsideEventRegion`) work only with the
platform help (`shcntx_ru.hbk`). OTK passes the analyzer the help of the platform of the check (`environment.platform`
or the installed one), otherwise the copy from `environment.helpDir`; the `v8platform.binPath` key of your own
`.bsl-language-server.json` takes precedence. Without the help (Linux without a platform, a container) these rules do
not work — a note in the summary says so.

## Scenario tests

Tests of the logic are written by the user — ordinary [YAxUnit](https://github.com/bia-technologies/yaxunit) tests in
the project's test extension `tests/cfe/<имя>` (a Designer XML dump; the same layout is shown by the test panel of VS
Code “1C: Platform Tools”). OTK does not invent them but selects them: a test is bound to a method by a tag with the
full name of the method — `ИмяОбщегоМодуля.Метод` (for other modules — `Справочник.Клиенты.Метод`), the module tag
is its name:

```bsl
Процедура ИсполняемыеСценарии() Экспорт
	ЮТТесты
		.ДобавитьТест("СхемаHttpЗаменяетсяНаHttps", "http:// -> https://",
			"демо_КлиентСервер.НормализованнаяСсылкаНаЗапись")
		.ДобавитьТест("ПроведениеДокумента").Тег("Документ.Продажа.ОбработкаПроведения")
			.ВТранзакции();
КонецПроцедуры
```

`gate` runs only the tests tagged with the changed methods (the YAxUnit `tags` filter), `--all-scenarios` — all of
them; if there are no tests for the change — “0 scenarios”, the client is not started. The test extensions are loaded
into OTK's own infobase copy (again — only when changed). A test must not leave data behind: `ВТранзакции()`,
YAxUnit's `УдалениеТестовыхДанных()`. A failed test is an error of the summary with the location in the test module
and the text of the assertion. The directory is `[scenarios] path` (`tests/cfe` by default). Scenarios are not
installed into a production infobase by the Platform Tools hook.

The YAxUnit engine is installed by OTK into its infobase copy by itself (`environment.yaxunit`). The engine sources
that the Platform Tools command “Add YAxUnit” puts in place (`tests/cfe/YAXUNIT`), and extensions without tests, are
skipped when looking for scenarios; it is convenient to add `tests/cfe/YAXUNIT` to `.gitignore`. To run tests from the
test panel of VS Code, the engine and the test extension must be loaded into the profile infobase (“Load test
extensions”) — without the engine 1C opens and waits, and there is no report.

### Test stubs

`otk scaffold [--base <ref>] [--head <ref>] [--extension <имя>] [--no-draft]` — for the changed methods (by default:
the working tree with the uncommitted changes against `[hook] base`) that have no test with their tag, it adds a test
skeleton to the test extension: the registration `ЮТТесты.ДобавитьТест(…, "<Модуль>.<Метод>")` and a procedure with
a call hint. The test module is `<префикс тестового расширения><имя модуля>` (a module of the same name is extended,
existing procedures are not touched); if there is no test extension, `tests/cfe/Тесты` is created. The skeleton is
marked with `ЮТест.Пропустить` until the test is written: in the `gate` summary it is “not verified”, not “passed”.

A draft instead of an empty skeleton is an external command `[scaffold] draft` (better in `otk.local.toml`): on stdin
it receives JSON about the method (module, name, parameters, contexts, method text), and on stdout it returns the body
of the test procedure. OTK does not embed AI — an example for Claude Code:
[scaffold/draft-claude.py](../scaffold/draft-claude.py); any tool with the same protocol will do.
An error of the command — an empty skeleton.

## Form smoke test

The changed managed forms — the form module or its `Form.xml` changed — are opened and closed by OTK in the thin
client of its infobase copy (by a separate client launch after the probes). It catches what cannot be seen without
opening: errors in the handlers `ПриОткрытии`, `ПриЗакрытии` and in the code called from them (the platform does not
pass them to `Открыть()` but writes them to the event log — OTK takes them from there by the form frame in the
stack), and also a form that does not close: a modal window, a question to the user, a long handler. OTK waits for
such a form for `[smoke] timeout` seconds (60 by default), terminates the client and checks the remaining forms with a
new launch.

- A form that refuses to open without parameters (`Отказ` in `ПриСозданииНаСервере`) is “not verified”.
- An error on opening is an error of the summary (`[smoke] severity = "warning"` — a warning).
- `[smoke] exclude` — patterns of full names of forms not to open (`"Обработка.*"`, `"*.ФормаНастроек"`);
  `[checks] smoke = "off"` — disable.
- The form is opened with the data of the infobase copy: handlers that depend on data or settings behave as in that
  infobase. A copy without initial filling (an empty infobase, the first launch not performed) gives false errors: in
  an empty 1C:ERP the document and catalog forms do not open (`Не удалось заполнить поле "Организация"`,
  `Преобразование значения к типу Булево`). For the smoke test take a copy with a filled infobase or set
  `[smoke] severity = "warning"`.
