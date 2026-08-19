"""The service layer: what happens between the agent's turn and the record.

The agent proposes. This layer decides whether to act on the proposal, runs the
executor, and returns the transcript alongside the determination. Two properties
carry the weight.

The determination never comes from the model. If the agent's turn contained a
verdict, this layer would let a model report a decision nobody made — so the
proposal is read for *what was asked*, never for *what the answer is*.

And a turn with no proposal executes nothing. A model that talks about promoting
an agent without calling the tool has not proposed anything, and the difference
between discussing an action and taking one is the whole point of the split.
"""

from __future__ import annotations

import pytest

from ops.api import NoProposal, extract_proposal, handle_ask
from ops.facts import InMemoryFactsStore
from ops.ledger import InMemoryLedger
from ops.store import AgentRecord, InMemoryFleetStore

CANDIDATE = AgentRecord(
    agent_id="billing-reconciler", state="candidate", owner="platform-ops",
    purpose="Reconciles invoice lines against settled payments.",
    revision="r7", previous_revision="r6",
)

CLEAN = {"attestation": {"expires_at": "2026-12-01"}, "incidents": []}
CLEAN_FACTS = InMemoryFactsStore({"billing-reconciler": CLEAN})


def _events(*, proposal=None, text="Proposed."):
    events = []
    if proposal is not None:
        events.append({"content": {"parts": [
            {"function_call": {"name": "propose_operation", "args": proposal}}
        ]}})
        events.append({"content": {"parts": [
            {"function_response": {"name": "propose_operation",
                                   "response": {"proposed": True, **proposal}}}
        ]}})
    events.append({"content": {"parts": [{"text": text}]}})
    return events


class FakeAgent:
    def __init__(self, events):
        self._events = events
        self.asked: list[str] = []

    def ask(self, message: str, user_id: str):
        self.asked.append(message)
        return self._events


class FakeBank:
    def retrieve(self, scope, query): return []
    def count(self, scope): return 0
    def revisions(self, name): return []


def _deps(events):
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    return {
        "agent": FakeAgent(events),
        "store": store,
        "ledger": InMemoryLedger([]),
        "bank": FakeBank(),
    }


def test_a_proposal_is_extracted_from_the_turn():
    proposal = extract_proposal(_events(proposal={
        "operation": "promote", "agent_id": "billing-reconciler", "cause": ""}))
    assert proposal == {"operation": "promote", "agent_id": "billing-reconciler",
                        "cause": ""}


def test_a_turn_with_no_proposal_raises_rather_than_guessing():
    """Talking about promoting an agent is not proposing it. Inferring intent
    from prose here would put the model back in the decision path."""
    with pytest.raises(NoProposal):
        extract_proposal(_events(text="I think we should promote the reconciler."))


def test_a_turn_with_no_proposal_executes_nothing():
    deps = _deps(_events(text="I cannot resolve which agent you mean."))

    result = handle_ask("promote something", facts=CLEAN_FACTS, now="2026-08-18T18:00:00Z",
                        **deps)

    assert result["determination"] is None
    assert deps["store"].get("billing-reconciler").state == "candidate"
    assert deps["ledger"].read_all() == []


def test_a_proposal_is_executed_and_recorded():
    deps = _deps(_events(proposal={"operation": "promote",
                                   "agent_id": "billing-reconciler", "cause": ""}))

    result = handle_ask("promote the reconciler", facts=CLEAN_FACTS,
                        now="2026-08-18T18:00:00Z", **deps)

    assert result["determination"]["outcome"] == "PERMITTED"
    assert deps["store"].get("billing-reconciler").state == "active"
    assert len(deps["ledger"].read_all()) == 1


def test_the_response_carries_the_transcript_so_a_viewer_can_see_the_seam():
    """The viewer must be able to see that the determination came from the gate
    and not from the model. That requires showing the tool call, the gate, and
    the prose as three separate things."""
    deps = _deps(_events(proposal={"operation": "promote",
                                   "agent_id": "billing-reconciler", "cause": ""}))

    result = handle_ask("promote it", facts=CLEAN_FACTS, now="2026-08-18T18:00:00Z",
                        **deps)

    kinds = [step["kind"] for step in result["transcript"]]
    assert "tool_call" in kinds
    assert "text" in kinds
    assert result["determination"]["decided_by"] == "policy evaluator"


