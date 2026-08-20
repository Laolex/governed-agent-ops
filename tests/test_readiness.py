import hashlib

from ops.readiness import REQUIRED_CHECKS, evaluate_release_readiness
from ops.store import AgentRecord

AGENT = AgentRecord("billing-reconciler", "active", "ops", "billing", "r7", "r6")


def evidence(revision="r7", status="PASS"):
    return [
        {
            "agent_revision": revision,
            "check_id": check,
            "status": status,
            "observed_at": "2026-08-20T20:00:00Z",
            "evidence_sha256": hashlib.sha256(check.encode()).hexdigest(),
        }
        for check in REQUIRED_CHECKS
    ]


def facts(**overrides):
    base = {
        "attestation": {"expires_at": "2026-12-01"},
        "incidents": [],
        "runtime_evidence": evidence(),
    }
    base.update(overrides)
    return base


def test_current_revision_with_every_check_passing_is_ready():
    result = evaluate_release_readiness(AGENT, facts(), now="2026-08-20")
    assert result.status == "READY"
    assert result.agent_revision == "r7"


def test_previous_revision_evidence_does_not_transfer():
    result = evaluate_release_readiness(
        AGENT, facts(runtime_evidence=evidence("r6")), now="2026-08-20"
    )
    assert result.status == "UNKNOWN"
    assert "r7" in result.blocking_condition


def test_a_missing_current_check_is_unknown():
    result = evaluate_release_readiness(
        AGENT, facts(runtime_evidence=evidence()[:-1]), now="2026-08-20"
    )
    assert result.status == "UNKNOWN"


def test_a_known_failed_check_blocks():
    items = evidence()
    items[1]["status"] = "FAIL"
    result = evaluate_release_readiness(
        AGENT, facts(runtime_evidence=items), now="2026-08-20"
    )
    assert result.status == "BLOCKED"
    assert "refusal-path" in result.blocking_condition


def test_absent_runtime_evidence_is_unknown():
    incomplete = facts()
    incomplete.pop("runtime_evidence")
    assert evaluate_release_readiness(AGENT, incomplete, now="2026-08-20").status == "UNKNOWN"


def test_malformed_evidence_cannot_make_a_revision_ready():
    no_time = evidence()
    no_time[0].pop("observed_at")
    bad_digest = evidence()
    bad_digest[0]["evidence_sha256"] = "not-a-sha256"

    assert evaluate_release_readiness(
        AGENT, facts(runtime_evidence=no_time), now="2026-08-20"
    ).status == "UNKNOWN"
    assert evaluate_release_readiness(
        AGENT, facts(runtime_evidence=bad_digest), now="2026-08-20"
    ).status == "UNKNOWN"


def test_expired_attestation_blocks_and_open_incident_blocks():
    expired = facts(attestation={"expires_at": "2026-01-01"})
    incident = facts(incidents=[{"id": "INC-8", "status": "open"}])
    assert evaluate_release_readiness(AGENT, expired, now="2026-08-20").status == "BLOCKED"
    assert evaluate_release_readiness(AGENT, incident, now="2026-08-20").status == "BLOCKED"


def test_a_non_active_agent_is_blocked_before_evidence_is_considered():
    candidate = AGENT.with_state("candidate")
    result = evaluate_release_readiness(candidate, {}, now="2026-08-20")
    assert result.status == "BLOCKED"
