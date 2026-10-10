from fastapi.testclient import TestClient

from cad2print.app import create_app
from tests.conftest import DID, EID, WID


def test_healthz_reports_ok(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_index_has_search_and_paste_forms(client):
    html = client.get("/").text
    assert 'name="q"' in html and 'name="url"' in html


def test_document_search_lists_documents_with_thumbnails(client):
    html = client.get("/documents", params={"q": "Rack"}).text
    assert "KVM Rack Mount" in html
    assert f"/d/{DID}" in html
    assert f"/thumb/d/{DID}/w/{WID}" in html


def test_document_page_lists_main_workspace_and_versions(client):
    html = client.get(f"/d/{DID}", params={"wid": WID}).text
    assert f"/d/{DID}/w/{WID}/elements" in html
    assert f"/d/{DID}/v/{'v' * 24}/elements" in html and "Start" in html


def test_elements_page_lists_part_studios(client):
    html = client.get(f"/d/{DID}/w/{WID}/elements").text
    assert "Part Studio 1" in html and f"/d/{DID}/w/{WID}/e/{EID}/parts" in html


def test_parts_page_has_checkbox_and_qty_per_part(client):
    html = client.get(f"/d/{DID}/w/{WID}/e/{EID}/parts").text
    assert "Plate" in html and "Lid" in html
    assert 'name="part"' in html and 'name="qty_JHD"' in html
    assert 'action="/setup"' in html


def test_open_url_redirects_to_parts_page(client):
    r = client.post("/open-url", data={"url": f"https://cad.onshape.com/documents/{DID}/w/{WID}/e/{EID}"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/d/{DID}/w/{WID}/e/{EID}/parts"


def test_open_url_without_element_redirects_to_elements(client):
    r = client.post("/open-url", data={"url": f"https://cad.onshape.com/documents/{DID}/v/{WID}"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/d/{DID}/v/{WID}/elements"


def test_open_url_rejects_garbage_with_message(client):
    r = client.post("/open-url", data={"url": "https://example.com/x"})
    assert r.status_code == 400 and "not an Onshape URL" in r.text


def test_thumbnail_proxy_returns_png(client):
    r = client.get(f"/thumb/d/{DID}/w/{WID}/e/{EID}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.content == b"\x89PNGfake"


def test_document_thumbnail_proxy_returns_png(client):
    r = client.get(f"/thumb/d/{DID}/w/{WID}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.content == b"\x89PNGdoc"


def test_onshape_outage_renders_502_not_a_traceback(client, fake_onshape):
    from cad2print.onshape import OnshapeError

    def down(*a, **k):
        raise OnshapeError(0, "ConnectError: no route to host")

    fake_onshape.search_documents = down
    fake_onshape.versions = down
    fake_onshape.part_studios = down
    for url in ("/documents?q=x", f"/d/{DID}?wid={WID}", f"/d/{DID}/w/{WID}/elements"):
        r = client.get(url)
        assert r.status_code == 502 and "no route to host" in r.text, url


def test_healthz_is_503_when_the_worker_is_dead(app):
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        app.state.runner.stop()
        assert c.get("/healthz").status_code == 503


def test_app_refuses_to_start_without_the_handoff_dir(tmp_path, presets, fake_onshape):
    import pytest
    from cad2print.config import Settings
    settings = Settings(handoff_dir=tmp_path / "not-mounted", work_dir=tmp_path / "work",
                        db_path=tmp_path / "db.sqlite", orca_bin="orca")
    app = create_app(settings, onshape=fake_onshape, presets=presets)
    with pytest.raises(RuntimeError, match="handoff"):
        with TestClient(app):
            pass
