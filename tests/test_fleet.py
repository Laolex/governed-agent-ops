"""The fleet state machine.

The machine is deliberately total: every (state, operation) pair either names a
destination or raises. A silent fallthrough here would let an operation report
success while changing nothing, which is the one failure this component can
produce that a decision record would faithfully record as a success.
"""

from __future__ import annotations

import pytest

from ops.fleet import STATES, InvalidTransition, OPERATIONS, transition


@pytest.mark.parametrize(
    "current,operation,expected",
    [
        ("candidate", "promote", "active"),
        ("candidate", "quarantine", "quarantined"),
        ("active", "quarantine", "quarantined"),
        ("quarantined", "promote", "active"),
        ("active", "rollback", "active"),
    ],
)
def test_permitted_transitions(current, operation, expected):
    assert transition(current, operation) == expected


@pytest.mark.parametrize(
    "current,operation",
    [
        ("active", "promote"),
        ("quarantined", "rollback"),
        ("candidate", "rollback"),
        ("retired", "promote"),
        ("retired", "quarantine"),
    ],
)
def test_forbidden_transitions_raise(current, operation):
    with pytest.raises(InvalidTransition):
        transition(current, operation)


def test_the_machine_is_total_over_states_and_operations():
    """No (state, operation) pair may fall through silently: each one either
    returns a state in STATES or raises InvalidTransition."""
    for state in STATES:
        for operation in OPERATIONS:
            try:
                result = transition(state, operation)
            except InvalidTransition:
                continue
            assert result in STATES, f"{state}/{operation} returned {result!r}"


def test_an_unknown_state_is_refused_rather_than_treated_as_new():
    with pytest.raises(InvalidTransition):
        transition("nonexistent", "promote")


def test_an_unknown_operation_is_refused():
    with pytest.raises(InvalidTransition):
        transition("candidate", "delete_everything")
