# Governed Agent Operations

An operations agent that manages a fleet of other agents — register, promote, quarantine,
roll back — where every action it takes carries a record that **binds the retrieval set it
decided from**.

> When an autonomous agent makes a decision, can you prove exactly why it was allowed to do it?

## Why this exists

On Google's agent platform, a decision's *inputs* are not recorded the way its *output* is.
Memory Bank retrieval returns the top three matches out of a population of any size, and the
cut is invisible: the trace captures the text that was injected into the prompt, but carries
no memory resource name and no revision ID — while memory revisions are first-class versioned
resources with their own identifiers. Memory reads are not audited at all; writes are.

Measured on live infrastructure: with 21 memories in scope, an agent resolved one referent
and returned one determination. A single additional memory, written by an unrelated process —
no code change, no config change — displaced one of the three winners, and the identical
request returned a different determination. Nothing in the platform's own record explains the
change.

This project records what the platform does not: which records were selected, out of what
population, and which versioned record the injected text came from.

**Live:** https://gao-597227190850.us-central1.run.app

## Status

Built and deployed for the All Things Agentic hackathon: the fleet state machine, the fleet
store, the deterministic policy gate, the retrieval manifest, the hash-chained ledger, the
executor, the ADK agent on Vertex AI Agent Engine, the console, the standalone verifier and
the ablation.

## Running the tests

```bash
python -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/python -m pytest tests/ -q
```

The suite runs offline with no credentials. To additionally exercise the store contract
against live Firestore:

```bash
GOOGLE_CLOUD_PROJECT=<project> GAO_LIVE_TESTS=1 .venv/bin/python -m pytest tests/ -q
```

## Running the console

```bash
GOOGLE_CLOUD_PROJECT=<project> GAO_ENGINE_ID=<reasoning engine id> \
  .venv/bin/uvicorn ops.service:app --port 8811
```

`/` serves the console; `/api/fleet`, `/api/ask`, `/api/decisions` are the surface behind it.
An unset `GAO_ENGINE_ID` returns 503 naming the variable, so a misconfiguration never reads
as a failure to decide.

## Verifying a record without us

The verifier is standalone: no credentials, no network, no dependencies beyond the standard
library. It reports a capability class, never a percentage — either a record binds its inputs
by identity or it does not.

| class | what the evidence supports |
|---|---|
| `BOUND` | identities, population, continuous chain, and the manifest agrees with the exported trace |
| `BOUND_UNCORROBORATED` | all of the above, but no trace was exported to check the manifest against |
| `UNBOUND` | the record names what was used, but not which versioned record it was, or not what it was drawn from |
| `NOT_CERTIFIED` | the chain is broken, a hash does not match, or the retrieval field is absent |

`BOUND_UNCORROBORATED` is the honest one. The manifest is built by the executor's own call to
Memory Bank, not by observing the agent's retrieval — so without a trace to compare against,
"this is what was in scope" and "this is what the model saw" are different claims, and this
verifier will not collapse them.

## The ablation

```bash
python3 scripts/ablation.py
```

Four arms through the production verifier, testing whether the retrieval binding is doing any
work. It exits non-zero if an arm lands where it should not, and that failure path is
verified by deliberately breaking the verifier — an ablation that has never failed is not
known to be able to fail.

```
PASS  intact                 BOUND
PASS  identities stripped    UNBOUND
PASS  population stripped    UNBOUND
PASS  manifest removed       NOT_CERTIFIED
```

The middle two arms are the interesting ones: they are what an ordinary observability record
looks like. It knows what the model was shown; it does not know which versioned record that
came from, or what else was in scope and lost.

## Rehearsing the demo

```bash
GOOGLE_CLOUD_PROJECT=<project> python3 scripts/rehearse.py
```

Ten checks against the live deployment, run before any recording session. On a sister project
a whole layer turned out never to have been wired into the write path, and it was found by
rehearsing the demo rather than by the tests — the tests covered the layer, not its absence
from the path.

## What the divergence looks like

Two records, same operator, same words, same policy revision. Between them, one memory was
written by an unrelated process — no code change, no config change:

| | record A | record B |
|---|---|---|
| target | `invoice-classifier` | `billing-reconciler` |
| in scope | 3 | 4 |
| selected | 3 | 3 |
| excluded | 0 | 1 |

Google's own trace carries the text that was injected into each prompt, and no memory
identity or revision. These records name which versioned records were selected, how many were
in scope, and how many lost.

## License

Apache-2.0.
