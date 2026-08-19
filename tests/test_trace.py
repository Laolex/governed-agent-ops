"""Extracting what the model was actually shown, from the platform's own trace.

This closes the project's one honest gap. The retrieval manifest is built by the
executor's own call to Memory Bank — it describes what was *in scope*, not what
the model *saw*. Until those two can be compared, the strongest class a record
can earn is BOUND_UNCORROBORATED.

Cloud Trace does carry the answer: the `call_llm` span's
`gcp.vertex.agent.llm_request` attribute contains the full request, and ADK's
memory tool injects retrieved facts inside a `<PAST_CONVERSATIONS>` block, one
per line, prefixed with a timestamp and a role. Hashing those lines gives
something directly comparable to the manifest's `fact_sha256` values.

The parsing is tested against the real shape, captured from a live span, because
a parser written against an imagined format is a parser that agrees with itself.
"""

from __future__ import annotations

import hashlib
import json

from ops.trace import extract_injected_facts, injected_fact_hashes

FACT = "The episode currently in release review is NORTHSTAR-S01E08."

# The exact shape observed in a live `call_llm` span on 2026-08-18.
LIVE_SHAPE = json.dumps({
    "model": "gemini-3.5-flash",
    "config": {"system_instruction": "You determine release readiness."},
    "contents": [
        {"parts": [{"text": (
            "The following content is from your previous conversations with the user.\n"
            "They may be useful for answering the user's current query.\n"
            "<PAST_CONVERSATIONS>\n"
            f"Time: 2026-08-18T17:46:39.993630+00:00\nuser: {FACT}\n"
            "</PAST_CONVERSATIONS>\n")}], "role": "user"},
        {"parts": [{"text": "Is it cleared?"}], "role": "user"},
    ],
})


def _span(llm_request: str) -> dict:
    return {"spans": [{"name": "call_llm",
                       "labels": {"gcp.vertex.agent.llm_request": llm_request}}]}


def test_the_injected_fact_is_recovered_from_a_real_span():
    assert extract_injected_facts(_span(LIVE_SHAPE)) == [FACT]


def test_the_timestamp_and_role_prefix_are_stripped():
    """The manifest hashes the fact as Memory Bank stores it. If the prefix
    survives, every comparison disagrees and the verifier reports a divergence
    that is really a parsing bug."""
    facts = extract_injected_facts(_span(LIVE_SHAPE))
    assert not facts[0].startswith("Time:")
    assert not facts[0].startswith("user:")


def test_several_injected_facts_are_recovered_in_order():
    block = ("<PAST_CONVERSATIONS>\n"
             "Time: 2026-08-18T17:00:00+00:00\nuser: First fact.\n"
             "Time: 2026-08-18T17:01:00+00:00\nuser: Second fact.\n"
             "</PAST_CONVERSATIONS>\n")
    payload = json.dumps({"contents": [{"parts": [{"text": block}], "role": "user"}]})

    assert extract_injected_facts(_span(payload)) == ["First fact.", "Second fact."]


def test_a_turn_with_no_memory_block_yields_an_empty_list_not_an_error():
    """A turn where retrieval returned nothing is a real turn, and its trace is
    a real trace. Raising here would make 'no memories' indistinguishable from
    'the trace could not be read'."""
    payload = json.dumps({"contents": [{"parts": [{"text": "Just a question."}]}]})

    assert extract_injected_facts(_span(payload)) == []


def test_spans_that_are_not_llm_calls_are_ignored():
    trace = {"spans": [
        {"name": "execute_tool check_release",
         "labels": {"gcp.vertex.agent.tool_response": "{}"}},
        {"name": "call_llm", "labels": {"gcp.vertex.agent.llm_request": LIVE_SHAPE}},
    ]}

    assert extract_injected_facts(trace) == [FACT]


def test_a_fact_repeated_across_two_llm_calls_is_counted_once():
    """A turn with a tool call makes two model requests, and the memory block is
    carried into both. Counting it twice would make the trace disagree with a
    manifest that is correct."""
    trace = {"spans": [
        {"name": "call_llm", "labels": {"gcp.vertex.agent.llm_request": LIVE_SHAPE}},
        {"name": "call_llm", "labels": {"gcp.vertex.agent.llm_request": LIVE_SHAPE}},
    ]}

    assert extract_injected_facts(trace) == [FACT]


def test_the_hashes_line_up_with_what_the_manifest_records():
    """The whole point: these two hashes are computed the same way, so the
    verifier can compare them."""
    expected = hashlib.sha256(FACT.encode("utf-8")).hexdigest()

    assert injected_fact_hashes(_span(LIVE_SHAPE)) == [expected]


def test_a_malformed_request_attribute_is_skipped_rather_than_fatal():
    trace = {"spans": [
        {"name": "call_llm", "labels": {"gcp.vertex.agent.llm_request": "not json"}},
        {"name": "call_llm", "labels": {"gcp.vertex.agent.llm_request": LIVE_SHAPE}},
    ]}

    assert extract_injected_facts(trace) == [FACT]
