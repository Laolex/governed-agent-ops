"""The fleet state machine.

An agent in the fleet is in exactly one state, and moves between states only by
a named operation. The machine is a pure function over (state, operation) so
that it can be reasoned about and tested without a store, and so that the
executor's write path has nothing to decide.
"""

from __future__ import annotations

STATES = ("candidate", "active", "quarantined", "retired")

OPERATIONS = ("promote", "quarantine", "rollback")

# (current state, operation) -> destination state. Absence means "not permitted";
# there is no default branch, so a pair that is not listed here is refused rather
# than silently treated as a no-op.
TRANSITIONS: dict[tuple[str, str], str] = {
    ("candidate", "promote"): "active",
    ("candidate", "quarantine"): "quarantined",
    ("active", "quarantine"): "quarantined",
    ("quarantined", "promote"): "active",
    # A rollback re-pins an active agent to its previous revision. The state is
    # unchanged by design: the agent stays in service, on different code. The
    # revision change is recorded by the executor, not by this machine.
    ("active", "rollback"): "active",
}


class InvalidTransition(Exception):
    """Raised when an operation is not permitted from the current state."""


def transition(current: str, operation: str) -> str:
    """Return the state `operation` moves `current` to.

    Raises InvalidTransition for an unknown state, an unknown operation, or a
    pair that is not permitted. The three are one refusal deliberately: from the
    caller's side they are the same event — this operation cannot be performed —
    and distinguishing them would invite a caller to handle one by retrying.
    """
    destination = TRANSITIONS.get((current, operation))
    if destination is None:
        raise InvalidTransition(
            f"{operation!r} is not permitted from state {current!r}"
        )
    return destination
