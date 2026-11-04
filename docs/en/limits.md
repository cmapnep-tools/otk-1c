*Translated from the [Russian original](../limits.md); if they differ, the Russian page is authoritative.*

# Known limits

- **Command modules, HTTP services and web services** — no probe (there is no way to compile without execution or
  publication): static checks only, the rest is the full check in CI.
- **The ordinary application** is not probed locally unless `ordinaryApplication` is enabled; modules with the
  “Client (ordinary application)” flag go into “not covered by this run”.
- **The web client** is not checked by probes. The static check using the Designer availability table finds platform
  methods, properties and types (`Новый ЧтениеТекста`, `Новый HTTPСоединение` on the client) that are unavailable in
  the web client: a warning, with `environment.webClient = true` — an error. The availability of types in the web
  client is not checked by the Designer `-WebClient` mode (it skips `Новый Запрос` too), so it was measured in a real
  web client (`src/otk_1c/availability/webclient/`; 8.3.27, 8.5.1, 8.5.4): the thin client according to the Designer
  and `Новый(Имя)` in the web client without “Type is not defined”. The help is wrong here (`ПараметрВыбора`,
  `HTTPСоединение` are listed in it without the web client). For a line without a measurement — by the help, and a
  finding on a type is always a warning. Code only for the web client (`#Если ВебКлиент`) is not checked by the
  platform static checks (it contains methods that are not in the help, such as `ПаузаАсинх`). The rest is caught by
  the full Designer check in the `-WebClient` mode.
- **Dynamically assembled queries.** The variable part and the template placeholders (`&Имя`/`#Имя`/`%Имя%`/`%1`/
  `[Имя]`/`{0}`/`<Имя>`, replaced with `СтрЗаменить`, `СтрШаблон`, `ПодставитьПараметрыВСтроку`) are substituted: a
  value from the code of the method (a literal or a variable with a single literal assignment), otherwise something
  neutral for the place (a condition — `ИСТИНА`, a value — `NULL`, a field — `NULL КАК …`). A parsing error of such a
  text is a **warning**. A table or field name from code (`ИЗ " + Имя`, `Справочник." + Имя`), a text that is appended
  to further on (`Текст = Текст + …`), and fragments are not parsed — “not verified” with the reason. A temporary
  table of another query (a temporary table manager) is also “not verified”. In real configurations two thirds of such
  queries take a fragment from another module or from the caller — they can be given to an **external restorer**
  (`[[queries.resolvers]]`, for example an AI assistant with search over the configuration): the command receives JSON
  on stdin
  `{"version": 1, "repo", "source", "queries": [{"id", "module", "owner", "method", "line", "text", "reason"}],
  "modules": {путь: текст}}` (`<?>` — an unknown part) and replies with `{"queries": [{"id", "text", "rationale"}]}`.
  The chain goes in order, and what the previous one did not restore goes to the next. The restored text is parsed by
  the platform, and its error is a warning “text restored by “<name>” (<rationale>)”. A failure or timeout is a note in
  the summary, and the queries remain “not verified”; the answers are cached in the run directory (`cache/resolvers`),
  and with `--static-only` the command is not called. Without the section no code is passed anywhere. The command
  runs without a shell (pipes and substitutions do not work), but it is still executable code from the settings:
  review a change to `[[queries.resolvers]]` in `otk.toml` in a pull request as code, before CI runs.
- **An error in a query that the change did not touch** (and the metadata did not change) is a warning: standard
  configurations contain a lot of universal SSL code that accesses objects that do not exist in this configuration. On
  two industry configurations there are about 2% of such queries (149 of 6,900 parsed).
- **The session module, global modules, the application module** are compiled when the client starts: an error in them
  prevents the session from starting — OTK recognizes this and names the guilty module.
- The extended check by the Designer is the reference: OTK is verified against it by the catalog of control defects
  (`otk controls --parity`) and in the duplication mode in CI, and does not replace it in CI. The check modes are all
  of them, including the web client: the catalog defect `webclient-only` (a method unavailable only in the web client,
  in a client common module) is caught by the extended check of the changed modules — both in an extension project and
  for the main configuration through a temporary extension (verified on an industry configuration). The modules of
  managed forms and objects (a Designer XML dump and EDT sources) are checked by the same temporary extension: the
  control defects `form-missing-proc`, `form-webclient-only`, `object-missing-proc`. An error of the “first stage”
  (type not defined, number of parameters, “already defined”) hides unknown names in its mode — the module is marked
  “checked partially”. Not checked: `Объект.НетРеквизита` of a form, `Элементы.…`, `Новый Запрос` on the client in
  the web client mode (the compiler skips it). Each module gets a canary procedure at the end: if its error is not in
  the output, the module is “not compiled” (not “clean”). For new modules (without “before”) the “Possibly erroneous…”
  messages are not shown — only their number in the notes.
- **The form smoke test** opens a form without parameters and with the data of the infobase copy; errors when reading
  the data of a dynamic list (the list query) were not checked separately. Ordinary (non-managed) forms are not opened.
- The 1C:Enterprise language (BSL) has Russian and English variants (`ScriptVariant`); the code of an English
  configuration may mix both languages — the static checks and parsing understand both. YAxUnit borrows the language
  “Russian”; in a configuration with another main language (English SSL — English only), OTK switches the borrowing
  to its language on installation.
- **The platform interface language is Russian only.** The module names and the signs of a compilation error in the
  platform messages and the Designer output are recognized by the `platform` section of the dictionary
  `src/otk_1c/lang/ru.json`. If the platform is started with an English interface, probe errors will get the outcome
  `unknown`, and the `designer-check` findings will not be matched to files. For an English interface the same section
  is needed in `src/otk_1c/lang/en.json` (there is none yet).
- **The infobase password is visible on the platform command line.** There is no other way to pass it to
  batch Designer (`/P`) and `ibcmd` (`--password`): while the command runs, any process on the machine that can
  list processes can see it. OTK does not write the command line to its logs and summaries. Create a separate
  user with minimal rights in the infobase copy for checks, and do not run password-protected runs on a shared
  CI agent.
