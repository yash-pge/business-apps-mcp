"""Retrieval-filter validation and server-side scoping.

Two jobs:

1. **Validate** a caller-supplied Bedrock metadata filter against the documented
   grammar before it is used. Unknown operators are rejected with an error that
   lists the valid ones, so the agent can correct itself instead of getting an
   opaque ``ValidationException`` from AWS.

2. **Scope** every query server-side. If the operator configures a scope filter
   (``BAMCP_SCOPE_FILTER``) it is always ANDed with whatever the caller sends,
   so a caller can narrow the results but never broaden them past the scope.

Same idea as ecm-mcp-server, where the server builds the category filter itself
and the caller only supplies a validated category code.
"""

from __future__ import annotations

from typing import Any

# Operators that compare one metadata key with a value.
LEAF_OPERATORS = (
    "equals",
    "notEquals",
    "greaterThan",
    "greaterThanOrEquals",
    "lessThan",
    "lessThanOrEquals",
    "in",
    "notIn",
    "startsWith",
    "listContains",
    "stringContains",
)
# Operators that combine other filters.
GROUP_OPERATORS = ("andAll", "orAll")
VALID_OPERATORS = tuple(sorted((*LEAF_OPERATORS, *GROUP_OPERATORS)))

# Operators whose value is a list.
_LIST_VALUE_OPERATORS = frozenset({"in", "notIn"})

# Bedrock accepts 2 to 5 conditions inside andAll / orAll.
MIN_GROUP_ITEMS = 2
MAX_GROUP_ITEMS = 5
# Bounds that keep a hostile filter from being expensive to build or evaluate.
MAX_DEPTH = 3
MAX_CONDITIONS = 20
MAX_KEY_LENGTH = 128
MAX_LIST_VALUES = 100

_SCALARS = (str, int, float, bool)


class FilterError(ValueError):
    """Raised when a retrieval filter is malformed. The message is safe to show."""


def _short(value: Any) -> str:
    """Render untrusted input for an error message without echoing huge values."""
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _check_leaf(op: str, value: Any, path: str) -> None:
    if not isinstance(value, dict) or set(value) != {"key", "value"}:
        raise FilterError(f"{path}.{op} must be an object with exactly 'key' and 'value'.")

    key = value["key"]
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_LENGTH:
        raise FilterError(
            f"{path}.{op}.key must be a non-empty string of at most {MAX_KEY_LENGTH} characters."
        )

    operand = value["value"]
    if op in _LIST_VALUE_OPERATORS:
        if (
            not isinstance(operand, list)
            or not operand
            or len(operand) > MAX_LIST_VALUES
            or not all(isinstance(item, _SCALARS) for item in operand)
        ):
            raise FilterError(
                f"{path}.{op}.value must be a non-empty list (at most {MAX_LIST_VALUES}) "
                "of strings, numbers or booleans."
            )
    elif not isinstance(operand, _SCALARS):
        raise FilterError(f"{path}.{op}.value must be a string, number or boolean.")


def _check(node: Any, depth: int, counter: list[int], path: str) -> None:
    if not isinstance(node, dict) or len(node) != 1:
        raise FilterError(
            f"{path} must be an object with exactly one operator. "
            f"Valid operators: {', '.join(VALID_OPERATORS)}."
        )

    ((op, value),) = node.items()

    if op in GROUP_OPERATORS:
        if depth >= MAX_DEPTH:
            raise FilterError(f"{path}: filters can be nested at most {MAX_DEPTH} levels deep.")
        if (
            not isinstance(value, list)
            or not MIN_GROUP_ITEMS <= len(value) <= MAX_GROUP_ITEMS
        ):
            raise FilterError(
                f"{path}.{op} must be a list of {MIN_GROUP_ITEMS} to {MAX_GROUP_ITEMS} conditions."
            )
        for index, child in enumerate(value):
            _check(child, depth + 1, counter, f"{path}.{op}[{index}]")
    elif op in LEAF_OPERATORS:
        counter[0] += 1
        if counter[0] > MAX_CONDITIONS:
            raise FilterError(f"A filter can contain at most {MAX_CONDITIONS} conditions.")
        _check_leaf(op, value, path)
    else:
        raise FilterError(
            f"Unknown filter operator {_short(op)} at {path}. "
            f"Valid operators: {', '.join(VALID_OPERATORS)}."
        )


def validate_filter(candidate: Any, name: str = "metadata_filter") -> dict[str, Any]:
    """Return ``candidate`` unchanged if it is a valid Bedrock filter, else raise."""
    _check(candidate, 0, [0], name)
    return candidate


def combine_filters(
    scope: dict[str, Any] | None, caller: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Combine the server-side scope with the caller's filter.

    The two are ANDed, so the caller's filter can only narrow the scope. The
    caller's filter is validated here, which makes this the one place that
    guarantees a bad or broadening filter never reaches AWS.
    """
    if caller is not None:
        validate_filter(caller)
    if scope and caller:
        return {"andAll": [scope, caller]}
    return scope or caller or None
