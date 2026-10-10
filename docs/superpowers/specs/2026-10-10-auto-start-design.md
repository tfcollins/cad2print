# cad2print auto-start — design (approved in chat 2026-10-10)

## Goal
Queue sliced plates and start them on the H2S without the manual Print click, while the
printer stays bound to the Bambu cloud account (no Developer Mode). Approach chosen by
Travis after the alternatives were laid out: keep the official Bambu Connect path and
automate the click on `bambu-win`; gate each start on the printer being idle **and** an
explicit "bed cleared" confirmation.

Rejected: Developer Mode + bambuddy queue (loses Handy/cloud); beambam signed MQTT (needs a
signing key extracted from the Bambu Handy Android app; unproven on H2S).

## Components

### cad2print (container on LXC 117)
- `printer.py` — read-only MQTT subscriber (`bblp` + access code, TLS insecure) to
  `device/<serial>/report`; `pushall` on connect. `PrinterState` (online, gcode_state,
  percent, remaining, task) merged from partial pushes; stale after 30 s = offline.
  `may_dispatch(state, bed_cleared)` is the single gate: live, state in
  {IDLE, FINISH, FAILED}, bed cleared.
- Jobs: new `auto_start: bool` on the spec (form checkbox, default on). Pipeline for
  auto-start jobs: exporting → slicing → **waiting_for_printer** → handing_off →
  **starting** → done. Manual jobs keep today's path.
- Dispatcher (in the worker thread, polling every 2 s): oldest waiting job; if
  `may_dispatch` → consume the bed-cleared flag, publish the plate into
  `HANDOFF_DIR/auto/`, stage `starting`. Then wait up to `START_TIMEOUT_S` (default 240)
  for either a `<plate>.started` marker from bambu-win **and** the printer reporting a busy
  state → done; or a `<plate>.failed` marker / timeout → failed with the marker's message
  ("print did not start — check Bambu Connect on bambu-win"). Plate stays in the share.
- Bed-cleared flag: one boolean in the settings table. Set by `POST /printer/bed-cleared`;
  cleared when a job is dispatched or when the printer transitions into a busy state.
- UI: printer strip (state, progress, task, bed-cleared status + button) on index and job
  pages; waiting jobs show their queue position and the gate reason.
- `/healthz` unchanged (monitor being down is reported in the strip, not as unhealthy —
  manual jobs still work).

### bambu-win (Windows VM)
- Bambu Connect Startup shortcut gains `--force-renderer-accessibility
  --enable-features=UiaProvider` (spike-verified: exposes the UI Automation tree).
- `Auto-Print.ps1` (logon scheduled task, interactive session): watches `P:\auto\*.3mf`.
  For each new plate: open via `bambu-connect://import-file?...`, Invoke `confirm` in the
  "Import file" modal, wait for the print page, select the printer if needed, Invoke the
  print button, verify the printer card/page reports printing; write `<plate>.started` or
  `<plate>.failed` (with reason) next to the file. Log to `%USERPROFILE%\auto-print.log`.
  `-DryRun` switch logs what it would invoke without invoking anything after `confirm`.
- `Watch-Prints.ps1` skips the `auto\` folder.
- Precondition: the session must be unlocked (console-attached); disconnecting RDP locks
  it — the runbook documents `tscon <id> /dest:console`.

### Infra
- Vault: `vault_bambu_h2s_serial`, `vault_bambu_h2s_access_code` (added).
- `env.j2`: `BAMBU_HOST=10.0.0.130`, `BAMBU_SERIAL`, `BAMBU_ACCESS_CODE`.
- `stacks/apps/cad2print/bambu-win/` holds the PowerShell scripts + an install script
  (scp + ssh) and the runbook section in `docs/cad2print.md`.

## Non-goals
Multiple printers; AMS slot mapping (Bambu Connect's defaults are used); notifications
when a print finishes (later: HA/Telegram "bed clear?" prompt).
