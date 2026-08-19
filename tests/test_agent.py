"""The operations agent, and the three read-only tools it is permitted to hold.

The agent is reachable by an operator typing free text, so "the instruction says
not to" is not a control. Everything that must be true of its authority is
asserted here structurally: the tool set is bounded, no tool can write, and the
module cannot even import the components that do.

One test looks pedantic and is not. Tool docstrings must contain no example
values. During the spike, a docstring reading "e.g. NORTHSTAR-S01E06" made the
model copy that identifier into every call — including the run with no memory at
all — producing a confident, consistent, entirely fake result that looked like a
clean negative. It is the failure mode most likely to recur, because nothing
about it looks like a bug.
"""

from __future__ import annotations

import inspect
import re

import pytest

from ops.agent import (
    PERMITTED_TOOLS,
    build_agent,
    build_propose_operation,
    build_read_fleet,
    tool_names,
)
from ops.store import AgentRecord, InMemoryFleetStore

CANDIDATE = AgentRecord(
    agent_id="billing-reconciler",
    state="candidate",
    owner="platform-ops",
    purpose="Reconciles invoice lines against settled payments.",
    revision="r7",
    previous_revision="r6",
)


def _store():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    return store


def test_the_agent_holds_exactly_the_permitted_tools():
    agent = build_agent(_store())
    assert set(tool_names(agent)) == set(PERMITTED_TOOLS)


def test_the_agents_model_is_pinned_to_where_it_is_served():
    """A Vertex publisher model is a per-region resource. gemini-3.5-flash is
    served from global, europe-west2 and asia-southeast1, and 404s from
    us-central1, europe-west4 and us-east5 (measured 2026-08-18) — a subset, not
    everywhere-but-global. This agent is deployed to an engine in us-central1,
    so the model cannot inherit the deployment's region, and the fix is not to
    move the process: that would send the memory service looking for the engine
    in a region where it does not exist."""
    agent = build_agent(_store())
    assert agent.model.model == "gemini-3.5-flash"
    assert agent.model.client_kwargs["location"] == "global"


def test_no_tool_docstring_carries_an_example_value():
    """See the module docstring. An example identifier in a tool schema gets
    copied into calls, which manufactures agreement out of nothing."""
    agent = build_agent(_store())
    for tool in agent.tools:
        doc = inspect.getdoc(tool) or ""
        assert "e.g." not in doc, f"{tool!r} docstring carries an example"
        assert "for example" not in doc.lower(), \
            f"{tool!r} docstring carries an example"
        assert not re.search(r"\b[A-Z][A-Z0-9]{2,}[-_][A-Z0-9]+\b", doc), \
            f"{tool!r} docstring carries an identifier-shaped literal"


def test_the_agent_module_cannot_reach_the_writers():
    """Structural, not advisory. If the module cannot import the executor or the
    ledger, no future edit can quietly give a tool a write path."""
    import ops.agent

    source = inspect.getsource(ops.agent)
    assert "ops.ledger" not in source
    assert "ops.executor" not in source


def test_propose_operation_returns_a_proposal_and_changes_nothing():
    """The tool's name is its whole contract. It hands back what it would do; the
    executor decides whether that happens."""
    store = _store()
    propose = build_propose_operation(store)

    proposal = propose("promote", "billing-reconciler", "", "", "")

    assert proposal["proposed"] is True
    assert proposal["operation"] == "promote"
    assert proposal["target"] == "billing-reconciler"
    assert store.get("billing-reconciler").state == "candidate"


def test_propose_operation_refuses_an_agent_it_cannot_find():
    """It must not invent a target. Returning a proposal for an agent that does
    not exist would put a fabricated identifier into the decision path."""
    propose = build_propose_operation(_store())

    proposal = propose("promote", "no-such-agent", "", "", "")

    assert proposal["proposed"] is False
    assert "not registered" in proposal["reason"]


def test_propose_operation_will_not_propose_an_unknown_operation():
    propose = build_propose_operation(_store())

    proposal = propose("delete_everything", "billing-reconciler", "", "", "")

    assert proposal["proposed"] is False


