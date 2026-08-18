"""The retrieval manifest — what the decision was made from.

This is the component the whole entry rests on, so its limits are tested as
explicitly as its behaviour.

Two platform facts shape it. Memory Bank's retrieve response returns the memory
resource name, its updateTime, the fact text and a distance — but **no revision
identity**, even though revisions are first-class versioned resources. So
binding a selection to a version takes a second call, which is itself part of
the gap this project exists to close. And retrieval returns the top three
regardless of how many memories are in scope, so the population has to be
counted separately; it cannot be inferred from the result.
"""

from __future__ import annotations

import pytest

from ops.retrieval import RetrievalManifest, build_manifest

SCOPE = {"app_name": "5258895396175872000", "user_id": "platform-ops"}


class FakeMemoryBank:
    """Stands in for Memory Bank, with the same shape as the live client:
    retrieval is capped and returns no revision, counting is a separate call,
    and revisions are a third."""

    CAP = 3

    def __init__(self, memories: list[dict], revisions: dict | None = None) -> None:
        self._memories = memories
        self._revisions = revisions or {}
        self.calls: list[str] = []

    def retrieve(self, scope: dict, query: str) -> list[dict]:
        self.calls.append("retrieve")
        ranked = sorted(self._memories, key=lambda m: m["distance"], reverse=True)
        return ranked[: self.CAP]

    def count(self, scope: dict) -> int:
        self.calls.append("count")
        return len(self._memories)

    def revisions(self, memory_name: str) -> list[dict]:
        self.calls.append("revisions")
        return self._revisions.get(memory_name, [])


def _memory(n: int, distance: float, updated: str = "2026-08-18T17:46:39.993630Z") -> dict:
    return {
        "name": f"projects/p/locations/l/reasoningEngines/e/memories/{n}",
        "fact": f"Operational note number {n}.",
        "update_time": updated,
        "distance": distance,
    }


def _revision(n: int, created: str) -> dict:
    return {
        "name": f"projects/p/locations/l/reasoningEngines/e/memories/{n}/revisions/{n}00",
        "create_time": created,
    }


def test_the_manifest_names_the_population_not_only_the_selection():
    """Invariant 2. A record listing only what was used cannot distinguish
    'nothing else existed' from 'something else existed and lost'."""
    bank = FakeMemoryBank([_memory(i, distance=1.0 - i / 100) for i in range(20)])

    manifest = build_manifest(bank, SCOPE, "which agent is failing", now="2026-08-18T18:00:00Z")

    assert manifest.candidate_count == 20
    assert len(manifest.selected) == 3
    assert manifest.excluded_count == 17


def test_the_excluded_count_is_derived_and_cannot_be_supplied():
    """It is arithmetic over two observations, not a field a caller can assert."""
    with pytest.raises(TypeError):
        RetrievalManifest(
            scope=SCOPE, query="q", candidate_count=20, selected=[],
            excluded_count=0, retrieved_at="2026-08-18T18:00:00Z",
        )


def test_each_selection_carries_a_revision_identity():
    """Invariant 1. Text can be reproduced from an identity; an identity cannot
    be reconstructed from text."""
    memory = _memory(7, distance=0.9, updated="2026-08-18T17:46:39.993630Z")
    bank = FakeMemoryBank(
        [memory],
        {memory["name"]: [
            _revision(7, "2026-08-01T09:00:00.000000Z"),
            _revision(7, "2026-08-18T17:46:39.993630Z"),
        ]},
    )

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    selection = manifest.selected[0]
    assert selection["memory"] == memory["name"]
    assert selection["revision"].endswith("/revisions/700")


def test_the_revision_recorded_is_the_one_current_at_retrieval():
    """A memory's updateTime equals the createTime of its current revision, so
    the current revision is identified rather than guessed at. An older revision
    must never be recorded as the one that was read."""
    memory = _memory(7, distance=0.9, updated="2026-08-01T09:00:00.000000Z")
    older = _revision(7, "2026-08-01T09:00:00.000000Z")
    newer = dict(_revision(7, "2026-08-18T17:46:39.993630Z"))
    newer["name"] = newer["name"].replace("/700", "/999")
    bank = FakeMemoryBank([memory], {memory["name"]: [older, newer]})

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    assert manifest.selected[0]["revision"].endswith("/revisions/700")


def test_an_unresolvable_revision_is_recorded_as_unresolved_not_omitted():
    """If no revision matches the memory's updateTime, the field says so. An
    omitted field would read downstream as 'this selection had no version',
    which is a different and false claim."""
    memory = _memory(7, distance=0.9)
    bank = FakeMemoryBank([memory], {memory["name"]: []})

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    assert manifest.selected[0]["revision"] is None
    assert manifest.selected[0]["revision_unresolved"] is True


def test_the_fact_text_is_hashed_but_never_stored():
    """The ledger stores identities. A later reader confirms the revision still
    says what it said by hashing it again; it does not read the text from us."""
    memory = _memory(7, distance=0.9)
    bank = FakeMemoryBank([memory], {memory["name"]: [_revision(7, memory["update_time"])]})

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    selection = manifest.selected[0]
    assert len(selection["fact_sha256"]) == 64
    assert "Operational note" not in str(manifest.to_dict())


def test_empty_retrieval_is_recorded_with_the_scope_it_searched():
    """Invariant 3. Absence is recorded as absence — the field is never omitted,
    because a missing field cannot be distinguished from a component that did
    not run."""
    bank = FakeMemoryBank([])

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    assert manifest.selected == []
    assert manifest.candidate_count == 0
    assert manifest.excluded_count == 0
    assert manifest.scope == SCOPE
    assert "selected" in manifest.to_dict()


def test_the_scope_recorded_is_the_full_map_that_was_searched():
    """A scope written as {user_id} alone is invisible to the agent's own memory
    service, which retrieves with {app_name, user_id} and matches it exactly.
    Recording a partial scope would make a manifest unreproducible."""
    bank = FakeMemoryBank([_memory(1, distance=0.9)])

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    assert manifest.scope == {"app_name": "5258895396175872000", "user_id": "platform-ops"}


def test_the_distance_of_each_selection_is_recorded():
    """The similarity score is the evidence for why these and not the others.

    Only that it is recorded, per selection, as a float. Deliberately no
    assertion about ordering: the live API returns its selections in its own
    order, which is not sorted by distance, and asserting the fake's sort order
    would be a test of the fake.
    """
    bank = FakeMemoryBank([_memory(i, distance=1.0 - i / 10) for i in range(5)])

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    distances = [s["distance"] for s in manifest.selected]
    assert len(distances) == 3
    assert all(isinstance(d, float) for d in distances)


def test_the_platforms_own_selection_order_is_preserved():
    """The order Memory Bank returns is part of what it did; re-sorting it would
    substitute our ranking for the one that actually drove the decision."""
    bank = FakeMemoryBank([_memory(i, distance=0.5) for i in range(3)])
    returned = [m["name"] for m in bank.retrieve(SCOPE, "q")]

    manifest = build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    assert [s["memory"] for s in manifest.selected] == returned


def test_the_population_is_counted_separately_from_the_retrieval():
    """Retrieval caps at three; the population cannot be inferred from it."""
    bank = FakeMemoryBank([_memory(i, distance=0.5) for i in range(9)])

    build_manifest(bank, SCOPE, "q", now="2026-08-18T18:00:00Z")

    assert "count" in bank.calls
