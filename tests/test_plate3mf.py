import re
import zipfile
from pathlib import Path

import pytest

from cad2print.plate3mf import PLACEHOLDER_PNG, finalize_plate, inspect_plate

MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2">
    <metadata key="name" value="cube.stl"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
    </model_instance>
  </plate>
</config>
"""
SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value=""/>
    <metadata key="nozzle_diameters" value="0.4"/>
  </plate>
</config>
"""
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""


def write_minimal_plate(p: Path) -> Path:
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("Metadata/model_settings.config", MODEL_SETTINGS)
        z.writestr("Metadata/slice_info.config", SLICE_INFO)
        z.writestr("Metadata/plate_1.gcode", "; gcode\nG28\n")
        z.writestr("Metadata/plate_1.gcode.md5", "d41d8cd98f00b204e9800998ecf8427e")
    return p


def _make(tmp_path: Path) -> Path:
    return write_minimal_plate(tmp_path / "cube.gcode.3mf")


def test_inspect_reports_missing_thumbnail_and_model_id(tmp_path):
    info = inspect_plate(_make(tmp_path))
    assert info.has_thumbnail is False and info.printer_model_id == ""


def test_finalize_adds_thumbnails_and_registers_them(tmp_path):
    p = _make(tmp_path)
    png = b"\x89PNG\r\n\x1a\nfakepng"
    finalize_plate(p, thumbnail_png=png, printer_model_id="O1S")
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        for n in ("Metadata/plate_1.png", "Metadata/plate_1_small.png", "Metadata/plate_no_light_1.png",
                  "Metadata/top_1.png", "Metadata/pick_1.png"):
            assert n in names and z.read(n) == png
        ms = z.read("Metadata/model_settings.config").decode()
        assert '<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>' in ms
        assert '<metadata key="thumbnail_no_light_file" value="Metadata/plate_no_light_1.png"/>' in ms
        assert '<metadata key="top_file" value="Metadata/top_1.png"/>' in ms
        assert '<metadata key="pick_file" value="Metadata/pick_1.png"/>' in ms
        assert ms.index("thumbnail_file") < ms.index("<model_instance>"), "keys live inside <plate>"
        si = z.read("Metadata/slice_info.config").decode()
        assert '<metadata key="printer_model_id" value="O1S"/>' in si
        ct = z.read("[Content_Types].xml").decode()
        assert 'Extension="png"' in ct
        assert z.read("Metadata/plate_1.gcode") == b"; gcode\nG28\n", "gcode untouched"
        assert len(names) == len(set(names)), "no duplicate entries"
    info = inspect_plate(p)
    assert info.has_thumbnail and info.printer_model_id == "O1S"


def test_finalize_uses_placeholder_when_no_thumbnail_given(tmp_path):
    p = _make(tmp_path)
    finalize_plate(p, thumbnail_png=None, printer_model_id="O1S")
    with zipfile.ZipFile(p) as z:
        assert z.read("Metadata/plate_1.png") == PLACEHOLDER_PNG
    assert PLACEHOLDER_PNG.startswith(b"\x89PNG")


def test_finalize_is_idempotent_and_keeps_existing_thumbnails(tmp_path):
    p = _make(tmp_path)
    finalize_plate(p, thumbnail_png=b"\x89PNGone", printer_model_id="O1S")
    finalize_plate(p, thumbnail_png=b"\x89PNGtwo", printer_model_id="O1S")
    with zipfile.ZipFile(p) as z:
        assert z.read("Metadata/plate_1.png") == b"\x89PNGone"
        assert z.read("Metadata/model_settings.config").decode().count('key="thumbnail_file"') == 1


def test_finalize_rejects_a_3mf_without_a_sliced_plate(tmp_path):
    p = tmp_path / "x.gcode.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
    with pytest.raises(ValueError, match="model_settings"):
        finalize_plate(p, thumbnail_png=None, printer_model_id="O1S")


def test_model_id_lookup_reads_the_printer_model_json(tmp_path):
    from cad2print.plate3mf import printer_model_id_for
    machine = tmp_path / "machine"; machine.mkdir()
    (machine / "Bambu Lab H2S.json").write_text('{"type": "machine_model", "name": "Bambu Lab H2S", "model_id": "O1S"}')
    (machine / "Bambu Lab H2S 0.4 nozzle.json").write_text('{"type": "machine", "printer_model": "Bambu Lab H2S"}')
    assert printer_model_id_for(machine / "Bambu Lab H2S 0.4 nozzle.json") == "O1S"
    (machine / "Weird.json").write_text('{"type": "machine"}')
    assert printer_model_id_for(machine / "Weird.json") == ""


def test_finalize_names_the_plate_so_the_printer_shows_it(tmp_path):
    p = _make(tmp_path)
    finalize_plate(p, thumbnail_png=None, printer_model_id="O1S", plate_name="Rack Mount")
    with zipfile.ZipFile(p) as z:
        ms = z.read("Metadata/model_settings.config").decode()
        assert '<metadata key="plater_name" value="Rack Mount"/>' in ms
    q = tmp_path / "b"; q.mkdir()
    finalize_plate(_make(q), thumbnail_png=None, printer_model_id="O1S", plate_name='A<"B"&C')
    with zipfile.ZipFile(q / "cube.gcode.3mf") as z:
        assert 'value="A&lt;&quot;B&quot;&amp;C"' in z.read("Metadata/model_settings.config").decode()
