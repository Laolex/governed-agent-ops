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

## Status

Under construction for the All Things Agentic hackathon. Built: the fleet state machine, the
fleet store (in-memory and Firestore behind one contract), and the deterministic policy gate.

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
