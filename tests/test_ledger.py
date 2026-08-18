"""The decision ledger: append-only, hash-chained.

The property that matters most here is the one that is easiest to believe you
have and not have. A ledger that keeps its tip in process memory and guards
appends with a process-local lock looks correct in every single-process test and
forks the moment there are two writers. So the contract carries an explicit
stale-tip test, and the Firestore arm additionally races real threads.

Same suite, both backends — in-memory always, Firestore when credentials are
present.
"""

from __future__ import annotations

import os

import pytest

from ops.ledger import ChainForked, GENESIS, InMemoryLedger, canonical, record_hash


class InMemoryHarness:
    name = "in-memory"

    def __init__(self) -> None:
        self._entries: list = []

    def open(self):
        return InMemoryLedger(self._entries)

    def cleanup(self) -> None:
        # The harness objects are built once, at parametrisation time, so the
        # backing list would otherwise carry entries between tests and the
        # chain assertions would silently be reading someone else's records.
        self._entries.clear()


class FirestoreHarness:
    name = "firestore"

    def __init__(self, collection: str) -> None:
        self._collection = collection

    def open(self):
        from ops.ledger import FirestoreLedger

        return FirestoreLedger(collection=self._collection)

    def cleanup(self) -> None:
        from google.cloud import firestore

        client = firestore.Client()
        for name in (self._collection, f"{self._collection}_chain"):
            for doc in client.collection(name).stream():
                doc.reference.delete()


def _harnesses():
    yield InMemoryHarness()
    if os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GAO_LIVE_TESTS"):
        import uuid

        yield FirestoreHarness(f"decisions_test_{uuid.uuid4().hex[:8]}")


@pytest.fixture(params=list(_harnesses()), ids=lambda h: h.name)
def harness(request):
    yield request.param
    request.param.cleanup()


BODY = {
    "who": {"operator": "ola", "agent": "ops-agent"},
    "what": {"operation": "promote", "target": "billing-reconciler"},
    "when": {"decided_at": "2026-08-18T18:00:00Z"},
    "why": {"rule_hits": [], "blocking_condition": ""},
    "policy": {"revision": "GAO-2026.08"},
    "retrieval": {"candidate_count": 7, "excluded_count": 4, "selected": []},
    "result": {"state_before": "candidate", "state_after": "active"},
}


def test_canonicalisation_is_stable_under_key_order():
    """Two records with the same content and different key order hash the same;
    otherwise a re-serialisation anywhere in the pipeline breaks every chain."""
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_canonicalisation_has_no_incidental_whitespace():
    assert b" " not in canonical({"a": 1, "b": [1, 2]})


def test_a_changed_field_changes_the_hash():
    original = dict(BODY)
    mutated = dict(BODY, why={"rule_hits": ["INC-001"], "blocking_condition": "open"})
    assert record_hash(original, GENESIS) != record_hash(mutated, GENESIS)


def test_the_first_record_chains_from_genesis(harness):
    ledger = harness.open()
    ledger.append(BODY)
    assert ledger.read_all()[0]["prev_hash"] == GENESIS


def test_each_record_chains_to_the_one_before_it(harness):
    ledger = harness.open()
    ledger.append(BODY)
    ledger.append(dict(BODY, what={"operation": "quarantine", "target": "x"}))

    entries = ledger.read_all()

    assert entries[1]["prev_hash"] == entries[0]["hash"]


def test_the_tip_moves_with_each_append(harness):
    ledger = harness.open()
    first = ledger.append(BODY)
    assert ledger.tip() == first
    second = ledger.append(dict(BODY, what={"operation": "quarantine", "target": "x"}))
    assert ledger.tip() == second


def test_a_second_writer_on_a_stale_tip_loses(harness):
    """The civ0 lesson. Both writers read the same tip; exactly one may win, and
    the loser must be told, not silently reordered onto the new tip."""
    ledger = harness.open()
    ledger.append(BODY)
    stale = ledger.tip()

    ledger.append(dict(BODY, what={"operation": "quarantine", "target": "a"}))

    with pytest.raises(ChainForked):
        ledger.append_after(dict(BODY, what={"operation": "rollback", "target": "b"}), stale)


def test_the_chain_survives_reopening(harness):
    """The tip lives in the store, not in the process. A ledger that caches it
    forks the moment a second process appends."""
    ledger = harness.open()
    first = ledger.append(BODY)

    reopened = harness.open()
    reopened.append(dict(BODY, what={"operation": "quarantine", "target": "x"}))

    assert reopened.read_all()[1]["prev_hash"] == first


