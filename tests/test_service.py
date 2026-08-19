"""The HTTP surface.

This layer was the least-tested file in the project, and it was untestable by
construction: every endpoint built its own Firestore client inline, so there was
no way to exercise a route without credentials and a live database. The fix is
the test — dependencies are now injectable, and FastAPI's override mechanism
supplies fakes.

What is checked here is only what this layer owns: status codes, shapes, and the
one behaviour that must never regress — an unconfigured engine reporting as a
configuration problem rather than as a failure to decide.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ops import service
from ops.facts import InMemoryFactsStore
from ops.ledger import InMemoryLedger
from ops.store import AgentRecord, InMemoryFleetStore

CANDIDATE = AgentRecord("billing-reconciler", "candidate", "platform-ops",
                        "Reconciles invoice lines against settled payments.",
                        "r7", "r6")
CLEAN = {"attestation": {"expires_at": "2026-12-01"}, "incidents": []}


class FakeAgent:
    def __init__(self, events):
        self._events = events

    def ask(self, message: str, user_id: str):
        return self._events


class FakeBank:
    def retrieve(self, scope, query): return []
    def count(self, scope): return 0
    def revisions(self, name): return []


def _proposal_events(**args):
    return [
        {"content": {"parts": [{"function_call": {"name": "propose_operation",
                                                  "args": args}}]}},
        {"content": {"parts": [{"text": "Proposed."}]}},
    ]


@pytest.fixture
def client():
    store = InMemoryFleetStore({})
    store.put(CANDIDATE)
    ledger = InMemoryLedger([])

    app = service.app
    # The engine id gates /api/ask by design, so a test of any other behaviour
    # has to configure it — discovering that here is the 503 guard proving it
    # fires before anything else does.
    original_engine = service.ENGINE_ID
    service.ENGINE_ID = "test-engine"
    app.dependency_overrides = {
        service.get_store: lambda: store,
        service.get_ledger: lambda: ledger,
        service.get_bank: lambda: FakeBank(),
        service.get_facts: lambda: InMemoryFactsStore({"billing-reconciler": CLEAN}),
        service.get_agent: lambda: FakeAgent(_proposal_events(
            operation="promote", agent_id="billing-reconciler", cause="")),
    }
    yield TestClient(app), store, ledger
    app.dependency_overrides = {}
    service.ENGINE_ID = original_engine


def test_the_fleet_endpoint_returns_every_agent(client):
    http, _, _ = client
    response = http.get("/api/fleet")
    assert response.status_code == 200
    assert response.json()["agents"][0]["agent_id"] == "billing-reconciler"


def test_asking_runs_the_operation_and_returns_the_record_hash(client):
    http, store, ledger = client

    body = http.post("/api/ask", json={"message": "promote it"}).json()

    assert body["determination"]["outcome"] == "PERMITTED"
    assert body["record_hash"] == ledger.read_all()[0]["hash"]
    assert store.get("billing-reconciler").state == "active"


def test_the_decisions_endpoint_reports_the_chain_state(client):
    http, _, _ = client
    http.post("/api/ask", json={"message": "promote it"})

    body = http.get("/api/decisions").json()

    assert len(body["decisions"]) == 1
    assert body["chain_ok"] is True


def test_one_decision_can_be_fetched_by_hash(client):
    http, _, _ = client
    record_hash = http.post("/api/ask", json={"message": "promote it"}).json()["record_hash"]

    response = http.get(f"/api/decisions/{record_hash}")

    assert response.status_code == 200
    assert response.json()["body"]["what"]["operation"] == "promote"


def test_an_unknown_record_hash_is_a_404_not_an_empty_record(client):
    http, _, _ = client
    assert http.get("/api/decisions/" + "0" * 64).status_code == 404


def test_an_unconfigured_engine_is_a_503_naming_the_variable():
    """A missing environment variable must never read as a failure to decide.
    The distinction matters most when it is least convenient — a judge opening
    the console on a misconfigured deploy should see a configuration error, not
    a system that appears to refuse."""
    original = service.ENGINE_ID
    service.ENGINE_ID = ""
    try:
        response = TestClient(service.app).post("/api/ask", json={"message": "hi"})
        assert response.status_code == 503
        assert "GAO_ENGINE_ID" in response.json()["detail"]
    finally:
        service.ENGINE_ID = original


def test_a_turn_that_proposes_nothing_returns_a_null_determination(client):
    http, store, ledger = client
    service.app.dependency_overrides[service.get_agent] = lambda: FakeAgent(
        [{"content": {"parts": [{"text": "I cannot tell which agent you mean."}]}}])

    body = http.post("/api/ask", json={"message": "do something"}).json()

    assert body["determination"] is None
    assert body["record_hash"] is None
    assert ledger.read_all() == []
    assert store.get("billing-reconciler").state == "candidate"


def test_a_registration_reaches_the_executor_with_its_owner_and_purpose(client):
    http, store, _ = client
    service.app.dependency_overrides[service.get_agent] = lambda: FakeAgent(
        _proposal_events(operation="register", agent_id="refund-router", cause="",
                         owner="platform-ops", purpose="Routes refunds."))

    body = http.post("/api/ask", json={"message": "register the refund router"}).json()

    assert body["determination"]["outcome"] == "PERMITTED"
    assert store.get("refund-router").owner == "platform-ops"


def test_a_rollback_reaches_the_executor_and_moves_the_revision(client):
    http, store, _ = client
    store.put(CANDIDATE.with_state("active"))
    service.app.dependency_overrides[service.get_agent] = lambda: FakeAgent(
        _proposal_events(operation="rollback", agent_id="billing-reconciler", cause=""))

    body = http.post("/api/ask", json={"message": "roll it back"}).json()

    assert body["determination"]["outcome"] == "PERMITTED"
    assert store.get("billing-reconciler").revision == "r6"


def test_the_console_is_served_from_the_same_origin_as_the_api(client):
    """One origin is a deliberate property, not an accident of hosting: a viewer's
    browser never needs a second host and there is no CORS surface to get wrong."""
    http, _, _ = client
    response = http.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_an_agent_outage_is_a_502_not_a_null_determination(client):
    """A 429 from the model must not reach the operator as 'nothing was
    proposed'. The console renders a null determination as 'the turn proposed no
    operation, so nothing was decided' — true of a working agent, and a lie
    about a broken one."""
    http, _, ledger = client
    service.app.dependency_overrides[service.get_agent] = lambda: FakeAgent([])

    response = http.post("/api/ask", json={"message": "promote it"})

    assert response.status_code == 502
    assert "agent" in response.json()["detail"].lower()
    assert ledger.read_all() == []
