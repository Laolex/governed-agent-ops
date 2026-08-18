"""The retrieval manifest: what the decision was made from.

This is the component the entry rests on, and the one whose limits matter most,
so both are stated here rather than in a commit message.

**What the platform gives us.** Memory Bank's retrieve response carries the
memory resource name, its updateTime, the fact text and a similarity distance.
It does *not* carry a revision identity, even though revisions are first-class
versioned resources with their own IDs and expiry. Binding a selection to a
version therefore takes a second call, per selection. Retrieval is also capped
at the top few matches regardless of how many memories are in scope, so the
population is a third call; it cannot be inferred from the result.

**What this manifest may and may not claim.** The executor builds this by
querying Memory Bank itself, with the same scope and query the agent's turn
used. It is a second call, not an observation of the agent's own retrieval, and
Memory Bank could in principle return a different set between the two. So the
manifest does not claim "these are the memories the model saw." It claims what
it can support: these are the memories in scope for this query at this moment,
this is how they ranked, and this many were excluded. The verifier cross-checks
the manifest against the trace's injected context and reports agreement or
disagreement, which turns that limit into a recorded property instead of an
unstated assumption.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol


class MemoryBank(Protocol):
    def retrieve(self, scope: dict, query: str) -> list[dict]: ...
    def count(self, scope: dict) -> int: ...
    def revisions(self, memory_name: str) -> list[dict]: ...


@dataclass(frozen=True)
class RetrievalManifest:
    scope: dict
    query: str
    candidate_count: int
    selected: list[dict]
    retrieved_at: str

    @property
    def excluded_count(self) -> int:
        """Derived, never supplied. It is arithmetic over two observations — the
        population and the selection — and a caller who could assert it could
        assert that nothing was excluded."""
        return self.candidate_count - len(self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": dict(self.scope),
            "query": self.query,
            "candidate_count": self.candidate_count,
            "selected": [dict(s) for s in self.selected],
            "excluded_count": self.excluded_count,
            "retrieved_at": self.retrieved_at,
        }


def _resolve_revision(bank: MemoryBank, memory: dict) -> tuple[str | None, bool]:
    """Identify the revision that was current when this memory was read.

    A memory's updateTime equals the createTime of its current revision, so the
    revision is identified rather than guessed at. If none matches — the memory
    changed between the two calls, or revisions are unavailable — that is
    recorded as unresolved rather than omitted, because an omitted field reads
    downstream as "this selection had no version", which is a different and
    false claim.
    """
    for revision in bank.revisions(memory["name"]):
        if revision.get("create_time") == memory.get("update_time"):
            return revision["name"], False
    return None, True


def build_manifest(
    bank: MemoryBank, scope: dict, query: str, now: str
) -> RetrievalManifest:
    """Build the manifest for one decision."""
    retrieved = bank.retrieve(scope, query)
    population = bank.count(scope)

    selected = []
    for memory in retrieved:
        revision, unresolved = _resolve_revision(bank, memory)
        selected.append(
            {
                "memory": memory["name"],
                "revision": revision,
                "revision_unresolved": unresolved,
                "fact_sha256": hashlib.sha256(
                    memory["fact"].encode("utf-8")
                ).hexdigest(),
                "distance": float(memory["distance"]),
            }
        )

    return RetrievalManifest(
        scope=dict(scope),
        query=query,
        candidate_count=population,
        selected=selected,
        retrieved_at=now,
    )


class VertexMemoryBank:
    """Memory Bank over the Vertex AI REST surface.

    Deliberately thin and dependency-light: three calls, no SDK object model. The
    SDK's memory service is what the *agent* uses; this is the executor reading
    the same store independently, and keeping it independent is the point.
    """

    def __init__(self, project: str, location: str, engine_id: str,
                 session: Any = None) -> None:
        self._host = f"https://{location}-aiplatform.googleapis.com/v1beta1"
        self._base = (
            f"{self._host}/projects/{project}/locations/{location}"
            f"/reasoningEngines/{engine_id}"
        )
        self._session = session

    def _authed(self):
        if self._session is None:
            import google.auth
            import google.auth.transport.requests as gtr

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._session = gtr.AuthorizedSession(credentials)
        return self._session

    def retrieve(self, scope: dict, query: str) -> list[dict]:
        response = self._authed().post(
            f"{self._base}/memories:retrieve",
            json={"scope": scope, "similarity_search_params": {"search_query": query}},
        )
        response.raise_for_status()
        return [
            {
                "name": item["memory"]["name"],
                "fact": item["memory"]["fact"],
                "update_time": item["memory"]["updateTime"],
                "distance": item["distance"],
            }
            for item in response.json().get("retrievedMemories", [])
        ]

    def count(self, scope: dict) -> int:
        """Count the memories in scope.

        Paged deliberately rather than trusting one response: the population is
        the number the manifest's whole claim rests on, and a silently truncated
        page would understate what was excluded.
        """
        total, page_token = 0, None
        while True:
            params = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            response = self._authed().get(f"{self._base}/memories", params=params)
            response.raise_for_status()
            body = response.json()
            total += sum(
                1
                for memory in body.get("memories", [])
                if all(memory.get("scope", {}).get(k) == v for k, v in scope.items())
            )
            page_token = body.get("nextPageToken")
            if not page_token:
                return total

    def revisions(self, memory_name: str) -> list[dict]:
        response = self._authed().get(f"{self._host}/{memory_name}/revisions")
        response.raise_for_status()
        return [
            {"name": r["name"], "create_time": r["createTime"]}
            for r in response.json().get("memoryRevisions", [])
        ]
