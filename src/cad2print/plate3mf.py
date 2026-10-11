"""Post-process a sliced .gcode.3mf so Bambu Connect accepts it.

OrcaSlicer 2.4.2's CLI cannot render plate thumbnails (it requests an invalid OpenGL 3.4
context — fixed upstream after the 2.4.2 release) and leaves `printer_model_id` empty.
Bambu Connect's importer reads `thumbnail_file` from model_settings.config and crashes
silently without it. We add a thumbnail (the Onshape preview when available) and the
printer model id; the G-code and its md5 are left untouched.
"""
from __future__ import annotations

import json
import os
import re
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

PLATE_PNG = "Metadata/plate_1.png"
PNG_ENTRIES = {
    "thumbnail_file": "Metadata/plate_1.png",
    "thumbnail_no_light_file": "Metadata/plate_no_light_1.png",
    "top_file": "Metadata/top_1.png",
    "pick_file": "Metadata/pick_1.png",
}
EXTRA_PNGS = ["Metadata/plate_1_small.png"]


def _solid_png(w: int, h: int, rgb=(0xE9, 0xEC, 0xEF)) -> bytes:
    """A valid PNG without Pillow: one colour, used when no preview is available."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


PLACEHOLDER_PNG = _solid_png(256, 256)


@dataclass(frozen=True)
class PlateInfo:
    has_thumbnail: bool
    printer_model_id: str


def inspect_plate(path: Path) -> PlateInfo:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        ms = z.read("Metadata/model_settings.config").decode("utf-8", "replace") if "Metadata/model_settings.config" in names else ""
        si = z.read("Metadata/slice_info.config").decode("utf-8", "replace") if "Metadata/slice_info.config" in names else ""
    m = re.search(r'<metadata key="printer_model_id" value="([^"]*)"', si)
    return PlateInfo(has_thumbnail='key="thumbnail_file"' in ms and PLATE_PNG in names,
                     printer_model_id=m.group(1) if m else "")


def printer_model_id_for(machine_json: Path) -> str:
    """Model id (e.g. O1S) for a machine profile, via its sibling `<printer_model>.json`."""
    try:
        model = json.loads(Path(machine_json).read_text()).get("printer_model", "")
        if not model:
            return ""
        return str(json.loads((Path(machine_json).parent / f"{model}.json").read_text()).get("model_id", ""))
    except (OSError, ValueError):
        return ""


def finalize_plate(path: Path, *, thumbnail_png: bytes | None, printer_model_id: str) -> Path:
    path = Path(path)
    png = thumbnail_png or PLACEHOLDER_PNG
    with zipfile.ZipFile(path) as src:
        names = src.namelist()
        if "Metadata/model_settings.config" not in names:
            raise ValueError(f"{path.name}: no Metadata/model_settings.config — not a sliced plate")
        entries = {n: src.read(n) for n in names}

    ms = entries["Metadata/model_settings.config"].decode("utf-8")
    if 'key="thumbnail_file"' not in ms:
        lines = "".join(f'    <metadata key="{k}" value="{v}"/>\n' for k, v in PNG_ENTRIES.items())
        ms, n = re.subn(r'(\n\s*<metadata key="gcode_file"[^\n]*\n)', r"\1" + lines, ms, count=1)
        if n == 0:  # no gcode_file line: put them right after <plate>
            ms = ms.replace("<plate>\n", "<plate>\n" + lines, 1)
        entries["Metadata/model_settings.config"] = ms.encode("utf-8")
        for name in list(PNG_ENTRIES.values()) + EXTRA_PNGS:
            entries.setdefault(name, png)

    if printer_model_id and "Metadata/slice_info.config" in entries:
        si = entries["Metadata/slice_info.config"].decode("utf-8")
        si = re.sub(r'<metadata key="printer_model_id" value=""/>',
                    f'<metadata key="printer_model_id" value="{printer_model_id}"/>', si, count=1)
        entries["Metadata/slice_info.config"] = si.encode("utf-8")

    ct = entries.get("[Content_Types].xml", b"").decode("utf-8")
    if ct and 'Extension="png"' not in ct:
        ct = ct.replace("</Types>", ' <Default Extension="png" ContentType="image/png"/>\n</Types>')
        entries["[Content_Types].xml"] = ct.encode("utf-8")

    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".part", dir=path.parent)
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in names:
            dst.writestr(name, entries[name])
        for name in entries:
            if name not in names:
                dst.writestr(name, entries[name])
    os.replace(tmp, path)
    return path
