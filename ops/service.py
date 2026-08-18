"""HTTP service: the console's one origin.

Thin by design. Every decision this serves is made by the executor and the gate;
this layer moves JSON and serves one static page.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ops.api import handle_ask
from ops.facts import FirestoreFactsStore
from ops.ledger import FirestoreLedger, verify_chain
from ops.retrieval import VertexMemoryBank
from ops.store import FirestoreFleetStore

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "sdl-cinema-2026")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
ENGINE_ID = os.environ.get("GAO_ENGINE_ID", "")
CONSOLE = Path(__file__).resolve().parent.parent / "console" / "index.html"

app = FastAPI(title="Governed Agent Operations")


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
def fleet() -> dict:
    store = FirestoreFleetStore()
    return {"agents": [a.to_dict() for a in store.list()]}


@app.post("/api/ask")
def ask(request: AskRequest) -> dict:
    if not ENGINE_ID:
        # Named explicitly: an unconfigured engine must never read as a failure
        # to decide.
        raise HTTPException(503, "GAO_ENGINE_ID is not configured")

    result = handle_ask(
        request.message,
        agent=VertexAgentClient(PROJECT, LOCATION, ENGINE_ID),
        store=FirestoreFleetStore(),
        ledger=FirestoreLedger(),
        bank=VertexMemoryBank(PROJECT, LOCATION, ENGINE_ID),
        facts=FirestoreFactsStore(),
        now=_now(),
        operator=request.operator,
        scope={"app_name": ENGINE_ID, "user_id": request.operator},
    )
    return result


@app.get("/api/decisions")
def decisions() -> dict:
    entries = FirestoreLedger().read_all()
    ok, reason = verify_chain(entries)
    return {"decisions": entries, "chain_ok": ok, "chain_reason": reason}


@app.get("/api/decisions/{record_hash}")
def decision(record_hash: str) -> dict:
    for entry in FirestoreLedger().read_all():
        if entry["hash"] == record_hash:
            return entry
    raise HTTPException(404, "no such record")


@app.get("/")
def console() -> FileResponse:
    return FileResponse(CONSOLE)
