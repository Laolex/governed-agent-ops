#!/usr/bin/env python3
"""Rehearse the demo against the live deployment, before recording anything.

Every beat in the film is asserted here first. The reason is specific: on a
sister project the rationale layer turned out never to have been wired into the
write path, and it was found by rehearsing the demo, not by the tests — the
tests covered the layer, not its absence from the path. A recording session is
the wrong place to discover that.

    GOOGLE_CLOUD_PROJECT=... GAO_ENGINE_ID=... python3 scripts/rehearse.py

Requires the service running on --base (default http://127.0.0.1:8811).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ops.verifier import verify_bundle  # noqa: E402

INDIRECT = ("Quarantine whatever started failing after the config change last night. "
            "Cause: failing since the config change.")


def _get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=120) as response:
        return json.load(response)


def _post(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if detail:
        print(f"        {detail}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8811")
    base = parser.parse_args().base

    print("Rehearsing against", base, "\n")
    results = []

    fleet = _get(base, "/api/fleet")
    results.append(check("the service is up and the fleet is readable",
                         len(fleet["agents"]) > 0,
                         f"{len(fleet['agents'])} agents"))

    # Beat 1: an ordinary operation, and a record that carries the manifest.
    turn = _post(base, "/api/ask", {"message": "Promote the billing reconciler."})
    determination = turn.get("determination")
    results.append(check("a direct request reaches a determination",
                         determination is not None,
                         determination and determination["outcome"]))
    results.append(check("the determination is attributed to the evaluator",
                         bool(determination) and
                         determination["decided_by"] == "policy evaluator"))

    # Beat 2: the referent lives in memory, not in the message. This is the beat
    # the entry lives on, so it is asserted rather than trusted: the agent must
    # name an agent that the message never named.
    turn = _post(base, "/api/ask", {"message": INDIRECT})
    proposed = [s for s in turn["transcript"]
                if s["kind"] == "tool_call" and s["name"] == "propose_operation"]
    target = proposed[0]["args"].get("agent_id") if proposed else None
    results.append(check("an indirect referent is resolved from memory",
                         bool(target) and target.lower() not in INDIRECT.lower(),
                         f"resolved to {target}"))

    record = _get(base, "/api/decisions/" + turn["record_hash"]) if turn.get(
        "record_hash") else {}
    manifest = record.get("body", {}).get("retrieval", {})
    results.append(check("the record states the population it drew from",
                         "candidate_count" in manifest,
                         f"{manifest.get('candidate_count')} in scope, "
                         f"{len(manifest.get('selected', []))} selected, "
                         f"{manifest.get('excluded_count')} excluded"))
    results.append(check("every selection carries a revision identity",
                         bool(manifest.get("selected")) and
                         all(s.get("revision") for s in manifest["selected"])))
    # Checked against the selections, not the whole manifest. The manifest's
    # `query` field is the operator's own words and belongs in the record; an
    # earlier version of this check searched the whole blob and flagged that,
    # which is a rehearsal catching a bad check rather than a bad record.
    selections = manifest.get("selected", [])
    results.append(check("selections carry identities and hashes, never text",
                         all(set(s) == {"memory", "revision", "revision_unresolved",
                                        "fact_sha256", "distance"}
                             for s in selections),
                         f"{len(selections)} selections, keys "
                         f"{sorted(selections[0]) if selections else '—'}"))

    # Beat 3: the chain, and what a third party can establish from it alone.
    ledger = _get(base, "/api/decisions")
    results.append(check("the chain verifies", ledger["chain_ok"],
                         f"{len(ledger['decisions'])} records"))
    verdict = verify_bundle({"records": ledger["decisions"], "traces": {}})
    results.append(check("the exported bundle is at least BOUND_UNCORROBORATED",
                         verdict.capability in ("BOUND", "BOUND_UNCORROBORATED"),
                         f"{verdict.capability} — {verdict.reason}"))

    # Beat 4: the ablation, run exactly as a viewer would run it.
    ablation = subprocess.run([sys.executable, str(ROOT / "scripts" / "ablation.py")],
                              capture_output=True, text=True)
    results.append(check("the ablation passes on bare python3",
                         ablation.returncode == 0))

    print()
    failed = results.count(False)
    if failed:
        print(f"{failed} check(s) failed. Do not record until they pass.")
        return 1
    print("All beats verified against the live deployment. Safe to record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
