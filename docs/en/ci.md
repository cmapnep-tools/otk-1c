*Translated from the [Russian original](../ci.md); if they differ, the Russian page is authoritative.*

# CI

In any CI system OTK is a single step: `otk ci gate` for a merge request and `otk ci designer-check` — the check of all
modules by the Designer, by a label, at night or manually. The exit codes set the job status: 0 — green, 1 — errors
found, 2 — environment failure (no platform, the infobase is busy, the negative control did not work). No wrapper
around the step is needed: OTK takes the base revision from the CI variables and puts the artifacts (summary, JUnit,
`problems.txt`, SARIF) into the `--artifacts` directory, which the CI system picks up by itself.

## Templates

| System | Template | Where to put it | Artifacts |
| --- | --- | --- | --- |
| Forgejo, Gitea Actions | [forgejo/otk-gate.yml](../forgejo/otk-gate.yml), [forgejo/otk-designer-check.yml](../forgejo/otk-designer-check.yml) | `.forgejo/workflows/` (Gitea — `.gitea/workflows/`) | uploaded by OTK itself using the `upload-artifact` v3 protocol (verified on Forgejo): the runner does not need node |
| GitHub Actions | [github/otk-gate.yml](../github/otk-gate.yml), [github/otk-designer-check.yml](../github/otk-designer-check.yml) | `.github/workflows/` | `actions/upload-artifact@v4` |
| GitHub Actions, own Linux runner | [github/otk-gate-linux.yml](../github/otk-gate-linux.yml) | `.github/workflows/` | `actions/upload-artifact@v4` |
| GitLab CI | [gitlab/otk.gitlab-ci.yml](../gitlab/otk.gitlab-ci.yml) | `.gitlab-ci.yml` or `include` | `artifacts`, the `junit` report — in the merge request |
| Jenkins | [jenkins/Jenkinsfile](../jenkins/Jenkinsfile) | `Jenkinsfile` (Multibranch Pipeline) | `archiveArtifacts`, `junit` |

