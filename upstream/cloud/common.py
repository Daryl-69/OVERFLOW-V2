"""Shared Lambda plumbing: one cached live service per container.

Every handler in this package runs from the same container image. On cold
start the pickled ward is loaded (``UPSTREAM_WARD_PATH``, baked into the image
by the Dockerfile) and wrapped in a :class:`LiveService` backed by DynamoDB, so
cloud scoring is byte-for-byte the code that runs locally.

Environment (set by ``aws/template.yaml``):

* ``UPSTREAM_WARD_PATH``          — pickled live ward (default ``/var/task/ward.pkl``)
* ``UPSTREAM_PHOTO_BUCKET``       — S3 bucket for first-glass photos
* ``UPSTREAM_READINGS_TABLE`` / ``UPSTREAM_META_TABLE`` / ``UPSTREAM_INCIDENTS_TABLE``
* ``UPSTREAM_STATE_MACHINE_ARN``  — IncidentFlow state machine
* ``UPSTREAM_ALERT_TOPIC_ARN``    — SNS topic for approved alerts
* ``UPSTREAM_TZ``                 — ward time zone (``Asia/Kolkata``)
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal

log = logging.getLogger("upstream.cloud")
log.setLevel(logging.INFO)

_cache: dict = {}


def ward():
    if "ward" not in _cache:
        from ..ward import load_ward
        _cache["ward"] = load_ward(os.environ.get("UPSTREAM_WARD_PATH", "/var/task/ward.pkl"))
    return _cache["ward"]


def store():
    if "store" not in _cache:
        from ..intake.dynamo import DynamoStore
        _cache["store"] = DynamoStore()
    return _cache["store"]


def live(open_incidents: bool = False, on_incident=None):
    """A live service on the cached ward and DynamoDB. Scoring state is not
    cached between invocations: other Lambdas write readings concurrently."""
    from ..intake.live import LiveService
    return LiveService(ward(), store(), "/tmp/photos", on_incident=on_incident, open_incidents=open_incidents)


def boto(service: str):
    key = f"boto:{service}"
    if key not in _cache:
        import boto3
        _cache[key] = boto3.client(service)
    return _cache[key]


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(obj) -> str:
    return json.dumps(obj, default=_default, separators=(",", ":"))


def http(status: int, body, headers: dict | None = None) -> dict:
    """An API Gateway (HTTP API, payload v2) response with CORS for the Amplify site."""
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json",
                        "Access-Control-Allow-Origin": os.environ.get("UPSTREAM_CORS_ORIGIN", "*"),
                        **(headers or {})},
            "body": dumps(body)}
