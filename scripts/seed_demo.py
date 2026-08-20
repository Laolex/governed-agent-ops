#!/usr/bin/env python3
"""Reset the demo to a known starting state.

Reproducible on purpose. A demo seeded by hand is a demo that cannot be re-run
after a bad take, and the state it leaves behind is the state the film records.

This clears the ledger, the fleet, the operational facts and the operator's
memories, then writes back exactly the starting position the four scenes need:

  billing-reconciler   candidate, attestation current   → Scene 1 promotes it
  invoice-classifier   active, NO attestation on file   → the ESCALATE case, and
                                                          the first referent of
                                                          "whatever started failing"
  dunning-writer       active, attestation expired      → the REFUSED case

Three memories are in scope at the start. Scene 2's divergence comes from
writing a fourth, mid-demo, with scripts/memories.sh — not from anything here.

    GOOGLE_CLOUD_PROJECT=... GAO_ENGINE_ID=... python3 scripts/seed_demo.py

Run it again after a rehearsal: the rehearsal writes real records, and a film
that opens on a ledger full of test traffic tells the wrong story.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import google.auth  # noqa: E402
import google.auth.transport.requests as gtr  # noqa: E402
from google.cloud import firestore  # noqa: E402

from ops.store import AgentRecord  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
ENGINE_ID = os.environ.get("GAO_ENGINE_ID", "8639269128982495232")
OPERATOR = os.environ.get("GAO_USER", "platform-ops")

FLEET = [
    AgentRecord("billing-reconciler", "candidate", "platform-ops",
                "Reconciles invoice lines against settled payments.", "r7", "r6"),
    AgentRecord("invoice-classifier", "active", "platform-ops",
                "Classifies inbound invoices by cost centre.", "r3", "r2"),
    AgentRecord("dunning-writer", "active", "revenue-ops",
                "Drafts dunning notices for overdue accounts.", "r11", "r10"),
]

FACTS = {
    "billing-reconciler": {
        "attestation": {"expires_at": "2026-12-01"},
        "incidents": [],
        # Readiness belongs to the exact code revision. These checks make r7
        # READY once it is promoted; they must not transfer if it rolls back
        # to r6.
        "runtime_evidence": [
            {
                "agent_revision": "r7",
                "check_id": check,
                "status": "PASS",
                "observed_at": "2026-08-20T20:00:00Z",
                "evidence_sha256": digest,
            }
            for check, digest in (
                ("tool-contract", "a" * 64),
                ("refusal-path", "b" * 64),
                ("rollback-smoke", "c" * 64),
            )
        ],
    },
    # dunning-writer's attestation exists and has lapsed — a failed condition,
    # which the gate must refuse rather than escalate.
    "dunning-writer": {"attestation": {"expires_at": "2026-07-01"}, "incidents": []},
    # invoice-classifier is deliberately absent from this mapping. An absent
    # record is not an empty one, and the ESCALATE beat depends on the
    # difference being real rather than staged.
}

MEMORIES = [
    "The invoice classifier has been failing since the config change last night.",
    "The dunning writer was reviewed last week and is healthy.",
    "Revenue-ops owns dunning; platform-ops owns billing and invoicing.",
]

BASE = (f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}"
        f"/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}")


def _session():
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return gtr.AuthorizedSession(credentials)


def _clear_collection(client, name: str) -> int:
    cleared = 0
    for document in client.collection(name).stream():
        document.reference.delete()
        cleared += 1
    return cleared


def main() -> int:
    client = firestore.Client(project=PROJECT)

    for name in ("decisions", "decisions_chain", "fleet", "facts"):
        print(f"  cleared {_clear_collection(client, name)} from {name}")

    session = _session()
    memories = session.get(f"{BASE}/memories", params={"pageSize": 100}).json()
    removed = 0
    for memory in memories.get("memories", []):
        if memory.get("scope", {}).get("user_id") == OPERATOR:
            session.delete(f"{BASE}/{memory['name'].split('/v1beta1/')[-1]}"
                           if "/v1beta1/" in memory["name"]
                           else f"https://{LOCATION}-aiplatform.googleapis.com"
                                f"/v1beta1/{memory['name']}")
            removed += 1
    print(f"  cleared {removed} memories in scope {OPERATOR}")

    for agent in FLEET:
        client.collection("fleet").document(agent.agent_id).set(agent.to_dict())
    print(f"  seeded {len(FLEET)} agents")

    for agent_id, facts in FACTS.items():
        client.collection("facts").document(agent_id).set(facts)
    print(f"  seeded facts for {len(FACTS)} agents "
          f"(invoice-classifier deliberately has none)")

    for fact in MEMORIES:
        session.post(f"{BASE}/memories", json={
            "fact": fact,
            # Scope must carry app_name as well as user_id, or the agent's own
            # memory service will not see what we wrote.
            "scope": {"app_name": ENGINE_ID, "user_id": OPERATOR},
        }).raise_for_status()
    print(f"  seeded {len(MEMORIES)} memories")

    print("\nReady. The ledger is empty and the first record written will be the "
          "first record of the film.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
