"""The deterministic policy gate.

A pure function over (operation, agent, facts, now). No I/O, no model, no clock
of its own — `now` is passed in so that a decision is reproducible from its
record rather than from the moment it is replayed.

POLICY_REVISION is never edited in place. A change to any rule below is a new
revision constant and a new policy document, because editing a policy changes
its hash and fails replay of already-recorded decisions for a reason unrelated
to their evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ops.store import AgentRecord

POLICY_REVISION = "GAO-2026.08"

OUTCOMES = ("PERMITTED", "REFUSED", "ESCALATE")


@dataclass(frozen=True)
class Determination:
    outcome: str
    rule_hits: list[str] = field(default_factory=list)
    blocking_condition: str = ""
    policy_revision: str = POLICY_REVISION

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"{self.outcome!r} is not one of {OUTCOMES}")


def _escalate(rule: str, condition: str) -> Determination:
    return Determination("ESCALATE", [rule], condition)


def _refuse(rule: str, condition: str) -> Determination:
    return Determination("REFUSED", [rule], condition)


def _evaluate_promote(agent: AgentRecord, facts: dict, now: str) -> Determination:
    # Absent facts are checked before failed conditions, and in a fixed order, so
    # that a knowable refusal never masks an unknowable one. An operator told
    # "refused: incident open" would never go looking for the missing
    # attestation record.
    if "attestation" not in facts:
        return _escalate("ATT-000", "No attestation record exists for this agent.")
    if "incidents" not in facts:
        return _escalate("INC-000", "No incident history exists for this agent.")

    if facts["attestation"].get("expires_at", "") < now:
        return _refuse(
            "ATT-001",
            f"Attestation expired on {facts['attestation'].get('expires_at')}.",
        )
    open_incidents = [i for i in facts["incidents"] if i.get("status") == "open"]
    if open_incidents:
        return _refuse(
            "INC-001",
            f"Open incident {open_incidents[0].get('id')} on this agent.",
        )
    return Determination("PERMITTED")


def _evaluate_quarantine(agent: AgentRecord, cause: str) -> Determination:
    # Nothing may block a quarantine — the operator must always be able to pull
    # an agent out of service. The only control is that they say why, because an
    # unexplained quarantine is indistinguishable in the record from an accident.
    if not cause.strip():
        return _refuse("CAU-001", "A quarantine must state its cause.")
    return Determination("PERMITTED")


def _evaluate_rollback(agent: AgentRecord) -> Determination:
    if not agent.previous_revision:
        return _refuse("REV-001", "No previous revision exists to roll back to.")
    return Determination("PERMITTED")


def evaluate(
    operation: str,
    agent: AgentRecord,
    facts: dict,
    now: str,
    cause: str = "",
) -> Determination:
    """Decide, deterministically, whether `operation` is permitted.

    Returns a Determination. An ordinary refusal is a result, not an exception:
    raising would make "the policy said no" and "the gate broke" the same event
    to a caller, and only one of those is a decision.
    """
    if operation == "promote":
        return _evaluate_promote(agent, facts, now)
    if operation == "quarantine":
        return _evaluate_quarantine(agent, cause)
    if operation == "rollback":
        return _evaluate_rollback(agent)
    return _refuse("OPS-001", f"{operation!r} is not an operation this policy governs.")
