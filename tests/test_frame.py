"""The frame must stay true, not merely stay pretty.

Both halves are claims about evidence: that the captured runs still diverge and
still name no memory identity, and that the production verifier still separates
a bound record from a stripped one. If either stops holding, the frame is a
claim we can no longer make and it should fail here rather than in a video.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.frame import (  # noqa: E402
    classify,
    governed_record,
    main,
    memory_identities_in_evidence,
    read_probe,
    render,
    unbound,
)


def test_the_captured_runs_still_diverge():
    before, after = read_probe("h4b-before.sse"), read_probe("h4b-after.sse")
    assert before["outcome"] == "ESCALATE" and before["title"] == "NORTHSTAR-S01E16"
    assert after["outcome"] == "BLOCKED" and after["title"] == "NORTHSTAR-S01E08"


def test_the_evidence_names_no_memory_identity():
    # The seam itself. If this ever becomes non-zero the platform has closed it
    # and the entry's claim has to be rewritten, not quietly kept.
    assert memory_identities_in_evidence() == 0


def test_the_binding_is_what_earns_the_classification():
    record = governed_record(extra=True, target="billing-reconciler")
    assert classify(record) == "BOUND"
    assert classify(unbound(record)) == "UNBOUND"


def test_the_manifest_records_the_displacement():
    a = governed_record(extra=False, target="invoice-classifier")["retrieval"]
    b = governed_record(extra=True, target="billing-reconciler")["retrieval"]
    assert (a["candidate_count"], a["excluded_count"]) == (3, 0)
    assert (b["candidate_count"], b["excluded_count"]) == (4, 1)
    assert len(a["selected"]) == len(b["selected"]) == 3


def test_the_frame_prints_both_columns():
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert main() == 0
    output = buffer.getvalue()
    for expected in ("GOOGLE'S RECORD", "THE GOVERNED RECORD", "ESCALATE → BLOCKED",
                     "UNBOUND — refuses to certify", "3 → 4"):
        assert expected in output, f"frame is missing {expected}"


def test_columns_clear_the_widest_cell():
    frame = render("t", ("L", "R"), [("l", "x" * 60, "y")], [])
    line = next(row for row in frame.split("\n") if "y" in row)
    assert line.index("y") > 60
