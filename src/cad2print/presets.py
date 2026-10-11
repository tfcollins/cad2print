"""Named slicing presets (profiles.yaml) and the few per-job overrides the UI exposes."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml


class PresetError(Exception):
    pass


@dataclass(frozen=True)
class Overrides:
    infill: int  # sparse infill density, percent
    supports: bool
    copies: int

    def merged(self, form: dict) -> "Overrides":
        """Apply form values (strings) on top of these defaults, validating ranges."""
        out = self
        if "infill" in form and form["infill"] not in ("", None):
            infill = int(form["infill"])
            if not 0 <= infill <= 100:
                raise ValueError("infill must be 0–100 %")
            out = replace(out, infill=infill)
        if "supports" in form:
            out = replace(out, supports=str(form["supports"]).lower() in {"1", "on", "true", "yes"})
        if "copies" in form and form["copies"] not in ("", None):
            copies = int(form["copies"])
            if not 1 <= copies <= 50:
                raise ValueError("copies must be 1–50")
            out = replace(out, copies=copies)
        return out


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    machine: Path
    process: Path
    filament: Path
    bed_type: str = ""  # OrcaSlicer curr_bed_type, e.g. "Textured PEI Plate"; must match the plate on the printer


@dataclass(frozen=True)
class PresetConfig:
    presets: dict[str, Preset]
    defaults: Overrides


def load_presets(path: Path | str) -> PresetConfig:
    path = Path(path)
    doc = yaml.safe_load(path.read_text()) or {}
    profiles_dir = Path(doc.get("profiles_dir", "."))
    if not profiles_dir.is_absolute():
        profiles_dir = (path.parent / profiles_dir).resolve()
    presets: dict[str, Preset] = {}
    for key, spec in (doc.get("presets") or {}).items():
        files = {}
        for kind in ("machine", "process", "filament"):
            p = profiles_dir / spec[kind]
            if not p.is_file():
                raise PresetError(f"preset {key!r}: {kind} profile not found: {p}")
            files[kind] = p
        presets[key] = Preset(key=key, label=spec.get("label", key),
                              bed_type=str(spec.get("bed_type") or doc.get("bed_type") or ""), **files)
    if not presets:
        raise PresetError(f"{path}: no presets defined")
    d = doc.get("defaults") or {}
    defaults = Overrides(infill=int(d.get("infill", 15)), supports=bool(d.get("supports", False)),
                         copies=int(d.get("copies", 1)))
    return PresetConfig(presets=presets, defaults=defaults)
