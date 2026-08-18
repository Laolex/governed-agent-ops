"""The operations agent: three read-only tools, no authority.

The agent resolves what the operator meant and proposes an operation. It never
performs one. The executor decides whether a proposal happens, performs it, and
records it — and this module deliberately cannot import either the executor or
the ledger, so no later edit can quietly hand a tool a write path.

Two conventions here are load-bearing and each was a bug first.

Tool docstrings carry **no example values**. An example identifier in a tool
schema gets copied by the model into its calls, which manufactures agreement out
of nothing: during the spike, "e.g. NORTHSTAR-S01E06" in a docstring produced the
same confident answer on every run, including the run with no memory at all.

The model is pinned to `global` through `client_kwargs`, because that is where
gemini-3.5-flash is served. Moving the whole process to `global` instead would
send the memory service looking for the agent engine in a region where it does
not exist, and retrieval would silently return nothing.
"""

from __future__ import annotations

from typing import Any, Callable

from ops.fleet import OPERATIONS
from ops.store import FleetStore, UnknownAgent

DEFAULT_MODEL = "gemini-3.5-flash"
MODEL_LOCATION = "global"

PERMITTED_TOOLS = ("read_fleet", "find_agent", "propose_operation")

INSTRUCTION = """\
You help a platform operator manage a fleet of agents.

Resolve what the operator is referring to. They will often name an agent
indirectly — by what it does, by what went wrong, or by something they told you
earlier. Use what you remember about this operator to resolve it, then call
propose_operation with the identifier you resolved.

Never invent an agent identifier. If you cannot resolve one, say so and stop.

You do not decide whether an operation is allowed. propose_operation records
what you would do; a policy evaluator outside this conversation decides, and its
determination is reported separately. Do not predict it, do not restate it as
your own judgement, and do not tell the operator an operation succeeded.
"""


def build_read_fleet(store: FleetStore) -> Callable[[], dict]:
    def read_fleet() -> dict:
        """Return every agent in the fleet with its current state.

        Use this to see what exists before resolving a reference.
        """
        return {
            "agents": [
                {"agent_id": a.agent_id, "state": a.state, "owner": a.owner,
                 "purpose": a.purpose, "revision": a.revision}
                for a in store.list()
            ]
        }

    return read_fleet


def build_find_agent(store: FleetStore) -> Callable[[str], dict]:
    def find_agent(agent_id: str) -> dict:
        """Return one agent's current record.

        Args:
            agent_id: The identifier of the agent to look up.
        """
        try:
            agent = store.get(agent_id)
        except UnknownAgent:
            return {"found": False, "agent_id": agent_id}
        return {"found": True, **agent.to_dict()}

    return find_agent


def build_propose_operation(store: FleetStore) -> Callable[[str, str, str], dict]:
    def propose_operation(operation: str, agent_id: str, cause: str) -> dict:
        """Propose an operation on one agent. This does not perform it.

        The proposal is handed to a policy evaluator, which decides. Never
        report the result of an operation from this tool's response.

        Args:
            operation: One of promote, quarantine or rollback.
            agent_id: The identifier of the agent to operate on, resolved from
                the fleet. Never a value you invented.
            cause: Why the operator wants this. Required for a quarantine;
                pass an empty string when the operator gave no reason.
        """
        if operation not in OPERATIONS:
            return {"proposed": False, "operation": operation, "target": agent_id,
                    "reason": f"{operation!r} is not an operation this fleet supports."}
        try:
            store.get(agent_id)
        except UnknownAgent:
            return {"proposed": False, "operation": operation, "target": agent_id,
                    "reason": f"{agent_id!r} is not registered in the fleet."}
        return {"proposed": True, "operation": operation, "target": agent_id,
                "cause": cause}

    return propose_operation


def tool_names(agent: Any) -> list[str]:
    return [getattr(t, "__name__", getattr(t, "name", str(t))) for t in agent.tools]


def build_agent(store: FleetStore, model: str = DEFAULT_MODEL):
    """The ADK agent, holding only the tools PERMITTED_TOOLS names."""
    from google.adk.agents import Agent
    from google.adk.models.google_llm import Gemini

    return Agent(
        name="fleet_ops_agent",
        model=Gemini(model=model, client_kwargs={"location": MODEL_LOCATION}),
        description=(
            "Resolves which fleet agent an operator means, and proposes a "
            "lifecycle operation on it for a policy evaluator to decide."
        ),
        instruction=INSTRUCTION,
        tools=[
            build_read_fleet(store),
            build_find_agent(store),
            build_propose_operation(store),
        ],
    )