def test_a_determination_in_the_model_turn_is_ignored():
    """A model that claims an outcome must not be able to publish one."""
    events = _events(proposal={"operation": "promote",
                               "agent_id": "billing-reconciler", "cause": ""},
                     text="PERMITTED. I have promoted the agent.")
    deps = _deps(events)
    deps["store"].put(CANDIDATE.with_state("candidate"))
    expired = InMemoryFactsStore({"billing-reconciler": {
        "attestation": {"expires_at": "2026-07-01"}, "incidents": []}})

    result = handle_ask("promote it", facts=expired, now="2026-08-18T18:00:00Z", **deps)

    assert result["determination"]["outcome"] == "REFUSED"
    assert deps["store"].get("billing-reconciler").state == "candidate"


def test_the_record_hash_is_returned_so_the_console_can_show_the_record():
    deps = _deps(_events(proposal={"operation": "promote",
                                   "agent_id": "billing-reconciler", "cause": ""}))

    result = handle_ask("promote it", facts=CLEAN_FACTS, now="2026-08-18T18:00:00Z",
                        **deps)

    assert result["record_hash"] == deps["ledger"].read_all()[0]["hash"]


def test_the_query_recorded_is_the_operators_words():
    """The manifest's query must be what drove retrieval, not a paraphrase the
    service invented after the fact."""
    deps = _deps(_events(proposal={"operation": "promote",
                                   "agent_id": "billing-reconciler", "cause": ""}))

    handle_ask("promote whatever is failing", facts=CLEAN_FACTS,
               now="2026-08-18T18:00:00Z", **deps)

    body = deps["ledger"].read_all()[0]["body"]
    assert body["retrieval"]["query"] == "promote whatever is failing"


def test_facts_are_looked_up_for_the_agent_the_proposal_names():
    """The target is only known after the agent's turn, so facts cannot be
    fetched up front. Looking them up for the wrong agent — or fetching one
    agent's facts and applying them to another — would decide on evidence about
    something else entirely."""
    deps = _deps(_events(proposal={"operation": "promote",
                                   "agent_id": "billing-reconciler", "cause": ""}))
    asked_for: list[str] = []

    class RecordingFacts(InMemoryFactsStore):
        def for_agent(self, agent_id):
            asked_for.append(agent_id)
            return super().for_agent(agent_id)

    handle_ask("promote it",
               facts=RecordingFacts({"billing-reconciler": CLEAN}),
               now="2026-08-18T18:00:00Z", **deps)

    assert asked_for == ["billing-reconciler"]


def test_an_agent_with_no_facts_escalates_rather_than_being_promoted():
    """End to end: absence survives the whole path, from an empty facts store to
    an ESCALATE in the record."""
    deps = _deps(_events(proposal={"operation": "promote",
                                   "agent_id": "billing-reconciler", "cause": ""}))

    result = handle_ask("promote it", facts=InMemoryFactsStore({}),
                        now="2026-08-18T18:00:00Z", **deps)

    assert result["determination"]["outcome"] == "ESCALATE"
    assert deps["store"].get("billing-reconciler").state == "candidate"


def test_a_registration_proposal_carries_its_owner_and_purpose_to_the_executor():
    """The two fields exist only on this operation, so a service layer that
    dropped them would refuse every registration with OWN-001 and look like a
    policy problem rather than a plumbing one."""
    deps = _deps(_events(proposal={
        "operation": "register", "agent_id": "refund-router", "cause": "",
        "owner": "platform-ops", "purpose": "Routes refund requests."}))

    result = handle_ask("register the refund router", facts=InMemoryFactsStore({}),
                        now="2026-08-18T18:00:00Z", **deps)

    assert result["determination"]["outcome"] == "PERMITTED"
    assert deps["store"].get("refund-router").owner == "platform-ops"


def test_a_rollback_proposal_reaches_the_executor():
    deps = _deps(_events(proposal={"operation": "rollback",
                                   "agent_id": "billing-reconciler", "cause": ""}))
    deps["store"].put(CANDIDATE.with_state("active"))

    result = handle_ask("roll it back", facts=CLEAN_FACTS,
                        now="2026-08-18T18:00:00Z", **deps)

    assert result["determination"]["outcome"] == "PERMITTED"
    assert deps["store"].get("billing-reconciler").revision == "r6"
