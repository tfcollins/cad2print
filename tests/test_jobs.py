import json
import threading
from pathlib import Path

import pytest

from cad2print.db import Database
from cad2print.jobs import Job, JobRunner, JobSpec, PartRef
from cad2print.presets import Overrides, Preset, PresetConfig
from cad2print.slicer import SliceResult

PRESET = Preset("p", "P", Path("/m.json"), Path("/pr.json"), Path("/f.json"))
CFG = PresetConfig(presets={"p": PRESET}, defaults=Overrides(15, False, 1))
PART = PartRef(did="d" * 24, wvm="w", wvmid="w" * 24, eid="e" * 24, part_id="JHD", name="Plate")


class FakeOnshape:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def export_stl(self, did, wvm, wvmid, eid, part_id, dest):
        self.calls.append(part_id)
        if self.fail:
            from cad2print.onshape import OnshapeError
            raise OnshapeError(404, "no such part")
        Path(dest).write_bytes(b"solid")
        return Path(dest)


class FakeSlicer:
    def __init__(self, ok=True):
        self.ok = ok
        self.commands = []

    def __call__(self, cmd, expected_output, timeout_s, tail_lines):
        self.commands.append(cmd)
        if not self.ok:
            return SliceResult(False, None, 3, 0.1, "boom", "orca-slicer exited with code 3")
        Path(expected_output).write_bytes(b"3MF")
        return SliceResult(True, Path(expected_output), 0, 0.1, "done")


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    return dict(db=db, work=tmp_path / "work", handoff=tmp_path / "prints")


def _runner(env, onshape, slicer):
    return JobRunner(db=env["db"], presets=CFG, onshape=onshape, slice_fn=slicer,
                     work_dir=env["work"], handoff_dir=env["handoff"], orca_bin="orca",
                     use_xvfb=False, slice_timeout_s=5, tail_lines=10)


def test_database_round_trips_a_job(env):
    db = env["db"]
    job = db.create(JobSpec(parts=[PART], preset="p", overrides=Overrides(20, True, 2)))
    got = db.get(job.id)
    assert got.id == job.id and got.status == "queued" and got.stage == "queued"
    assert got.spec.parts == [PART] and got.spec.overrides == Overrides(20, True, 2)
    assert db.list_recent(10)[0].id == job.id


def test_runner_processes_job_through_all_stages(env):
    onshape, slicer = FakeOnshape(), FakeSlicer()
    r = _runner(env, onshape, slicer)
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=Overrides(20, True, 1)))

    r.run_one(job.id)

    done = env["db"].get(job.id)
    assert done.status == "done" and done.stage == "done"
    assert done.handoff_name == "Plate.gcode.3mf"
    assert (env["handoff"] / "Plate.gcode.3mf").read_bytes() == b"3MF"
    assert onshape.calls == ["JHD"]
    assert "--sparse-infill-density=20%" in slicer.commands[0]
    assert not (env["work"] / job.id).exists(), "work dir removed on success"


def test_stage_history_is_recorded(env):
    r = _runner(env, FakeOnshape(), FakeSlicer())
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    assert env["db"].get(job.id).stages_seen == ["exporting", "slicing", "handing_off", "done"]


def test_export_failure_marks_job_failed_with_message_and_keeps_work_dir(env):
    r = _runner(env, FakeOnshape(fail=True), FakeSlicer())
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    failed = env["db"].get(job.id)
    assert failed.status == "failed" and failed.stage == "exporting"
    assert "no such part" in failed.error
    assert (env["work"] / job.id).is_dir()


def test_slice_failure_keeps_log_tail(env):
    r = _runner(env, FakeOnshape(), FakeSlicer(ok=False))
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    failed = env["db"].get(job.id)
    assert failed.status == "failed" and failed.stage == "slicing"
    assert failed.log_tail == "boom" and "code 3" in failed.error


def test_multi_part_job_names_plate_after_first_part_and_count(env):
    r = _runner(env, FakeOnshape(), FakeSlicer())
    p2 = PartRef(**{**PART.__dict__, "part_id": "JHE", "name": "Lid"})
    job = r.submit(JobSpec(parts=[PART, p2], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    assert env["db"].get(job.id).handoff_name == "Plate_+1.gcode.3mf"


def test_rerun_clones_spec_into_a_new_queued_job(env):
    r = _runner(env, FakeOnshape(), FakeSlicer())
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=Overrides(33, False, 2)))
    r.run_one(job.id)
    clone = r.rerun(job.id)
    assert clone.id != job.id and clone.status == "queued"
    assert clone.spec == job.spec


def test_background_worker_drains_queue(env):
    r = _runner(env, FakeOnshape(), FakeSlicer())
    r.start()
    try:
        job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
        assert r.wait_idle(timeout=5)
        assert env["db"].get(job.id).status == "done"
    finally:
        r.stop()


def test_unknown_preset_fails_at_submit(env):
    r = _runner(env, FakeOnshape(), FakeSlicer())
    with pytest.raises(KeyError):
        r.submit(JobSpec(parts=[PART], preset="nope", overrides=CFG.defaults))


def test_part_qty_exports_once_and_repeats_the_stl_for_the_slicer(env):
    onshape, slicer = FakeOnshape(), FakeSlicer()
    r = _runner(env, onshape, slicer)
    three = PartRef(**{**PART.__dict__, "qty": 3})
    job = r.submit(JobSpec(parts=[three], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    assert onshape.calls == ["JHD"], "one download per distinct part"
    stl_args = [a for a in slicer.commands[0] if a.endswith(".stl")]
    assert len(stl_args) == 3 and len(set(stl_args)) == 1
    assert env["db"].get(job.id).handoff_name == "Plate.gcode.3mf"
