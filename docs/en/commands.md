*Translated from the [Russian original](../commands.md); if they differ, the Russian page is authoritative.*

# Commands and running without settings

## Commands

```
otk gate --base <ref> [--head <ref> | --worktree] [--static-only] [--fast] [--problems] [--quiet]
otk gate --all                     # all modules of the dump (small projects, comparison with the Designer)
otk gate --dir <выгрузка> [--base-dir <было>]   # without git: the whole directory or the differences from "before" (see [“Without git”](#without-git))
otk gate --vendor --ib <ИБ>        # one-off: customizations of the infobase relative to its vendor configuration (minutes)
otk gate … --all-scenarios         # all scenario tests, not only those for the changed methods
otk collect --base <ref>           # plan only: what has changed and how to check it (otk.json)
otk scaffold                       # test stubs for changed methods that have no test
otk static | build | run           # the steps of gate separately
otk report                         # summary from the step files of the run directory (after static/run)
otk doctor                         # environment: which checks are available and what is lost
otk gate … --json | otk doctor --json   # for agents: the outcome as a single JSON object, reasons with reasonCode (see agents.md)
otk availability build [--out <каталог>]   # table of platform method availability for your platform
otk designer-check [--update-baseline]  # all modules by the Designer with a negative control
otk gate --base <ref> --designer   # gate and all modules by the Designer in the same infobase copy: one summary, one code
otk controls [--parity] [--doc ФАЙЛ]   # control defects: each one must be caught; --doc — the parity table
otk controls --analyzers [--doc ФАЙЛ]  # the same defects by static checks with both BSL analyzers: who catches what
otk hook install | uninstall       # git pre-push hook
otk ci gate [--designer] | designer-check   # run in CI (artifacts)
otk console query|check|metadata|eventlog|bases --ib <база> …   # infobase copy console (read-only)
otk mcp [--idle 120]               # the same console as an MCP server (stdio) for an AI agent
otk tj calls|queries|sdbl|errors|locks|timeouts|events <каталог ТЖ>   # technological log analysis
```

