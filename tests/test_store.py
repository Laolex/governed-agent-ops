"""The fleet store, exercised through one contract against every backend.

The same suite runs against the in-memory store always, and against Firestore
when credentials are present. Writing it once is the point: a fake that is only
tested against itself proves that the fake works.

`reopen()` builds a fresh store object over the same underlying data, which is
how invariant 7 — a quarantined agent stays quarantined across a restart of the
operations agent — is actually tested rather than asserted.
"""

from __future__ import annotations

import os

import pytest

from ops.store import AgentRecord, InMemoryFleetStore, UnknownAgent


class InMemoryHarness:
    name = "in-memory"

    def __init__(self) -> None:
        self._data: dict = {}

    def open(self):
        return InMemoryFleetStore(self._data)

    def cleanup(self) -> None:
        # Harness objects are built once at parametrisation time, so without
        # this the backing dict carries agents between tests and an exact-set
        # assertion would be reading someone else's fleet.
        self._data.clear()


class FirestoreHarness:
    name = "firestore"

    def __init__(self, collection: str) -> None:
        self._collection = collection

    def open(self):
        from ops.store import FirestoreFleetStore

        return FirestoreFleetStore(collection=self._collection)

    def cleanup(self) -> None:
        from google.cloud import firestore

        for doc in firestore.Client().collection(self._collection).stream():
            doc.reference.delete()


def _harnesses():
    yield InMemoryHarness()
    if os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GAO_LIVE_TESTS"):
        import uuid

        yield FirestoreHarness(f"fleet_test_{uuid.uuid4().hex[:8]}")


@pytest.fixture(params=list(_harnesses()), ids=lambda h: h.name)
def harness(request):
    yield request.param
    # Firestore collections are per-test and disposable; leaving them behind
    # would accumulate junk in the project the demo is recorded from.
    cleanup = getattr(request.param, "cleanup", None)
    if cleanup is not None:
        cleanup()


CANDIDATE = AgentRecord(
    agent_id="billing-reconciler",
    state="candidate",
    owner="platform-ops",
    purpose="Reconciles invoice lines against settled payments.",
    revision="r7",
    previous_revision="r6",
)


def test_an_agent_is_readable_after_it_is_written(harness):
    store = harness.open()
    store.put(CANDIDATE)
    assert store.get("billing-reconciler") == CANDIDATE


def test_reading_an_unknown_agent_raises_rather_than_returning_none(harness):
    """A missing agent must not arrive at the policy gate as a None that some
    caller treats as an empty record; absence is an error here, and becomes an
    ESCALATE at the gate, never a silent refusal."""
    store = harness.open()
    with pytest.raises(UnknownAgent):
        store.get("never-registered")


def test_state_survives_a_restart_of_the_operations_agent(harness):
    """Invariant 7. Fleet state lives in the store, not in the conversation."""
    store = harness.open()
    store.put(CANDIDATE)
    store.put(CANDIDATE.with_state("quarantined"))

    reopened = harness.open()

    assert reopened.get("billing-reconciler").state == "quarantined"


def test_listing_returns_every_agent_written(harness):
    store = harness.open()
    store.put(CANDIDATE)
    store.put(CANDIDATE.with_id("invoice-classifier"))

    listed = {a.agent_id for a in store.list()}

    assert listed == {"billing-reconciler", "invoice-classifier"}


def test_with_state_does_not_mutate_the_original():
    quarantined = CANDIDATE.with_state("quarantined")
    assert CANDIDATE.state == "candidate"
    assert quarantined.state == "quarantined"


def test_a_record_refuses_a_state_the_machine_does_not_know():
    with pytest.raises(ValueError):
        CANDIDATE.with_state("deleted")


def test_the_firestore_store_can_be_constructed_without_credentials():
    """Constructing a store must not reach for credentials. A test suite that
    needs them to import a module is a suite that stops running."""
    from ops.store import FirestoreFleetStore

    FirestoreFleetStore(collection="never-read")


def test_the_firestore_store_survives_serialisation():
    """Agent Engine deploys by pickling the app by value, and a live Firestore
    client is explicitly unpicklable. If the store holds one at construction,
    deployment fails with a serialisation error that names nothing useful."""
    import cloudpickle

    from ops.store import FirestoreFleetStore

    restored = cloudpickle.loads(cloudpickle.dumps(FirestoreFleetStore()))

    assert isinstance(restored, FirestoreFleetStore)


def test_a_rollback_moves_the_agent_onto_its_previous_revision():
    rolled = CANDIDATE.rolled_back()

    assert rolled.revision == "r6"
    assert CANDIDATE.revision == "r7", "the original must not mutate"


def test_after_a_rollback_there_is_no_further_known_good_revision():
    """r6 is now live and nothing records what preceded it, so a second rollback
    has nowhere to go. Leaving `previous_revision` pointing at r7 would let a
    'rollback' roll *forward* onto the revision we just backed out of."""
    rolled = CANDIDATE.rolled_back()

    assert rolled.previous_revision is None


def test_rolling_back_with_no_previous_revision_is_refused_by_the_record():
    from ops.store import AgentRecord

    pinned = AgentRecord("a", "active", "o", "p", "r1", None)
    with pytest.raises(ValueError):
        pinned.rolled_back()


def test_deleting_removes_an_agent(harness):
    store = harness.open()
    store.put(CANDIDATE)

    store.delete("billing-reconciler")

    with pytest.raises(UnknownAgent):
        store.get("billing-reconciler")


def test_deleting_an_agent_that_is_not_there_is_not_an_error(harness):
    """The only caller is the registration rollback path, which runs when a
    record could not be written. It must not raise a second exception on top of
    the one it is already handling."""
    harness.open().delete("never-registered")
