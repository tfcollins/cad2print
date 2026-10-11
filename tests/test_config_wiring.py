from fastapi.testclient import TestClient

from cad2print.app import create_app
from cad2print.config import Settings


def test_settings_read_bambu_env():
    s = Settings.from_env({"BAMBU_HOST": "10.0.0.130", "BAMBU_SERIAL": "SN1", "BAMBU_ACCESS_CODE": "12345678",
                           "START_TIMEOUT_S": "300"})
    assert (s.bambu_host, s.bambu_serial, s.bambu_access_code, s.start_timeout_s) == ("10.0.0.130", "SN1", "12345678", 300)


def test_app_without_bambu_settings_has_no_printer_and_creates_auto_dir(tmp_path, presets, fake_onshape):
    (tmp_path / "prints").mkdir()
    settings = Settings(handoff_dir=tmp_path / "prints", work_dir=tmp_path / "work",
                        db_path=tmp_path / "db.sqlite", orca_bin="orca")
    app = create_app(settings, onshape=fake_onshape, presets=presets)
    with TestClient(app):
        assert app.state.runner.printer.enabled is False
        assert (tmp_path / "prints" / "auto").is_dir()


def test_app_with_bambu_settings_wires_a_monitor(tmp_path, presets, fake_onshape, monkeypatch):
    import cad2print.printer as printer_mod
    started = []
    monkeypatch.setattr(printer_mod.PrinterMonitor, "start", lambda self: started.append(self.host))
    monkeypatch.setattr(printer_mod.PrinterMonitor, "stop", lambda self: None)
    (tmp_path / "prints").mkdir()
    settings = Settings(handoff_dir=tmp_path / "prints", work_dir=tmp_path / "work", db_path=tmp_path / "db.sqlite",
                        orca_bin="orca", bambu_host="10.0.0.130", bambu_serial="SN1", bambu_access_code="12345678")
    app = create_app(settings, onshape=fake_onshape, presets=presets)
    with TestClient(app):
        assert app.state.runner.printer.enabled is True and started == ["10.0.0.130"]
