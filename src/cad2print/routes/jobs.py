"""Print setup → job creation → live status → history / re-run."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..app import render
from ..db import JobSpec, PartRef

router = APIRouter()

STAGES = [("exporting", "Exporting from Onshape"), ("slicing", "Slicing"),
          ("handing_off", "Handing to Bambu Connect"), ("done", "Ready to print")]


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
    return render(request, "setup.html", ref=ref, chosen=chosen,
                  defaults=request.app.state.presets.defaults)


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
    job = request.app.state.runner.submit(JobSpec(parts=parts, preset=preset, overrides=overrides))
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def history(request: Request):
    return render(request, "history.html", jobs=request.app.state.db.list_recent(100))


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = request.app.state.db.get(job_id)
    if job is None:
        return render(request, "error.html", message="No such job.", status_code=404)
    return render(request, "job.html", job=job, stages=STAGES)


@router.get("/jobs/{job_id}/fragment", response_class=HTMLResponse)
def job_fragment(request: Request, job_id: str):
    job = request.app.state.db.get(job_id)
    if job is None:
        return render(request, "error.html", message="No such job.", status_code=404)
    return render(request, "_job_fragment.html", job=job, stages=STAGES)


@router.post("/jobs/{job_id}/rerun")
def rerun(request: Request, job_id: str):
    try:
        job = request.app.state.runner.rerun(job_id)
    except KeyError:
        return render(request, "error.html", message="No such job.", status_code=404)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)
