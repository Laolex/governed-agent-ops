"""Live ablation over a real ledger record.

Mirrors `scripts/ablation.py` but operates on an actual record from the ledger
rather than the synthetic BODY, so the demo can run the necessity test against
a real decision in the console. The client needs no credentials: this runs
inside the service, and the service exposes only the verdicts.

Each arm mutates a copy of the record's body, re-seals it (a changed body must
produce a changed hash, or the verifier would report NOT_CERTIFIED for a reason
unrelated to the binding), and classifies it. If the intact record certifies and
every stripped arm does not, the binding is doing the work. If a stripped arm
still certifies, the binding is decoration and this says so — the point is to
test our own necessity, not to demonstrate it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def _seal(body: dict, prev: str) -> dict:
    digest = hashlib.sha256(
        json.dumps({"body": body, "prev_hash": prev},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"body": body, "prev_hash": prev, "hash": digest}


def _classify(entry: dict) -> tuple[str, str]:
    from ops.verifier import verify_record

    verdict = verify_record(entry, trace=None)
    return verdict.capability, verdict.reason


def arms(entry: dict) -> list[dict]:
    """Return the four ablation arms for one real ledger record.

    Each arm: (name, capability, reason). A demo can render these side by side
    with the expected class so the drop from intact to stripped is visible.
    """
    prev = entry.get("prev_hash", "genesis")
    body = entry["body"]

    def arm(name: str, mutate) -> dict:
        mutated = copy.deepcopy(body)
        mutate(mutated)
        capability, reason = _classify(_seal(mutated, prev))
        return {"name": name, "capability": capability, "reason": reason}

    return [
        arm("intact", lambda b: None),
        arm("identities stripped", lambda b: _strip_identities(b)),
        arm("population stripped", lambda b: _strip_population(b)),
        arm("manifest removed", lambda b: b.pop("retrieval", None)),
    ]


def _strip_identities(body: dict) -> None:
    """Keep the fact text's hash, drop the revision identity — what an ordinary
    observability record looks like: it knows what the model was shown, not which
    versioned record it came from."""
    for selection in body["retrieval"]["selected"]:
        selection["revision"] = None
        selection["revision_unresolved"] = True


def _strip_population(body: dict) -> None:
    """Keep the selections, drop what they were drawn from, so the record can no
    longer distinguish 'nothing else existed' from 'something else lost'."""
    body["retrieval"].pop("candidate_count", None)


def expected(arm_name: str) -> str:
    """The class an honest binding must fall to for each arm."""
    return {
        "identities stripped": "UNBOUND",
        "population stripped": "UNBOUND",
        "manifest removed": "NOT_CERTIFIED",
    }.get(arm_name)