from __future__ import annotations

MIN_SUCCESS_RATE = 0.90


def completion_rate(complete: int | float, total: int | float) -> float:
    try:
        total_value = float(total)
        complete_value = float(complete)
    except (TypeError, ValueError):
        return 0.0
    if total_value <= 0:
        return 0.0
    return complete_value / total_value


def meets_rate(
    complete: int | float,
    total: int | float,
    *,
    threshold: float = MIN_SUCCESS_RATE,
) -> bool:
    return completion_rate(complete, total) >= threshold


def valid_after_errors(
    total: int | float,
    errors: int | float,
    *,
    threshold: float = MIN_SUCCESS_RATE,
) -> bool:
    try:
        total_value = float(total)
        error_value = float(errors)
    except (TypeError, ValueError):
        return False
    if total_value <= 0:
        return False
    valid = max(total_value - max(error_value, 0.0), 0.0)
    return (valid / total_value) >= threshold
