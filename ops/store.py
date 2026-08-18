"""The fleet store: current state, one document per agent.

Two implementations behind one contract. The in-memory one exists so the suite
runs with no credentials; Firestore is what the deployed service uses. Both are
exercised by the same tests, because a fake tested only against itself proves
only that the fake works.

This store holds *current* state and nothing else. It is a cache in the strict
sense: every state it holds was produced by an operation that the decision
ledger records. If the two ever disagree, the ledger is right.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Protocol

from ops.fleet import STATES


class UnknownAgent(Exception):
    """Raised when an agent id is not in the fleet."""


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    state: str
    owner: str
    purpose: str
    revision: str
    previous_revision: str | None = None

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"{self.state!r} is not one of {STATES}")

    def with_state(self, state: str) -> "AgentRecord":
        return replace(self, state=state)

    def with_id(self, agent_id: str) -> "AgentRecord":
        return replace(self, agent_id=agent_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "state": self.state,
            "owner": self.owner,
            "purpose": self.purpose,
            "revision": self.revision,
            "previous_revision": self.previous_revision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRecord":
        return cls(
            agent_id=data["agent_id"],
            state=data["state"],
            owner=data["owner"],
            purpose=data["purpose"],
            revision=data["revision"],
            previous_revision=data.get("previous_revision"),
        )


class FleetStore(Protocol):
    def get(self, agent_id: str) -> AgentRecord: ...
    def put(self, agent: AgentRecord) -> None: ...
    def list(self) -> Iterable[AgentRecord]: ...


class InMemoryFleetStore:
    """Backed by a caller-supplied dict, so a test can build a second store over
    the same data and check that state survives a restart."""

    def __init__(self, data: dict[str, dict] | None = None) -> None:
        self._data = data if data is not None else {}

    def get(self, agent_id: str) -> AgentRecord:
        stored = self._data.get(agent_id)
        if stored is None:
            raise UnknownAgent(agent_id)
        return AgentRecord.from_dict(stored)

    def put(self, agent: AgentRecord) -> None:
        self._data[agent.agent_id] = agent.to_dict()

    def list(self) -> Iterable[AgentRecord]:
        return [AgentRecord.from_dict(d) for d in self._data.values()]


class FirestoreFleetStore:
    """Firestore-backed, with the client built lazily on first use.

    The laziness is not an optimisation. Agent Engine deploys by pickling the
    application by value, and a live Firestore client is explicitly unpicklable
    — holding one at construction fails the deploy with a serialisation error
    that names nothing useful. It also means constructing a store in a test
    never reaches for credentials.
    """

    def __init__(self, collection: str = "fleet", client: Any = None) -> None:
        self._collection_name = collection
        self._client = client

    def __getstate__(self) -> dict:
        # Never carry a client across a pickle, even one that was injected.
        return {"_collection_name": self._collection_name, "_client": None}

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)

    @property
    def _collection(self):
        if self._client is None:
            from google.cloud import firestore

            self._client = firestore.Client()
        return self._client.collection(self._collection_name)

    def get(self, agent_id: str) -> AgentRecord:
        snapshot = self._collection.document(agent_id).get()
        if not snapshot.exists:
            raise UnknownAgent(agent_id)
        return AgentRecord.from_dict(snapshot.to_dict())

    def put(self, agent: AgentRecord) -> None:
        self._collection.document(agent.agent_id).set(agent.to_dict())

    def list(self) -> Iterable[AgentRecord]:
        return [AgentRecord.from_dict(d.to_dict()) for d in self._collection.stream()]