def test_verification_walks_the_chain(harness):
    ledger = harness.open()
    ledger.append(BODY)
    ledger.append(dict(BODY, what={"operation": "quarantine", "target": "x"}))

    from ops.ledger import verify_chain

    assert verify_chain(ledger.read_all()) == (True, "")


def test_verification_catches_a_mutated_body():
    from ops.ledger import verify_chain

    entries = []
    prev = GENESIS
    for what in ("promote", "quarantine"):
        body = dict(BODY, what={"operation": what, "target": "x"})
        entries.append({"body": body, "prev_hash": prev,
                        "hash": record_hash(body, prev)})
        prev = entries[-1]["hash"]

    entries[0]["body"] = dict(entries[0]["body"], who={"operator": "someone-else"})

    ok, reason = verify_chain(entries)
    assert ok is False
    assert "hash" in reason.lower()


def test_verification_catches_a_broken_link():
    from ops.ledger import verify_chain

    body = dict(BODY)
    first = {"body": body, "prev_hash": GENESIS, "hash": record_hash(body, GENESIS)}
    orphan_prev = "0" * 64
    second = {"body": body, "prev_hash": orphan_prev,
              "hash": record_hash(body, orphan_prev)}

    ok, reason = verify_chain([first, second])
    assert ok is False
    assert "chain" in reason.lower()


@pytest.mark.skipif(
    not (os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GAO_LIVE_TESTS")),
    reason="needs live Firestore",
)
def test_concurrent_writers_produce_one_chain_not_two():
    """The real test of the property. Eight threads append at once from separate
    ledger objects. Every append must either land in a single continuous chain
    or raise ChainForked — never two records claiming the same predecessor,
    which is what a cached tip produces and what no single-process test sees."""
    import concurrent.futures
    import uuid

    from ops.ledger import FirestoreLedger, verify_chain

    collection = f"decisions_race_{uuid.uuid4().hex[:8]}"

    def write(n: int) -> str | None:
        ledger = FirestoreLedger(collection=collection)
        try:
            return ledger.append(dict(BODY, what={"operation": "promote",
                                                  "target": f"agent-{n}"}))
        except ChainForked:
            return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(8)))

        landed = [r for r in results if r is not None]
        entries = FirestoreLedger(collection=collection).read_all()

        assert len(landed) >= 1
        assert len(entries) == len(landed), "a record landed without winning the tip"
        assert len({e["prev_hash"] for e in entries}) == len(entries), \
            "two records claim the same predecessor — the chain forked"
        assert verify_chain(entries) == (True, "")
    finally:
        from google.cloud import firestore

        client = firestore.Client()
        for name in (collection, f"{collection}_chain"):
            for doc in client.collection(name).stream():
                doc.reference.delete()


class ContentiousLedger(InMemoryLedger):
    """Loses the tip race a fixed number of times, then stops.

    Models what Firestore does under contention: a measured race of eight
    concurrent writers had six lose and two land, so a caller that does not
    retry drops most of its records.
    """

    def __init__(self, losses: int) -> None:
        super().__init__([])
        self._remaining_losses = losses
        self.attempts = 0

    def append_after(self, body: dict, prev_hash: str) -> str:
        self.attempts += 1
        if self._remaining_losses > 0:
            self._remaining_losses -= 1
            raise ChainForked("another writer won the tip")
        return super().append_after(body, prev_hash)


def test_append_retries_when_another_writer_wins_the_tip():
    """A lost race is not a failed decision. The state change already happened;
    dropping the record because someone else appended first would leave an
    operation that occurred with nothing recording it."""
    ledger = ContentiousLedger(losses=3)

    ledger.append(BODY)

    assert ledger.attempts == 4
    assert len(ledger.read_all()) == 1


def test_append_gives_up_rather_than_retrying_forever():
    ledger = ContentiousLedger(losses=99)

    with pytest.raises(ChainForked):
        ledger.append(BODY)


def test_append_after_never_retries():
    """The caller named a specific predecessor. Retrying would silently move the
    record onto a different one, which is the reordering the strict form exists
    to prevent."""
    ledger = ContentiousLedger(losses=1)

    with pytest.raises(ChainForked):
        ledger.append_after(BODY, GENESIS)

    assert ledger.attempts == 1
