import pytest
from fastapi.testclient import TestClient

from upstream.agent.messages import advisory_text, hindi_time
from upstream.intake.app import create_app
from upstream.intake.workflow import LocalWorkflow


def test_hindi_messages():
    assert hindi_time(19 * 60 + 40) == "शाम 7:40 बजे"
    assert hindi_time(6 * 60 + 5) == "सुबह 6:05 बजे"
    assert "7:40 pm" in advisory_text("advisory", 19 * 60 + 40, "en")
    assert "शाम 7:40 बजे" in advisory_text("advisory", 19 * 60 + 40, "hi")
    assert "उबला" in advisory_text("no_safe_window", None, "hi")
    with pytest.raises(ValueError):
        advisory_text("advisory", 600, "fr")


@pytest.fixture()
def app(tmp_path, scenario_file, monkeypatch):
    monkeypatch.delenv("UPSTREAM_STATE_MACHINE_ARN", raising=False)
    return create_app(var_dir=tmp_path, scenario_path=scenario_file,
                      workflow=LocalWorkflow(sync=True, agent_mode="rules"))


def _open_incident(c):
    homes = c.get("/api/scenario").json()["truth"]["downstream_households"]
    for h in homes[:4]:
        c.post("/api/reports", json={"household": h, "turbidity": 0.9, "chlorine": 0.0})
    incs = c.get("/api/incidents").json()
    assert len(incs) == 1
    return incs[0]


def test_agent_plan_and_approval(app):
    with TestClient(app) as c:
        inc = _open_incident(c)
        assert inc["status"] == "awaiting_approval" and "task_token" not in inc
        plan = inc["plan"]
        assert plan["agent"] == "rules"
        tools = [t["tool"] for t in plan["trace"]]
        assert tools[:3] == ["get_incident_readings", "run_triangulation", "downstream_households"]
        assert {"draft_advisory", "submit_for_approval"} <= set(tools)
        assert plan["downstream"] and len(plan["alerts"]["en"]) == len(plan["alerts"]["hi"])
        assert "बजे" in plan["alerts"]["hi"][0]["text"] or "उबला" in plan["alerts"]["hi"][0]["text"]
        assert plan["summary"] and "red" in plan["summary"]
        assert plan["triangulation"]["p_breach"] >= 0.25

        r = c.post(f"/api/incidents/{inc['id']}/approve", json={"engineer": "A. Sharma", "note": "dig"})
        assert r.status_code == 200, r.text
        done = r.json()
        assert done["status"] == "notified" and done["decision"]["engineer"] == "A. Sharma"
        kinds = {m["kind"] for m in done["notifications"]}
        assert "advisory" in kinds and all(m["hi"] for m in done["notifications"] if m["kind"] == "advisory")
        # a decided incident can't be decided again
        assert c.post(f"/api/incidents/{inc['id']}/reject", json={"engineer": "x"}).status_code == 409


def test_reject_sends_nothing_and_key_is_enforced(app, monkeypatch, tmp_path, scenario_file):
    with TestClient(app) as c:
        inc = _open_incident(c)
        r = c.post(f"/api/incidents/{inc['id']}/reject", json={"engineer": "B", "note": "false alarm"})
        assert r.json()["status"] == "rejected" and "notifications" not in r.json()
        assert c.post("/api/incidents/INC-nope/approve", json={"engineer": "B"}).status_code == 404

    monkeypatch.setenv("UPSTREAM_ENGINEER_KEY", "s3cret")
    keyed = create_app(var_dir=tmp_path / "k", scenario_path=scenario_file,
                       workflow=LocalWorkflow(sync=True, agent_mode="rules"))
    with TestClient(keyed) as c:
        inc = _open_incident(c)
        url = f"/api/incidents/{inc['id']}/approve"
        assert c.post(url, json={"engineer": "C"}).status_code == 401
        assert c.post(url, json={"engineer": "C"}, headers={"X-Engineer-Key": "s3cret"}).status_code == 200
