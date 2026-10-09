"""Incident workflow: agent -> engineer approval -> resident alerts.

Two interchangeable backends behind one interface, chosen by environment:

* :class:`LocalWorkflow` (default) runs the agent in-process and "sends"
  alerts into the incident's outbox, so the whole approval gate works on a
  laptop with no AWS account.
* :class:`StepFunctionsWorkflow` (``UPSTREAM_STATE_MACHINE_ARN`` set) starts
  the ``IncidentFlow`` state machine; the agent Lambda stores the task token
  on the incident and the engineer's click returns it with
  ``SendTaskSuccess``. SNS publishing happens in the state machine, only on
  the approved branch.

In both, nothing reaches residents without an explicit engineer approval.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

log = logging.getLogger("upstream.workflow")


class WorkflowError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def outbox(plan: dict) -> list[dict]:
    """The messages an approval releases: resident alerts (en + hi) and strip requests."""
    msgs = []
    en = {m["household"]: m for m in plan.get("alerts", {}).get("en", [])}
    hi = {m["household"]: m for m in plan.get("alerts", {}).get("hi", [])}
    for hid, m in en.items():
        msgs.append({"household": hid, "kind": "advisory", "en": m["text"], "hi": hi.get(hid, {}).get("text")})
    for s in plan.get("strip_tests", []):
        msgs.append({"household": s["household"], "kind": "strip_request"})
    return msgs


class Workflow:
    """Shared approve/reject validation; backends implement ``start`` and ``_release``."""

    def __init__(self, store=None) -> None:
        # ``None``: bound to the live service's store by ``create_app``.
        self.store = store

    def start(self, incident: dict, live) -> None:
        raise NotImplementedError

    def _get_awaiting(self, incident_id: str) -> dict:
        inc = self.store.get_incident(incident_id)
        if inc is None:
            raise WorkflowError(f"unknown incident {incident_id}", 404)
        if inc["status"] != "awaiting_approval":
            raise WorkflowError(f"incident {incident_id} is {inc['status']}, not awaiting approval", 409)
        return inc

    def approve(self, incident_id: str, engineer: str, note: str = "") -> dict:
        inc = self._get_awaiting(incident_id)
        decision = {"approved": True, "engineer": engineer, "note": note, "at": _now()}
        inc = self.store.update_incident(incident_id, status="approved", decision=decision)
        return self._release(inc, decision)

    def reject(self, incident_id: str, engineer: str, note: str = "") -> dict:
        inc = self._get_awaiting(incident_id)
        decision = {"approved": False, "engineer": engineer, "note": note, "at": _now()}
        inc = self.store.update_incident(incident_id, status="rejected", decision=decision)
        return self._release(inc, decision)

    def _release(self, inc: dict, decision: dict) -> dict:
        raise NotImplementedError


class LocalWorkflow(Workflow):
    def __init__(self, store=None, sync: bool = False, agent_mode: str | None = None) -> None:
        super().__init__(store)
        self.sync = sync
        self.agent_mode = agent_mode

    def start(self, incident: dict, live) -> None:
        from ..agent import run_incident_agent

        def run():
            try:
                run_incident_agent(live, incident["id"], mode=self.agent_mode)
            except Exception:  # noqa: BLE001
                log.exception("agent failed for %s", incident["id"])

        if self.sync:
            run()
        else:
            threading.Thread(target=run, name=f"agent-{incident['id']}", daemon=True).start()

    def _release(self, inc: dict, decision: dict) -> dict:
        if not decision["approved"]:
            return inc
        sent = [m | {"channel": "local-outbox", "sent_at": _now()} for m in outbox(inc.get("plan", {}))]
        return self.store.update_incident(inc["id"], status="notified", notifications=sent)


class StepFunctionsWorkflow(Workflow):
    def __init__(self, store, state_machine_arn: str, client=None) -> None:
        super().__init__(store)
        self.arn = state_machine_arn
        if client is None:
            import boto3
            client = boto3.client("stepfunctions")
        self.sfn = client

    def start(self, incident: dict, live=None) -> None:
        payload = {"incident_id": incident["id"], "cycle": incident.get("cycle"), "opened_at": incident.get("opened_at")}
        try:
            resp = self.sfn.start_execution(stateMachineArn=self.arn, name=incident["id"], input=json.dumps(payload))
        except self.sfn.exceptions.ExecutionAlreadyExists:
            return  # the same incident re-triggered (e.g. a stream retry): already running
        self.store.update_incident(incident["id"], execution_arn=resp["executionArn"])

    def _release(self, inc: dict, decision: dict) -> dict:
        token = inc.get("task_token")
        if not token:
            raise WorkflowError(f"incident {inc['id']} has no task token", 409)
        self.sfn.send_task_success(taskToken=token, output=json.dumps({"incident_id": inc["id"], **decision}))
        return inc


def workflow_from_env(store, **kw) -> Workflow:
    arn = os.environ.get("UPSTREAM_STATE_MACHINE_ARN")
    if arn:
        return StepFunctionsWorkflow(store, arn)
    return LocalWorkflow(store, **kw)
