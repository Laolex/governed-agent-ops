"""Operational facts about an agent: its attestation and its incident history.

The only property that matters here is that a missing document stays missing.
Returning an empty attestation or an empty incident list for an agent nothing is
known about would turn "we have no record" into "we checked and it is fine",
which is the exact collapse the policy gate's ESCALATE path exists to prevent.
"""

from __future__ import annotations

from ops.facts import InMemoryFactsStore


def test_known_facts_are_returned_as_written():
    facts = InMemoryFactsStore({"billing-reconciler": {
        "attestation": {"expires_at": "2026-12-01"}, "incidents": []}})
    assert facts.for_agent("billing-reconciler")["attestation"]["expires_at"] == "2026-12-01"


def test_an_agent_with_no_facts_yields_an_empty_mapping_not_empty_fields():
    """Absent must reach the gate as absent. An empty dict has no 'attestation'
    key at all, which is what makes the gate escalate rather than refuse."""
    facts = InMemoryFactsStore({})

    result = facts.for_agent("never-seen")

    assert result == {}
    assert "attestation" not in result
    assert "incidents" not in result


def test_partial_facts_stay_partial():
    """An agent with an incident history but no attestation must not acquire
    one on the way through."""
    facts = InMemoryFactsStore({"a": {"incidents": []}})
    assert facts.for_agent("a") == {"incidents": []}
