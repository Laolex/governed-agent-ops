#!/usr/bin/env python3
"""The one shot: what each record can answer when the outcome moves.

Two panels, because the claim has two halves and stating only one of them
puts this entry back in the crowded "we built an agent audit log" category.

  Panel 1  Google's stack diverged. Identical request, one memory written
           between the runs by a separate process, opposite outcomes — read
           out of the committed probe evidence in `evidence/`.
  Panel 2  What each kind of record can say about it. The governed record
           names the population, the exclusion and the revision identity;
           strip the binding and the verifier refuses to certify.

Panel 1 is real captured output. Panel 2 is computed here through the
production manifest builder and the production verifier, on a deterministic
store — it is the record shape, not a replay of the probe run, and the panel
says so. Mixing the two would be the overclaim this project exists to avoid.

Runs on bare `python3` from a clean clone. No credentials, no network, no
dependencies. Exits non-zero if any half fails to hold.

    python3 scripts/frame.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ops.retrieval import build_manifest  # noqa: E402
from ops.verifier import verify_bundle  # noqa: E402

EVIDENCE = ROOT / "evidence"
PAD = 26

BASE = "projects/p/locations/l/reasoningEngines/e"


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(body: dict, prev: str = "genesis") -> dict:
    digest = hashlib.sha256(_canonical({"body": body, "prev_hash": prev})).hexdigest()
    return {"body": body, "prev_hash": prev, "hash": digest}


# --- Panel 1 ------------------------------------------------------------------


def read_probe(name: str) -> dict:
    """Pull the outcome out of a captured response stream.

    Nothing is parsed that the file does not literally contain, so a claim
    here fails loudly rather than drifting away from the evidence.
    """
    text = (EVIDENCE / name).read_text()
    titles = sorted(set(re.findall(r"NORTHSTAR-S01E\d+", text)))
    outcomes = sorted({o for o in ("ESCALATE", "BLOCKED", "CLEARED") if o in text})
    if len(titles) != 1 or len(outcomes) != 1:
        raise SystemExit(f"{name}: expected one title and one outcome, got {titles} {outcomes}")
    return {"title": titles[0], "outcome": outcomes[0]}


def memory_identities_in_evidence() -> int:
    """How many memory resource names appear anywhere in the captured records.

    The answer is the finding. Memory revisions are first-class versioned
    resources with their own ids; the decision record never names one.
    """
    total = 0
    for path in sorted(EVIDENCE.glob("h4*")):
        total += len(re.findall(r"memories/\d+", path.read_text()))
    return total


# --- Panel 2 ------------------------------------------------------------------


class FixedBank:
    """A deterministic Memory Bank, so the manifest is computed rather than written.

    `after` adds one memory at the closest distance. Retrieval is capped at
    three, as the platform's is, so the newcomer displaces the weakest of the
    incumbents — which is the whole mechanism: nobody changed any code.
    """

    TOP_K = 3

    def __init__(self, extra: bool) -> None:
        self._memories = [
            {"name": f"{BASE}/memories/{i}", "update_time": f"2026-08-18T0{i}:00:00Z",
             "fact": f"fleet fact {i}", "distance": d}
            for i, d in ((1, 0.42), (2, 0.55), (3, 0.61))
        ]
        if extra:
            self._memories.append(
                {"name": f"{BASE}/memories/4", "update_time": "2026-08-18T04:00:00Z",
                 "fact": "billing-reconciler passed attestation last night", "distance": 0.19}
            )

    def retrieve(self, scope: dict, query: str) -> list[dict]:
        return sorted(self._memories, key=lambda m: m["distance"])[: self.TOP_K]

    def count(self, scope: dict) -> int:
        return len(self._memories)

    def revisions(self, memory_name: str) -> list[dict]:
        memory = next(m for m in self._memories if m["name"] == memory_name)
        return [{"name": f"{memory_name}/revisions/900", "create_time": memory["update_time"]}]


SCOPE = {"app_name": "engine", "user_id": "platform-ops"}
QUERY = "whatever started failing last night"


def governed_record(extra: bool, target: str) -> dict:
    manifest = build_manifest(FixedBank(extra), SCOPE, QUERY, "2026-08-18T18:00:00Z")
    return {
        "who": {"operator": "ola", "agent": "ops-agent"},
        "what": {"operation": "quarantine", "target": target, "cause": "operator request"},
        "when": {"decided_at": "2026-08-18T18:00:00Z"},
        "why": {"rule_hits": [], "blocking_condition": "", "outcome": "PERMITTED"},
        "policy": {"revision": "GAO-2026.08"},
        "retrieval": manifest.to_dict(),
        "result": {"state_before": "active", "state_after": "quarantined"},
    }


def unbound(body: dict) -> dict:
    """The same record as an ordinary observability layer would keep it: the
    fact text is known, which versioned record it came from is not."""
    stripped = json.loads(json.dumps(body))
    for selection in stripped["retrieval"]["selected"]:
        selection["revision"] = None
        selection["revision_unresolved"] = True
    return stripped


def classify(body: dict) -> str:
    entry = _seal(body)
    shas = [s["fact_sha256"] for s in body["retrieval"]["selected"]]
    return verify_bundle(
        {"records": [entry], "traces": {entry["hash"]: {"injected_fact_sha256": shas}}}
    ).capability


# --- The frame ----------------------------------------------------------------


def render(title: str, headers: tuple[str, str], rows: list[tuple[str, str, str]],
           footer: list[str]) -> str:
    width = max([len(row[1]) for row in rows] + [len(headers[0])])
    line = "─" * (PAD + width + 28)
    out = [line, f"  {title}", line,
           f"  {'':{PAD}}{headers[0]:{width + 4}}{headers[1]}", ""]
    out += [f"  {label:{PAD}}{left:{width + 4}}{right}".rstrip() for label, left, right in rows]
    out += [line]
    out += [f"  {note}" for note in footer]
    return "\n".join(out)


def main() -> int:
    before, after = read_probe("h4b-before.sse"), read_probe("h4b-after.sse")
    identities = memory_identities_in_evidence()

    if before["outcome"] == after["outcome"] or before["title"] == after["title"]:
        print("The evidence no longer shows a divergence. Refusing to print the frame.")
        return 1
    if identities:
        print(f"The evidence now names {identities} memory identities. The seam this "
              "frame describes has closed — rewrite the claim before printing it.")
        return 1

    record_a = governed_record(extra=False, target="invoice-classifier")
    record_b = governed_record(extra=True, target="billing-reconciler")
    bound_b, stripped_b = classify(record_b), classify(unbound(record_b))

    if bound_b != "BOUND" or stripped_b != "UNBOUND":
        print(f"The verifier answered {bound_b} / {stripped_b}. Refusing to print a "
              "picture that is not true.")
        return 1

    manifest_a, manifest_b = record_a["retrieval"], record_b["retrieval"]
    entered = next(s for s in manifest_b["selected"]
                   if s["memory"] not in {x["memory"] for x in manifest_a["selected"]})

    print(render(
        "One request, run twice. Between them, one memory written by another process.",
        ("GOOGLE'S RECORD", "THE GOVERNED RECORD"),
        [
            ("request", "byte-identical", "byte-identical"),
            ("code / config changed", "none", "none"),
            ("outcome", f"{before['outcome']} → {after['outcome']}  ← moved",
             "invoice-classifier → billing-reconciler"),
            ("", "", ""),
            ("fact text kept", "yes, verbatim", "yes, by SHA-256"),
            ("memory identities named", f"{identities} — in any file", "every selection, by revision"),
            ("population in scope", "absent", f"{manifest_a['candidate_count']} → {manifest_b['candidate_count']}"),
            ("anything excluded?", "unrecorded", f"{manifest_a['excluded_count']} → {manifest_b['excluded_count']}"),
            ("what displaced it", "unrecoverable", f"{entered['memory'].rsplit('/', 1)[-1]} at distance {entered['distance']}"),
            ("", "", ""),
            ("verifier's answer", "cannot explain the move", f"{bound_b}"),
            ("binding stripped", "—", f"{stripped_b} — refuses to certify"),
        ],
        [
            "Left column: real captured output, evidence/h4b-*.sse, run 2026-08-18 on",
            "sdl-cinema-2026. Nothing is parsed that those files do not literally contain.",
            "Right column: the record shape, computed here through the production manifest",
            "builder and the production verifier on a deterministic store — not a replay of",
            "the run on the left. The two are separate claims and are kept separate.",
            "",
            "What this does not establish: that the model saw exactly these memories. The",
            "manifest is the executor's own read of the same store, and the verifier reports",
            "agreement with the trace rather than assuming it.",
        ],
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
