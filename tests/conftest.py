from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cad2print.app import create_app
from cad2print.config import Settings
from cad2print.presets import Overrides, Preset, PresetConfig
from cad2print.slicer import SliceResult

DID, WID, EID = "d" * 24, "1" * 24, "e" * 24


class FakeOnshape:
    """Just enough of OnshapeClient for the routes."""

    def __init__(self):
        self.exported = []

    def search_documents(self, q="", limit=20):
        return [{"id": DID, "name": "KVM Rack Mount", "workspace_id": WID, "modified_at": "2026-07-14"}]

    def versions(self, did):
        return [{"id": "v" * 24, "name": "Start"}]

    def part_studios(self, did, wvm, wvmid):
        return [{"id": EID, "name": "Part Studio 1"}]

    def parts(self, did, wvm, wvmid, eid):
        return [{"id": "JHD", "name": "Plate"}, {"id": "JHE", "name": "Lid"}]

    def thumbnail(self, did, wvm, wvmid, eid, size="300x170"):
        return b"\x89PNGfake"

    def export_stl(self, did, wvm, wvmid, eid, part_id, dest):
        self.exported.append(part_id)
        Path(dest).write_bytes(b"solid")
        return Path(dest)


def fake_slice(cmd, expected_output, timeout_s, tail_lines):
    Path(expected_output).write_bytes(b"3MF")
    return SliceResult(True, Path(expected_output), 0, 0.01, "sliced ok")


@pytest.fixture
def presets():
    p = Preset("h2s-pla-020", "H2S PLA 0.20", Path("/m.json"), Path("/p.json"), Path("/f.json"))
    return PresetConfig(presets={"h2s-pla-020": p}, defaults=Overrides(15, False, 1))


@pytest.fixture
def fake_onshape():
    return FakeOnshape()


@pytest.fixture
def app(tmp_path, presets, fake_onshape):
    settings = Settings(handoff_dir=tmp_path / "prints", work_dir=tmp_path / "work",
                        db_path=tmp_path / "db.sqlite", orca_bin="orca")
    return create_app(settings, onshape=fake_onshape, presets=presets, slice_fn=fake_slice)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
