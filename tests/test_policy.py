"""The deterministic policy gate.

The gate decides; the model never does. Two properties carry most of the weight
and are asserted rather than documented: the gate is a pure function of its
inputs, and it distinguishes an absent fact from a failed condition. Collapsing
that distinction is the failure mode this component exists to avoid — an agent
with no attestation on file and an agent whose attestation expired are not the
same situation, and reporting both as a refusal hides the one a human must look
at.
"""

from __future__ import annotations

import pytest

from ops.policy import POLICY_REVISION, evaluate
from ops.store import AgentRecord

CANDIDATE = AgentRecord(
    agent_id="billing-reconciler",
    state="candidate",
    owner="platform-ops",
    purpose="Reconciles invoice lines against settled payments.",
    revision="r7",
    previous_revision="r6",
)

CLEAN = {"attestation": {"expires_at": "2026-12-01"}, "incidents": []}


def test_a_clean_candidate_may_be_promoted():
    determination = evaluate("promote", CANDIDATE, CLEAN, now="2026-08-18")
    assert determination.outcome == "PERMITTED"
    assert determination.rule_hits == []


def test_an_absent_attestation_escalates_rather_than_refusing():
    """Absence is reported as absence. There is no attestation record at all,
    so the policy cannot determine safely and a human must look."""
    determination = evaluate("promote", CANDIDATE, {"incidents": []}, now="2026-08-18")
    assert determination.outcome == "ESCALATE"
    assert determination.rule_hits == ["ATT-000"]


def test_an_expired_attestation_is_refused_not_escalated():
    """A failed condition, not an absent fact. The record exists and says no."""
    facts = {"attestation": {"expires_at": "2026-07-01"}, "incidents": []}
    determination = evaluate("promote", CANDIDATE, facts, now="2026-08-18")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["ATT-001"]


def test_an_open_incident_refuses_promotion():
    facts = {"attestation": {"expires_at": "2026-12-01"},
             "incidents": [{"id": "INC-4", "status": "open"}]}
    determination = evaluate("promote", CANDIDATE, facts, now="2026-08-18")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["INC-001"]


def test_absent_incident_history_escalates():
    """No incident list at all is not the same as an empty incident list."""
    determination = evaluate(
        "promote", CANDIDATE, {"attestation": {"expires_at": "2026-12-01"}},
        now="2026-08-18",
    )
    assert determination.outcome == "ESCALATE"
    assert determination.rule_hits == ["INC-000"]


def test_an_absent_fact_is_reported_before_a_failed_condition():
    """Both wrong at once: the attestation is missing AND an incident is open.
    The absence must win, because a knowable refusal would otherwise mask the
    unknowable one and nobody would go looking for the missing record."""
    facts = {"incidents": [{"id": "INC-4", "status": "open"}]}
    determination = evaluate("promote", CANDIDATE, facts, now="2026-08-18")
    assert determination.outcome == "ESCALATE"
    assert determination.rule_hits == ["ATT-000"]


def test_quarantine_is_always_permitted_and_carries_its_cause():
    determination = evaluate("quarantine", CANDIDATE, {}, now="2026-08-18",
                             cause="failing health checks since 03:14")
    assert determination.outcome == "PERMITTED"
    assert determination.blocking_condition == ""


def test_quarantine_without_a_stated_cause_is_refused():
    """Quarantine is the one operation no fact can block, so the only control on
    it is that the operator must say why. An unexplained quarantine is
    indistinguishable in the record from an accident."""
    determination = evaluate("quarantine", CANDIDATE, {}, now="2026-08-18", cause="")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["CAU-001"]


def test_rollback_requires_a_previous_revision():
    active = CANDIDATE.with_state("active")
    no_previous = AgentRecord(
        agent_id=active.agent_id, state="active", owner=active.owner,
        purpose=active.purpose, revision="r1", previous_revision=None,
    )
    determination = evaluate("rollback", no_previous, CLEAN, now="2026-08-18")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["REV-001"]


def test_the_determination_names_the_policy_revision_that_produced_it():
    determination = evaluate("promote", CANDIDATE, CLEAN, now="2026-08-18")
    assert determination.policy_revision == POLICY_REVISION


def test_the_gate_is_pure():
    """Same inputs, same determination — twice, with the inputs untouched."""
    facts = {"attestation": {"expires_at": "2026-12-01"}, "incidents": []}
    first = evaluate("promote", CANDIDATE, facts, now="2026-08-18")
    second = evaluate("promote", CANDIDATE, facts, now="2026-08-18")
    assert first == second
    assert facts == {"attestation": {"expires_at": "2026-12-01"}, "incidents": []}


def test_an_unknown_operation_is_refused_rather_than_permitted_by_default():
    determination = evaluate("delete_everything", CANDIDATE, CLEAN, now="2026-08-18")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["OPS-001"]


def test_registration_requires_an_owner():
    """An agent nobody owns is an agent nobody will quarantine when it
    misbehaves. Ownership is the one fact that makes the rest of the lifecycle
    actionable, so it is checked at the only point where it can still be
    refused cheaply."""
    determination = evaluate("register", None, {}, now="2026-08-18",
                             owner="", purpose="Reconciles invoices.")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["OWN-001"]


def test_registration_requires_a_purpose():
    determination = evaluate("register", None, {}, now="2026-08-18",
                             owner="platform-ops", purpose="   ")
    assert determination.outcome == "REFUSED"
    assert determination.rule_hits == ["PUR-001"]


def test_a_complete_registration_is_permitted():
    determination = evaluate("register", None, {}, now="2026-08-18",
                             owner="platform-ops", purpose="Reconciles invoices.")
    assert determination.outcome == "PERMITTED"


def test_registration_does_not_require_an_attestation():
    """A candidate has not been attested yet — that is what promotion is for.
    Demanding one at registration would make the fleet unenterable."""
    determination = evaluate("register", None, {}, now="2026-08-18",
                             owner="platform-ops", purpose="Reconciles invoices.")
    assert determination.rule_hits == []
