"""The executor: the only writer.

Ordering is the whole component. The gate decides, the state changes, then the
record is written — and each of the three failure modes has a different correct
response, which is why they are tested separately rather than as one "it works"
case:

  - the gate refuses      → no state change, but the refusal IS recorded
  - the ledger is broken  → the state change is rolled back
  - the ledger lost a race→ retried, never rolled back; the operation happened

Getting the third wrong would undo legitimate operations purely because another
writer appended first. A measured race had six of eight writers lose the tip, so
this is the common case under load, not the exotic one.
"""

from __future__ import annotations

import pytest

from ops.executor import Executor, LedgerUnavailable
from ops.ledger import ChainForked, InMemoryLedger, verify_chain
from ops.store import AgentRecord, InMemoryFleetStore

CANDIDATE = AgentRecord(
    agent_id="billing-reconciler",
    state="candidate",
    owner="platform-ops",
    purpose="Reconciles invoice lines against settled payments.",
    revision="r7",
    previous_revision="r6",
)

CLEAN_FACTS = {"attestation": {"expires_at": "2026-12-01"}, "incidents": []}
SCOPE = {"app_name": "engine-1", "user_id": "platform-ops"}
NOW = "2026-08-18T18:00:00Z"


class FakeBank:
    def __init__(self, memories=None, population=None):
        self._memories = memories or []
        self._population = population if population is not None else len(self._memories)

    def retrieve(self, scope, query):
        return self._memories[:3]

    def count(self, scope):
        return self._population

    def revisions(self, memory_name):
        return [{"name": f"{memory_name}/revisions/1", "create_time": "2026-08-18T17:00:00Z"}]


def _memory(n):
    return {"name": f"memories/{n}", "fact": f"note {n}",
            "update_time": "2026-08-18T17:00:00Z", "distance": 0.8}


class RecordingLedger(InMemoryLedger):
    def __init__(self, entries=None, fail_with=None):
        super().__init__(entries if entries is not None else [])
        self._fail_with = fail_with

    def append(self, body):
        if self._fail_with is not None:
            raise self._fail_with
        return super().append(body)


def _executor(store=None, ledger=None, bank=None):
    return Executor(
        store=store or InMemoryFleetStore({}),
        ledger=ledger or InMemoryLedger([]),
        bank=bank or FakeBank(),
        scope=SCOPE,
    )


def test_a_permitted_operation_changes_state_and_records_it():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    result = executor.execute("promote", "billing-reconciler", operator="ola",
                              facts=CLEAN_FACTS, query="promote it", now=NOW)

    assert result.determination.outcome == "PERMITTED"
    assert store.get("billing-reconciler").state == "active"
    assert len(ledger.read_all()) == 1


def test_a_refusal_is_recorded_and_changes_nothing():
    """A refusal is a decision. Not recording it would leave the ledger claiming
    the operator never asked."""
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)
    expired = {"attestation": {"expires_at": "2026-07-01"}, "incidents": []}

    result = executor.execute("promote", "billing-reconciler", operator="ola",
                              facts=expired, query="promote it", now=NOW)

    assert result.determination.outcome == "REFUSED"
    assert store.get("billing-reconciler").state == "candidate"
    assert len(ledger.read_all()) == 1
    assert ledger.read_all()[0]["body"]["result"]["state_after"] == "candidate"


def test_an_escalation_is_recorded_and_changes_nothing():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    result = executor.execute("promote", "billing-reconciler", operator="ola",
                              facts={"incidents": []}, query="promote it", now=NOW)

    assert result.determination.outcome == "ESCALATE"
    assert store.get("billing-reconciler").state == "candidate"
    assert len(ledger.read_all()) == 1


def test_the_state_change_is_rolled_back_when_the_record_cannot_be_written():
    """A decision that happened with nothing recording it is worse than a
    decision that did not happen."""
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = RecordingLedger(fail_with=RuntimeError("firestore is down"))
    executor = _executor(store, ledger)

    with pytest.raises(LedgerUnavailable):
        executor.execute("promote", "billing-reconciler", operator="ola",
                         facts=CLEAN_FACTS, query="promote it", now=NOW)

    assert store.get("billing-reconciler").state == "candidate"


def test_a_lost_tip_race_is_not_a_rollback():
    """The operation happened. Undoing it because another writer appended first
    would make concurrency into a policy decision."""
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = RecordingLedger(fail_with=ChainForked("someone else won"))
    executor = _executor(store, ledger)

    with pytest.raises(ChainForked):
        executor.execute("promote", "billing-reconciler", operator="ola",
                         facts=CLEAN_FACTS, query="promote it", now=NOW)

    assert store.get("billing-reconciler").state == "active"


def test_the_record_carries_the_retrieval_manifest():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    bank = FakeBank([_memory(i) for i in range(9)], population=9)
    executor = _executor(store, ledger, bank)

    executor.execute("promote", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="which one is failing", now=NOW)

    retrieval = ledger.read_all()[0]["body"]["retrieval"]
    assert retrieval["candidate_count"] == 9
    assert retrieval["excluded_count"] == 6
    assert len(retrieval["selected"]) == 3


def test_the_record_carries_every_field_a_reader_needs():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    executor.execute("promote", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="promote it", now=NOW)

    body = ledger.read_all()[0]["body"]
    assert set(body) == {"who", "what", "when", "why", "policy", "retrieval", "result"}
    assert body["who"]["operator"] == "ola"
    assert body["what"]["operation"] == "promote"
    assert body["policy"]["revision"] == "GAO-2026.08"
    assert body["result"] == {"state_before": "candidate", "state_after": "active",
                              "revision_before": "r7", "revision_after": "r7"}


