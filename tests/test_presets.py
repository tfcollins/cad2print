from pathlib import Path

import pytest
import yaml

from cad2print.presets import Overrides, Preset, PresetError, load_presets


def _write(tmp_path: Path, presets: dict, profiles_dir: Path | None = None) -> Path:
    pdir = profiles_dir or tmp_path / "BBL"
    for sub in ("machine", "process", "filament"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    (pdir / "machine" / "H2S 0.4.json").write_text("{}")
    (pdir / "process" / "0.20 Standard.json").write_text("{}")
    (pdir / "filament" / "PLA.json").write_text("{}")
    doc = {"profiles_dir": str(pdir), "presets": presets, "defaults": {"infill": 15, "supports": False, "copies": 1}}
    f = tmp_path / "profiles.yaml"
    f.write_text(yaml.safe_dump(doc, sort_keys=False))
    return f


GOOD = {
    "h2s-pla-020": {
        "label": "H2S · PLA · 0.20",
        "machine": "machine/H2S 0.4.json",
        "process": "process/0.20 Standard.json",
        "filament": "filament/PLA.json",
    }
}


def test_load_resolves_absolute_paths_and_label(tmp_path):
    cfg = load_presets(_write(tmp_path, GOOD))
    p = cfg.presets["h2s-pla-020"]
    assert isinstance(p, Preset)
    assert p.label == "H2S · PLA · 0.20"
    assert p.machine == tmp_path / "BBL" / "machine" / "H2S 0.4.json"
    assert p.process.is_absolute() and p.filament.is_absolute()
    assert cfg.defaults == Overrides(infill=15, supports=False, copies=1)


def test_missing_profile_file_fails_fast_with_the_path(tmp_path):
    bad = {"x": dict(GOOD["h2s-pla-020"], filament="filament/Nope.json")}
    with pytest.raises(PresetError, match="Nope.json"):
        load_presets(_write(tmp_path, bad))


def test_preset_order_is_preserved_for_the_ui(tmp_path):
    two = {"b": GOOD["h2s-pla-020"], "a": GOOD["h2s-pla-020"]}
    cfg = load_presets(_write(tmp_path, two))
    assert list(cfg.presets) == ["b", "a"]


def test_overrides_merge_form_values_onto_defaults():
    d = Overrides(infill=15, supports=False, copies=1)
    assert d.merged({"infill": "30", "supports": "on"}) == Overrides(infill=30, supports=True, copies=1)
    assert d.merged({"copies": "3"}) == Overrides(infill=15, supports=False, copies=3)


@pytest.mark.parametrize("bad", [{"infill": "150"}, {"infill": "-1"}, {"copies": "0"}, {"copies": "x"}])
def test_overrides_reject_out_of_range_values(bad):
    with pytest.raises(ValueError):
        Overrides(infill=15, supports=False, copies=1).merged(bad)
