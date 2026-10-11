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
        self.thumb_calls = []

    def thumbnail(self, did, wvm, wvmid, eid, size="300x170"):
        self.thumb_calls.append(eid)
        return b"\x89PNGonshape"

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
        from tests.test_plate3mf import write_minimal_plate
        write_minimal_plate(Path(expected_output))
        return SliceResult(True, Path(expected_output), 0, 0.1, "done")


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    (tmp_path / "prints").mkdir()
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
    assert (env["handoff"] / "Plate.gcode.3mf").is_file()
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


def test_start_marks_jobs_interrupted_by_a_previous_process_as_failed(env):
    db = env["db"]
    stale_running = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    db.set_stage(stale_running.id, "slicing")
    stale_queued = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r = _runner(env, FakeOnshape(), FakeSlicer())
    r.start()
    try:
        for jid in (stale_running.id, stale_queued.id):
            j = db.get(jid)
            assert j.status == "failed" and "restart" in j.error
    finally:
        r.stop()


def test_worker_survives_a_crashing_job_and_a_failing_finish(env, monkeypatch):
    r = _runner(env, FakeOnshape(), FakeSlicer())
    monkeypatch.setattr(r, "run_one", lambda job_id: 1 / 0)
    calls = {"n": 0}
    real_finish = env["db"].finish

    def flaky_finish(job_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return real_finish(job_id, **kw)

    monkeypatch.setattr(env["db"], "finish", flaky_finish)
    r.start()
    try:
        a = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
        b = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
        assert r.wait_idle(5)
        assert r.alive, "worker thread must outlive job crashes"
        assert env["db"].get(b.id).status == "failed"
    finally:
        r.stop()


def test_transport_error_during_export_fails_job_with_a_readable_message(env):
    class Flaky:
        def export_stl(self, *a, **k):
            from cad2print.onshape import OnshapeError
            raise OnshapeError(0, "ConnectError: all connection attempts failed")

    r = _runner(env, Flaky(), FakeSlicer())
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    assert "connection attempts failed" in env["db"].get(job.id).error


# --- auto-start (A2): spec flag, settings, stages -------------------------------------------

def test_job_spec_carries_auto_start_and_db_round_trips_it(env):
    db = env["db"]
    a = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults, auto_start=True))
    m = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults, auto_start=False))
    assert db.get(a.id).spec.auto_start is True and db.get(m.id).spec.auto_start is False
    assert JobSpec(parts=[PART], preset="p", overrides=CFG.defaults).auto_start is True, "default on"


def test_bed_cleared_flag_persists(env):
    db = env["db"]
    assert db.get_flag("bed_cleared") is False
    db.set_flag("bed_cleared", True)
    assert db.get_flag("bed_cleared") is True
    db.set_flag("bed_cleared", False)
    assert db.get_flag("bed_cleared") is False


def test_waiting_jobs_are_listed_oldest_first(env):
    db = env["db"]
    j1 = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    j2 = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    db.set_stage(j1.id, "waiting_for_printer"); db.set_stage(j2.id, "waiting_for_printer")
    assert [j.id for j in db.list_by_stage("waiting_for_printer")] == [j1.id, j2.id]


