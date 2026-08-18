"""The decision ledger: append-only, hash-chained.

Every record names the record before it, so a reader can tell whether anything
was removed from the middle. The chain is not the product — the retrieval
manifest is — but without it a record can be deleted after the fact and nothing
would show.

The tip is read from the store on every append, never cached in the process. A
ledger that caches its tip and guards appends with a process-local lock passes
every single-process test and forks the moment there are two writers. That is
not hypothetical; it is how the civ0 event log forked.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Protocol

# Deliberately not a run of zeroes: an all-zero prev_hash is what a truncated or
# zero-initialised record looks like, and genesis must not be confusable with it.
GENESIS = "genesis"


class ChainForked(Exception):
    """Raised when the tip moved between reading it and appending to it."""


def canonical(obj: Any) -> bytes:
    """Serialise deterministically: sorted keys, no incidental whitespace.

    Every hash in the ledger is taken over this form, so any re-serialisation
    anywhere in the pipeline must produce the same bytes or every chain breaks.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def record_hash(body: dict, prev_hash: str) -> str:
    return hashlib.sha256(canonical({"body": body, "prev_hash": prev_hash})).hexdigest()


def verify_chain(entries: Iterable[dict]) -> tuple[bool, str]:
    """Walk the chain. Returns (ok, reason); the reason is empty when ok."""
    prev = GENESIS
    for index, entry in enumerate(entries):
        if entry["prev_hash"] != prev:
            return False, (
                f"chain broken at record {index}: prev_hash {entry['prev_hash'][:12]}… "
                f"does not follow {prev[:12]}…"
            )
        expected = record_hash(entry["body"], entry["prev_hash"])
        if entry["hash"] != expected:
            return False, (
                f"hash mismatch at record {index}: body does not produce "
                f"{entry['hash'][:12]}…"
            )
        prev = entry["hash"]
    return True, ""


# A measured race of eight concurrent writers against Firestore had six lose the
# tip and two land, so a caller that does not retry drops most of its records.
APPEND_ATTEMPTS = 8


class Ledger(Protocol):
    def append(self, body: dict) -> str: ...
    def append_after(self, body: dict, prev_hash: str) -> str: ...
    def tip(self) -> str: ...
    def read_all(self) -> list[dict]: ...


def _append_with_retry(ledger: "Ledger", body: dict) -> str:
    """Append at the current tip, re-reading it when another writer wins.

    A lost race is not a failed decision. By the time the executor writes, the
    state change has already happened; dropping the record because someone else
    appended first would leave an operation that occurred with nothing recording
    it. Bounded, so a genuinely stuck tip surfaces instead of spinning.

    Only the loose form retries. `append_after` names a specific predecessor, and
    retrying it would silently move the record onto a different one — the exact
    reordering the strict form exists to prevent.
    """
    last: ChainForked | None = None
    for _ in range(APPEND_ATTEMPTS):
        try:
            return ledger.append_after(body, ledger.tip())
        except ChainForked as forked:
            last = forked
    raise ChainForked(
        f"could not append after {APPEND_ATTEMPTS} attempts: {last}"
    )


class InMemoryLedger:
    """Backed by a caller-supplied list, so a test can build a second ledger over
    the same entries and check that the tip is read rather than remembered."""

    def __init__(self, entries: list[dict] | None = None) -> None:
        self._entries = entries if entries is not None else []

    def tip(self) -> str:
        return self._entries[-1]["hash"] if self._entries else GENESIS

    def append(self, body: dict) -> str:
        return _append_with_retry(self, body)

    def append_after(self, body: dict, prev_hash: str) -> str:
        if self.tip() != prev_hash:
            raise ChainForked(
                f"tip is {self.tip()[:12]}…, not {prev_hash[:12]}…"
            )
        digest = record_hash(body, prev_hash)
        self._entries.append({"body": body, "prev_hash": prev_hash, "hash": digest,
                              "sequence": len(self._entries)})
        return digest

    def read_all(self) -> list[dict]:
        return list(self._entries)


class FirestoreLedger:
    """Append-only over Firestore, with the tip held in its own document.

    The append runs in a transaction that reads the tip and writes both the entry
    and the new tip. Firestore aborts the transaction if the tip document changed
    under it, which is what makes a concurrent second writer lose rather than
    silently reorder onto the new tip.
    """

    def __init__(self, collection: str = "decisions", client: Any = None) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client()
        self._client = client
        self._entries = client.collection(collection)
        self._tip_doc = client.collection(f"{collection}_chain").document("tip")

    def tip(self) -> str:
        snapshot = self._tip_doc.get()
        return snapshot.to_dict()["hash"] if snapshot.exists else GENESIS

    def append(self, body: dict) -> str:
        return _append_with_retry(self, body)

    def append_after(self, body: dict, prev_hash: str) -> str:
        from google.cloud import firestore

        digest = record_hash(body, prev_hash)
        entries, tip_doc = self._entries, self._tip_doc

        @firestore.transactional
        def commit(transaction):
            snapshot = tip_doc.get(transaction=transaction)
            current = snapshot.to_dict()["hash"] if snapshot.exists else GENESIS
            sequence = snapshot.to_dict()["sequence"] + 1 if snapshot.exists else 0
            if current != prev_hash:
                raise ChainForked(f"tip is {current[:12]}…, not {prev_hash[:12]}…")
            transaction.set(
                entries.document(digest),
                {"body": body, "prev_hash": prev_hash, "hash": digest,
                 "sequence": sequence},
            )
            transaction.set(tip_doc, {"hash": digest, "sequence": sequence})
            return digest

        return commit(self._client.transaction())

    def read_all(self) -> list[dict]:
        return [
            d.to_dict()
            for d in self._entries.order_by("sequence").stream()
        ]
