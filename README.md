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
