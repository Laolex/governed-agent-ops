# Probe evidence

Raw output from the divergence spike run on 2026-08-18 against `sdl-cinema-2026`.
Committed unedited so the claims in the README and in `scripts/frame.py` can be
checked rather than taken on trust.

| File | What it is |
|---|---|
| `h4b-before.sse` | The agent's response stream with 21 memories in scope. Resolves `NORTHSTAR-S01E16`, returns `ESCALATE`. |
| `h4b-after.sse` | The **identical** request after one further memory was written by a separate process — no code change, no config change. Resolves `NORTHSTAR-S01E08`, returns `BLOCKED`. |
| `h4-trace-run2.json` | Cloud Trace export for a run of the same agent. |
| `h4-trace-run3.json` | Cloud Trace export for another. |

The traces carry the injected fact text verbatim. What no file here contains is a
memory resource name or a revision id — grep for `memories/` and you get nothing.
That is the seam: the identity exists on the platform (memory revisions are
first-class versioned resources), and the decision record never references it.

The probe agent is not this repository's agent. These files establish that the
divergence is real on Google's stack; the governed record is what this project
adds on top.
