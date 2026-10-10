from fastapi.testclient import TestClient

from cad2print.app import create_app


def test_healthz_reports_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFF_DIR", str(tmp_path / "handoff"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    client = TestClient(create_app())

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
