"""Build and run the OrcaSlicer CLI (command shape proven in docs/spike-cli.md)."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .presets import Overrides, Preset


@dataclass(frozen=True)
class SliceResult:
    ok: bool
    out_path: Path | None
    returncode: int | None
    duration_s: float
    log_tail: str
    error: str = ""


def build_command(orca_bin: str, stl_paths: list[Path], preset: Preset, overrides: Overrides,
                  out_dir: Path, out_name: str, use_xvfb: bool = False) -> list[str]:
    cmd: list[str] = [orca_bin]
    for p in stl_paths:
        cmd.extend([str(p)] * overrides.copies)
    cmd += [
        "--load-settings", f"{preset.process};{preset.machine}",
        "--load-filaments", str(preset.filament),
        f"--sparse-infill-density={overrides.infill}%",
        f"--enable-support={1 if overrides.supports else 0}",
        *([f"--curr-bed-type={preset.bed_type}"] if preset.bed_type else []),
        "--arrange", "1", "--orient", "1", "--slice", "0",
        "--export-3mf", out_name,
        "--outputdir", str(out_dir),
        "--debug", "2",
    ]
    if use_xvfb:
        cmd = ["xvfb-run", "-a", *cmd]
    return cmd


def run(cmd: list[str], expected_output: Path, timeout_s: float = 600, tail_lines: int = 40) -> SliceResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        tail = _tail((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), tail_lines)
        return SliceResult(False, None, None, time.monotonic() - start, tail,
                           f"orca-slicer timed out after {timeout_s:g}s")
    except OSError as e:
        return SliceResult(False, None, None, time.monotonic() - start, "", f"could not start slicer: {e}")
    duration = time.monotonic() - start
    tail = _tail(proc.stdout + ("\n" + proc.stderr if proc.stderr else ""), tail_lines)
    if proc.returncode != 0:
        return SliceResult(False, None, proc.returncode, duration, tail,
                           f"orca-slicer exited with code {proc.returncode}")
    if not expected_output.is_file():
        return SliceResult(False, None, proc.returncode, duration, tail,
                           f"orca-slicer exited 0 but did not write {expected_output.name}")
    return SliceResult(True, expected_output, proc.returncode, duration, tail)


def _tail(text: str, n: int) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])
