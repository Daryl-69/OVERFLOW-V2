"""Lambda entry points (one container image, one ``CMD`` per function).

=====================  =====================================  ==============================
Function               Trigger                                Does
=====================  =====================================  ==============================
``api``                API Gateway HTTP API                   dashboard + household API,
                                                              presigned uploads, approve/reject
``reader``             S3 ``ObjectCreated`` on ``photos/``    read the card photo, store the
                                                              reading (DynamoDB)
``locate``             DynamoDB stream on Readings (red/      re-score the ward; open an
                       amber filter) or Step Functions        incident -> ``StartExecution``
``agent``              Step Functions ``waitForTaskToken``    run the incident agent, park the
                                                              task token for the engineer
``incident_status``    Step Functions                         prepare alerts / mark notified /
                                                              mark expired
=====================  =====================================  ==============================
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import uuid

from . import common
from .common import http, log

HOUSEHOLD_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KEY_RE = re.compile(r"^photos/[A-Za-z0-9_-]{1,32}/[0-9a-f-]{36}\.jpg$")
ANSWER_RE = re.compile(r"^[a-z_]{1,20}$")
UPLOAD_TTL_S = 300


def _start_incident(incident: dict, state: dict) -> None:
    from ..intake.workflow import StepFunctionsWorkflow
    StepFunctionsWorkflow(common.store(), os.environ["UPSTREAM_STATE_MACHINE_ARN"],
                          client=common.boto("stepfunctions")).start(incident)


# ------------------------------------------------------------------ reader
def reader(event, context=None):
    """S3 ObjectCreated -> read_photo -> reading in DynamoDB; result parked for the phone."""
    from ..intake.live import IntakeError
    s3 = common.boto("s3")
    svc = common.live(open_incidents=False)
    out = []
    for rec in event.get("Records", []):
        bucket = rec["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(rec["s3"]["object"]["key"])
        if not KEY_RE.match(key):
            log.warning("ignoring unexpected key %s", key)
            continue
        obj = s3.get_object(Bucket=bucket, Key=key)
        meta = obj.get("Metadata", {})
        household = key.split("/")[1]
        household = None if household == "_" else household
        answer = meta.get("answer") or None
        try:
            res = svc.submit_photo(obj["Body"].read(), household=household, answer=answer, source="photo")
            result = {"ok": True, **{k: res[k] for k in ("level", "reasons", "message", "advisory")},
                      "report": res["report"]}
        except IntakeError as e:
            result = {"ok": False, "error": str(e), **e.detail}
        common.store().set_meta(f"result#{key}", common.dumps(result))
        out.append({"key": key, "ok": result["ok"]})
    return {"processed": out}


# ------------------------------------------------------------------ locate
def locate(event, context=None):
    """Stream mode: re-score, maybe open an incident. Step Functions mode
    (``{"incident_id": ...}``): return the triangulation for the workflow."""
    if "incident_id" in event:
        svc = common.live(open_incidents=False)
        st = svc.state()
        post = st.get("posterior") or {}
        inc = common.store().get_incident(event["incident_id"])
        if inc is None:
            raise ValueError(f"unknown incident {event['incident_id']}")
        return {"incident_id": inc["id"], "p_breach": post.get("p_breach"), "dig": post.get("dig"),
                "region_length_m": post.get("region_length_m"), "n_advisories": len(st.get("advisories", []))}
    svc = common.live(open_incidents=True, on_incident=_start_incident)
    st = svc.state()
    inc = st.get("incident") or {}
    return {"alert": st.get("alert"), "incident": inc.get("id") if inc.get("open") else None}


# ------------------------------------------------------------------- agent
def agent(event, context=None):
    """Step Functions task (``.waitForTaskToken``): the execution resumes only
    when the engineer approves or rejects via the API."""
    from ..agent import run_incident_agent
    plan = run_incident_agent(common.live(open_incidents=False), event["incident_id"],
                              task_token=event["task_token"])
    return {"incident_id": event["incident_id"], "agent": plan.get("agent"), "status": "awaiting_approval"}


# --------------------------------------------------------- incident status
def incident_status(event, context=None):
    from ..intake.workflow import outbox
    st = common.store()
    iid, action = event["incident_id"], event["action"]
    inc = st.get_incident(iid)
    if inc is None:
        raise ValueError(f"unknown incident {iid}")
    if action == "prepare":
        plan = inc.get("plan", {})
        strip = "\n".join(plan.get("strip_message", {}).values())
        msgs = []
        for m in outbox(plan):
            if m["kind"] == "advisory":
                text = "\n".join(t for t in (m.get("en"), m.get("hi")) if t)
            else:
                text = strip
            msgs.append({"household": m["household"], "kind": m["kind"], "text": text})
        return {"incident_id": iid, "messages": msgs, "count": len(msgs)}
    if action == "notified":
        inc = st.update_incident(iid, status="notified", notified_count=event.get("count", 0))
        return {"incident_id": iid, "status": inc["status"]}
    if action == "expired":
        inc = st.update_incident(iid, status="closed", decision={"approved": False, "engineer": "timeout",
                                                                 "note": "no decision within the approval window"})
        return {"incident_id": iid, "status": inc["status"]}
    raise ValueError(f"unknown action {action}")


# --------------------------------------------------------------------- api
def _public(inc: dict) -> dict:
    return {k: v for k, v in inc.items() if k != "task_token"}


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _presign(body: dict) -> dict:
    household = (body.get("household") or "").strip() or "_"
    if household != "_" and not HOUSEHOLD_RE.match(household):
        return http(400, {"error": "invalid household id"})
    if household != "_" and household not in common.live().households:
        return http(404, {"error": f"unknown household {household}"})
    answer = body.get("answer") or ""
    if answer and not ANSWER_RE.match(answer):
        return http(400, {"error": "invalid answer"})
    key = f"photos/{household}/{uuid.uuid4()}.jpg"
    meta = {"answer": answer} if answer else {}
    url = common.boto("s3").generate_presigned_url(
        "put_object", ExpiresIn=UPLOAD_TTL_S,
        Params={"Bucket": os.environ["UPSTREAM_PHOTO_BUCKET"], "Key": key, "ContentType": "image/jpeg",
                "Metadata": meta})
    headers = {"Content-Type": "image/jpeg", **{f"x-amz-meta-{k}": v for k, v in meta.items()}}
    return http(200, {"url": url, "key": key, "headers": headers, "expires_in": UPLOAD_TTL_S})


def _decide(iid: str, body: dict, headers: dict, approve: bool) -> dict:
    from ..intake.workflow import StepFunctionsWorkflow, WorkflowError
    key = os.environ.get("UPSTREAM_ENGINEER_KEY")
    if key and headers.get("x-engineer-key") != key:
        return http(401, {"error": "engineer key required"})
    engineer = str(body.get("engineer") or "").strip()[:80]
    if not engineer:
        return http(400, {"error": "engineer name required"})
    flow = StepFunctionsWorkflow(common.store(), os.environ["UPSTREAM_STATE_MACHINE_ARN"],
                                 client=common.boto("stepfunctions"))
    try:
        inc = (flow.approve if approve else flow.reject)(iid, engineer, str(body.get("note") or "")[:1000])
    except WorkflowError as e:
        return http(e.status, {"error": str(e)})
    return http(200, _public(inc))


def api(event, context=None):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    qs = event.get("queryStringParameters") or {}
    try:
        if method == "OPTIONS":
            return http(204, {})
        if path == "/api/health":
            return http(200, {"service": "upstream", "ok": True, "cloud": True,
                              "engineer_key": bool(os.environ.get("UPSTREAM_ENGINEER_KEY"))})
        if path == "/api/live" and method == "GET":
            return http(200, common.live().state())
        if path == "/api/households" and method == "GET":
            svc = common.live()
            st = svc.sc.streets
            return http(200, [h.public(st) | {"arrival_min": svc.arrival[h.id]} for h in svc.sc.world.households])
        if path == "/api/presign" and method == "POST":
            return _presign(_body(event))
        if path == "/api/result" and method == "GET":
            key = qs.get("key", "")
            if not KEY_RE.match(key):
                return http(400, {"error": "bad key"})
            res = common.store().get_meta(f"result#{key}")
            return http(200, json.loads(res)) if res else http(202, {"pending": True})
        if path == "/api/incidents" and method == "GET":
            return http(200, [_public(i) for i in common.store().list_incidents()])
        m = re.match(r"^/api/incidents/([A-Za-z0-9-]{1,40})(?:/(approve|reject))?$", path)
        if m:
            iid, verb = m.groups()
            if verb and method == "POST":
                return _decide(iid, _body(event), headers, verb == "approve")
            if not verb and method == "GET":
                inc = common.store().get_incident(iid)
                return http(200, _public(inc)) if inc else http(404, {"error": "unknown incident"})
        return http(404, {"error": f"no route {method} {path}"})
    except Exception:  # noqa: BLE001 - never leak a stack trace to the browser
        log.exception("api error on %s %s", method, path)
        return http(500, {"error": "internal error"})