`--worktree` checks uncommitted changes; `--static-only` — static checks only, without an infobase copy (seconds);
`--fast` — do not run probes if the static checks have already found errors. When there are more than 50 findings,
`gate` and `report` print a brief summary (see [“Brief summary in the console”](../report.md#краткая-сводка-в-консоли)
(in Russian)); `--full` — the whole one.

## Command line only

OTK works from the downloaded directory (`otk.cmd` / `otk.sh` in the root, Python 3.11+ is required) without
`otk.toml`: everything that is set in the settings can also be set by the arguments of `gate`, `static`, `build`,
`run`, `doctor`. Paths are relative to the current directory.

| Key | What |
| --- | --- |
| `--parent <выгрузка>` | XML dump of the main configuration (for an extension) — for all steps |
| `--extension <имя>` | extension name; by default — from Configuration.xml of the dump (an extension is recognized automatically) |
| `--ib <каталог>` | your own file infobase copy (OTK changes it); OTK does **not** create the copy itself |
| `--connection "/F…"` | or a production infobase: after the check it is returned to its previous state |
| `--user` | infobase user; the password is given by the `OTK_PASSWORD` variable or in `otk.local.toml` (there is no argument: it would be visible in the process list) |
| `--platform <bin>` | 1C platform (by default — the newest installed one) |
| `--yaxunit <.cfe>` | [YAxUnit](https://github.com/bia-technologies/yaxunit); OTK does **not** download it |
| `--bslls <exe>` | BSL LS (by default `OTK_BSLLS`, `PATH` or a copy of the VS Code extension “Language 1C (BSL)”) |
| `--bsl-analyzer <exe>`, `--analyzer <имя>` | bsl-analyzer (by default `OTK_BSL_ANALYZER` or `PATH`) and the choice of analyzer: `bslls`, `bsl-analyzer`, `both` |
| `--run-dir <каталог>` | run directory |
| `--set раздел.ключ=значение` | any setting, for example `checks.analyzers=off`, `smoke.timeout=120`; may be repeated |

The defaults are the maximum of checks without an infobase copy: without `--ib` / `--connection` — static checks only
(probes are “not verified” with a hint of the key), without `--yaxunit` — no client probes, scenarios or forms. What
will be checked with these arguments is shown by `otk doctor` with the same keys (and `--dir`).

An extension without `--parent` is checked, but references to objects and methods of the main configuration cannot be
verified: such static findings are warnings, and the summary reminds of this.

```
# configuration without git: static checks only
otk.cmd gate --dir D:/Выгрузки/ERP

# extension and main configuration, differences from "before" with probes in your own infobase copy (the contract is in "Without git" below)
otk.cmd gate --dir расширение --base-dir расширение_было --parent D:/Выгрузки/Основная ^
    --ib D:/ib/копия --user Администратор --yaxunit D:/tools/YAxUnit.cfe

# what is available on this machine
otk.cmd doctor --dir расширение --parent D:/Выгрузки/Основная --ib D:/ib/копия
```

In a CI script — the same keys, the password in the `OTK_PASSWORD` variable, exit code 0/1/2 (see [“Summary and exit
codes”](../report.md) (in Russian)):

```
set OTK_PASSWORD=%IB_PASSWORD%
call otk.cmd gate --dir "%CHECKOUT%\src\cf" --ib "%IB%" --user ci --quiet --problems
if errorlevel 1 exit /b %errorlevel%
```

## Without git

Git is one of the sources of changes, not a mandatory dependency. `--dir` is the dump directory (Configuration.xml in
the root), `--base-dir` is the “before” dump. The directories are compared in place on every run (file list, size,
content; module lines — by comparing texts); OTK keeps no history of its own. Renames without git are not recognized
(deleted + added). The settings are `otk.toml` in the `--repo` directory (the current one by default). The metadata
index of `--dir` is cached by the directory fingerprint (size and time of all files: ERP — ~14 s versus ~80 s to
build), the index of `--parent` — by `Configuration.xml` and `ConfigDumpInfo.xml` (every dump rewrites them). The
result is in the run directory (`--run-dir`, by default `%TEMP%/otk/<имя>-<хеш пути>`, the path to the summary is at
the end of the output): the summary, the plan and the step files contain fragments of the checked code — see
[“Summary and exit codes”](../report.md) (in Russian).

**Infobase copy contract.** With `--base-dir`, OTK assumes that the infobase copy **already contains “before”** and
loads into it only the differences of `--dir`. This is not verified: if the copy contains something else, the probes
will check the wrong code. If you are not sure, run without `--base-dir` (full load) or with `--static-only`. A chain
of checks keeps the contract by itself: after `gate --base-dir A --dir B` the copy contains B, and the next check is
`--base-dir B --dir C`.

**Which mode to choose without git.** During development (a hook, an answer in seconds) — `--dir` + `--base-dir`: put
the “before” dump aside before starting the task, and the differences from it are checked. A one-off check — the same
keys, if you have both dumps, or `--vendor`, if you have an infobase under support.

### Customizations relative to the vendor of the infobase (`--vendor`)

```
otk gate --vendor --ib <копия ИБ> | --connection <база> [--user …]
```

A one-off check of all customizations of an infobase relative to its vendor configuration — when there is neither git
nor a “before” dump. The configuration must be under support **with changes allowed**: only then is the vendor
configuration stored in the infobase separately. The procedure:

1. Dump of the infobase root (seconds): the support state is taken from `Ext/ParentConfigurations.bin`, which is dumped
   by the infobase itself (OTK does not trust a file in the repository). Under support without changes — there are no
   customizations, the check is over; removed from support — a refusal (code 2) with a hint: `--base-dir` with the
   vendor dump or git.
2. `/CompareCfg` vendor → main, the full report (a customized 1C:KA — 200 s).
3. Full dump of the main configuration of the infobase — “after” (ERP/KA — ~11 min; the OTK cache by infobase, next
   time — only the changed objects).
4. “Before” — the same dump in which the module texts from the report are returned to the vendor state and the new
   objects are removed; then — as with `--base-dir`. Nothing needs to be loaded into the infobase: the checked
   revision is already in it. Probes execute the database configuration, so if the main configuration has not been
   applied to it (`КонфигурацияИзменена()`, for example after an edit in the Designer), OTK updates the copy
   (`/UpdateDBCfg`, a note in the summary); in a production infobase (`--connection`) it changes nothing — the
   execution probes are “not verified” with a recommendation to update the database configuration.

Limitations: only module texts are restored (forms, templates and properties stay as in the infobase); a module whose
text contains “·” (this is how the report prints a tab) is checked as a whole; only the main configuration in the
Designer format. If the vendor configuration has not been updated together with the main one for a long time, the
updated vendor code will also turn out to be a “customization” — the check will be long, the number of objects in the
report is visible in the summary.