def test_read_fleet_reports_state_without_exposing_a_way_to_change_it():
    read_fleet = build_read_fleet(_store())

    fleet = read_fleet()

    assert fleet["agents"][0]["agent_id"] == "billing-reconciler"
    assert fleet["agents"][0]["state"] == "candidate"


def test_read_fleet_returns_an_empty_fleet_rather_than_failing():
    read_fleet = build_read_fleet(InMemoryFleetStore({}))
    assert read_fleet() == {"agents": []}


def test_a_tool_response_never_carries_a_determination():
    """The gate decides. A tool that returned an outcome would let the model
    report a determination nobody made."""
    propose = build_propose_operation(_store())
    proposal = propose("promote", "billing-reconciler", "", "", "")
    assert "outcome" not in proposal
    assert "rule_hits" not in proposal


def test_the_agent_can_retrieve_what_the_operator_told_it_before():
    """Scene 2 depends on this. An operator says 'quarantine whatever started
    failing last night' — the referent lives in memory, not in the message, so
    without retrieval in the decision path there is no decision to record."""
    agent = build_agent(_store())
    assert "preload_memory" in tool_names(agent)


def test_memory_retrieval_is_not_counted_as_a_permitted_write_tool():
    """preload_memory reads. It is in the tool set but it is not one of the
    three fleet tools, and conflating the two would hide a change to the agent's
    authority behind a change to its context."""
    from ops.agent import FLEET_TOOLS

    assert set(FLEET_TOOLS) == {"read_fleet", "find_agent", "propose_operation"}
    assert "preload_memory" in PERMITTED_TOOLS
    assert "preload_memory" not in FLEET_TOOLS


def test_a_registration_can_be_proposed_with_its_owner_and_purpose():
    """Registration needs two facts no other operation does. Without them on the
    tool signature the agent has no way to carry them, and the operation is
    unreachable however well the executor implements it."""
    from ops.agent import build_propose_operation

    propose = build_propose_operation(_store())

    proposal = propose("register", "refund-router", "", "platform-ops",
                       "Routes refund requests to the right ledger.")

    assert proposal["proposed"] is True
    assert proposal["owner"] == "platform-ops"
    assert proposal["purpose"].startswith("Routes refund")


def test_registering_an_existing_agent_is_not_proposed():
    from ops.agent import build_propose_operation

    propose = build_propose_operation(_store())

    proposal = propose("register", "billing-reconciler", "", "platform-ops", "Anything.")

    assert proposal["proposed"] is False
    assert "already registered" in proposal["reason"]


def test_a_non_registration_still_requires_the_agent_to_exist():
    """Registration inverts the existence check; nothing else does."""
    from ops.agent import build_propose_operation

    propose = build_propose_operation(_store())

    assert propose("rollback", "no-such-agent", "", "", "")["proposed"] is False


def test_a_rollback_can_be_proposed():
    from ops.agent import build_propose_operation

    proposal = build_propose_operation(_store())("rollback", "billing-reconciler",
                                                 "", "", "")

    assert proposal["proposed"] is True
    assert proposal["operation"] == "rollback"


def test_the_model_location_can_be_moved_without_touching_the_deployment(monkeypatch):
    """`global` has its own quota pool and it can exhaust while the regional
    endpoints still serve — observed 2026-08-19: global returned 429 while
    europe-west2 and asia-southeast1 both returned 200. Routing through global
    is therefore not insulation from quota, and the serving location has to be
    changeable without redeploying to a different region, because the engine and
    the memory service must stay where they are."""
    monkeypatch.setenv("GEMINI_LOCATION", "europe-west2")

    import importlib

    import ops.agent

    importlib.reload(ops.agent)
    try:
        agent = ops.agent.build_agent(_store())
        assert agent.model.client_kwargs["location"] == "europe-west2"
    finally:
        monkeypatch.delenv("GEMINI_LOCATION")
        importlib.reload(ops.agent)


def test_the_model_location_defaults_to_global():
    import ops.agent

    assert ops.agent.MODEL_LOCATION == "global"