def test_the_retrieval_manifest_is_built_before_the_gate_runs():
    """The manifest must describe what was in scope at decision time. Building it
    after the state change would describe a world the decision did not see."""
    order: list[str] = []

    class OrderedBank(FakeBank):
        def retrieve(self, scope, query):
            order.append("retrieve")
            return []

    class OrderedStore(InMemoryFleetStore):
        def put(self, agent):
            order.append("put")
            super().put(agent)

    class OrderedLedger(InMemoryLedger):
        def append(self, body):
            order.append("append")
            return super().append(body)

    store = OrderedStore({})
    store.put(CANDIDATE)
    order.clear()
    executor = Executor(store=store, ledger=OrderedLedger([]),
                        bank=OrderedBank(), scope=SCOPE)

    executor.execute("promote", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="promote it", now=NOW)

    assert order == ["retrieve", "put", "append"]


def test_an_unknown_agent_escalates_rather_than_raising():
    """An operator naming an agent that does not exist is a situation for a
    human, not a stack trace — and it must still leave a record."""
    ledger = InMemoryLedger([])
    executor = _executor(InMemoryFleetStore({}), ledger)

    result = executor.execute("promote", "no-such-agent", operator="ola",
                              facts=CLEAN_FACTS, query="promote it", now=NOW)

    assert result.determination.outcome == "ESCALATE"
    assert result.determination.rule_hits == ["AGT-000"]
    assert len(ledger.read_all()) == 1


def test_successive_decisions_form_one_verifiable_chain():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    executor.execute("promote", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="promote", now=NOW)
    executor.execute("quarantine", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="pull it", now=NOW,
                     cause="failing health checks")

    assert verify_chain(ledger.read_all()) == (True, "")
    assert store.get("billing-reconciler").state == "quarantined"


def test_registering_an_agent_adds_it_to_the_fleet_and_records_it():
    store = InMemoryFleetStore({})
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    result = executor.execute("register", "refund-router", operator="ola",
                              facts={}, query="register the refund router",
                              now=NOW, owner="platform-ops",
                              purpose="Routes refund requests to the right ledger.")

    assert result.determination.outcome == "PERMITTED"
    assert store.get("refund-router").state == "candidate"
    assert result.state_before == ""
    assert result.state_after == "candidate"
    assert len(ledger.read_all()) == 1


def test_registering_an_agent_that_already_exists_is_refused_not_overwritten():
    """An overwrite here would silently reassign ownership of a live agent."""
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    result = executor.execute("register", "billing-reconciler", operator="ola",
                              facts={}, query="register it", now=NOW,
                              owner="someone-else", purpose="Something else.")

    assert result.determination.outcome == "REFUSED"
    assert store.get("billing-reconciler").owner == "platform-ops"


def test_an_unknown_agent_still_escalates_for_every_other_operation():
    """Registration is the only operation for which a missing agent is normal."""
    ledger = InMemoryLedger([])
    executor = _executor(InMemoryFleetStore({}), ledger)

    result = executor.execute("promote", "no-such-agent", operator="ola",
                              facts=CLEAN_FACTS, query="promote it", now=NOW)

    assert result.determination.rule_hits == ["AGT-000"]


def test_a_rollback_actually_moves_the_revision():
    """The state is unchanged by design — the agent stays in service on
    different code — so if the revision does not move, a rollback is a no-op
    that records a success."""
    store = InMemoryFleetStore({})
    store.put(CANDIDATE.with_state("active"))
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    result = executor.execute("rollback", "billing-reconciler", operator="ola",
                              facts=CLEAN_FACTS, query="roll it back", now=NOW)

    assert result.determination.outcome == "PERMITTED"
    assert store.get("billing-reconciler").revision == "r6"
    assert store.get("billing-reconciler").state == "active"


def test_the_record_names_the_revision_a_rollback_moved_between():
    """A rollback that records only 'active -> active' is indistinguishable in
    the ledger from doing nothing at all."""
    store = InMemoryFleetStore({})
    store.put(CANDIDATE.with_state("active"))
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    executor.execute("rollback", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="roll it back", now=NOW)

    result = ledger.read_all()[0]["body"]["result"]
    assert result["revision_before"] == "r7"
    assert result["revision_after"] == "r6"


def test_a_second_rollback_is_refused_because_nothing_records_what_preceded():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE.with_state("active"))
    ledger = InMemoryLedger([])
    executor = _executor(store, ledger)

    executor.execute("rollback", "billing-reconciler", operator="ola",
                     facts=CLEAN_FACTS, query="roll back", now=NOW)
    result = executor.execute("rollback", "billing-reconciler", operator="ola",
                              facts=CLEAN_FACTS, query="roll back again", now=NOW)

    assert result.determination.outcome == "REFUSED"
    assert result.determination.rule_hits == ["REV-001"]
    assert store.get("billing-reconciler").revision == "r6"


def test_a_registration_is_undone_when_the_record_cannot_be_written():
    """Same rule as every other operation: a change that happened with nothing
    recording it is worse than one that did not happen. This path calls
    store.delete, which nothing else does — so without this test it would only
    run for the first time in production, on the day the ledger broke."""
    store = InMemoryFleetStore({})
    ledger = RecordingLedger(fail_with=RuntimeError("firestore is down"))
    executor = _executor(store, ledger)

    with pytest.raises(LedgerUnavailable):
        executor.execute("register", "refund-router", operator="ola", facts={},
                         query="register it", now=NOW, owner="platform-ops",
                         purpose="Routes refunds.")

    from ops.store import UnknownAgent

    with pytest.raises(UnknownAgent):
        store.get("refund-router")
