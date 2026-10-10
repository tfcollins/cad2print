"""Thin Onshape REST client (API-key basic auth, /api/v9).

Only the handful of calls cad2print needs. Responses are reduced to plain dicts so the rest of
the app never sees Onshape's field names.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

_THUMB_TTL_S = 600


class OnshapeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Onshape HTTP {status}: {message}")
        self.status = status


class OnshapeClient:
    def __init__(self, access_key: str, secret_key: str, base_url: str = "https://cad.onshape.com",
                 timeout_s: float = 60.0):
        self._auth = httpx.BasicAuth(access_key, secret_key)
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(
            auth=self._auth,
            timeout=timeout_s,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        self._thumbs: dict[str, tuple[float, bytes]] = {}

    # -- helpers -----------------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None, accept: str | None = None) -> httpx.Response:
        headers = {"Accept": accept} if accept else None
        try:
            resp = self._http.get(f"{self._base}/api/v9{path}", params=params, headers=headers)
            if resp.is_redirect:
                # The STL export 307s to another Onshape host; httpx drops auth across hosts, so
                # follow the hop by hand with the same credentials.
                resp = self._http.get(resp.headers["location"], auth=self._auth, headers=headers)
        except httpx.HTTPError as e:
            raise OnshapeError(0, f"{type(e).__name__}: {e}") from e
        if resp.status_code >= 400:
            raise OnshapeError(resp.status_code, _snippet(resp))
        return resp

    # -- browse ------------------------------------------------------------------------------

    def search_documents(self, q: str = "", limit: int = 20) -> list[dict]:
        data = self._get("/documents", {"q": q, "filter": 0, "limit": limit}).json()
        return [
            {
                "id": d["id"],
                "name": d["name"],
                "workspace_id": d.get("defaultWorkspace", {}).get("id"),
                "modified_at": d.get("modifiedAt"),
            }
            for d in data.get("items", [])
        ]

    def versions(self, did: str) -> list[dict]:
        data = self._get(f"/documents/d/{did}/versions").json()
        return [{"id": v["id"], "name": v["name"]} for v in data]

    def part_studios(self, did: str, wvm: str, wvmid: str) -> list[dict]:
        data = self._get(
            f"/documents/d/{did}/{wvm}/{wvmid}/elements", {"elementType": "PARTSTUDIO"}
        ).json()
        return [{"id": e["id"], "name": e["name"]} for e in data]

    def parts(self, did: str, wvm: str, wvmid: str, eid: str) -> list[dict]:
        data = self._get(f"/parts/d/{did}/{wvm}/{wvmid}/e/{eid}").json()
        return [
            {"id": p["partId"], "name": p["name"]}
            for p in data
            if p.get("bodyType") == "solid" and not p.get("isHidden")
        ]

    def thumbnail(self, did: str, wvm: str, wvmid: str, eid: str, size: str = "300x170") -> bytes:
        return self._cached_png(f"/thumbnails/d/{did}/{wvm}/{wvmid}/e/{eid}/s/{size}")

    def document_thumbnail(self, did: str, wid: str, size: str = "300x170") -> bytes:
        return self._cached_png(f"/thumbnails/d/{did}/w/{wid}/s/{size}")

    def _cached_png(self, path: str) -> bytes:
        hit = self._thumbs.get(path)
        now = time.monotonic()
        if hit and now - hit[0] < _THUMB_TTL_S:
            return hit[1]
        png = self._get(path, accept="image/png").content
        self._thumbs[path] = (now, png)
        return png

    # -- export ------------------------------------------------------------------------------

    def export_stl(self, did: str, wvm: str, wvmid: str, eid: str, part_id: str, dest: Path) -> Path:
        resp = self._get(
            f"/parts/d/{did}/{wvm}/{wvmid}/e/{eid}/partid/{part_id}/stl",
            {"mode": "binary", "units": "millimeter"},
            accept="application/vnd.onshape.v1+octet-stream",
        )
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest


def _snippet(resp: httpx.Response, limit: int = 200) -> str:
    try:
        body = resp.json()
        msg = body.get("message") if isinstance(body, dict) else None
        if msg:
            return str(msg)[:limit]
    except ValueError:
        pass
    return resp.text[:limit]
