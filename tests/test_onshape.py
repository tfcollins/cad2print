import httpx
import pytest
import respx

from cad2print.onshape import OnshapeClient, OnshapeError

BASE = "https://cad.onshape.com"
DID, WID, EID, PID = "d" * 24, "1" * 24, "e" * 24, "JHD"


@pytest.fixture
def client():
    return OnshapeClient("AK", "SK", BASE)


def _auth_ok(request: httpx.Request) -> bool:
    return request.headers.get("Authorization", "").startswith("Basic ")


@respx.mock
def test_search_documents_returns_id_name_and_thumbnail(client):
    route = respx.get(f"{BASE}/api/v9/documents", params={"q": "Rack", "filter": "0", "limit": "20"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": DID,
                        "name": "KVM Rack Mount",
                        "defaultWorkspace": {"id": WID},
                        "thumbnail": {"href": f"{BASE}/api/thumbnails/d/{DID}/s/300x170"},
                        "modifiedAt": "2026-07-14T11:00:00.000+00:00",
                    }
                ]
            },
        )
    )
    docs = client.search_documents("Rack")
    assert route.called and _auth_ok(route.calls[0].request)
    assert docs == [
        {
            "id": DID,
            "name": "KVM Rack Mount",
            "workspace_id": WID,
            "modified_at": "2026-07-14T11:00:00.000+00:00",
        }
    ]


@respx.mock
def test_versions_lists_name_and_id(client):
    respx.get(f"{BASE}/api/v9/documents/d/{DID}/versions").mock(
        return_value=httpx.Response(200, json=[{"id": "v" * 24, "name": "Start", "type": "version"}])
    )
    assert client.versions(DID) == [{"id": "v" * 24, "name": "Start"}]


@respx.mock
def test_part_studios_filters_by_element_type(client):
    route = respx.get(
        f"{BASE}/api/v9/documents/d/{DID}/w/{WID}/elements", params={"elementType": "PARTSTUDIO"}
    ).mock(
        return_value=httpx.Response(
            200, json=[{"id": EID, "name": "Part Studio 1", "elementType": "PARTSTUDIO"}]
        )
    )
    assert client.part_studios(DID, "w", WID) == [{"id": EID, "name": "Part Studio 1"}]
    assert route.called


@respx.mock
def test_parts_drops_hidden_and_non_solid_bodies(client):
    respx.get(f"{BASE}/api/v9/parts/d/{DID}/v/{WID}/e/{EID}").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"partId": "JHD", "name": "Plate", "bodyType": "solid", "isHidden": False},
                {"partId": "JHE", "name": "Ghost", "bodyType": "solid", "isHidden": True},
                {"partId": "JHF", "name": "Sketchy", "bodyType": "sheet", "isHidden": False},
            ],
        )
    )
    assert client.parts(DID, "v", WID, EID) == [{"id": "JHD", "name": "Plate"}]


@respx.mock
def test_thumbnail_returns_png_bytes(client):
    respx.get(f"{BASE}/api/v9/thumbnails/d/{DID}/w/{WID}/e/{EID}/s/300x170").mock(
        return_value=httpx.Response(200, content=b"\x89PNG...", headers={"content-type": "image/png"})
    )
    assert client.thumbnail(DID, "w", WID, EID) == b"\x89PNG..."


@respx.mock
def test_export_stl_follows_cross_host_redirect_with_auth(client, tmp_path):
    first = respx.get(
        f"{BASE}/api/v9/parts/d/{DID}/w/{WID}/e/{EID}/partid/{PID}/stl",
        params={"mode": "binary", "units": "millimeter"},
    ).mock(
        return_value=httpx.Response(
            307, headers={"location": "https://cad-usw2.onshape.com/modelexport?x=1"}
        )
    )
    second = respx.get("https://cad-usw2.onshape.com/modelexport", params={"x": "1"}).mock(
        return_value=httpx.Response(200, content=b"\x00" * 84 + b"STLDATA")
    )
    dest = tmp_path / "part.stl"

    out = client.export_stl(DID, "w", WID, EID, PID, dest)

    assert out == dest and dest.read_bytes().endswith(b"STLDATA")
    assert first.called and second.called
    assert _auth_ok(second.calls[0].request), "auth must be re-sent on the redirect hop"


@respx.mock
def test_http_error_surfaces_as_onshape_error_with_status(client):
    respx.get(f"{BASE}/api/v9/documents/d/{DID}/versions").mock(
        return_value=httpx.Response(401, json={"message": "Unauthenticated API"})
    )
    with pytest.raises(OnshapeError) as ei:
        client.versions(DID)
    assert ei.value.status == 401 and "Unauthenticated" in str(ei.value)


@respx.mock
def test_export_stl_without_redirect_streams_body(client, tmp_path):
    respx.get(
        f"{BASE}/api/v9/parts/d/{DID}/w/{WID}/e/{EID}/partid/{PID}/stl",
        params={"mode": "binary", "units": "millimeter"},
    ).mock(return_value=httpx.Response(200, content=b"DIRECT"))
    dest = tmp_path / "p.stl"
    client.export_stl(DID, "w", WID, EID, PID, dest)
    assert dest.read_bytes() == b"DIRECT"
