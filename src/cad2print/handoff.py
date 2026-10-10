"""Publish a sliced plate into the folder the bambu-win watcher feeds to Bambu Connect.

The watcher reacts to any new *.3mf and waits for its size to settle, so we copy to a `.part`
name first and rename into place: it never sees a half-written file.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

SUFFIX = ".gcode.3mf"
_UNSAFE = re.compile(r"[^A-Za-z0-9._+-]+")


def safe_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", name).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or "plate"


def publish(src: Path, display_name: str, handoff_dir: Path) -> Path:
    handoff_dir = Path(handoff_dir)
    if not handoff_dir.is_dir():
        # Creating it would hide an unmounted prints share: the plate would never reach bambu-win.
        raise FileNotFoundError(f"handoff dir is not mounted: {handoff_dir}")
    base = safe_name(display_name)
    dest = handoff_dir / f"{base}{SUFFIX}"
    n = 2
    while dest.exists():
        dest = handoff_dir / f"{base}-{n}{SUFFIX}"
        n += 1
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, part)
    os.replace(part, dest)
    return dest
