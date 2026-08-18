"""The verifier: what a third party can establish from a record alone.

Standalone by construction. No credentials, no network, no client — it runs from
a clean clone against an exported bundle, because a verifier that needs our
service to run is not independent verification.

It reports a capability class, never a percentage. A score would invite a reader
to treat a weak record as a mostly-good one, and there is no such thing: either
the record binds its inputs by identity or it does not.

    BOUND                 identities, population, continuous chain, and the
                          manifest agrees with the exported trace
    BOUND_UNCORROBORATED  all of that, but no trace was exported
    UNBOUND               names what was used, but not which versioned record it
                          was, or not what it was drawn from
    NOT_CERTIFIED         chain broken, hash mismatch, or no retrieval field

BOUND_UNCORROBORATED exists because of a limit this project refuses to hide. The
manifest is built by the executor's own call to Memory Bank, not by observing the
agent's retrieval. Without a trace to compare against, "this is what was in
scope" and "this is what the model saw" are different claims, and collapsing
them would be the overclaim that makes the whole record untrustworthy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# Kept local rather than imported from ops.ledger, so this module stands alone in
# an exported bundle with nothing else beside it.
GENESIS = "genesis"

CAPABILITIES = ("BOUND", "BOUND_UNCORROBORATED", "UNBOUND", "NOT_CERTIFIED")

# Weakest last. A bundle takes the weakest class any of its records earns.
_ORDER = {name: index for index, name in enumerate(CAPABILITIES)}


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _record_hash(body: dict, prev_hash: str) -> str:
    return hashlib.sha256(_canonical({"body": body, "prev_hash": prev_hash})).hexdigest()


@dataclass(frozen=True)
class Verdict:
    capability: str
    reason: str
    record_hash: str = ""


@dataclass(frozen=True)
class BundleVerdict:
    capability: str
    reason: str
    records: list[Verdict] = field(default_factory=list)


def verify_record(entry: dict, trace: dict | None) -> Verdict:
    """Classify one record. `trace` is the exported platform trace, or None."""
    digest = entry.get("hash", "")

    if _record_hash(entry["body"], entry["prev_hash"]) != digest:
        return Verdict("NOT_CERTIFIED",
                       "hash mismatch: the body does not produce the recorded hash",
                       digest)

    manifest = entry["body"].get("retrieval")
    if manifest is None:
        # Absent is not empty. A missing field cannot be distinguished from a
        # component that never ran, so it certifies nothing.
        return Verdict("NOT_CERTIFIED",
                       "no retrieval field: the record does not say what it was "
                       "decided from", digest)

    if "candidate_count" not in manifest:
        return Verdict("UNBOUND",
                       "no population: the record names its selections but not "
                       "what they were drawn from", digest)

    for selection in manifest.get("selected", []):
        if not selection.get("revision"):
            return Verdict("UNBOUND",
                           "a selection carries no revision identity, so it cannot "
                           "be bound to a versioned record", digest)

    if trace is None:
        return Verdict("BOUND_UNCORROBORATED",
                       "no trace was exported, so the manifest is unchecked against "
                       "what the model was actually shown", digest)

    recorded = {s["fact_sha256"] for s in manifest.get("selected", [])}
    injected = set(trace.get("injected_fact_sha256", []))
    if recorded != injected:
        return Verdict("UNBOUND",
                       f"manifest and trace disagree: {len(recorded)} recorded, "
                       f"{len(injected)} injected, "
                       f"{len(recorded & injected)} in common", digest)

    return Verdict("BOUND",
                   "identities, population and chain intact; manifest agrees with "
                   "the trace", digest)


def verify_bundle(bundle: dict) -> BundleVerdict:
    """Classify an exported bundle: every record, plus the chain that links them."""
    entries = bundle.get("records", [])
    traces = bundle.get("traces", {})

    verdicts = [verify_record(e, traces.get(e.get("hash", ""))) for e in entries]

    previous = GENESIS
    for index, entry in enumerate(entries):
        if entry["prev_hash"] != previous:
            return BundleVerdict(
                "NOT_CERTIFIED",
                f"chain broken at record {index}: prev_hash does not follow the "
                f"record before it",
                verdicts,
            )
        previous = entry["hash"]

    if not verdicts:
        return BundleVerdict("NOT_CERTIFIED", "the bundle contains no records", [])

    weakest = max(verdicts, key=lambda v: _ORDER[v.capability])
    return BundleVerdict(weakest.capability, weakest.reason, verdicts)
