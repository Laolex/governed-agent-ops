"""HTTP service: the console's one origin.

Thin by design. Every decision this serves is made by the executor and the gate;
this layer moves JSON and serves one static page.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ops.api import AgentUnavailable, handle_ask
from ops.facts import FirestoreFactsStore
from ops.ledger import FirestoreLedger, verify_chain
from ops.retrieval import VertexMemoryBank
from ops.store import FirestoreFleetStore

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
ENGINE_ID = os.environ.get("GAO_ENGINE_ID", "")
CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
CONSOLE = CONSOLE_DIR / "index.html"
BUILD_ARTICLE = CONSOLE_DIR / "build-article.html"

app = FastAPI(title="Governed Agent Operations")
app.mount(
    "/build-article-assets",
    StaticFiles(directory=CONSOLE_DIR / "article-assets"),
    name="build-article-assets",
)


# Dependencies rather than inline constructors. Building a Firestore client
# inside each endpoint made this layer untestable — there was no way to exercise
# a route without credentials and a live database, so the HTTP surface was the
# only part of the project with no tests at all. These are overridable, which is
# the entire reason they exist.
def get_store():
    return FirestoreFleetStore()


def get_ledger():
    return FirestoreLedger()


def get_facts():
    return FirestoreFactsStore()


def get_bank():
    return VertexMemoryBank(PROJECT, LOCATION, ENGINE_ID)


def get_agent():
    return VertexAgentClient(PROJECT, LOCATION, ENGINE_ID)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class AskRequest(BaseModel):
    message: str
    operator: str = "platform-ops"


class VertexAgentClient:
    """Calls the deployed operations agent and returns its raw turn."""

    def __init__(self, project: str, location: str, engine_id: str) -> None:
        self._resource = (
            f"projects/{project}/locations/{location}/reasoningEngines/{engine_id}"
        )

    def ask(self, message: str, user_id: str) -> list[dict]:
        import vertexai
        from vertexai import agent_engines

        vertexai.init(project=PROJECT, location=LOCATION)
        engine = agent_engines.get(self._resource)
        session = engine.create_session(user_id=user_id)
        return list(
            engine.stream_query(
                user_id=user_id, session_id=session["id"], message=message
            )
        )


@app.get("/api/fleet")
def fleet(store=Depends(get_store)) -> dict:
    return {"agents": [a.to_dict() for a in store.list()]}


@app.post("/api/ask")
def ask(request: AskRequest, store=Depends(get_store), ledger=Depends(get_ledger),
        facts=Depends(get_facts), bank=Depends(get_bank),
        agent=Depends(get_agent)) -> dict:
    if not ENGINE_ID:
        # Named explicitly: an unconfigured engine must never read as a failure
        # to decide. A judge opening the console on a misconfigured deploy
        # should see a configuration error, not a system that appears to refuse.
        raise HTTPException(503, "GAO_ENGINE_ID is not configured")

    try:
        return handle_ask(
            request.message,
            agent=agent,
            store=store,
            ledger=ledger,
            bank=bank,
            facts=facts,
            now=_now(),
            operator=request.operator,
            scope={"app_name": ENGINE_ID, "user_id": request.operator},
        )
    except AgentUnavailable as unavailable:
        # 502, not 200 with a null determination: the console renders a null
        # determination as "the turn proposed no operation", which is true of a
        # working agent and a lie about a broken one.
        raise HTTPException(502, f"the agent could not be reached: {unavailable}")


@app.get("/api/decisions")
def decisions(ledger=Depends(get_ledger)) -> dict:
    entries = ledger.read_all()
    ok, reason = verify_chain(entries)
    return {"decisions": entries, "chain_ok": ok, "chain_reason": reason}


@app.get("/api/decisions/{record_hash}")
def decision(record_hash: str, ledger=Depends(get_ledger)) -> dict:
    return _find_decision(ledger, record_hash)


def _find_decision(ledger, record_hash: str) -> dict:
    for entry in ledger.read_all():
        if entry["hash"] == record_hash:
            return entry
    raise HTTPException(404, "no such record")


@app.get("/api/decisions/{record_hash}/verify")
def decision_verify(record_hash: str, ledger=Depends(get_ledger)) -> dict:
    """The capability class a third party can establish from this record alone.

    Traces are exported out-of-band, so no live trace is consulted here: the
    strongest class a record earns on its own is BOUND_UNCORROBORATED. What THIS
    endpoint is for is the refusal — a record that has been tampered with, or
    that never carried revision identities or a population, certifies nothing.
    """
    from ops.verifier import verify_record

    entry = _find_decision(ledger, record_hash)
    verdict = verify_record(entry, trace=None)
    return {
        "hash": record_hash,
        "capability": verdict.capability,
        "reason": verdict.reason,
    }


@app.get("/api/decisions/{record_hash}/ablate")
def decision_ablate(record_hash: str, ledger=Depends(get_ledger)) -> dict:
    """The necessity test: does the retrieval binding change what can be proven?

    Classifies the intact record, then one thing removed at a time. If a stripped
    arm still certifies, the binding is decoration and this says so. The client
    renders the drop (e.g. BOUND_UNCORROBORATED -> UNBOUND) so a judge watches the
    ablation fire against a real record, not a scripted one.
    """
    from ops.ablate import arms, expected

    entry = _find_decision(ledger, record_hash)
    return {
        "hash": record_hash,
        "arms": [
            {
                "name": a["name"],
                "capability": a["capability"],
                "reason": a["reason"],
                "expected": expected(a["name"]),
            }
            for a in arms(entry)
        ],
    }


@app.get("/")
def console() -> FileResponse:
    return FileResponse(CONSOLE)


@app.get("/build-article")
def build_article() -> FileResponse:
    return FileResponse(BUILD_ARTICLE)
