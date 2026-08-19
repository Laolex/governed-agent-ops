#!/usr/bin/env python3
"""Export the ledger, with the platform traces that corroborate it.

The bundle this writes is what a third party verifies. Records alone can only
reach BOUND_UNCORROBORATED, because the manifest describes what was in scope
rather than what the model saw. Pairing each record with the trace of the turn
that produced it is what lets the verifier compare the two and report agreement
— or, more usefully, disagreement.

Matching is by fact hashes rather than by an identifier, because nothing links a
decision record to a trace: the record is ours and the trace is Google's, and
that missing link is itself part of what this project is about. A trace is
attached only when its injected facts are exactly the manifest's selections, and
records that find no such trace are exported bare and verify as
BOUND_UNCORROBORATED. Attaching a near-match would manufacture the corroboration
this file exists to supply honestly.

    GOOGLE_CLOUD_PROJECT=... python3 scripts/export_bundle.py --base <url> \
        --out bundle.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.trace import CloudTrace, injected_fact_hashes  # noqa: E402
from ops.verifier import verify_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://gao-597227190850.us-central1.run.app")
    parser.add_argument("--project", default="sdl-cinema-2026")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--out", default="bundle.json")
    args = parser.parse_args()

    with urllib.request.urlopen(args.base + "/api/decisions", timeout=120) as response:
        records = json.load(response)["decisions"]
    print(f"  {len(records)} records")

    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(hours=args.hours)
    traces = CloudTrace(args.project).recent(
        start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        limit=50)
    print(f"  {len(traces)} traces in the last {args.hours}h")

    by_hashes: dict[frozenset, dict] = {}
    for trace in traces:
        hashes = injected_fact_hashes(trace)
        if hashes:
            by_hashes.setdefault(frozenset(hashes), {"injected_fact_sha256": hashes})

    attached = {}
    for record in records:
        selections = record["body"].get("retrieval", {}).get("selected", [])
        wanted = frozenset(s["fact_sha256"] for s in selections)
        if not wanted:
            continue
        if wanted in by_hashes:
            attached[record["hash"]] = by_hashes[wanted]

    bundle = {"records": records, "traces": attached,
              "exported_at": now.isoformat(), "source": args.base}
    Path(args.out).write_text(json.dumps(bundle, indent=2))

    verdict = verify_bundle(bundle)
    print(f"  {len(attached)} of {len(records)} records corroborated by a trace")
    print(f"\n  {verdict.capability} — {verdict.reason}")
    print(f"\nWritten to {args.out}. Verify it anywhere with:")
    print(f"  python3 -c \"import json,sys;sys.path.insert(0,'.');"
          f"from ops.verifier import verify_bundle;"
          f"print(verify_bundle(json.load(open('{args.out}'))).capability)\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