The runner (agent) is your own, not a cloud one: it has the 1C platform, Python 3.11+ and OTK installed
(`python -m pip install otk-1c`, on Linux — `pipx install otk-1c` and the `otk` command in `PATH`), and holds your
own file infobase copy and the run directory. They live between runs: OTK loads only the changed part into the copy.
The first run is a full load of the dump. On a Linux runner
the bench is the same, but there is no external connection: server probes and query parsing go through YAxUnit, and
OTK starts the X server for the Designer and the client by itself. What to install, where the bench lives and which
environment profile the job sets — [linux.md](../linux.md#раннер-ci) (in Russian). A runner started as a Windows
service requires an additional 1C license ([licensing.md](../licensing.md) (in Russian)).

The job passes machine paths in `OTK_*` variables (`IB`, `USER`, `PASSWORD`, `PLATFORM`, `YAXUNIT`,
`BSLLS`, `RUN_DIR`; the full list — [“Settings”](configuration.md)); like everywhere, they take precedence over
`otk.toml` and `otk.local.toml`, and OTK does not write them into the files of the project directory. The password is
a secret of the CI system. In the templates the test bench of the runner is set by a single variable `OTK_HOME`
(infobase copies, runs, YAxUnit, BSL LS), and the other paths are built from it; for a different layout, set the paths
separately.

The `gate` base is the target branch of the request: `GITHUB_BASE_REF` (GitHub, Forgejo, Gitea),
`CI_MERGE_REQUEST_TARGET_BRANCH_NAME` (GitLab), `CHANGE_TARGET` (Jenkins). GitLab clones only the request branch with
an incomplete history, Jenkins often without the target branch: OTK fetches what is missing by itself. Not a merge
request (push, schedule) — the base is set explicitly: `--base origin/<ветка>`, otherwise code 2.

## Check of all modules by the Designer

- `otk designer-check` — the check of all modules by the Designer by the PR label `otk-designer-check`, at night and
  manually. It is preceded by a **negative control**: a known error must be found, otherwise the job fails with “the
  Designer check does not work”. The main variant is a small infobase of its own for the control in the user cache (one
  per platform version, created by itself in ~10 s): an error in a configuration module and in an extension module,
  `/CheckModules -Server` without and with `-Extension` — ~2 s. If the control infobase is not created (a license) or
  did not work, the error is loaded into a server common module of the copy and must be found by the check of all
  modules itself (a copy under support without the right to change is taken off support in this case — the copy is
  its own). The modes follow the configuration properties (the ordinary application — if it is the main launch mode or
  `ordinaryApplication = true`); explicit `modes` are validated: the Designer silently skips an unknown key, and
  without a mode it answers “no errors” having checked nothing — so this is a configuration error. Standard
  configurations usually do not pass the check of all modules without errors, so the comparison is made against the
  **baseline** — `otk-designer-check.baseline.json` in the repository (`otk designer-check --update-baseline`): only
  new errors turn it red.
- `otk ci gate --designer` — the same in the gate job: after the probes, the control and the check of all modules in
  the same infobase copy, the same baseline. New errors are findings of the `designer` step (in the same line as an
  error of another check, its source: “found: static check (…), full Designer check (server)”), one summary, one
  JUnit/Problems and one exit code; if the control did not work or the Designer output was not parsed — code 2. Only
  your own infobase copy (`environment.ib`), not a production infobase; minutes on an industry configuration, longer
  on ERP — a mode for CI, not for the pre-push hook.

## What OTK replaces in your CI

OTK does not add one more step to the pipeline but takes over some of the existing ones. “Replaces” here means only
where this is confirmed by control defects ([vs-designer.md](../vs-designer.md) (in Russian),
[vs-analyzers.md](../vs-analyzers.md) (in Russian)) and by the pilot measurements ([pilot.md](../pilot.md) (in
Russian)); in the other rows OTK is embedded or does not take part.

| Step of a typical pipeline | What is usually used | With OTK |
| --- | --- | --- |
| Syntax check for every request | `/CheckModules` over the whole configuration, vanessa-runner (`vrunner syntax-check`, in newer versions `vrunner validate syntax-check`) | **replaces** it on every request: `gate` checks the changed part — all control defects caught by `/CheckModules`, and in addition queries, form opening, methods newer than the compatibility mode. The check of all modules remains a safety net by a label or at night (`designer-check`) |
| Loading sources into the infobase for checking | vanessa-runner, `ibcmd`, `/LoadConfigFromFiles` | **replaces**: `gate` itself loads the changed part into its infobase copy (partial load). OTK does not build the delivery (`.cf`, `.cfe` for a release) — this step remains |
| Static analysis | BSL Language Server, SonarQube | **embedded**: BSL LS or bsl-analyzer work inside `gate`, and only new remarks for the change get into the summary. The SonarQube dashboard over all the code remains |
| Opening forms | smoke tests of Vanessa-ADD, Vanessa Automation | **embedded**: the probe opens the changed forms in the infobase copy (an exception and a hang on opening are caught). It does not replace scenario tests |
| Unit tests | YAxUnit | **embedded**: YAxUnit tests run in the same infobase copy, the result is in the same summary |
| Check of an EDT project | `1cedtcli` | does not replace: OTK reads an EDT project but does not repeat the check of EDT itself ([edt.md](../edt.md) (in Russian)) |

### Before and after

An extension project on two industry configurations, two weeks in CI next to `/CheckModules`
([pilot.md](../pilot.md) (in Russian), 105 checks, not a single case of “OTK green — Designer red”):

| | Before: check of all modules on every request | After: `otk ci gate` on every request |
| --- | --- | --- |
| Time per request in an industry configuration | 12 min (median), up to 21 min | 1 min 53 s: static checks and probes of the changed part |
| What is caught | compilation errors | compilation errors and what it does not see: typos in query fields, exceptions and hangs on form opening, an extra parameter in a call, methods newer than the compatibility mode |
| Check of all modules | on every request | by a label, at night or on merge — `designer-check` |

On a small project the Designer is faster (an extension — 5 s versus 53 s for OTK): there OTK pays off not with time but
with checks beyond compilation. To repeat it on your machine — the demo project
[otk-1c-demo](https://github.com/cmapnep-tools/otk-1c-demo): `otk controls --parity` introduces every control defect
and shows who caught it ([vs-designer.md](../vs-designer.md) (in Russian)).
