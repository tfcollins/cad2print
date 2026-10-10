# cad2print auto-start — implementation plan

Spec: docs/superpowers/specs/2026-10-10-auto-start-design.md. TDD per task; commit per task.

- **A0 Spike (done):** read-only MQTT works without Developer Mode (fixture captured);
  Bambu Connect UIA tree visible with `--force-renderer-accessibility --enable-features=UiaProvider`;
  import modal = Window 'Import file' with Buttons 'cancel'/'confirm'. Remaining: confirm the
  print page's control names once the VM session is console-attached.
- **A1 printer.py (done):** PrinterState, apply_push, may_dispatch, PrinterMonitor.
- **A2 DB + spec:** `auto_start` on JobSpec/jobs table (migration: ALTER TABLE ADD COLUMN
  default 1); settings table with `bed_cleared`; stages `waiting_for_printer`, `starting`.
- **A3 Dispatcher:** JobRunner gets `printer` (monitor or fake) + `auto_dir`; after slicing,
  auto jobs park in `waiting_for_printer`; worker loop ticks every 2 s calling
  `dispatch_next()`; `starting` watches markers + printer state with timeout. Tests with a
  FakePrinter whose state the test sets; marker files written by the test.
- **A4 Routes/UI:** form checkbox, `POST /printer/bed-cleared`, printer strip partial
  (htmx poll every 5 s), gate reason + queue position on job fragment.
- **A5 Config/app wiring:** BAMBU_* settings; monitor started in lifespan when configured;
  `auto/` dir created under the handoff dir at startup.
- **A6 bambu-win scripts:** `Auto-Print.ps1`, shortcut flags, Watch-Prints exclusion,
  `install.ps1`; dry-run first, then one real start with the bed confirmed clear.
- **A7 Infra + deploy:** env.j2, docs, playbook unchanged; tag v0.2.0; deploy; e2e.
