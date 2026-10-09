"""The Lambda handlers end to end on moto (S3 + DynamoDB) and a fake Step Functions."""

import json

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from upstream.cloud import common, handlers  # noqa: E402
from upstream.reader.synth import PhotoParams, render_photo  # noqa: E402
from upstream.ward import build_live_ward, save_ward  # noqa: E402


class FakeSFN:
    class exceptions:
        class ExecutionAlreadyExists(Exception):
            pass

    def __init__(self):
        self.started, self.successes = [], []

    def start_execution(self, stateMachineArn, name, input):
        if any(s["name"] == name for s in self.started):
            raise self.exceptions.ExecutionAlreadyExists()
        self.started.append({"name": name, "input": json.loads(input)})
        return {"executionArn": f"arn:exec:{name}"}

    def send_task_success(self, taskToken, output):
        self.successes.append({"token": taskToken, "output": json.loads(output)})


@pytest.fixture(scope="module")
def ward_file(tmp_path_factory, demo_json):
    p = tmp_path_factory.mktemp("ward") / "ward.pkl"
    save_ward(build_live_ward(demo_json), p)
    return p


@pytest.fixture()
def cloud(ward_file, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("UPSTREAM_WARD_PATH", str(ward_file))
    monkeypatch.setenv("UPSTREAM_PHOTO_BUCKET", "photos-bucket")
    monkeypatch.setenv("UPSTREAM_STATE_MACHINE_ARN", "arn:sm:IncidentFlow")
    monkeypatch.setenv("UPSTREAM_AGENT", "rules")
    monkeypatch.delenv("UPSTREAM_ENGINEER_KEY", raising=False)
    with moto.mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="photos-bucket", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        ddb = boto3.resource("dynamodb")
        from upstream.intake import dynamo
        for name, key in ((dynamo.READINGS_TABLE, "id"), (dynamo.META_TABLE, "key"), (dynamo.INCIDENTS_TABLE, "id")):
            ddb.create_table(TableName=name, KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                             BillingMode="PAY_PER_REQUEST")
        common._cache.clear()
        sfn = FakeSFN()
        common._cache["boto:stepfunctions"] = sfn
        yield s3, sfn
        common._cache.clear()


def api(method, path, body=None, qs=None, headers=None):
    ev = {"requestContext": {"http": {"method": method}}, "rawPath": path, "headers": headers or {},
          "queryStringParameters": qs, "body": json.dumps(body) if body is not None else None}
    r = handlers.api(ev)
    return r["statusCode"], json.loads(r["body"])


def upload(s3, household, turbidity, chlorine, seed, answer=None):
    code, pre = api("POST", "/api/presign", {"household": household, "answer": answer})
    assert code == 200 and pre["key"].startswith(f"photos/{household}/")
    meta = {"answer": answer} if answer else {}
    s3.put_object(Bucket="photos-bucket", Key=pre["key"], Body=render_photo(
        household, PhotoParams(turbidity=turbidity, chlorine=chlorine), seed=seed), Metadata=meta)
    handlers.reader({"Records": [{"s3": {"bucket": {"name": "photos-bucket"}, "object": {"key": pre["key"]}}}]})
    return api("GET", "/api/result", qs={"key": pre["key"]})


def test_cloud_pipeline(cloud, demo_json):
    s3, sfn = cloud
    assert api("GET", "/api/health")[1]["cloud"]
    assert api("POST", "/api/presign", {"household": "../x"})[0] == 400
    assert api("POST", "/api/presign", {"household": "NOPE"})[0] == 404
    assert api("GET", "/api/result", qs={"key": "photos/H-1/" + "0" * 36 + ".jpg"})[0] == 202

    homes = demo_json["truth"]["downstream_households"]
    code, res = upload(s3, homes[0], 60, 0.0, seed=5, answer="smell")
    assert code == 200 and res["ok"] and res["level"] == "red"
    assert res["report"]["answer"] == "smell"
    # the reader never opens incidents; the stream-triggered locate does
    assert api("GET", "/api/incidents")[1] == []
    assert handlers.locate({"Records": []})["incident"] is None

    for i, h in enumerate(homes[1:4]):
        upload(s3, h, 60, 0.0, seed=10 + i)
    out = handlers.locate({"Records": [{"eventSource": "aws:dynamodb"}]})
    iid = out["incident"]
    assert iid and [s["name"] for s in sfn.started] == [iid]
    handlers.locate({"Records": []})  # stream retry: no second execution
    assert len(sfn.started) == 1

    tri = handlers.locate({"incident_id": iid})
    assert tri["p_breach"] >= 0.25 and tri["dig"]
    assert handlers.agent({"incident_id": iid, "task_token": "TOKEN-1"})["status"] == "awaiting_approval"
    code, inc = api("GET", f"/api/incidents/{iid}")
    assert inc["status"] == "awaiting_approval" and "task_token" not in inc and inc["plan"]["alerts"]["hi"]

    assert api("POST", f"/api/incidents/{iid}/approve", {})[0] == 400
    code, inc = api("POST", f"/api/incidents/{iid}/approve", {"engineer": "A. Sharma"})
    assert code == 200 and inc["status"] == "approved"
    assert sfn.successes == [{"token": "TOKEN-1", "output": sfn.successes[0]["output"]}]
    assert sfn.successes[0]["output"]["approved"] is True

    prep = handlers.incident_status({"incident_id": iid, "action": "prepare"})
    assert prep["count"] == len(prep["messages"]) > 0
    adv = next(m for m in prep["messages"] if m["kind"] == "advisory")
    assert "Upstream water alert" in adv["text"] and "अपस्ट्रीम" in adv["text"]
    assert handlers.incident_status({"incident_id": iid, "action": "notified", "count": prep["count"]})["status"] == "notified"
    assert api("POST", f"/api/incidents/{iid}/reject", {"engineer": "B"})[0] == 409
    assert api("GET", "/api/nope")[0] == 404
