"""What the model was actually shown, read from the platform's own trace.

This exists to close the project's one honest gap. The retrieval manifest is
built by the executor's own call to Memory Bank: it says what was *in scope*,
not what the model *saw*. Until the two can be compared, the strongest class a
record can earn is BOUND_UNCORROBORATED.

Cloud Trace carries the other half. The `call_llm` span holds the full request
in `gcp.vertex.agent.llm_request`, and ADK's memory tool injects retrieved facts
into the prompt inside a `<PAST_CONVERSATIONS>` block — one entry per fact, each
a `Time:` line followed by a `role: fact` line. Stripping that prefix and hashing
what remains produces values directly comparable to the manifest's
`fact_sha256`, which lets the verifier report agreement or disagreement instead
of declining to look.

Two things this deliberately does not do. It never raises on a trace it cannot
parse — a turn where retrieval returned nothing is a real turn, and making that
indistinguishable from an unreadable trace would defeat the purpose. And it does
not reconcile anything: comparison belongs to the verifier, which is the
component a third party runs.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

LLM_SPAN = "call_llm"
REQUEST_ATTRIBUTE = "gcp.vertex.agent.llm_request"

_BLOCK = re.compile(r"<PAST_CONVERSATIONS>\n(.*?)</PAST_CONVERSATIONS>", re.DOTALL)
# Each entry is a Time: line followed by "role: the fact". The role is whatever
# ADK used when it wrote the memory, so it is matched rather than assumed.
_ENTRY = re.compile(r"^Time:[^\n]*\n(?:[A-Za-z_]+):[ ]?(.*)$", re.MULTILINE)


def _request_payloads(trace: dict[str, Any]) -> list[str]:
    return [
        span["labels"][REQUEST_ATTRIBUTE]
        for span in trace.get("spans", [])
        if span.get("name", "").startswith(LLM_SPAN)
        and REQUEST_ATTRIBUTE in (span.get("labels") or {})
    ]


def extract_injected_facts(trace: dict[str, Any]) -> list[str]:
    """Return the facts injected into the prompt, in order, without duplicates.

    A turn that calls a tool makes two model requests and the memory block is
    carried into both, so the same fact appears twice in one trace. Counting it
    twice would make the trace disagree with a manifest that is correct.
    """
    facts: list[str] = []
    for payload in _request_payloads(trace):
        try:
            request = json.loads(payload)
        except (TypeError, ValueError):
            # A truncated or malformed attribute is skipped, not fatal. Span
            # attribute values are capped by the exporter, so this is an
            # expected condition on a long prompt rather than a broken trace.
            continue
        for content in request.get("contents", []):
            for part in content.get("parts", []):
                text = part.get("text")
                if not text:
                    continue
                for block in _BLOCK.findall(text):
                    for fact in _ENTRY.findall(block):
                        stripped = fact.strip()
                        if stripped and stripped not in facts:
                            facts.append(stripped)
    return facts


def injected_fact_hashes(trace: dict[str, Any]) -> list[str]:
    """SHA-256 of each injected fact, hashed exactly as the manifest hashes it."""
    return [
        hashlib.sha256(fact.encode("utf-8")).hexdigest()
        for fact in extract_injected_facts(trace)
    ]


class CloudTrace:
    """Fetches traces for a reasoning engine. Read-only, and only used by the
    export path — the verifier never needs credentials."""

    def __init__(self, project: str, session: Any = None) -> None:
        self._project = project
        self._session = session

    def _authed(self):
        if self._session is None:
            import google.auth
            import google.auth.transport.requests as gtr

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self._session = gtr.AuthorizedSession(credentials)
        return self._session

    def recent(self, start_time: str, end_time: str, limit: int = 20) -> list[dict]:
        """Return recent traces, newest first, with their spans."""
        listing = self._authed().get(
            f"https://cloudtrace.googleapis.com/v1/projects/{self._project}/traces",
            params={"startTime": start_time, "endTime": end_time,
                    "pageSize": limit, "orderBy": "start desc", "view": "ROOTSPAN"},
        )
        listing.raise_for_status()

        traces = []
        for summary in listing.json().get("traces", []):
            detail = self._authed().get(
                f"https://cloudtrace.googleapis.com/v1/projects/{self._project}"
                f"/traces/{summary['traceId']}")
            detail.raise_for_status()
            traces.append(detail.json())
        return traces
