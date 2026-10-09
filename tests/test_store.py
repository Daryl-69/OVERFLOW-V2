import pytest

from upstream.intake.store import Store
from upstream.reports import Report


@pytest.fixture(params=["sqlite", "dynamo"])
def store(request):
    if request.param == "sqlite":
        yield Store(":memory:")
        return
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from upstream.intake.dynamo import DynamoStore
    with moto.mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        for name, key in (("R", "id"), ("M", "key"), ("I", "id")):
            ddb.create_table(TableName=name, KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                             BillingMode="PAY_PER_REQUEST")
        yield DynamoStore(ddb, readings="R", meta="M", incidents="I")


def _report(store, cycle, hid, t, **kw):
    return Report(id=store.next_report_id(cycle), household=hid, cycle=cycle, supply_start=360.0, t_obs=t,
                  source="manual", **kw)


def test_store_contract(store):
    assert store.current_supply() is None and store.get_meta("epoch") is None
    store.set_meta("epoch", "2025-01-01T00:00:00+05:30")
    assert store.get_meta("epoch") == "2025-01-01T00:00:00+05:30"
    assert store.add_supply(360.0, "2025-01-01T06:00:00") == 0
    assert store.add_supply(1800.5, "2025-01-02T06:00:30") == 1
    cur = store.current_supply()
    assert cur["cycle"] == 1 and cur["start_min"] == 1800.5

    a = _report(store, 0, "H-0001", 380.0, turbidity=0.12, chlorine=0.4)
    store.add_report(a, "t", sha256="abc", phash="ff00", taken_at="2025:01:01 06:20:00")
    b = _report(store, 1, "H-0002", 1820.0, turbidity=0.8, chlorine=None, reading={"ok": True}, flags=["x"])
    store.add_report(b, "t")
    assert (a.id, b.id) == ("L000-001", "L001-001")
    assert store.next_report_id(1) == "L001-002"

    b.level, b.reasons = "red", ["turbid"]
    store.update_levels([b])
    got = store.reports(min_cycle=1)
    assert len(got) == 1 and got[0].level == "red" and got[0].reasons == ["turbid"]
    assert got[0].reading == {"ok": True} and got[0].flags == ["x"] and got[0].chlorine is None
    assert [r.id for r in store.reports()] == [a.id, b.id]
    assert store.reports()[0].turbidity == 0.12

    assert store.set_answer(a.id, "smell") and not store.set_answer("nope", "smell")
    assert store.rows()[0]["answer"] == "smell"
    assert store.find_photo(sha256="abc")[0]["household"] == "H-0001"
    assert store.find_photo(taken_at="2025:01:01 06:20:00")[0]["id"] == a.id
    assert store.phashes() == [{"id": a.id, "household": "H-0001", "cycle": 0, "phash": "ff00"}]

    store.put_incident({"id": "INC-1", "status": "open", "opened_at": "2025-01-02T07:00", "p_breach": 0.62,
                        "cluster": ["H-0002"], "dig": {"lat": 22.76, "lon": 75.84}})
    inc = store.update_incident("INC-1", status="awaiting_approval", task_token="tok")
    assert store.get_incident("INC-1")["task_token"] == "tok" and inc["p_breach"] == 0.62
    assert store.get_incident("INC-1")["dig"]["lat"] == 22.76
    with pytest.raises(ValueError):
        store.update_incident("INC-1", status="bogus")
    assert [i["id"] for i in store.list_incidents()] == ["INC-1"]

    store.set_session("+91999", a.id)
    assert store.session("+91999") == a.id and store.session("+1") is None
    store.reset()
    assert store.reports() == [] and store.current_supply() is None and store.list_incidents() == []
