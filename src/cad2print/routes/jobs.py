"""Print setup → job creation → live status → history / re-run."""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..app import render
from ..db import JobSpec, PartRef

router = APIRouter()

STAGES = [("exporting", "Exporting from Onshape"), ("slicing", "Slicing"),
          ("handing_off", "Handing to Bambu Connect"), ("done", "Ready to print")]
AUTO_STAGES = [("exporting", "Exporting from Onshape"), ("slicing", "Slicing"),
               ("waiting_for_printer", "Waiting for printer"), ("starting", "Starting in Bambu Connect"),
               ("done", "Printing")]

STATE_LABELS = {"IDLE": "Idle", "FINISH": "Finished", "FAILED": "Failed", "RUNNING": "Printing",
                "PREPARE": "Preparing", "PAUSE": "Paused", "SLICING": "Preparing", "INIT": "Starting"}


def _stages_for(job):
    return AUTO_STAGES if job.spec.auto_start and "waiting_for_printer" in job.stages_seen else STAGES


def _printer_ctx(request: Request) -> dict:
    runner = request.app.state.runner
    st = runner.printer.state
    live = runner.printer.enabled and st.is_live(runner.clock(), runner.stale_after_s)
    return {
        "enabled": runner.printer.enabled,
        "live": live,
        "label": STATE_LABELS.get(st.gcode_state or "", st.gcode_state or "Unknown") if live else "Offline",
        "busy": st.busy,
        "percent": st.percent,
        "remaining_min": st.remaining_min,
        "task_name": st.task_name,
        "bed_cleared": request.app.state.db.get_flag("bed_cleared"),
        "gate_reason": runner.gate_reason(),
        "waiting": len(request.app.state.db.list_by_stage("waiting_for_printer")),
        "trays": [t for t in getattr(st, "trays", ()) if not t.empty],
    }


async def _selected_parts(request: Request) -> tuple[dict, list[dict]]:
    """Read the parts form: repeated `part` ids with `name_<id>` and `qty_<id>` fields."""
    form = await request.form()
    ref = {k: form.get(k, "") for k in ("did", "wvm", "wvmid", "eid")}
    chosen = []
    for pid in form.getlist("part"):
        try:
            qty = max(1, min(50, int(form.get(f"qty_{pid}", "1") or 1)))
        except ValueError:
            qty = 1
        chosen.append({"id": pid, "name": form.get(f"name_{pid}", pid), "qty": qty})
    return ref, chosen


@router.post("/setup", response_class=HTMLResponse)
async def setup(request: Request):
    ref, chosen = await _selected_parts(request)
    if not chosen:
        return render(request, "error.html", message="Choose at least one part to print.", status_code=400)
    runner = request.app.state.runner
    return render(request, "setup.html", ref=ref, chosen=chosen,
                  defaults=request.app.state.presets.defaults,
                  printer_enabled=runner.printer.enabled,
                  trays=[t for t in getattr(runner.printer.state, "trays", ()) if not t.empty])


@router.post("/jobs")
async def create_job(request: Request):
    ref, chosen = await _selected_parts(request)
    form = await request.form()
    presets = request.app.state.presets
    if not chosen:
        return render(request, "error.html", message="Choose at least one part to print.", status_code=400)
    preset = form.get("preset", "")
    if preset not in presets.presets:
        return render(request, "error.html", message=f"Unknown preset {preset!r}.", status_code=400)
    try:
        overrides = presets.defaults.merged({k: form.get(k) for k in ("infill", "copies") if k in form}
                                            | {"supports": form.get("supports", "")})
    except ValueError as e:
        return render(request, "error.html", message=str(e), status_code=400)
    parts = [PartRef(did=ref["did"], wvm=ref["wvm"], wvmid=ref["wvmid"], eid=ref["eid"],
                     part_id=c["id"], name=c["name"], qty=c["qty"])
             for c in chosen]
    auto_start = str(form.get("auto_start", "")).lower() in {"1", "on", "true", "yes"}
    slot = str(form.get("slot", "") or "").strip().upper() or None
    if slot and not re.fullmatch(r"[A-D][1-4]", slot):
        return render(request, "error.html", message=f"Unknown filament slot {slot!r}.", status_code=400)
    job = request.app.state.runner.submit(JobSpec(parts=parts, preset=preset, overrides=overrides,
                                                  auto_start=auto_start, slot=slot))
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def history(request: Request):
    return render(request, "history.html", jobs=request.app.state.db.list_recent(100))


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = request.app.state.db.get(job_id)
    if job is None:
        return render(request, "error.html", message="No such job.", status_code=404)
    return render(request, "job.html", job=job, stages=_stages_for(job), printer=_printer_ctx(request),
                  queue_position=request.app.state.runner.queue_position(job.id),
                  tray=request.app.state.runner.tray(job.spec.slot))


@router.get("/jobs/{job_id}/fragment", response_class=HTMLResponse)
def job_fragment(request: Request, job_id: str):
    job = request.app.state.db.get(job_id)
    if job is None:
        return render(request, "error.html", message="No such job.", status_code=404)
    return render(request, "_job_fragment.html", job=job, stages=_stages_for(job),
                  printer=_printer_ctx(request),
                  queue_position=request.app.state.runner.queue_position(job.id))


@router.post("/jobs/{job_id}/rerun")
def rerun(request: Request, job_id: str):
    try:
        job = request.app.state.runner.rerun(job_id)
    except KeyError:
        return render(request, "error.html", message="No such job.", status_code=404)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@router.get("/printer/strip", response_class=HTMLResponse)
def printer_strip(request: Request):
    return render(request, "_printer_strip.html", printer=_printer_ctx(request))


@router.post("/printer/bed-cleared", response_class=HTMLResponse)
def bed_cleared(request: Request):
    request.app.state.db.set_flag("bed_cleared", True)
    return render(request, "_printer_strip.html", printer=_printer_ctx(request))
