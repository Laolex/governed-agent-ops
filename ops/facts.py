"""Operational facts about an agent: attestation and incident history.

One rule governs this module. A missing document stays missing. Returning an
empty attestation, or an empty incident list, for an agent nothing is known
about would turn "we have no record" into "we checked and it is fine" — the
exact collapse the gate's ESCALATE path exists to prevent. So there are no
defaults here, and no `setdefault` anywhere.
"""

from __future__ import annotations

from typing import Any, Protocol


class FactsStore(Protocol):
    def for_agent(self, agent_id: str) -> dict: ...


class InMemoryFactsStore:
    def __init__(self, data: dict[str, dict] | None = None) -> None:
        self._data = data if data is not None else {}

    def for_agent(self, agent_id: str) -> dict:
        return dict(self._data.get(agent_id, {}))


class FirestoreFactsStore:
    """Lazy client, for the same reason the fleet store is lazy: Agent Engine
    pickles by value and a live client is unpicklable."""

    def __init__(self, collection: str = "facts", client: Any = None) -> None:
        self._collection_name = collection
        self._client = client

    def __getstate__(self) -> dict:
        return {"_collection_name": self._collection_name, "_client": None}

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)

    @property
    def _collection(self):
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client()
        return self._client.collection(self._collection_name)

    def for_agent(self, agent_id: str) -> dict:
        snapshot = self._collection.document(agent_id).get()
        return snapshot.to_dict() if snapshot.exists else {}
