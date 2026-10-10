from cad2print.handoff import publish, safe_name


def test_safe_name_replaces_unsafe_characters():
    assert safe_name("ADRV9361 Rack Mount / Part 1 (v2)") == "ADRV9361_Rack_Mount_Part_1_v2"


def test_publish_moves_file_atomically_into_handoff_dir(tmp_path):
    src = tmp_path / "work" / "job.gcode.3mf"
    src.parent.mkdir()
    src.write_bytes(b"3MF")
    handoff = tmp_path / "prints"
    handoff.mkdir()

    out = publish(src, "Rack Mount", handoff)

    assert out == handoff / "Rack_Mount.gcode.3mf"
    assert out.read_bytes() == b"3MF"
    assert sorted(p.name for p in handoff.iterdir()) == ["Rack_Mount.gcode.3mf"], "no .part left behind"


def test_publish_suffixes_on_name_collision(tmp_path):
    handoff = tmp_path / "prints"
    handoff.mkdir()
    (handoff / "cube.gcode.3mf").write_bytes(b"old")
    (handoff / "cube-2.gcode.3mf").write_bytes(b"old2")
    src = tmp_path / "cube.gcode.3mf"
    src.write_bytes(b"new")

    out = publish(src, "cube", handoff)

    assert out.name == "cube-3.gcode.3mf" and out.read_bytes() == b"new"
    assert (handoff / "cube.gcode.3mf").read_bytes() == b"old"


def test_publish_refuses_to_invent_a_missing_handoff_dir(tmp_path):
    """A missing dir means the prints share is not mounted; silently creating it would strand plates."""
    import pytest
    src = tmp_path / "a.gcode.3mf"
    src.write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        publish(src, "a", tmp_path / "deep" / "prints")
    assert not (tmp_path / "deep").exists()
