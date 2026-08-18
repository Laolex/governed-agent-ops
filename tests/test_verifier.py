"""The verifier: what a third party can establish from a record alone.

It reports a capability class, never a percentage and never a score. A record it
cannot bind is reported as one it cannot bind — the verifier refuses rather than
guessing, because a number would invite a reader to treat a weak record as a
mostly-good one.

The four classes are ordered by what the evidence supports:

  BOUND                  identities, population, continuous chain, and the
                         manifest agrees with the exported trace
  BOUND_UNCORROBORATED   all of the above, but no trace was exported, so the
                         manifest is unchecked against what the model actually saw
  UNBOUND                the record names what was used but not which versioned
                         record it was, or not what it was drawn from
  NOT_CERTIFIED          the chain is broken, a hash does not match, or the
                         retrieval field is absent entirely

The distinction between BOUND and BOUND_UNCORROBORATED is the one that keeps the
project honest. The executor's manifest comes from its own call to Memory Bank,
not from observing the agent's retrieval, so without a trace to compare against,
"this is what was in scope" is not the same claim as "this is what the model
saw" — and the verifier must not let the two blur.
"""

from __future__ import annotations

import hashlib

import pytest

from ops.ledger import GENESIS, record_hash
from ops.verifier import verify_bundle, verify_record

FACT = "The dunning writer started failing after last night's config change."
FACT_SHA = hashlib.sha256(FACT.encode()).hexdigest()


def _manifest(*, selected=True, identity=True, population=True):
    selection = {
        "memory": "projects/p/locations/l/reasoningEngines/e/memories/7",
        "revision": ("projects/p/locations/l/reasoningEngines/e/memories/7"
                     "/revisions/700") if identity else None,
        "revision_unresolved": not identity,
        "fact_sha256": FACT_SHA,
        "distance": 0.81,
    }
    manifest = {
        "scope": {"app_name": "e", "user_id": "platform-ops"},
        "query": "what started failing",
        "selected": [selection] if selected else [],
        "retrieved_at": "2026-08-18T18:00:00Z",
        "excluded_count": 17,
    }
    if population:
        manifest["candidate_count"] = 18
    return manifest


def _body(manifest=None, drop_retrieval=False):
    body = {
        "who": {"operator": "ola", "agent": "ops-agent"},
        "what": {"operation": "quarantine", "target": "dunning-writer", "cause": "failing"},
        "when": {"decided_at": "2026-08-18T18:00:00Z"},
        "why": {"rule_hits": [], "blocking_condition": "", "outcome": "PERMITTED"},
        "policy": {"revision": "GAO-2026.08"},
        "retrieval": manifest if manifest is not None else _manifest(),
        "result": {"state_before": "active", "state_after": "quarantined"},
    }
    if drop_retrieval:
        del body["retrieval"]
    return body


def _entry(body, prev=GENESIS):
    return {"body": body, "prev_hash": prev, "hash": record_hash(body, prev)}


def test_a_full_record_with_a_matching_trace_is_bound():
    entry = _entry(_body())
    trace = {"injected_fact_sha256": [FACT_SHA]}

    assert verify_record(entry, trace=trace).capability == "BOUND"


def test_a_full_record_with_no_trace_is_bound_but_uncorroborated():
    """The manifest is the executor's own second call to Memory Bank. Without a
    trace it cannot be checked against what the model actually saw, and saying
    so is the difference between this project and one that overclaims."""
    result = verify_record(_entry(_body()), trace=None)

    assert result.capability == "BOUND_UNCORROBORATED"
    assert "trace" in result.reason.lower()


def test_a_trace_that_disagrees_with_the_manifest_is_reported_not_absorbed():
    """A disagreement is the most interesting thing the verifier can find. It
    must never be smoothed into a pass."""
    entry = _entry(_body())
    trace = {"injected_fact_sha256": [hashlib.sha256(b"something else").hexdigest()]}

    result = verify_record(entry, trace=trace)

    assert result.capability == "UNBOUND"
    assert "disagree" in result.reason.lower()


def test_a_selection_without_a_revision_identity_is_unbound():
    """Invariant 1. The text can be reproduced from an identity; an identity
    cannot be reconstructed from text, so a record naming neither binds nothing."""
    result = verify_record(_entry(_body(_manifest(identity=False))), trace=None)

    assert result.capability == "UNBOUND"
    assert "revision" in result.reason.lower()


def test_a_record_without_a_population_is_unbound():
    """Invariant 2. Selections alone cannot distinguish 'nothing else existed'
    from 'something else existed and lost'."""
    result = verify_record(_entry(_body(_manifest(population=False))), trace=None)

    assert result.capability == "UNBOUND"
    assert "population" in result.reason.lower()


def test_an_empty_retrieval_is_bound_when_it_says_so():
    """Invariant 3. Retrieval that returned nothing, recorded as nothing with the
    scope it searched, is a complete record — not a deficient one."""
    empty = _manifest(selected=False)
    empty["candidate_count"] = 0
    empty["excluded_count"] = 0

    result = verify_record(_entry(_body(empty)), trace={"injected_fact_sha256": []})

    assert result.capability == "BOUND"


def test_a_record_with_no_retrieval_field_is_not_certified():
    """An absent field is not an empty one. It cannot be distinguished from a
    component that never ran."""
    result = verify_record(_entry(_body(drop_retrieval=True)), trace=None)

    assert result.capability == "NOT_CERTIFIED"


def test_a_mutated_body_is_not_certified():
    entry = _entry(_body())
    entry["body"]["who"]["operator"] = "someone-else"

    result = verify_record(entry, trace=None)

    assert result.capability == "NOT_CERTIFIED"
    assert "hash" in result.reason.lower()


def test_the_verifier_never_reports_a_number():
    """Per the standing framing rule: a capability class, never a percentage. A
    score would invite a reader to treat a weak record as a mostly-good one."""
    result = verify_record(_entry(_body()), trace=None)

    assert not hasattr(result, "score")
    assert "%" not in result.reason


def test_a_bundle_takes_the_weakest_class_of_any_record():
    """One unbindable record is not diluted by ten good ones."""
    first = _entry(_body())
    second = _entry(_body(_manifest(identity=False)), prev=first["hash"])
    bundle = {"records": [first, second], "traces": {}}

    assert verify_bundle(bundle).capability == "UNBOUND"


def test_a_broken_chain_is_not_certified_however_good_the_records_are():
    first = _entry(_body())
    orphan = _entry(_body(), prev="0" * 64)
    bundle = {"records": [first, orphan], "traces": {}}

    result = verify_bundle(bundle)

    assert result.capability == "NOT_CERTIFIED"
    assert "chain" in result.reason.lower()


def test_a_bundle_reports_every_record_not_only_the_verdict():
    first = _entry(_body())
    bundle = {"records": [first], "traces": {first["hash"]: {"injected_fact_sha256": [FACT_SHA]}}}

    result = verify_bundle(bundle)

    assert len(result.records) == 1
    assert result.records[0].capability == "BOUND"


def test_the_verifier_needs_no_credentials_and_no_network():
    """It runs from a clean clone against an exported bundle. Importing it must
    not reach for a client."""
    import ops.verifier

    source = open(ops.verifier.__file__).read()
    assert "firestore" not in source.lower()
    assert "google" not in source.lower()
