from scraper.quality_rules import (
    MIN_SUCCESS_RATE,
    completion_rate,
    meets_rate,
    valid_after_errors,
)


def test_min_success_rate_is_90_percent():
    assert MIN_SUCCESS_RATE == 0.90


def test_meets_rate_accepts_exactly_90_percent():
    assert meets_rate(90, 100)
    assert meets_rate(9, 10)
    assert meets_rate(455, 456)


def test_meets_rate_rejects_below_90_percent():
    assert not meets_rate(89, 100)
    assert not meets_rate(8, 10)


def test_valid_after_errors_uses_same_threshold():
    assert valid_after_errors(100, 10)
    assert not valid_after_errors(100, 11)
    assert valid_after_errors(455, 2)


def test_completion_rate_handles_empty_total():
    assert completion_rate(0, 0) == 0.0
    assert not meets_rate(0, 0)
