"""Slow: runs the real orca-slicer on a generated cube with the real H2S profiles.

Skipped unless ORCA_BIN points at an existing binary (true inside the Docker image).
"""
import os
import zipfile
from pathlib import Path

import pytest

from cad2print import slicer
from cad2print.presets import Overrides, load_presets

ORCA = os.environ.get("ORCA_BIN", "/opt/orcaslicer/bin/orca-slicer")
PROFILES = Path(os.environ.get("PROFILES_FILE", Path(__file__).parents[2] / "profiles.yaml"))

pytestmark = pytest.mark.slow


@pytest.mark.skipif(not Path(ORCA).is_file(), reason="orca-slicer binary not available")
def test_real_slice_of_cube_produces_gcode_3mf(tmp_path):
    from scripts.make_cube import cube_stl

    stl = tmp_path / "cube.stl"
    stl.write_text(cube_stl(20))
    cfg = load_presets(PROFILES)
    preset = next(iter(cfg.presets.values()))
    cmd = slicer.build_command(ORCA, [stl], preset, Overrides(infill=20, supports=False, copies=2),
                               tmp_path, "cube.gcode.3mf")

    res = slicer.run(cmd, expected_output=tmp_path / "cube.gcode.3mf", timeout_s=300)

    assert res.ok, res.error + "\n" + res.log_tail
    with zipfile.ZipFile(res.out_path) as z:
        assert "Metadata/plate_1.gcode" in z.namelist()
        gcode = z.read("Metadata/plate_1.gcode").decode(errors="replace")
    assert "; printer_model = Bambu Lab H2S" in gcode
    assert "; sparse_infill_density = 20%" in gcode