def test_existing_database_without_the_column_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
          status TEXT NOT NULL, stage TEXT NOT NULL, stages_seen TEXT NOT NULL DEFAULT '[]',
          preset TEXT NOT NULL, overrides TEXT NOT NULL, parts TEXT NOT NULL,
          handoff_name TEXT, error TEXT, log_tail TEXT, duration_s REAL);
        INSERT INTO jobs VALUES ('old1','2026-10-10T00:00:00+00:00','2026-10-10T00:00:00+00:00','done','done','[]','p',
          '{"infill":15,"supports":false,"copies":1}', '[]', 'x.gcode.3mf', NULL, NULL, 1.0);
    """)
    con.commit(); con.close()
    db = Database(path)
    assert db.get("old1").spec.auto_start is True


# --- auto-start (A3): dispatcher ---------------------------------------------------------------

from cad2print.printer import PrinterState


class FakePrinter:
    def __init__(self, gcode_state="IDLE"):
        self.now = 1000.0
        self.state = PrinterState(online=True, updated_at=self.now, gcode_state=gcode_state)
        self.enabled = True

    def set(self, gcode_state):
        self.state = PrinterState(online=True, updated_at=self.now, gcode_state=gcode_state)


def _auto_runner(env, printer, slicer=None, start_timeout_s=5.0):
    r = _runner(env, FakeOnshape(), slicer or FakeSlicer())
    r.printer = printer
    r.start_timeout_s = start_timeout_s
    r.clock = lambda: printer.now
    return r


def test_auto_job_parks_in_waiting_for_printer_after_slicing(env):
    r = _auto_runner(env, FakePrinter("RUNNING"))
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults, auto_start=True))
    r.run_one(job.id)
    j = env["db"].get(job.id)
    assert j.status == "running" and j.stage == "waiting_for_printer"
    assert j.handoff_name is None and not list((env["handoff"]).glob("*.3mf"))
    assert (env["work"] / job.id / f"{job.id}.gcode.3mf").is_file(), "sliced plate kept until dispatch"


def test_manual_job_ignores_the_gate(env):
    r = _auto_runner(env, FakePrinter("RUNNING"))
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults, auto_start=False))
    r.run_one(job.id)
    assert env["db"].get(job.id).status == "done"
    assert (env["handoff"] / "Plate.gcode.3mf").is_file()


def test_dispatch_waits_for_idle_and_bed_cleared_then_publishes_into_auto_dir(env):
    printer = FakePrinter("RUNNING")
    r = _auto_runner(env, printer)
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults, auto_start=True))
    r.run_one(job.id)
    assert r.dispatch_next() is None and env["db"].get(job.id).stage == "waiting_for_printer"
    printer.set("FINISH")
    assert r.dispatch_next() is None, "bed not cleared yet"
    env["db"].set_flag("bed_cleared", True)
    assert r.dispatch_next() == job.id
    j = env["db"].get(job.id)
    assert j.stage == "starting" and j.handoff_name == "Plate.gcode.3mf"
    assert (env["handoff"] / "auto" / "Plate.gcode.3mf").is_file()
    assert env["db"].get_flag("bed_cleared") is False, "flag consumed by the dispatch"


def test_dispatch_is_fifo_and_one_at_a_time(env):
    printer = FakePrinter("IDLE")
    r = _auto_runner(env, printer)
    a = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(a.id)
    b = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(b.id)
    env["db"].set_flag("bed_cleared", True)
    assert r.dispatch_next() == a.id
    env["db"].set_flag("bed_cleared", True)
    assert r.dispatch_next() is None, "a is still starting; b must wait"


def test_starting_job_completes_when_marker_and_printer_agree(env):
    printer = FakePrinter("IDLE")
    r = _auto_runner(env, printer)
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(job.id)
    env["db"].set_flag("bed_cleared", True); r.dispatch_next()
    r.check_starting()
    assert env["db"].get(job.id).stage == "starting"
    (env["handoff"] / "auto" / "Plate.gcode.3mf.started").write_text("ok")
    r.check_starting()
    assert env["db"].get(job.id).stage == "starting", "marker alone is not enough"
    printer.set("RUNNING")
    r.check_starting()
    j = env["db"].get(job.id)
    assert j.status == "done" and j.stage == "done"
    assert not (env["work"] / job.id).exists()


def test_starting_job_fails_on_failed_marker_with_its_message(env):
    printer = FakePrinter("IDLE")
    r = _auto_runner(env, printer)
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(job.id)
    env["db"].set_flag("bed_cleared", True); r.dispatch_next()
    (env["handoff"] / "auto" / "Plate.gcode.3mf.failed").write_text("print button not found")
    r.check_starting()
    j = env["db"].get(job.id)
    assert j.status == "failed" and "print button not found" in j.error
    assert (env["handoff"] / "auto" / "Plate.gcode.3mf").is_file(), "plate left for a manual retry"


def test_starting_job_times_out_with_a_pointer_to_bambu_win(env):
    printer = FakePrinter("IDLE")
    r = _auto_runner(env, printer, start_timeout_s=30)
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(job.id)
    env["db"].set_flag("bed_cleared", True); r.dispatch_next()
    printer.now += 31
    r.check_starting()
    j = env["db"].get(job.id)
    assert j.status == "failed" and "bambu-win" in j.error


def test_bed_cleared_flag_resets_when_printer_starts_on_its_own(env):
    printer = FakePrinter("IDLE")
    r = _auto_runner(env, printer)
    env["db"].set_flag("bed_cleared", True)
    printer.set("RUNNING"); r.tick()
    assert env["db"].get_flag("bed_cleared") is False


def test_queue_position_and_gate_reason_are_exposed(env):
    printer = FakePrinter("RUNNING")
    r = _auto_runner(env, printer)
    a = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(a.id)
    b = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); r.run_one(b.id)
    assert r.queue_position(b.id) == 2
    assert "printing" in r.gate_reason()


def test_published_plate_carries_the_onshape_thumbnail_and_model_id(env, tmp_path):
    import zipfile
    onshape = FakeOnshape()
    r = _runner(env, onshape, FakeSlicer())
    (tmp_path / "m.json").write_text('{"type":"machine","printer_model":"Bambu Lab H2S"}')
    (tmp_path / "Bambu Lab H2S.json").write_text('{"type":"machine_model","model_id":"O1S"}')
    r.presets = PresetConfig(presets={"p": Preset("p", "P", tmp_path / "m.json", Path("/pr.json"), Path("/f.json"))},
                             defaults=CFG.defaults)
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    with zipfile.ZipFile(env["handoff"] / "Plate.gcode.3mf") as z:
        assert z.read("Metadata/plate_1.png") == b"\x89PNGonshape"
        assert 'value="O1S"' in z.read("Metadata/slice_info.config").decode()
    assert onshape.thumb_calls == [PART.eid]


def test_thumbnail_failure_falls_back_to_placeholder(env):
    import zipfile
    from cad2print.onshape import OnshapeError
    from cad2print.plate3mf import PLACEHOLDER_PNG
    onshape = FakeOnshape()
    def boom(*a, **k): raise OnshapeError(500, "nope")
    onshape.thumbnail = boom
    r = _runner(env, onshape, FakeSlicer())
    job = r.submit(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults))
    r.run_one(job.id)
    with zipfile.ZipFile(env["handoff"] / "Plate.gcode.3mf") as z:
        assert z.read("Metadata/plate_1.png") == PLACEHOLDER_PNG


def test_parked_and_starting_jobs_survive_a_restart(env):
    db = env["db"]
    waiting = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); db.set_stage(waiting.id, "waiting_for_printer")
    starting = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); db.set_stage(starting.id, "starting")
    slicing = db.create(JobSpec(parts=[PART], preset="p", overrides=CFG.defaults)); db.set_stage(slicing.id, "slicing")
    r = _auto_runner(env, FakePrinter("RUNNING"), start_timeout_s=30)
    r.start()
    try:
        assert db.get(waiting.id).status == "running" and db.get(waiting.id).stage == "waiting_for_printer"
        assert db.get(starting.id).status == "running" and db.get(starting.id).stage == "starting"
        assert db.get(slicing.id).status == "failed"
        # the restarted 'starting' job gets a fresh timeout window, not an instant timeout
        r.check_starting()
        assert db.get(starting.id).status == "running"
    finally:
        r.stop()
