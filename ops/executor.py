"""The executor: the only component that writes.

The agent cannot reach this. It proposes; the executor decides whether the
proposal happens, performs it, and records it. That separation is what makes the
agent's read-only guarantee structural rather than a line in a system prompt.

Ordering is the component. Retrieval is captured first, because the manifest has
to describe what was in scope at decision time rather than afterwards. Then the
gate. Then the state change. Then the record.

The three failure modes have three different correct responses, and conflating
them is the mistake this module exists to avoid:

  - the gate refuses        no state change, but the refusal is recorded
  - the ledger is broken    the state change is rolled back
  - the ledger lost a race  retried by the ledger, never rolled back

The last one matters more than it looks: a measured race of eight concurrent
writers had six lose the tip. Rolling back on a lost race would make concurrency
into a policy decision, undoing operations that were permitted and performed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ops.fleet import InvalidTransition, transition
from ops.ledger import ChainForked, Ledger
from ops.policy import Determination, evaluate
from ops.retrieval import MemoryBank, RetrievalManifest, build_manifest
from ops.store import AgentRecord, FleetStore, UnknownAgent


class LedgerUnavailable(Exception):
    """The record could not be written, and the state change was undone."""


@dataclass(frozen=True)
class Result:
    determination: Determination
    record_hash: str
    manifest: RetrievalManifest
    state_before: str
    state_after: str


class Executor:
    def __init__(self, store: FleetStore, ledger: Ledger, bank: MemoryBank,
                 scope: dict) -> None:
        self._store = store
        self._ledger = ledger
        self._bank = bank
        self._scope = scope

    def execute(self, operation: str, agent_id: str, *, operator: str, facts: dict,
                query: str, now: str, cause: str = "", owner: str = "",
                purpose: str = "") -> Result:
        manifest = build_manifest(self._bank, self._scope, query, now)

        if operation == "register":
            return self._register(agent_id, operator, manifest, now, owner, purpose)

        try:
            agent = self._store.get(agent_id)
        except UnknownAgent:
            # Absence, not an error. An operator naming an agent that does not
            # exist is a situation for a human — and it still leaves a record,
            # because "nothing happened" and "nobody asked" are different.
            return self._record(
                Determination("ESCALATE", ["AGT-000"],
                              f"No agent {agent_id!r} is registered in the fleet."),
                operation, agent_id, operator, manifest, now, cause,
                state_before="", state_after="",
            )

        determination = evaluate(operation, agent, facts, now=now, cause=cause)

        if determination.outcome != "PERMITTED":
            return self._record(determination, operation, agent_id, operator,
                                manifest, now, cause,
                                state_before=agent.state, state_after=agent.state)

        try:
            destination = transition(agent.state, operation)
        except InvalidTransition as invalid:
            return self._record(
                Determination("REFUSED", ["FSM-001"], str(invalid)),
                operation, agent_id, operator, manifest, now, cause,
                state_before=agent.state, state_after=agent.state,
            )

        # A rollback leaves the state alone and moves the revision instead: the
        # agent stays in service, on different code. Recording only
        # "active -> active" would make it indistinguishable from doing nothing.
        updated = (agent.rolled_back() if operation == "rollback"
                   else agent.with_state(destination))
        self._store.put(updated)
        try:
            return self._record(determination, operation, agent_id, operator,
                                manifest, now, cause,
                                state_before=agent.state, state_after=destination,
                                revision_before=agent.revision,
                                revision_after=updated.revision)
        except ChainForked:
            # The ledger already retried at the re-read tip and still lost. The
            # operation happened; undoing it here would make concurrency into a
            # policy decision. Surface it and leave the state change standing.
            raise
        except Exception as failure:
            self._store.put(agent)
            raise LedgerUnavailable(
                f"record could not be written, state change undone: {failure}"
            ) from failure

    def _register(self, agent_id: str, operator: str, manifest: RetrievalManifest,
                  now: str, owner: str, purpose: str) -> Result:
        """Admit a new agent. The only operation for which a missing agent is
        normal — and the only one where an existing agent is the error, because
        an overwrite here would silently reassign ownership of a live agent."""
        try:
            self._store.get(agent_id)
        except UnknownAgent:
            pass
        else:
            return self._record(
                Determination("REFUSED", ["AGT-001"],
                              f"{agent_id!r} is already registered."),
                "register", agent_id, operator, manifest, now, "",
                state_before="", state_after="")

        determination = evaluate("register", None, {}, now=now,
                                 owner=owner, purpose=purpose)
        if determination.outcome != "PERMITTED":
            return self._record(determination, "register", agent_id, operator,
                                manifest, now, "", state_before="", state_after="")

        destination = transition("", "register")
        self._store.put(AgentRecord(agent_id=agent_id, state=destination, owner=owner,
                                    purpose=purpose, revision="r1",
                                    previous_revision=None))
        try:
            return self._record(determination, "register", agent_id, operator,
                                manifest, now, "", state_before="",
                                state_after=destination, revision_before="",
                                revision_after="r1")
        except ChainForked:
            raise
        except Exception as failure:
            self._store.delete(agent_id)
            raise LedgerUnavailable(
                f"record could not be written, registration undone: {failure}"
            ) from failure

    def _record(self, determination: Determination, operation: str, agent_id: str,
                operator: str, manifest: RetrievalManifest, now: str, cause: str,
                state_before: str, state_after: str, revision_before: str = "",
                revision_after: str = "") -> Result:
        body = {
            "who": {"operator": operator, "agent": "ops-agent"},
            "what": {"operation": operation, "target": agent_id, "cause": cause},
            "when": {"decided_at": now},
            "why": {
                "rule_hits": list(determination.rule_hits),
                "blocking_condition": determination.blocking_condition,
                "outcome": determination.outcome,
            },
            "policy": {"revision": determination.policy_revision},
            "retrieval": manifest.to_dict(),
            "result": {"state_before": state_before, "state_after": state_after,
                       "revision_before": revision_before,
                       "revision_after": revision_after},
        }
        digest = self._ledger.append(body)
        return Result(determination, digest, manifest, state_before, state_after)
