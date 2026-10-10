"""Onshape browsing: search → document → part studios → parts, plus paste-a-URL."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..app import render
from ..onshape import OnshapeError
from ..urlparse import parse_onshape_url

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    recent = request.app.state.db.list_recent(8)
    return render(request, "index.html", recent=recent)


@router.get("/documents", response_class=HTMLResponse)
def documents(request: Request, q: str = ""):
    try:
        docs = request.app.state.onshape.search_documents(q)
    except OnshapeError as e:
        return render(request, "error.html", message=str(e), status_code=502)
    return render(request, "documents.html", q=q, docs=docs)


@router.get("/d/{did}", response_class=HTMLResponse)
def document(request: Request, did: str, wid: str = ""):
    versions = request.app.state.onshape.versions(did)
    return render(request, "document.html", did=did, wid=wid, versions=versions)


@router.get("/d/{did}/{wvm}/{wvmid}/elements", response_class=HTMLResponse)
def elements(request: Request, did: str, wvm: str, wvmid: str):
    studios = request.app.state.onshape.part_studios(did, wvm, wvmid)
    return render(request, "elements.html", did=did, wvm=wvm, wvmid=wvmid, studios=studios)


@router.get("/d/{did}/{wvm}/{wvmid}/e/{eid}/parts", response_class=HTMLResponse)
def parts(request: Request, did: str, wvm: str, wvmid: str, eid: str):
    try:
        parts = request.app.state.onshape.parts(did, wvm, wvmid, eid)
    except OnshapeError as e:
        return render(request, "error.html", message=str(e), status_code=502)
    return render(request, "parts.html", did=did, wvm=wvm, wvmid=wvmid, eid=eid, parts=parts)


@router.post("/open-url")
def open_url(request: Request, url: str = Form(...)):
    try:
        ref = parse_onshape_url(url)
    except ValueError as e:
        return render(request, "error.html", message=str(e), status_code=400)
    base = f"/d/{ref.did}/{ref.wvm}/{ref.wvmid}"
    target = f"{base}/e/{ref.eid}/parts" if ref.eid else f"{base}/elements"
    return RedirectResponse(target, status_code=303)


@router.get("/thumb/d/{did}/{wvm}/{wvmid}/e/{eid}")
def thumb(request: Request, did: str, wvm: str, wvmid: str, eid: str):
    try:
        png = request.app.state.onshape.thumbnail(did, wvm, wvmid, eid)
    except OnshapeError:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})
