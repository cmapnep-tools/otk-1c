*Translated from the [Russian original](../configuration.md); if they differ, the Russian page is authoritative.*

# Settings (`otk.toml` / `otk.local.toml`)

Priority: command-line arguments ([“Command line only”](commands.md#command-line-only)) > `OTK_*` variables >
`otk.local.toml` > `otk.toml` > defaults. A variable overrides the project setting for you personally (your own
platform, your own infobase copy); what was taken from variables is shown by `otk doctor`. An unknown section or key
(a typo) is a warning in `doctor` and in the notes of the summary.
An example with all the keys — [otk.example.toml](../../otk.example.toml).

| Variable | Key |
| --- | --- |
| `OTK_IB`, `OTK_USER`, `OTK_PASSWORD` | `environment.ib`, `user`, `password` |
| `OTK_PLATFORM`, `OTK_COM`, `OTK_IB_TOOL` | `environment.platform`, `comConnector`, `ibTool` |
| `OTK_YAXUNIT`, `OTK_EDTCLI` | `environment.yaxunit`, `edtcli` |
| `OTK_BSLLS`, `OTK_BSL_ANALYZER` | `environment.bslls`, `bslAnalyzer` |
| `OTK_RUN_DIR`, `OTK_HIDDEN_DESKTOP` | `environment.runDir`, `hiddenDesktop` (`0`, `false`, `no` — false) |

Without a key in the settings: `OTK_CACHE` — the OTK cache (platform help, availability tables), `OTK_BASES` — the
infobase file of `otk console`.

| Section | Key | What |
| --- | --- | --- |
| `project` | `source` | directory of the XML dump or of the EDT project relative to the repository root (`src/cf`; if not set and there is no dump in `src/cf` — the repository root, if the dump is there) |
| | `parent`, `extension` | for an extension: the XML dump of the main configuration and the name (by default — from Configuration.xml) |
| `environment` | `ib`, `user`, `password` | your own infobase copy and the sign-in to it |
| | `lockTimeout` | how many seconds to wait while another OTK run checks the same infobase (900), then exit code 2 (`ib-busy`) — see [“Troubleshooting”](troubleshooting.md) |
| | `connection`, `keepTools`, `loadProject` | a production infobase instead of a copy (`/F…`, `/S…`), see [“Production infobase instead of a copy”](../integrations.md#рабочая-база-вместо-копии) (in Russian) |
| | `platform`, `comConnector`, `bslls`, `yaxunit` | tools (the first three are found automatically) |
| | `bsllsTimeout`, `bsllsBatchMB` | timeout of a BSL LS run and the size of a text batch for a large run (200 MB) — see [“Summary and exit codes”](../report.md) (in Russian) |
| | `helpDir`, `helpAuto` | a copy of the platform help (a directory named after the version) and automatic build of its index (`true`; `false` — only a warning with the command) — see [“Checks and dependencies”](checks.md#checks-and-dependencies) |
| | `hiddenDesktop` | the 1C client on a hidden Windows desktop (`true`; `false` — see the window when debugging, `OTK_HIDDEN_DESKTOP`) |
| | `ibTool` | what OTK uses to change the configuration of the infobase copy — partial load, dump, database update, extensions: `designer` (batch Designer, the default) or `ibcmd` (`OTK_IB_TOOL`). A full load of a dump uses `ibcmd` if it is present (without it — the Designer), an infobase on a server (`/S…`) — always the Designer; `ibcmd` without `ibcmd` in the platform directory is a failure; see “Designer or ibcmd” in [troubleshooting.md](troubleshooting.md) |
| | `analyzer`, `bslAnalyzer`, `bslAnalyzerTimeout` | the BSL analyzer of the `analyzers` check: `bslls` (BSL LS, the default), `bsl-analyzer` or `both` — for all formats or as a table by dump format (`[environment.analyzer]` `designer-xml = "both"`, `edt = "bslls"`); the path to bsl-analyzer (`OTK_BSL_ANALYZER` or `PATH`) and the timeout of its run (1800 s) — see [“BSL analyzers”](checks.md#bsl-analyzers) |
| | `edtcli`, `edtTimeout` | `1cedtcli` and the export timeout for EDT sources (see [“EDT format”](../edt.md) (in Russian)) |
| | `contexts` | probe contexts available locally (`Server`, `ThinClient`, `ExternalConnection`); without `ExternalConnection` COM is not used, the server one goes through [YAxUnit](https://github.com/bia-technologies/yaxunit), the external connection — “not covered by this run” |
| | `ordinaryApplication` | the configuration also works in the ordinary application |
| | `webClient` | the configuration works in the web client: a platform method, property or type that is unavailable in the web client is an error, not a warning |
| | `minPlatform`, `compatWarnings` | warnings “newer than the compatibility mode” for the same line: only newer than the minimum platform (`--min-platform 8.3.24`) or none at all (`false`, `--no-compat-warnings`); hidden ones are counted in the notes, errors of the newer line remain |
| | `executors.server` | `auto` (COM, otherwise YAxUnit) or `yaxunit` |
| | `runDir` | run directory (`%TEMP%/otk`, `OTK_RUN_DIR`) |
| `checks` | check name | `on` / `auto` / `off` — see [“Checks and dependencies”](checks.md#checks-and-dependencies) |
| `platformNames` | `methods`, `constructors`, `globals`, `forms`, `managerMethods` | additions to the platform name dictionary — see [“Platform names dictionary”](#platform-names-dictionary) |
| `analyzerSeverity` | `bslls`, `bsl-analyzer` | which BSL analyzer rules are errors: `error` — add, `warning` — remove — see [“Analyzer finding level”](#analyzer-finding-level) |
| `[[queries.resolvers]]` | `name`, `command`, `timeout` | external restorers of assembled queries (off) — see [“Known limits”](limits.md) |
| `hook` | `base`, `mode`, `fast`, `infraFail` | [pre-push hook](../integrations.md#хук-pre-push) (in Russian) |
| `designerCheck` | `modes`, `baseline` | modes and baseline of the check of all modules (`otk designer-check`) |
| `smoke` | `exclude`, `timeout`, `severity` | [smoke test](checks.md#form-smoke-test) of opening the changed forms |
| `scenarios` | `path` | directory of scenario tests (`tests/cfe` by default) — see [“Checks and dependencies”](checks.md#checks-and-dependencies) |
| `report` | `open` | open the summary after the run (`html`) — see [“Summary and exit codes”](../report.md) (in Russian) |
| `scaffold` | `extension`, `draft` | [test stubs](checks.md#test-stubs): the test extension and the draft command |

## Platform names dictionary

The static checks know the platform methods, types and system enumerations from the syntax assistant help. Names that
are not in the help but are accepted by the Designer, and the standard names of metadata objects, are in the built-in
dictionary [`platform-names.toml`](../../src/otk_1c/availability/platform-names.toml):

| Key | What | Check |
| --- | --- | --- |
| `methods` | a method without a dot (deprecated methods that the platform supports) | `platform-api` |
| `constructors` | a type in `Новый Тип` | `platform-api` |
| `globals` | the name before the dot (`Имя.Значение`): system enumerations and values | `platform-api`, “Variable is not defined” |
| `forms` | a standard form of an object (`ФормаСписка`) | `form-name` |
| `managerMethods` | the beginning of the name of a standard manager method (`Создать`, `ПустаяСсылка`) | `manager-method` |

A false finding on a name that the platform accepts (for example, after a platform update) — add the name to the
`[platformNames]` section of your `otk.toml` or `otk.local.toml` without waiting for an OTK release. The lists are
merged with the built-in ones, the case does not matter, the Russian and English names go as separate entries:

```toml
[platformNames]
globals = ["НовоеСистемноеПеречисление", "NewSystemEnum"]
```

An unknown key or a value that is not a list — a note in the summary, the section is not applied, the run continues.

## Analyzer finding level

A new remark of a BSL analyzer (the `analyzers` check) is an error if its rule is in the built-in list
(`ParseError`, `ProcedureReturnsValue`, `UnknownPreprocessorSymbol`, `ThisObjectAssign` — for both analyzers), the
rest are warnings ([“Findings”](../findings.md) (in Russian)). A new version of an analyzer may rename a rule or add
its own — correct the list with the `[analyzerSeverity]` section without waiting for an OTK release. For each analyzer
(`bslls` — BSL LS, `bsl-analyzer`), `error` adds rules to the errors, `warning` lowers them to warnings; the rule name
is as in the finding after the colon, case-sensitive:

```toml
[analyzerSeverity]
bslls = { error = ["UsingModalWindows"] }
bsl-analyzer = { error = ["UnresolvedMethodCall"], warning = ["ThisObjectAssign"] }
```

The set of rules that an analyzer checks at all is defined by its configuration (`.bsl-language-server.json`,
`bsl-analyzer.toml`), not by this section. An unknown analyzer or key, a value that is not a list, the same rule in
both `error` and `warning` — a note in the summary, the section is not applied, the run continues.

## Extensions

For an extension project, set `source` (the extension XML dump) and `parent` (the XML dump of the main
configuration); the extension and its name are recognized from Configuration.xml of the dump (`extension` — only to
override the name). Without `parent`, references to the main configuration cannot be verified — such static findings
are lowered to warnings. Extension modules are probed in an infobase copy with the extension;
the extended check is `/CheckModules … -ExtendedModulesCheck -Extension <имя>` of the extension itself
(seconds on a small extension). For small extensions `gate --all` is convenient.
