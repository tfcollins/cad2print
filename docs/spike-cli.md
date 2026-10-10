# Spike: headless OrcaSlicer CLI with Bambu H2S system profiles (2026-10-10)

Run inside the `orcaslicer` container (`lscr.io/linuxserver/orcaslicer`, OrcaSlicer 2.4.2)
on LXC 117 (10.0.0.18). Input: 20 mm cube from `scripts/make_cube.py`.

## Working command (attempt A — passed first try)

```sh
P=/opt/orcaslicer/resources/profiles/BBL
env -u DISPLAY /opt/orcaslicer/bin/orca-slicer cube.stl \
  --load-settings "$P/process/0.20mm Standard @BBL H2S.json;$P/machine/Bambu Lab H2S 0.4 nozzle.json" \
  --load-filaments "$P/filament/Bambu PLA Basic @BBL H2S.json" \
  --arrange 1 --orient 1 --slice 0 --export-3mf cube.gcode.3mf --outputdir OUT --debug 2
```

Findings:

- Exit 0, < 1 s. Output dir contains `cube.gcode.3mf` (86 KB, has `Metadata/plate_1.gcode`),
  `plate_1.gcode`, `result.json` (`return_code`, `sliced_plates[]`, `layer_height`, ...), `00000.log`.
- **System profiles with `inherits` resolve fine** — no flattening needed. The gcode header
  shows `printer_model = Bambu Lab H2S`, `printer_settings_id = Bambu Lab H2S 0.4 nozzle`,
  `print_settings_id = 0.20mm Standard @BBL H2S`, `filament_settings_id = "Bambu PLA Basic @BBL H2S"`.
- **No display / xvfb needed** (`DISPLAY` unset). One harmless stderr line:
  `error: XDG_RUNTIME_DIR is invalid or not set in the environment.`
- Overrides work: `--sparse-infill-density=25% --enable-support=1` appear in the gcode header
  (`sparse_infill_density = 25%`, `enable_support = 1`). Filament swap to
  `Bambu PETG HF @BBL H2S.json` reflected in `filament_settings_id`.
- Copies: passing the same STL twice yields two objects on the plate (print time 11m → 24m).
- Verify a result programmatically with `result.json` → `return_code == 0` and the `.gcode.3mf`
  containing `Metadata/plate_1.gcode`.

## Packaging decision

`ldd /opt/orcaslicer/bin/orca-slicer` lists ~150 host libraries (GTK3, WebKitGTK, GL/EGL,
flite, dav1d, ...) besides the one bundled `lib/orca-runtime`; `/opt/orcaslicer` is 380 MB.
Rebuilding that on `python:3.12-slim` is the ">1 h library wrangling" case the plan allows
falling back from, so the cad2print image is built **FROM the linuxserver orcaslicer image**
(Debian trixie, Python 3 present) with the entrypoint overridden to uvicorn. The web desktop
(s6, Selkies) is never started in the cad2print container.
