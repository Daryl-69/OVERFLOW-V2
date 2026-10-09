"""The incident agent's six tools (IMPLEMENTATION §5.5).

Every tool is a thin wrapper over code that already exists — the live
service's state, the locator posterior, ``advisories.py`` and ``planner.py`` —
so the agent can only *propose* what the science supports. Tools return plain
JSON-able dicts and record every call in ``trace`` so the dashboard can show
the engineer exactly what the agent looked at.

The same class backs both the rule-based agent and the Strands/Bedrock agent.
"""

from __future__ import annotations

import time
from datetime import datetime

from ..geo import project
from ..locate.planner import nearby_requests
from .messages import LANGS, advisory_text, crew_brief, strip_request_text

TOOL_NAMES = ("get_incident_readings", "run_triangulation", "downstream_households",
              "request_strip_tests", "draft_advisory", "submit_for_approval")


class IncidentTools:
    def __init__(self, live, incident_id: str, task_token: str | None = None, agent_name: str = "rules") -> None:
        self.live = live
        self.store = live.store
        self.incident_id = incident_id
        self.task_token = task_token
        self.agent_name = agent_name
        self.trace: list[dict] = []
        self.plan: dict = {"incident_id": incident_id}
        if self.store.get_incident(incident_id) is None:
            raise KeyError(f"unknown incident {incident_id}")

    # ------------------------------------------------------------ helpers
    def _log(self, tool: str, args: dict, result: dict, t0: float) -> dict:
        self.trace.append({"tool": tool, "args": args, "ms": round((time.perf_counter() - t0) * 1000, 1),
                           "summary": result.get("_summary", "")})
        return {k: v for k, v in result.items() if k != "_summary"}

    def _incident(self) -> dict:
        return self.store.get_incident(self.incident_id)

    # -------------------------------------------------------------- tools
    def get_incident_readings(self) -> dict:
        """The readings in the incident window: household, level, turbidity, chlorine, delay."""
        t0 = time.perf_counter()
        st = self.live.state()
        rows = [{"id": r["id"], "household": r["household"], "cycle": r["cycle"], "level": r["level"],
                 "turbidity": r["turbidity"], "chlorine": r["chlorine"], "delay_min": r["delay_min"],
                 "answer": r.get("answer"), "excluded": r["excluded"], "reasons": r.get("reasons", [])}
                for r in st["reports"]]
        counts = {lv: sum(1 for r in rows if r["level"] == lv and not r["excluded"]) for lv in ("red", "amber", "green")}
        res = {"readings": rows, "counts": counts, "excluded": sum(1 for r in rows if r["excluded"]),
               "_summary": f"{len(rows)} readings: {counts['red']} red, {counts['amber']} amber, {counts['green']} green"}
        self.plan["counts"] = counts
        return self._log("get_incident_readings", {}, res, t0)

    def run_triangulation(self) -> dict:
        """Re-run the Bayesian locator: dig spot, 90% region, breach probability, best lanes to inspect."""
        t0 = time.perf_counter()
        st = self.live.state()
        post = st.get("posterior")
        if not post:
            res = {"available": False, "_summary": "no posterior (no red evidence in the window)"}
            return self._log("run_triangulation", {}, res, t0)
        res = {"available": True, "dig": post["dig"], "p_breach": post["p_breach"],
               "log10_bayes_factor": post["log10_bf"], "region_length_m": post["region_length_m"],
               "ring": post["ring"], "lanes": st.get("lanes", [])[:3], "n_reports": post["n_reports"],
               "_summary": (f"dig spot {post['dig']['lat']:.5f},{post['dig']['lon']:.5f}; p_breach "
                            f"{post['p_breach']:.2f}; 90% region {post['region_length_m']:.0f} m")}
        self.plan["triangulation"] = {k: res[k] for k in ("dig", "p_breach", "region_length_m", "ring", "lanes")}
        return self._log("run_triangulation", {}, res, t0)

    def downstream_households(self) -> dict:
        """Homes downstream of the suspected breach, with probability affected and safe-after time."""
        t0 = time.perf_counter()
        adv = self.live.state().get("advisories", [])
        homes = [{"household": a["household"], "p_affected": a["p_affected"], "status": a["status"],
                  "safe_after": a.get("safe_after"), "safe_after_ampm": a.get("safe_after_ampm")} for a in adv]
        homes.sort(key=lambda h: -h["p_affected"])
        n_none = sum(1 for h in homes if h["status"] == "no_safe_window")
        res = {"households": homes, "n": len(homes), "no_safe_window": n_none,
               "_summary": f"{len(homes)} homes downstream ({n_none} with no safe window today)"}
        self.plan["downstream"] = homes
        return self._log("downstream_households", {}, res, t0)

    def request_strip_tests(self, n: int = 5) -> dict:
        """Pick the nearby homes whose strip test at the next supply would narrow the search most."""
        t0 = time.perf_counter()
        n = max(1, min(int(n), 10))
        post = self.live.posterior()
        st = self.live.state()
        if post is None or not st.get("posterior"):
            res = {"households": [], "_summary": "no posterior; nothing to request"}
            return self._log("request_strip_tests", {"n": n}, res, t0)
        dig = st["posterior"]["dig"]
        origin = self.live.sc.streets.origin
        x, y = project(dig["lat"], dig["lon"], origin)
        reported = {r["household"] for r in st["reports"]}
        picks = []
        for radius in (400.0, 800.0, 1600.0):  # widen when few homes live near the dig spot
            picks = nearby_requests(self.live.sc.gis, post.joint, (float(x), float(y)), self.live.positions,
                                    exclude=reported, n=n, radius_m=radius)
            if picks:
                break
        homes = [{"household": p["household"], "gain_bits": round(float(p["gain_bits"]), 3)} for p in picks]
        res = {"households": homes, "message": {lg: strip_request_text(lg) for lg in LANGS},
               "_summary": f"asked {len(homes)} homes near the dig spot for strip tests"}
        self.plan["strip_tests"] = homes
        self.plan["strip_message"] = res["message"]
        return self._log("request_strip_tests", {"n": n}, res, t0)

    def draft_advisory(self, lang: str = "en") -> dict:
        """Draft resident alerts and the crew brief in English ('en') or Hindi ('hi')."""
        t0 = time.perf_counter()
        if lang not in LANGS:
            res = {"error": f"lang must be one of {LANGS}", "_summary": f"bad lang {lang!r}"}
            return self._log("draft_advisory", {"lang": lang}, res, t0)
        if "downstream" not in self.plan:
            self.downstream_households()
        msgs = [{"household": h["household"], "status": h["status"],
                 "text": advisory_text(h["status"], h.get("safe_after"), lang)} for h in self.plan["downstream"]]
        inc = self._incident() | {"n_advisories": len(msgs)}
        brief = crew_brief(inc, lang)
        res = {"lang": lang, "messages": msgs, "crew_brief": brief,
               "_summary": f"{len(msgs)} {lang} alerts drafted"}
        self.plan.setdefault("alerts", {})[lang] = msgs
        self.plan.setdefault("crew_brief", {})[lang] = brief
        return self._log("draft_advisory", {"lang": lang}, res, t0)

    def submit_for_approval(self, summary: str) -> dict:
        """Hand the plan to the engineer. Nothing is sent to residents until they approve."""
        t0 = time.perf_counter()
        for lg in LANGS:
            if lg not in self.plan.get("alerts", {}):
                self.draft_advisory(lg)
        res = {"status": "awaiting_approval", "n_alerts": len(self.plan["alerts"]["en"]),
               "_summary": "plan submitted; waiting for engineer approval"}
        out = self._log("submit_for_approval", {"summary_chars": len(summary)}, res, t0)
        self.plan = dict(self.plan, summary=summary.strip()[:2000], trace=list(self.trace), agent=self.agent_name,
                         submitted_at=datetime.now().astimezone().isoformat())
        changes = {"status": "awaiting_approval", "plan": self.plan}
        if self.task_token:
            changes["task_token"] = self.task_token
        self.store.update_incident(self.incident_id, **changes)
        return out
