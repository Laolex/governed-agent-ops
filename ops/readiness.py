"""Version-scoped release readiness for one governed agent revision."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from ops.policy import POLICY_REVISION
from ops.store import AgentRecord

READINESS_POLICY_REVISION = f"{POLICY_REVISION}.release-1"
REQUIRED_CHECKS = ("tool-contract", "refusal-path", "rollback-smoke")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReleaseReadiness:
    status: str
    agent_id: str
    agent_revision: str
    policy_revision: str
    required_checks: tuple[str, ...]
    evidence: tuple[dict, ...]
    blocking_condition: str

    def to_dict(self) -> dict:
        return asdict(self)


def _result(agent: AgentRecord, status: str, evidence: list[dict], reason: str) -> ReleaseReadiness:
    return ReleaseReadiness(
        status=status,
        agent_id=agent.agent_id,
        agent_revision=agent.revision,
        policy_revision=READINESS_POLICY_REVISION,
        required_checks=REQUIRED_CHECKS,
        evidence=tuple(evidence),
        blocking_condition=reason,
    )


def evaluate_release_readiness(agent: AgentRecord, facts: dict, *, now: str) -> ReleaseReadiness:
    """Return READY, BLOCKED or UNKNOWN for the exact current revision."""
    if agent.state != "active":
        return _result(agent, "BLOCKED", [], f"Agent state is {agent.state}, not active.")
    if "attestation" not in facts:
        return _result(agent, "UNKNOWN", [], "No attestation record exists for this agent.")
    if "incidents" not in facts:
        return _result(agent, "UNKNOWN", [], "No incident history exists for this agent.")
    if "runtime_evidence" not in facts:
        return _result(agent, "UNKNOWN", [], "No runtime evidence exists for this agent revision.")

    expires_at = facts["attestation"].get("expires_at", "")
    if not expires_at:
        return _result(agent, "UNKNOWN", [], "The attestation has no expiry.")
    if expires_at < now:
        return _result(agent, "BLOCKED", [], f"Attestation expired on {expires_at}.")

    open_incidents = [item for item in facts["incidents"] if item.get("status") == "open"]
    if open_incidents:
        return _result(
            agent, "BLOCKED", [], f"Open incident {open_incidents[0].get('id')} on this agent."
        )

    current = [
        dict(item)
        for item in facts["runtime_evidence"]
        if item.get("agent_revision") == agent.revision
    ]
    by_check: dict[str, list[dict]] = {
        check: [item for item in current if item.get("check_id") == check]
        for check in REQUIRED_CHECKS
    }
    for check in REQUIRED_CHECKS:
        entries = by_check[check]
        if not entries:
            return _result(agent, "UNKNOWN", current, f"No {check} evidence exists for revision {agent.revision}.")
        statuses = {entry.get("status") for entry in entries}
        if len(statuses) != 1 or not statuses <= {"PASS", "FAIL"}:
            return _result(agent, "UNKNOWN", current, f"{check} evidence is contradictory or malformed.")
        if any(not entry.get("observed_at") for entry in entries):
            return _result(agent, "UNKNOWN", current, f"{check} evidence has no observation time.")
        if any(not SHA256.fullmatch(str(entry.get("evidence_sha256", ""))) for entry in entries):
            return _result(agent, "UNKNOWN", current, f"{check} evidence has no valid SHA-256 digest.")
        if statuses == {"FAIL"}:
            return _result(agent, "BLOCKED", current, f"{check} failed for revision {agent.revision}.")

    return _result(agent, "READY", current, "Every required check passes for the current revision.")
