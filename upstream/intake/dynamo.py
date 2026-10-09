"""DynamoDB implementation of :class:`upstream.intake.store.BaseStore`.

Three tables (names from the SAM template, overridable by environment):

* ``Readings``  — one item per report, key ``id``; GSI-free, a ward has only
  hundreds of readings per window so a filtered scan is cheap and simple.
* ``Meta``      — key ``key``: meta values, supply cycles (``supply#000``),
  atomic counters (``counter#...``) and WhatsApp sessions (``wa#...``).
* ``Incidents`` — key ``id``: incident records, including the Step Functions
  task token while an incident awaits engineer approval.

Floats are stored as ``Decimal`` (DynamoDB has no float type); list/dict
fields are stored as JSON strings so the item shape matches the SQLite rows.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

from ..reports import Report
from .store import BaseStore

READINGS_TABLE = os.environ.get("UPSTREAM_READINGS_TABLE", "UpstreamReadings")
META_TABLE = os.environ.get("UPSTREAM_META_TABLE", "UpstreamMeta")
INCIDENTS_TABLE = os.environ.get("UPSTREAM_INCIDENTS_TABLE", "UpstreamIncidents")


def _to_ddb(v):
    if isinstance(v, float):
        return Decimal(repr(v))
    if isinstance(v, dict):
        return {k: _to_ddb(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [_to_ddb(x) for x in v]
    return v


def _from_ddb(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 and "." not in str(v) else float(v)
    if isinstance(v, dict):
        return {k: _from_ddb(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_ddb(x) for x in v]
    return v


class DynamoStore(BaseStore):
    def __init__(self, resource=None, readings: str = READINGS_TABLE, meta: str = META_TABLE,
                 incidents: str = INCIDENTS_TABLE) -> None:
        if resource is None:
            import boto3
            resource = boto3.resource("dynamodb")
        self.readings = resource.Table(readings)
        self.meta = resource.Table(meta)
        self.incidents = resource.Table(incidents)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _scan(table, **kw) -> list[dict]:
        items, start = [], None
        while True:
            if start:
                kw["ExclusiveStartKey"] = start
            resp = table.scan(**kw)
            items.extend(resp.get("Items", []))
            start = resp.get("LastEvaluatedKey")
            if not start:
                return [_from_ddb(i) for i in items]

    def _counter(self, name: str) -> int:
        """Atomically increment and return a counter (first value is 1)."""
        resp = self.meta.update_item(Key={"key": f"counter#{name}"}, UpdateExpression="ADD n :one",
                                     ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
        return int(resp["Attributes"]["n"])

    # --------------------------------------------------------------- meta
    def get_meta(self, key: str) -> str | None:
        item = self.meta.get_item(Key={"key": f"meta#{key}"}).get("Item")
        return item["value"] if item else None

    def set_meta(self, key: str, value: str) -> None:
        self.meta.put_item(Item={"key": f"meta#{key}", "value": value})

    # ------------------------------------------------------------- supply
    def add_supply(self, start_min: float, started_at: str) -> int:
        cycle = self._counter("supply") - 1
        self.meta.put_item(Item=_to_ddb({"key": f"supply#{cycle:06d}", "cycle": cycle,
                                         "start_min": float(start_min), "started_at": started_at}))
        self.meta.put_item(Item={"key": "supply#current", "cycle": cycle})
        return cycle

    def current_supply(self) -> dict | None:
        cur = self.meta.get_item(Key={"key": "supply#current"}).get("Item")
        if not cur:
            return None
        item = self.meta.get_item(Key={"key": f"supply#{int(cur['cycle']):06d}"}).get("Item")
        if not item:
            return None
        d = _from_ddb(item)
        return {"cycle": int(d["cycle"]), "start_min": float(d["start_min"]), "started_at": d["started_at"]}

    # ------------------------------------------------------------ reports
    def next_report_id(self, cycle: int) -> str:
        return f"L{cycle:03d}-{self._counter(f'reports#{cycle}'):03d}"

    def add_report(self, r: Report, received_at: str, photo: str | None = None, sha256: str | None = None,
                   phash: str | None = None, taken_at: str | None = None) -> None:
        item = {"id": r.id, "household": r.household, "cycle": r.cycle, "supply_start": float(r.supply_start),
                "t_obs": float(r.t_obs), "received_at": received_at, "turbidity": r.turbidity,
                "chlorine": r.chlorine, "answer": r.answer, "source": r.source, "level": r.level,
                "reasons": json.dumps(r.reasons), "flags": json.dumps(r.flags),
                "reading": json.dumps(r.reading) if r.reading is not None else None, "photo": photo,
                "sha256": sha256, "phash": phash, "taken_at": taken_at}
        self.readings.put_item(Item=_to_ddb(item))

    def update_levels(self, reports: list[Report]) -> None:
        for r in reports:
            values = {":r": json.dumps(r.reasons)}
            if r.level is None:
                expr = "SET reasons = :r REMOVE #lv"
            else:
                expr = "SET reasons = :r, #lv = :l"
                values[":l"] = r.level
            self.readings.update_item(Key={"id": r.id}, UpdateExpression=expr,
                                      ExpressionAttributeNames={"#lv": "level"}, ExpressionAttributeValues=values)

    def set_answer(self, report_id: str, answer: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.readings.update_item(Key={"id": report_id}, UpdateExpression="SET answer = :a",
                                      ConditionExpression="attribute_exists(id)",
                                      ExpressionAttributeValues={":a": answer})
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def rows(self, min_cycle: int | None = None) -> list[dict]:
        kw = {}
        if min_cycle is not None:
            kw = {"FilterExpression": "#c >= :m", "ExpressionAttributeNames": {"#c": "cycle"},
                  "ExpressionAttributeValues": {":m": min_cycle}}
        rows = self._scan(self.readings, **kw)
        cols = ("turbidity", "chlorine", "answer", "level", "reading", "photo", "sha256", "phash", "taken_at")
        for d in rows:
            for c in cols:
                d.setdefault(c, None)
            d.setdefault("reasons", "[]")
            d.setdefault("flags", "[]")
        return sorted(rows, key=lambda d: (float(d["t_obs"]), d["id"]))

    def find_photo(self, sha256: str | None = None, taken_at: str | None = None) -> list[dict]:
        out = []
        for d in self.rows():
            if (sha256 and d.get("sha256") == sha256) or (taken_at and d.get("taken_at") == taken_at):
                out.append({k: d.get(k) for k in ("id", "household", "cycle", "sha256", "phash", "taken_at")})
        return out

    def phashes(self) -> list[dict]:
        return [{k: d[k] for k in ("id", "household", "cycle", "phash")} for d in self.rows() if d.get("phash")]

    # ---------------------------------------------------------- incidents
    def put_incident(self, incident: dict) -> None:
        self.incidents.put_item(Item=_to_ddb(incident))

    def get_incident(self, incident_id: str) -> dict | None:
        item = self.incidents.get_item(Key={"id": incident_id}).get("Item")
        return _from_ddb(item) if item else None

    def list_incidents(self) -> list[dict]:
        return sorted(self._scan(self.incidents), key=lambda i: i.get("opened_at", ""), reverse=True)

    # ----------------------------------------------------------- whatsapp
    def set_session(self, sender: str, report_id: str) -> None:
        self.meta.put_item(Item={"key": f"wa#{sender}", "report_id": report_id})

    def session(self, sender: str) -> str | None:
        item = self.meta.get_item(Key={"key": f"wa#{sender}"}).get("Item")
        return item["report_id"] if item else None

    def reset(self) -> None:
        for table, key in ((self.readings, "id"), (self.meta, "key"), (self.incidents, "id")):
            with table.batch_writer() as bw:
                for item in self._scan(table, ProjectionExpression="#k", ExpressionAttributeNames={"#k": key}):
                    bw.delete_item(Key={key: item[key]})
