import re

from tests.conftest import DID, EID, WID

FORM = {"did": DID, "wvm": "w", "wvmid": WID, "eid": EID, "part": ["JHD", "JHE"],
        "name_JHD": "Plate", "name_JHE": "Lid", "qty_JHD": "2", "qty_JHE": "1"}


def test_setup_page_shows_chosen_parts_preset_and_overrides(client):
    html = client.post("/setup", data=FORM).text
    assert "Plate" in html and "Lid" in html and ("&times;2" in html or "×2" in html)
    assert 'name="preset"' in html and "H2S PLA 0.20" in html
    assert 'name="infill"' in html and 'value="15"' in html
    assert 'name="supports"' in html and 'name="copies"' in html
    assert 'action="/jobs"' in html


def test_setup_requires_at_least_one_part(client):
    r = client.post("/setup", data={**FORM, "part": []})
    assert r.status_code == 400 and "at least one part" in r.text


def _create(client, **over):
    data = {**FORM, "preset": "h2s-pla-020", "infill": "20", "copies": "1", **over}
    return client.post("/jobs", data=data, follow_redirects=False)


def test_create_job_redirects_to_status_page_and_runs(client, app, fake_onshape):
    r = _create(client, supports="on")
    assert r.status_code == 303
    m = re.fullmatch(r"/jobs/([0-9a-f]{12})", r.headers["location"])
    assert m
    assert app.state.runner.wait_idle(5)
    job = app.state.db.get(m.group(1))
    assert job.status == "done"
    assert fake_onshape.exported == ["JHD", "JHE"]
    assert job.spec.overrides.infill == 20 and job.spec.overrides.supports is True
    assert [(p.name, p.qty) for p in job.spec.parts] == [("Plate", 2), ("Lid", 1)]


def test_create_job_with_bad_override_shows_error(client):
    r = _create(client, infill="250")
    assert r.status_code == 400 and "infill" in r.text


def test_status_page_and_fragment_show_stages_and_handoff(client, app):
    job_id = _create(client).headers["location"].rsplit("/", 1)[1]
    assert app.state.runner.wait_idle(5)
    page = client.get(f"/jobs/{job_id}").text
    assert "Plate" in page and 'hx-get="/jobs/' + job_id + '/fragment"' in page
    frag = client.get(f"/jobs/{job_id}/fragment")
    assert frag.status_code == 200
    assert "Plate_+1.gcode.3mf" in frag.text
    assert "Bambu Connect" in frag.text and "bambu-win" in frag.text
    assert 'hx-trigger' not in frag.text, "terminal state stops polling"


def test_fragment_keeps_polling_while_running(client, app):
    app.state.runner.stop()  # no worker: the job stays queued
    job_id = _create(client).headers["location"].rsplit("/", 1)[1]
    frag = client.get(f"/jobs/{job_id}/fragment").text
    assert 'hx-trigger="every 2s"' in frag and "queued" in frag.lower()


def test_failed_job_fragment_shows_error_and_log_tail(client, app):
    def bad_slice(cmd, expected_output, timeout_s, tail_lines):
        from cad2print.slicer import SliceResult
        return SliceResult(False, None, 3, 0.1, "Error: unknown setting foo", "orca-slicer exited with code 3")
    app.state.runner.slice_fn = bad_slice
    job_id = _create(client).headers["location"].rsplit("/", 1)[1]
    assert app.state.runner.wait_idle(5)
    frag = client.get(f"/jobs/{job_id}/fragment").text
    assert "exited with code 3" in frag and "unknown setting foo" in frag


def test_history_lists_jobs_newest_first_and_rerun_clones(client, app):
    first = _create(client).headers["location"].rsplit("/", 1)[1]
    second = _create(client).headers["location"].rsplit("/", 1)[1]
    assert app.state.runner.wait_idle(5)
    html = client.get("/jobs").text
    assert html.index(second) < html.index(first)
    r = client.post(f"/jobs/{first}/rerun", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    new_id = r.headers["location"].rsplit("/", 1)[1]
    assert new_id != first and app.state.db.get(new_id).spec == app.state.db.get(first).spec


def test_unknown_job_is_404(client):
    assert client.get("/jobs/doesnotexist").status_code == 404


def test_create_job_with_unknown_preset_is_rejected(client):
    r = _create(client, preset="nope")
    assert r.status_code == 400 and "nope" in r.text


def test_hostile_part_name_is_escaped_in_setup_page(client):
    html = client.post("/setup", data={**FORM, "name_JHD": "<script>alert(1)</script>"}).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
