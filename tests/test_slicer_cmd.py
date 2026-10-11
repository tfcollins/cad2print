from pathlib import Path

from cad2print.presets import Overrides, Preset
from cad2print.slicer import SliceResult, build_command, run

PRESET = Preset(
    key="p", label="P",
    machine=Path("/p/machine/H2S 0.4.json"),
    process=Path("/p/process/0.20 Standard.json"),
    filament=Path("/p/filament/PLA.json"),
)


def test_build_command_golden():
    cmd = build_command(
        orca_bin="/opt/orcaslicer/bin/orca-slicer",
        stl_paths=[Path("/w/a.stl"), Path("/w/b.stl")],
        preset=PRESET,
        overrides=Overrides(infill=20, supports=True, copies=1),
        out_dir=Path("/w/out"),
        out_name="job1.gcode.3mf",
    )
    assert cmd == [
        "/opt/orcaslicer/bin/orca-slicer",
        "/w/a.stl", "/w/b.stl",
        "--load-settings", "/p/process/0.20 Standard.json;/p/machine/H2S 0.4.json",
        "--load-filaments", "/p/filament/PLA.json",
        "--sparse-infill-density=20%",
        "--enable-support=1",
        "--arrange", "1", "--orient", "1", "--slice", "0",
        "--export-3mf", "job1.gcode.3mf",
        "--outputdir", "/w/out",
        "--debug", "2",
    ]


def test_copies_repeat_every_input_file():
    cmd = build_command("orca", [Path("/w/a.stl"), Path("/w/b.stl")], PRESET,
                        Overrides(infill=15, supports=False, copies=3), Path("/o"), "x.gcode.3mf")
    assert cmd[1:7] == ["/w/a.stl", "/w/a.stl", "/w/a.stl", "/w/b.stl", "/w/b.stl", "/w/b.stl"]
    assert "--enable-support=0" in cmd


def test_run_reports_success_and_output_path(tmp_path):
    script = tmp_path / "fake-orca.sh"
    script.write_text('#!/bin/sh\necho "slicing..."; touch "$(dirname "$0")/out.gcode.3mf"; exit 0\n')
    script.chmod(0o755)
    res = run([str(script)], expected_output=tmp_path / "out.gcode.3mf", timeout_s=10)
    assert isinstance(res, SliceResult)
    assert res.ok and res.out_path == tmp_path / "out.gcode.3mf"
    assert "slicing..." in res.log_tail and res.duration_s >= 0


def test_run_failure_keeps_stderr_tail_and_is_not_ok(tmp_path):
    script = tmp_path / "fake-orca.sh"
    script.write_text('#!/bin/sh\necho "boom: bad profile" >&2; exit 3\n')
    script.chmod(0o755)
    res = run([str(script)], expected_output=tmp_path / "missing.gcode.3mf", timeout_s=10)
    assert not res.ok and res.returncode == 3 and "bad profile" in res.log_tail


def test_run_zero_exit_without_output_file_is_a_failure(tmp_path):
    script = tmp_path / "fake-orca.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    res = run([str(script)], expected_output=tmp_path / "never.gcode.3mf", timeout_s=10)
    assert not res.ok and "never.gcode.3mf" in res.error


def test_run_timeout_is_reported(tmp_path):
    script = tmp_path / "slow.sh"
    script.write_text("#!/bin/sh\nsleep 5\n")
    script.chmod(0o755)
    res = run([str(script)], expected_output=tmp_path / "x", timeout_s=0.2)
    assert not res.ok and "timed out" in res.error


def test_xvfb_wrapper_is_prepended_when_requested():
    cmd = build_command("orca", [Path("/a.stl")], PRESET, Overrides(15, False, 1), Path("/o"),
                        "x.gcode.3mf", use_xvfb=True)
    assert cmd[:3] == ["xvfb-run", "-a", "orca"]


def test_bed_type_from_preset_is_passed_to_the_slicer():
    from cad2print.presets import Preset
    preset = Preset("p", "P", PRESET.machine, PRESET.process, PRESET.filament, bed_type="Textured PEI Plate")
    cmd = build_command("orca", [Path("/a.stl")], preset, Overrides(15, False, 1), Path("/o"), "x.gcode.3mf")
    assert "--curr-bed-type=Textured PEI Plate" in cmd


def test_no_bed_type_means_no_flag():
    cmd = build_command("orca", [Path("/a.stl")], PRESET, Overrides(15, False, 1), Path("/o"), "x.gcode.3mf")
    assert not any(a.startswith("--curr-bed-type") for a in cmd)
