"""The service layer between the agent's turn and the record.

The agent proposes; this decides whether to act, runs the executor, and returns
the transcript alongside the determination.

Two properties are the reason this layer exists rather than the agent calling the
executor directly. The determination never comes from the model — the proposal is
read for *what was asked*, never for *what the answer is*, so a model that says
"PERMITTED, I have promoted it" publishes nothing. And a turn with no proposal
executes nothing: talking about promoting an agent is not proposing it, and
inferring intent from prose would put the model back in the decision path.
"""

from __future__ import annotations

from typing import Any, Protocol

from ops.executor import Executor
from ops.facts import FactsStore
from ops.retrieval import MemoryBank
from ops.store import FleetStore

PROPOSAL_TOOL = "propose_operation"


class NoProposal(Exception):
    """The turn contained no proposal to act on."""


class AgentUnavailable(Exception):
    """The agent produced no turn at all.

    Distinct from NoProposal on purpose. Observed in production: the model
    returned 429 RESOURCE_EXHAUSTED, ADK swallowed it, and the turn came back
    with zero events — which downstream is indistinguishable from an agent that
    considered the request and proposed nothing. Reporting a quota outage to an
    operator as "the system decided not to act" is the same class of mistake as
    an unconfigured engine reading as a refusal to decide.
    """


class AgentClient(Protocol):
    def ask(self, message: str, user_id: str) -> list[dict]: ...


def _parts(events: list[dict]):
    for event in events:
        for part in event.get("content", {}).get("parts", []):
            yield part


def extract_proposal(events: list[dict]) -> dict:
    """Return the arguments of the proposal the agent made.

    Raises NoProposal when the turn made none. Deliberately strict: the only
    thing that counts as a proposal is a call to the proposal tool.
    """
    for part in _parts(events):
        call = part.get("function_call")
        if call and call.get("name") == PROPOSAL_TOOL:
            return dict(call.get("args") or {})
    raise NoProposal("the turn contained no call to propose_operation")


def build_transcript(events: list[dict]) -> list[dict]:
    """Flatten the turn into steps a viewer can read.

    Tool calls, tool responses and prose stay distinct, so a viewer can see that
    the determination came from the evaluator rather than from the model.
    """
    steps: list[dict] = []
    for part in _parts(events):
        if call := part.get("function_call"):
            steps.append({"kind": "tool_call", "name": call.get("name"),
                          "args": call.get("args") or {}})
        elif response := part.get("function_response"):
            steps.append({"kind": "tool_response", "name": response.get("name"),
                          "response": response.get("response")})
        elif text := part.get("text"):
            steps.append({"kind": "text", "text": text})
    return steps


def handle_ask(
    message: str,
    *,
    agent: AgentClient,
    store: FleetStore,
    ledger: Any,
    bank: MemoryBank,
    facts: FactsStore,
    now: str,
    operator: str = "platform-ops",
    scope: dict | None = None,
) -> dict:
    """Run one operator turn end to end."""
    events = agent.ask(message, user_id=operator)
    if not events:
        raise AgentUnavailable(
            "the agent returned no turn — it could not be reached, or the model "
            "refused the request before producing anything"
        )
    transcript = build_transcript(events)

    try:
        proposal = extract_proposal(events)
    except NoProposal:
        return {"transcript": transcript, "determination": None, "record_hash": None}

    # Facts are fetched only now, for the agent the proposal actually names. The
    # target is not known before the turn, and applying one agent's attestation
    # to another would decide on evidence about something else entirely.
    target = proposal.get("agent_id", "")

    executor = Executor(store=store, ledger=ledger, bank=bank,
                        scope=scope or {"user_id": operator})
    result = executor.execute(
        proposal.get("operation", ""),
        target,
        operator=operator,
        facts=facts.for_agent(target),
        # The operator's own words drove retrieval, so they are what the manifest
        # records — not a paraphrase invented after the fact.
        query=message,
        now=now,
        cause=proposal.get("cause", ""),
        owner=proposal.get("owner", ""),
        purpose=proposal.get("purpose", ""),
    )

    return {
        "transcript": transcript,
        "determination": {
            "outcome": result.determination.outcome,
            "rule_hits": list(result.determination.rule_hits),
            "blocking_condition": result.determination.blocking_condition,
            "policy_revision": result.determination.policy_revision,
            # Named explicitly so the console can label it, and so nobody reading
            # this response mistakes it for something the model said.
            "decided_by": "policy evaluator",
        },
        "record_hash": result.record_hash,
        "state_before": result.state_before,
        "state_after": result.state_after,
    }
