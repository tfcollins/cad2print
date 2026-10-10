"""Parse pasted Onshape document URLs into (did, wvm, wvmid, eid)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_PATH = re.compile(
    r"^/documents/(?P<did>[0-9a-f]{24})/(?P<wvm>[wvm])/(?P<wvmid>[0-9a-f]{24})"
    r"(?:/e/(?P<eid>[0-9a-f]{24}))?/?$"
)


@dataclass(frozen=True)
class OnshapeRef:
    did: str
    wvm: str  # "w" workspace, "v" version, "m" microversion
    wvmid: str
    eid: str | None = None


def parse_onshape_url(url: str) -> OnshapeRef:
    """Return the ids embedded in an Onshape document URL; raise ValueError if it is not one."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not (
        host == "onshape.com" or host.endswith(".onshape.com")
    ):
        raise ValueError(f"not an Onshape URL: {url!r}")
    m = _PATH.match(parts.path)
    if not m:
        raise ValueError(f"unrecognised Onshape document path: {parts.path!r}")
    return OnshapeRef(did=m["did"], wvm=m["wvm"], wvmid=m["wvmid"], eid=m["eid"])
