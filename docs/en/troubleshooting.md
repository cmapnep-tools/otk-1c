*Translated from the [Russian original](../troubleshooting.md); if they differ, the Russian page is authoritative.*

# Troubleshooting

- **“infobase … is busy”** — the infobase copy is held by another 1C process (Designer, a session): close it. OTK only
  removes dangling locks and does not terminate other people's processes.
- **“another OTK run is checking the infobase copy” / “the infobase copy is busy with another OTK run”** — two
  runs on the same infobase (CI and a local one, two runners) go in turn: the second waits up to
  `environment.lockTimeout` seconds (900), then exit code 2 (`ib-busy`) with the holder's PID and command. The lock
  is the file `<infobase directory>.otk-lock` next to a file infobase; for a server one (`/S…`) — in the user's
  temporary directory, i.e. only within one account on the machine. It is held until the end of its process, and
  the OS releases it if the process crashes.
- **“1C platform not found”** — set `environment.platform` or `OTK_PLATFORM` (the `bin` directory).
- **“COM unavailable” / “connection failed”** — COM has priority (it is faster), but the check does not depend on it:
  if the connector of the version of the infobase platform is not registered (`V83`/`V85.COMConnector`) or does not
  connect for a reason other than the code (a different version, a license), the server probes and query parsing go
  through the thin client ([YAxUnit](https://github.com/bia-technologies/yaxunit)), with a note in the summary. A
  compilation error on connection (the session module, global modules) is a finding. To get COM back — register the
  connector (`regsvr32 comcntr.dll` from the `bin` directory of the platform, as administrator); to always go without
  COM — `executors.server = "yaxunit"`. The Platform Tools hook does not install YAxUnit into a production infobase:
  without COM its probes are skipped with a note. YAxUnit compiles in the “Server” context, without an external
  connection: the external connection module and the code under `#Если ВнешнееСоединение` bypassing COM are “not
  verified”. To not check the external connection locally at all — remove `ExternalConnection` from
  `environment.contexts`: COM is not started, and such code is “not covered by this run”.
- **YAxUnit name conflict with the configuration** — OTK itself reinstalls YAxUnit, renaming the conflicting module (a
  message in the run log). If it did not help in 4 attempts — the YAxUnit probes are “not verified” with the conflict
  names (with `on` — an infrastructure failure, code 2); report the names in an issue.
- **Designer or ibcmd.** Partial load, database update and extensions are done by OTK with the tool from
  `environment.ibTool`: by default with the batch Designer — on Windows it is faster (`ibcmd` spends about 2.5 s on
  startup, on ERP it is on par or up to 30% slower). `ibcmd` does not need a graphical session; an infobase on a
  server (`/S…`) always uses the Designer. A full load of a dump uses `ibcmd` if it is present: on an industry
  configuration (12.9 thousand files) 56 s versus 182 s for the Designer. `ibcmd` aborted with “requests a user name”
  — the infobase has users: set `environment.user` and `environment.password`.
- **No ibcmd.** `ibcmd` is installed with the 1C:Enterprise server components; a client installation of the platform
  does not have it, and `otk doctor` shows this. OTK works without it too: the full load goes through the Designer
  (three times slower, a note in the summary), and the safe mode with YAxUnit and the probe extensions are removed
  through an external connection (COM). Without `ibcmd` and COM (Linux) there is nothing to remove the safe mode with
  — the YAxUnit probes are unavailable: install the server components. `ibTool = "ibcmd"` without `ibcmd` is a failure
  before the check starts: install the server components or choose `designer`.
- **The 1C client window is not visible** — this is by design: on Windows the client with YAxUnit (probes, scenarios)
  is started on a separate hidden desktop and does not pop up over your work or take focus (a CI runner in a user
  session, the pre-push hook). To see the window when debugging — `environment.hiddenDesktop = false` or
  `OTK_HIDDEN_DESKTOP=0`. A hung client (a modal error window) is recognized by the event log and the timeout and is
  terminated. On Linux the Designer and the clients are opened on OTK's own Xvfb.
- **Linux: “no Xvfb and DISPLAY”** — the 1C windows have nowhere to open: `sudo apt install xvfb`
  ([linux.md](../linux.md#дисплей-xvfb) (in Russian)). The Designer does not start at all (`ldd … not found`, no
  license activation item) — [platform installation](../linux.md#установка-платформы-ubuntu-2404) (in Russian).
