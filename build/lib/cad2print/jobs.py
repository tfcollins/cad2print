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

from . import handoff, slicer
from .db import Database, Job, JobSpec, PartRef  # re-exported for callers
from .onshape import OnshapeError
from .presets import PresetConfig

__all__ = ["Job", "JobSpec", "PartRef", "JobRunner"]

log = logging.getLogger(__name__)


class _Exporter(Protocol):
    def export_stl(self, did: str, wvm: str, wvmid: str, eid: str, part_id: str, dest: Path) -> Path: ...


SliceFn = Callable[..., slicer.SliceResult]


class JobRunner:
    def __init__(self, *, db: Database, presets: PresetConfig, onshape: _Exporter, work_dir: Path,
                 handoff_dir: Path, orca_bin: str, use_xvfb: bool = False, slice_timeout_s: float = 600,
                 tail_lines: int = 40, slice_fn: SliceFn = slicer.run):
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
            self._thread = threading.Thread(target=self._loop, name="cad2print-worker", daemon=True)
            self._thread.start()

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
            job_id = self._q.get()
            if job_id is None:
                break
            try:
                self.run_one(job_id)
            except Exception:  # pragma: no cover - last-resort guard so the worker never dies
                log.exception("job %s crashed", job_id)
                self.db.finish(job_id, error="internal error; see server log")
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

        self.db.set_stage(job.id, "handing_off")
        try:
            published = handoff.publish(result.out_path, job.title, self.handoff_dir)
        except OSError as e:
            self.db.finish(job.id, error=f"could not write to handoff dir: {e}", log_tail=result.log_tail,
                           duration_s=time.monotonic() - start)
            return

        self.db.finish(job.id, handoff_name=published.name, log_tail=result.log_tail,
                       duration_s=time.monotonic() - start)
        shutil.rmtree(work, ignore_errors=True)
