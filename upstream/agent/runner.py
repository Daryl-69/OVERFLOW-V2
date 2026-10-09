"""Run the incident agent: Strands on Bedrock when available, rule-based otherwise.

Both paths drive the same :class:`IncidentTools`, so the plan the engineer
sees has the same shape either way; only the written summary differs. If the
model is unavailable or misbehaves, the rule-based agent takes over — an
incident must never stall because a model call failed.

Environment:

* ``UPSTREAM_AGENT``          — ``auto`` (default: Bedrock if ``strands`` is
  installed and AWS credentials resolve), ``bedrock`` or ``rules``
* ``UPSTREAM_BEDROCK_MODEL``  — model id (default Claude 3.5 Sonnet v2 on Bedrock)
* ``AWS_REGION``              — Bedrock region
"""

from __future__ import annotations

import logging
import os

from ..incident import pct
from .tools import IncidentTools

log = logging.getLogger("upstream.agent")

DEFAULT_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"

SYSTEM_PROMPT = """You are Upstream's incident agent for a municipal water utility in India.
A cluster of household first-glass readings suggests sewage is entering a distribution pipe.
Your job is to prepare an action plan for the duty engineer. You never send anything yourself.

Rules:
1. Always call get_incident_readings and run_triangulation first. Base every claim on tool output.
2. Call downstream_households, then draft_advisory with lang="en" and again with lang="hi".
3. If the 90% region is longer than 150 m, or p_breach is below 0.5, call request_strip_tests.
4. Finish by calling submit_for_approval exactly once with a summary of at most 120 words:
   where to dig, how confident the model is, how many homes are advised, which strip tests were requested.
5. Never invent households, times or coordinates. Never change the wording of resident alerts.
6. Say plainly when evidence is weak. Readings marked excluded are possible duplicates; ignore them.
"""


def rule_summary(plan: dict) -> str:
    tri = plan.get("triangulation") or {}
    dig = tri.get("dig") or {}
    homes = plan.get("downstream", [])
    strips = plan.get("strip_tests", [])
    counts = plan.get("counts", {})
    parts = [f"{counts.get('red', 0)} red and {counts.get('amber', 0)} amber readings in the window."]
    if dig:
        parts.append(f"Most likely breach at {dig['lat']:.5f}, {dig['lon']:.5f} "
                     f"(breach probability {pct(tri.get('p_breach'))}, 90% region {tri.get('region_length_m', 0):.0f} m).")
    n_none = sum(1 for h in homes if h["status"] == "no_safe_window")
    parts.append(f"{len(homes)} downstream homes get a boil-water advisory in English and Hindi"
                 + (f"; {n_none} have no safe window today." if n_none else "."))
    if strips:
        parts.append(f"Strip tests requested from {len(strips)} nearby homes to narrow the search.")
    return " ".join(parts)


def run_rules(tools: IncidentTools) -> dict:
    tools.get_incident_readings()
    tri = tools.run_triangulation()
    tools.downstream_households()
    tools.draft_advisory("en")
    tools.draft_advisory("hi")
    if tri.get("available") and (tri["region_length_m"] > 150 or tri["p_breach"] < 0.5):
        tools.request_strip_tests()
    tools.submit_for_approval(rule_summary(tools.plan))
    return tools.plan


def _strands_agent(tools: IncidentTools):
    from strands import Agent, tool
    from strands.models import BedrockModel

    @tool
    def get_incident_readings() -> dict:
        """Readings in the incident window with level, turbidity, chlorine and delay."""
        return tools.get_incident_readings()

    @tool
    def run_triangulation() -> dict:
        """Re-run the Bayesian locator: dig spot, 90% region length, breach probability, lanes."""
        return tools.run_triangulation()

    @tool
    def downstream_households() -> dict:
        """Homes downstream of the suspected breach with probability affected and safe-after time."""
        return tools.downstream_households()

    @tool
    def request_strip_tests(n: int = 5) -> dict:
        """Ask the n most informative nearby homes for a strip test at the next supply."""
        return tools.request_strip_tests(n)

    @tool
    def draft_advisory(lang: str = "en") -> dict:
        """Draft resident alerts and the crew brief. lang is 'en' or 'hi'."""
        return tools.draft_advisory(lang)

    @tool
    def submit_for_approval(summary: str) -> dict:
        """Submit the plan and summary to the duty engineer for approval. Call exactly once, last."""
        return tools.submit_for_approval(summary)

    model = BedrockModel(model_id=os.environ.get("UPSTREAM_BEDROCK_MODEL", DEFAULT_MODEL),
                         region_name=os.environ.get("AWS_REGION"), temperature=0.1)
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None,
                 tools=[get_incident_readings, run_triangulation, downstream_households,
                        request_strip_tests, draft_advisory, submit_for_approval])


def bedrock_available() -> bool:
    try:
        import boto3
        import strands  # noqa: F401
    except ImportError:
        return False
    return boto3.Session().get_credentials() is not None


def run_incident_agent(live, incident_id: str, task_token: str | None = None, mode: str | None = None) -> dict:
    """Prepare the incident plan and submit it for approval. Returns the plan."""
    mode = mode or os.environ.get("UPSTREAM_AGENT", "auto")
    use_bedrock = mode == "bedrock" or (mode == "auto" and bedrock_available())
    if use_bedrock:
        tools = IncidentTools(live, incident_id, task_token, agent_name="bedrock")
        try:
            agent = _strands_agent(tools)
            agent(f"Incident {incident_id} has opened. Prepare the plan for the engineer.")
            if "submitted_at" not in tools.plan:
                # The model stopped early: finish deterministically with what it gathered.
                for step in ("get_incident_readings", "run_triangulation", "downstream_households"):
                    if not any(t["tool"] == step for t in tools.trace):
                        getattr(tools, step)()
                tools.submit_for_approval(rule_summary(tools.plan))
            return tools.plan
        except Exception:  # noqa: BLE001 - any model failure falls back to rules
            log.exception("Bedrock agent failed for %s; using the rule-based agent", incident_id)
    return run_rules(IncidentTools(live, incident_id, task_token))
