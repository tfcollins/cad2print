"""Job pipeline: export STLs from Onshape → slice → publish to the handoff folder.

One background thread drains a queue; `run_one` is the synchronous unit of work so tests can
drive it directly with fakes.
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from . import handoff, plate3mf, slicer
from .db import Database, Job, JobSpec, PartRef  # re-exported for callers
from .onshape import OnshapeError
from .presets import PresetConfig
from .printer import PrinterState, may_dispatch

__all__ = ["Job", "JobSpec", "PartRef", "JobRunner"]

log = logging.getLogger(__name__)


class _Exporter(Protocol):
    def export_stl(self, did: str, wvm: str, wvmid: str, eid: str, part_id: str, dest: Path) -> Path: ...


SliceFn = Callable[..., slicer.SliceResult]


class _NoPrinter:
    """Stand-in when no printer is configured: auto-start jobs can never dispatch."""
    enabled = False
    state = PrinterState.offline()


AUTO_SUBDIR = "auto"
STARTED_SUFFIX, FAILED_SUFFIX = ".started", ".failed"


class JobRunner:
    def __init__(self, *, db: Database, presets: PresetConfig, onshape: _Exporter, work_dir: Path,
                 handoff_dir: Path, orca_bin: str, use_xvfb: bool = False, slice_timeout_s: float = 600,
                 tail_lines: int = 40, slice_fn: SliceFn = slicer.run, printer=None,
                 start_timeout_s: float = 240.0, stale_after_s: float = 30.0, tick_s: float = 2.0):
        self.printer = printer or _NoPrinter()
        self.start_timeout_s = start_timeout_s
        self.stale_after_s = stale_after_s
        self.tick_s = tick_s
        self.clock = time.monotonic
        self._last_printer_busy = False
        self._dispatched_at: dict[str, float] = {}
        self.db = db
        self.presets = presets
        self.onshape = onshape
        self.work_dir = Path(work_dir)
        self.handoff_dir = Path(handoff_dir)
        self.orca_bin = orca_bin
        self.use_xvfb = use_xvfb
        self.slice_timeout_s = slice_timeout_s
        self.tail_lines = tail_lines
        self.slice_fn = slice_fn
        self._q: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()

    # -- public API --------------------------------------------------------------------------

    def submit(self, spec: JobSpec) -> Job:
        if spec.preset not in self.presets.presets:
            raise KeyError(f"unknown preset {spec.preset!r}")
        job = self.db.create(spec)
        self._idle.clear()
        self._q.put(job.id)
        return job

    def rerun(self, job_id: str) -> Job:
        old = self.db.get(job_id)
        if old is None:
            raise KeyError(job_id)
        return self.submit(old.spec)

    def start(self) -> None:
        if self._thread is None:
            # Jobs left queued/running by a previous process would poll forever: the queue is
            # in-memory, so they cannot resume. Fail them visibly instead.
            for job in self.db.list_recent(500):
                if job.status in {"queued", "running"}:
                    self.db.finish(job.id, error="interrupted by a cad2print restart; run it again")
            self._thread = threading.Thread(target=self._loop, name="cad2print-worker", daemon=True)
            self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 5) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout)
            self._thread = None

    def wait_idle(self, timeout: float) -> bool:
        return self._idle.wait(timeout)

    # -- worker ------------------------------------------------------------------------------

    def _loop(self) -> None:
        while True:
            try:
                job_id = self._q.get(timeout=self.tick_s)
            except queue.Empty:
                try:
                    self.tick()
                except Exception:
                    log.exception("dispatcher tick failed")
                continue
            if job_id is None:
                break
            try:
                self.run_one(job_id)
            except Exception:
                log.exception("job %s crashed", job_id)
                try:
                    self.db.finish(job_id, error="internal error; see server log")
                except Exception:
                    log.exception("could not record the failure of job %s", job_id)
            finally:
                if self._q.empty():
                    self._idle.set()

    def run_one(self, job_id: str) -> None:
        job = self.db.get(job_id)
        if job is None:
            return
        preset = self.presets.presets[job.spec.preset]
        work = self.work_dir / job.id
        work.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()

        self.db.set_stage(job.id, "exporting")
        stls: list[Path] = []
        try:
            for i, part in enumerate(job.spec.parts):
                dest = work / f"{i:02d}_{handoff.safe_name(part.name)}.stl"
                stl = self.onshape.export_stl(part.did, part.wvm, part.wvmid, part.eid, part.part_id, dest)
                stls.extend([stl] * max(1, part.qty))
        except OnshapeError as e:
            self.db.finish(job.id, error=f"Onshape export failed for {part.name!r}: {e}",
                           duration_s=time.monotonic() - start)
            return

        self.db.set_stage(job.id, "slicing")
        out_name = f"{job.id}.gcode.3mf"
        cmd = slicer.build_command(self.orca_bin, stls, preset, job.spec.overrides, work, out_name,
                                   use_xvfb=self.use_xvfb)
        result = self.slice_fn(cmd, expected_output=work / out_name, timeout_s=self.slice_timeout_s,
                               tail_lines=self.tail_lines)
        if not result.ok:
            self.db.finish(job.id, error=result.error, log_tail=result.log_tail,
                           duration_s=time.monotonic() - start)
            return

        # Bambu Connect needs a plate thumbnail + printer model id that the CLI cannot produce.
        try:
            plate3mf.finalize_plate(result.out_path, thumbnail_png=self._thumbnail_for(job),
                                    printer_model_id=plate3mf.printer_model_id_for(preset.machine))
        except (OSError, ValueError) as e:
            self.db.finish(job.id, error=f"could not finalize the sliced plate: {e}", log_tail=result.log_tail,
                           duration_s=time.monotonic() - start)
            return

        if job.spec.auto_start and self.printer.enabled:
            # Park until the printer is free and the bed has been confirmed clear (see tick()).
            self.db.set_stage(job.id, "waiting_for_printer")
            self.db.save_log(job.id, result.log_tail)
            return

        self.db.set_stage(job.id, "handing_off")
        try:
            published = handoff.publish(result.out_path, job.title, self.handoff_dir)
        except OSError as e:  # includes FileNotFoundError for an unmounted handoff dir
            self.db.finish(job.id, error=f"could not write to handoff dir: {e}", log_tail=result.log_tail,
                           duration_s=time.monotonic() - start)
            return

        self.db.finish(job.id, handoff_name=published.name, log_tail=result.log_tail,
                       duration_s=time.monotonic() - start)
        shutil.rmtree(work, ignore_errors=True)

    def _thumbnail_for(self, job: Job) -> bytes | None:
        """Onshape's preview of the first part's studio, or None (placeholder) if unavailable."""
        part = job.spec.parts[0] if job.spec.parts else None
        getter = getattr(self.onshape, "thumbnail", None)
        if part is None or getter is None:
            return None
        try:
            return getter(part.did, part.wvm, part.wvmid, part.eid)
        except Exception as e:  # noqa: BLE001 - any failure just means a placeholder thumbnail
            log.warning("job %s: no Onshape thumbnail (%s)", job.id, e)
            return None

    # -- auto-start dispatcher ---------------------------------------------------------------

    @property
    def auto_dir(self) -> Path:
        return self.handoff_dir / AUTO_SUBDIR

    def tick(self) -> None:
        """Periodic housekeeping: reset the bed flag when the printer starts, then dispatch/check."""
        busy = self.printer.state.busy
        if busy and not self._last_printer_busy:
            self.db.set_flag("bed_cleared", False)
        self._last_printer_busy = busy
        self.check_starting()
        self.dispatch_next()

    def gate_reason(self) -> str:
        ok, why = may_dispatch(self.printer.state, bed_cleared=self.db.get_flag("bed_cleared"),
                               now=self.clock(), stale_after_s=self.stale_after_s)
        if not self.printer.enabled:
            return "no printer configured"
        return "" if ok else why

    def queue_position(self, job_id: str) -> int | None:
        for i, j in enumerate(self.db.list_by_stage("waiting_for_printer"), start=1):
            if j.id == job_id:
                return i
        return None

    def dispatch_next(self) -> str | None:
        """Hand the oldest waiting plate to Bambu Connect if the printer is free; returns its id."""
        if self.db.list_by_stage("starting"):
            return None
        waiting = self.db.list_by_stage("waiting_for_printer")
        if not waiting or not self.printer.enabled:
            return None
        ok, _ = may_dispatch(self.printer.state, bed_cleared=self.db.get_flag("bed_cleared"),
                             now=self.clock(), stale_after_s=self.stale_after_s)
        if not ok:
            return None
        job = waiting[0]
        plate = self.work_dir / job.id / f"{job.id}.gcode.3mf"
        try:
            self.auto_dir.mkdir(exist_ok=True)
            published = handoff.publish(plate, job.title, self.auto_dir)
        except OSError as e:
            self.db.finish(job.id, error=f"could not write to the auto-start folder: {e}")
            return None
        self.db.set_flag("bed_cleared", False)
        self.db.set_handoff_name(job.id, published.name)
        self.db.set_stage(job.id, "starting")
        self._dispatched_at[job.id] = self.clock()
        return job.id

    def check_starting(self) -> None:
        """Resolve jobs in `starting` from bambu-win's marker files and the printer's state."""
        for job in self.db.list_by_stage("starting"):
            plate = self.auto_dir / (job.handoff_name or "")
            failed = plate.with_name(plate.name + FAILED_SUFFIX)
            started = plate.with_name(plate.name + STARTED_SUFFIX)
            since = self.clock() - self._dispatched_at.get(job.id, self.clock())
            if failed.is_file():
                msg = failed.read_text(errors="replace").strip() or "Bambu Connect did not start the print"
                self.db.finish(job.id, error=f"print did not start — {msg} (check Bambu Connect on bambu-win)")
            elif started.is_file() and self.printer.state.busy:
                self.db.finish(job.id, handoff_name=job.handoff_name, duration_s=job.duration_s)
                shutil.rmtree(self.work_dir / job.id, ignore_errors=True)
            elif since > self.start_timeout_s:
                self.db.finish(job.id, error="print did not start within the time limit — "
                                             "check Bambu Connect on bambu-win")
            else:
                continue
            self._dispatched_at.pop(job.id, None)
