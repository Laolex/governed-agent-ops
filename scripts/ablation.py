#!/usr/bin/env python3
"""Is the retrieval binding doing any work?

Four arms through the production verifier. The intact record is classified, then
one thing is removed at a time and it is classified again. If the stripped arms
still certify, the binding is decoration and this script says so — the point is
to test our own necessity, not to demonstrate it.

Runs on bare `python3` from a clean clone. No credentials, no network, no
dependencies. Exits non-zero if any arm lands where it should not.

    python3 scripts/ablation.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.verifier import verify_bundle  # noqa: E402

FACT = "The dunning writer started failing after last night's config change."
FACT_SHA = hashlib.sha256(FACT.encode()).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(body: dict, prev: str = "genesis") -> dict:
    digest = hashlib.sha256(_canonical({"body": body, "prev_hash": prev})).hexdigest()
    return {"body": body, "prev_hash": prev, "hash": digest}


BODY = {
    "who": {"operator": "ola", "agent": "ops-agent"},
    "what": {"operation": "quarantine", "target": "dunning-writer",
             "cause": "failing health checks since 03:14"},
    "when": {"decided_at": "2026-08-18T18:00:00Z"},
    "why": {"rule_hits": [], "blocking_condition": "", "outcome": "PERMITTED"},
    "policy": {"revision": "GAO-2026.08"},
    "retrieval": {
        "scope": {"app_name": "engine", "user_id": "platform-ops"},
        "query": "what started failing last night",
        "candidate_count": 18,
        "excluded_count": 17,
        "selected": [{
            "memory": "projects/p/locations/l/reasoningEngines/e/memories/7",
            "revision": "projects/p/locations/l/reasoningEngines/e/memories/7/revisions/700",
            "revision_unresolved": False,
            "fact_sha256": FACT_SHA,
            "distance": 0.81,
        }],
        "retrieved_at": "2026-08-18T18:00:00Z",
    },
    "result": {"state_before": "active", "state_after": "quarantined"},
}


def _bundle(body: dict) -> dict:
    entry = _seal(body)
    return {"records": [entry],
            "traces": {entry["hash"]: {"injected_fact_sha256": [FACT_SHA]}}}


def intact() -> dict:
    return _bundle(copy.deepcopy(BODY))


def identities_stripped() -> dict:
    """Keep the fact text's hash, drop the revision identity. This is what an
    ordinary observability record looks like: it knows what the model was shown
    and not which versioned record it came from."""
    body = copy.deepcopy(BODY)
    for selection in body["retrieval"]["selected"]:
        selection["revision"] = None
        selection["revision_unresolved"] = True
    return _bundle(body)


def population_stripped() -> dict:
    """Keep the selections, drop what they were drawn from. The record can no
    longer distinguish 'nothing else existed' from 'something else lost'."""
    body = copy.deepcopy(BODY)
    del body["retrieval"]["candidate_count"]
    return _bundle(body)


def manifest_removed() -> dict:
    """No retrieval field at all — a record of the decision with no account of
    what produced it."""
    body = copy.deepcopy(BODY)
    del body["retrieval"]
    return _bundle(body)


ARMS = [
    ("intact", intact, "BOUND"),
    ("identities stripped", identities_stripped, "UNBOUND"),
    ("population stripped", population_stripped, "UNBOUND"),
    ("manifest removed", manifest_removed, "NOT_CERTIFIED"),
]


def main() -> int:
    print("Ablation — does the retrieval binding change what can be proven?\n")
    failures = 0
    for name, build, expected in ARMS:
        verdict = verify_bundle(build())
        ok = verdict.capability == expected
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {verdict.capability:<22} "
              f"(expected {expected})")
        print(f"        {verdict.reason}")

    print()
    if failures:
        print(f"{failures} arm(s) landed where they should not. Either the verifier "
              f"changed or the binding is not doing what this claims.")
        return 1

    print("The intact record binds. Removing the revision identities, or the\n"
          "population they were drawn from, costs it that binding — so the\n"
          "binding is what earns the classification, not the rest of the record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
