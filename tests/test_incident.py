from fastapi.testclient import TestClient

from upstream.incident import decide, incident_id, pct, red_cluster
from upstream.intake.app import create_app

POS = {"A": (0.0, 0.0), "B": (400.0, 0.0), "C": (0.0, 900.0), "D": (5000.0, 0.0), "E": (5300.0, 0.0)}


def test_pct():
    assert pct(0.734) == "73%" and pct(0.25, 1) == "25.0%" and pct(None) == "n/a"


def test_cluster_needs_three_close_red_homes():
    assert red_cluster(["A", "B", "D", "E"], POS) == ["A", "B"]
    assert red_cluster(["A", "B", "C"], POS) == ["A", "B", "C"]
    d = decide(["A", "B", "D", "E"], POS, 0.9)
    assert not d.open and "need 3" in d.reason


def test_breach_probability_gate():
    assert not decide(["A", "B", "C"], POS, 0.2).open
    assert not decide(["A", "B", "C"], POS, None).open
    d = decide(["A", "B", "C"], POS, 0.25)
    assert d.open and d.cluster == ["A", "B", "C"] and d.to_dict()["rule"]["min_red"] == 3


def test_incident_id_is_stable_per_cycle():
    assert incident_id(2, "2025-01-01T00:00") == incident_id(2, "2025-01-01T00:00")
    assert incident_id(2, "2025-01-01T00:00") != incident_id(3, "2025-01-01T00:00")


def test_live_service_opens_one_incident(tmp_path, scenario_file):
    app = create_app(var_dir=tmp_path, scenario_path=scenario_file)
    opened = []
    app.state.live.on_incident = lambda inc, state: opened.append(inc)
    with TestClient(app) as c:
        sc = c.get("/api/scenario").json()
        homes = sc["truth"]["downstream_households"]
        c.post("/api/reports", json={"household": homes[0], "turbidity": 0.9, "chlorine": 0.0})
        live = c.get("/api/live").json()
        assert live["alert"] and not live["incident"]["open"] and opened == []
        for h in homes[1:4]:
            c.post("/api/reports", json={"household": h, "turbidity": 0.9, "chlorine": 0.0})
        live = c.get("/api/live").json()
        assert live["incident"]["open"] and live["incident"]["id"].startswith("INC-")
        assert len(opened) == 1 and opened[0]["id"] == live["incident"]["id"]
        # re-scoring after another report keeps the same incident, no second start
        c.post("/api/reports", json={"household": homes[4], "turbidity": 0.9, "chlorine": 0.0})
        assert c.get("/api/live").json()["incident"]["id"] == opened[0]["id"] and len(opened) == 1
